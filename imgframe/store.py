"""Redis data model and reliable task queue.

Queue design (fault tolerance):
  pending    LIST  task ids waiting for a worker
  processing LIST  task ids currently claimed
  leases     ZSET  task id -> lease expiry (unix time); heartbeats extend it
  attempts   HASH  task id -> number of times claimed

A worker claims a task atomically (pending -> processing + lease). If the
worker dies, its lease stops being renewed; the reaper moves expired tasks back
to `pending` until MAX_RETRIES is reached, then marks the job failed.
Completion is idempotent (a per-job `completed` set), so a task that was
retried after a slow worker finished anyway is never counted twice.

Task ids look like "<job_id>:<tile_index>".
"""
import json
import time
import uuid
from dataclasses import dataclass

import redis
from redis.backoff import ExponentialBackoff
from redis.retry import Retry

from .config import settings
from .tiling import TileSpec

P = "imgframe:"
PENDING = P + "queue:pending"
PROCESSING = P + "queue:processing"
LEASES = P + "queue:leases"
ATTEMPTS = P + "queue:attempts"
JOBS = P + "jobs"
WORKERS = P + "workers"
STATS = P + "stats"


def job_key(job_id: str, part: str = "") -> str:
    return f"{P}job:{job_id}" + (f":{part}" if part else "")


def split_task(task_id: str) -> tuple[str, int]:
    job_id, idx = task_id.rsplit(":", 1)
    return job_id, int(idx)


# KEYS: pending, processing, leases, attempts   ARGV: now, lease_seconds, prefix
_CLAIM = """
local task = redis.call('LMOVE', KEYS[1], KEYS[2], 'LEFT', 'RIGHT')
if not task then return false end
redis.call('ZADD', KEYS[3], tonumber(ARGV[1]) + tonumber(ARGV[2]), task)
redis.call('HINCRBY', KEYS[4], task, 1)
local job = ARGV[3] .. 'job:' .. string.match(task, '^(.*):%d+$')
if redis.call('HSETNX', job, 'started', ARGV[1]) == 1 then
  redis.call('HSET', job, 'status', 'running')
end
return task
"""

# KEYS: pending, processing, leases, attempts, stats
# ARGV: task, idx, result, prefix
# Returns {done, total} for a newly counted tile, {-1, total} for a duplicate.
_COMPLETE = """
local task = ARGV[1]
redis.call('LREM', KEYS[2], 0, task)
redis.call('LREM', KEYS[1], 0, task)
redis.call('ZREM', KEYS[3], task)
redis.call('HDEL', KEYS[4], task)
local job = ARGV[4] .. 'job:' .. string.match(task, '^(.*):%d+$')
local total = tonumber(redis.call('HGET', job, 'total') or '0')
if redis.call('SADD', job .. ':completed', ARGV[2]) == 0 then return {-1, total} end
redis.call('HSET', job .. ':results', ARGV[2], ARGV[3])
redis.call('HINCRBY', KEYS[5], 'tasks_done', 1)
return {redis.call('HINCRBY', job, 'done', 1), total}
"""

# KEYS: pending, processing, leases, attempts, stats   ARGV: now, max_retries, prefix
# Returns {requeued, failed}
_REAP = """
local expired = redis.call('ZRANGEBYSCORE', KEYS[3], '-inf', ARGV[1])
local requeued, failed = 0, 0
for _, task in ipairs(expired) do
  redis.call('ZREM', KEYS[3], task)
  redis.call('LREM', KEYS[2], 0, task)
  local job = ARGV[3] .. 'job:' .. string.match(task, '^(.*):%d+$')
  local attempts = tonumber(redis.call('HGET', KEYS[4], task) or '0')
  if attempts >= tonumber(ARGV[2]) then
    redis.call('HDEL', KEYS[4], task)
    if redis.call('HGET', job, 'status') ~= 'done' then
      redis.call('HSET', job, 'status', 'failed', 'error',
        'tile ' .. task .. ' lost after ' .. attempts .. ' attempts', 'finished', ARGV[1])
    end
    failed = failed + 1
  else
    redis.call('LPUSH', KEYS[1], task)
    redis.call('HINCRBY', job, 'retries', 1)
    redis.call('HINCRBY', KEYS[5], 'retries', 1)
    requeued = requeued + 1
  end
end
return {requeued, failed}
"""

# Graceful hand-back (e.g. SIGTERM): does not count as a failed attempt.
# KEYS: pending, processing, leases, attempts   ARGV: task
_RELEASE = """
if redis.call('LREM', KEYS[2], 0, ARGV[1]) == 0 then return 0 end
redis.call('ZREM', KEYS[3], ARGV[1])
redis.call('HINCRBY', KEYS[4], ARGV[1], -1)
redis.call('LPUSH', KEYS[1], ARGV[1])
return 1
"""

# Drop a task without a result (its job failed or was deleted).
_DISCARD = """
redis.call('LREM', KEYS[2], 0, ARGV[1])
redis.call('ZREM', KEYS[3], ARGV[1])
redis.call('HDEL', KEYS[4], ARGV[1])
return 1
"""


@dataclass
class TaskPayload:
    task_id: str
    job_id: str
    op: str
    spec: TileSpec
    tile: bytes


def _s(v) -> str:
    return v.decode() if isinstance(v, bytes) else v


class Store:
    def __init__(self, client: redis.Redis | None = None, url: str | None = None):
        # Retry transient connection drops (Redis restart, pod rescheduling). Retrying a
        # claim whose reply was lost is safe: the orphaned lease expires and is requeued.
        self.r = client or redis.Redis.from_url(
            url or settings.redis_url,
            retry=Retry(ExponentialBackoff(cap=2.0, base=0.05), retries=5),
            retry_on_error=[redis.ConnectionError, redis.TimeoutError],
            health_check_interval=30,
        )
        self._claim = self.r.register_script(_CLAIM)
        self._complete = self.r.register_script(_COMPLETE)
        self._reap = self.r.register_script(_REAP)
        self._release = self.r.register_script(_RELEASE)
        self._discard = self.r.register_script(_DISCARD)

    # ---------------------------------------------------------------- jobs
    def create_job(self, *, op: str, height: int, width: int, tile_size: int,
                   tiles: list[tuple[TileSpec, bytes]], filename: str = "",
                   partition_s: float = 0.0) -> str:
        job_id = uuid.uuid4().hex[:12]
        now = time.time()
        ttl = settings.job_ttl_seconds
        pipe = self.r.pipeline(transaction=True)
        pipe.hset(job_key(job_id), mapping={
            "id": job_id, "status": "queued", "op": op, "filename": filename,
            "height": height, "width": width, "tile_size": tile_size,
            "total": len(tiles), "done": 0, "retries": 0,
            "created": now, "partition_s": partition_s,
        })
        pipe.hset(job_key(job_id, "specs"),
                  mapping={s.index: json.dumps(s.to_dict()) for s, _ in tiles})
        pipe.hset(job_key(job_id, "tiles"), mapping={s.index: data for s, data in tiles})
        for part in ("", "specs", "tiles"):
            pipe.expire(job_key(job_id, part), ttl)
        pipe.zadd(JOBS, {job_id: now})
        pipe.rpush(PENDING, *[f"{job_id}:{s.index}" for s, _ in tiles])
        pipe.execute()
        return job_id

    def get_job(self, job_id: str) -> dict | None:
        raw = self.r.hgetall(job_key(job_id))
        if not raw:
            return None
        job = {_s(k): _s(v) for k, v in raw.items()}
        for k in ("height", "width", "tile_size", "total", "done", "retries"):
            job[k] = int(job.get(k, 0))
        for k in ("created", "started", "processed", "finished", "partition_s", "aggregate_s",
                  "compute_s"):
            if k in job:
                job[k] = float(job[k])
        return job

    def list_jobs(self, limit: int = 20) -> list[dict]:
        ids = [_s(j) for j in self.r.zrevrange(JOBS, 0, limit - 1)]
        return [j for j in (self.get_job(i) for i in ids) if j]

    def get_result(self, job_id: str) -> bytes | None:
        return self.r.get(job_key(job_id, "result"))

    # --------------------------------------------------------------- queue
    def claim(self, now: float | None = None) -> str | None:
        task = self._claim(keys=[PENDING, PROCESSING, LEASES, ATTEMPTS],
                           args=[now or time.time(), settings.lease_seconds, P])
        return _s(task) if task else None

    def load_task(self, task_id: str) -> TaskPayload | None:
        """Fetch what a worker needs; None if the job is gone or no longer runnable."""
        job_id, idx = split_task(task_id)
        pipe = self.r.pipeline()
        pipe.hmget(job_key(job_id), "status", "op")
        pipe.hget(job_key(job_id, "specs"), idx)
        pipe.hget(job_key(job_id, "tiles"), idx)
        (status, op), spec, tile = pipe.execute()
        if status is None or _s(status) in ("failed", "done") or spec is None or tile is None:
            return None
        return TaskPayload(task_id, job_id, _s(op), TileSpec.from_dict(json.loads(spec)), tile)

    def heartbeat(self, task_id: str, now: float | None = None) -> bool:
        return bool(self.r.zadd(LEASES, {task_id: (now or time.time()) + settings.lease_seconds},
                                xx=True, ch=True))

    def expire_lease(self, task_id: str) -> None:
        """Mark a claimed task as failed now; the reaper retries it (counting the attempt)."""
        self.r.zadd(LEASES, {task_id: 0}, xx=True)

    def complete(self, task_id: str, result: bytes, compute_s: float = 0.0) -> tuple[int, int]:
        """Store a tile result. Returns (done, total); done == -1 for a duplicate."""
        job_id, idx = split_task(task_id)
        done, total = self._complete(keys=[PENDING, PROCESSING, LEASES, ATTEMPTS, STATS],
                                     args=[task_id, idx, result, P])
        if done != -1:
            self.r.hincrbyfloat(job_key(job_id), "compute_s", compute_s)
            self.r.expire(job_key(job_id, "results"), settings.job_ttl_seconds)
            self.r.expire(job_key(job_id, "completed"), settings.job_ttl_seconds)
        return int(done), int(total)

    def release(self, task_id: str) -> bool:
        return bool(self._release(keys=[PENDING, PROCESSING, LEASES, ATTEMPTS], args=[task_id]))

    def discard(self, task_id: str) -> None:
        self._discard(keys=[PENDING, PROCESSING, LEASES, ATTEMPTS], args=[task_id])

    def fail_job(self, job_id: str, error: str) -> None:
        self.r.hset(job_key(job_id), mapping={"status": "failed", "error": error,
                                              "finished": time.time()})

    def reap(self, now: float | None = None) -> tuple[int, int]:
        """Requeue tasks whose lease expired. Returns (requeued, failed)."""
        requeued, failed = self._reap(keys=[PENDING, PROCESSING, LEASES, ATTEMPTS, STATS],
                                      args=[now or time.time(), settings.max_retries, P])
        return int(requeued), int(failed)

    # --------------------------------------------------------- aggregation
    def load_results(self, job_id: str) -> list[tuple[TileSpec, bytes]]:
        specs = self.r.hgetall(job_key(job_id, "specs"))
        results = self.r.hgetall(job_key(job_id, "results"))
        return [(TileSpec.from_dict(json.loads(specs[k])), results[k]) for k in results]

    def finish_job(self, job_id: str, result: bytes, aggregate_s: float,
                   processed_at: float | None = None) -> None:
        """Store the reconstructed image (raw tile format) and mark the job done."""
        ttl = settings.job_ttl_seconds
        pipe = self.r.pipeline(transaction=True)
        pipe.set(job_key(job_id, "result"), result, ex=settings.result_ttl_seconds)
        pipe.hset(job_key(job_id), mapping={"status": "done", "finished": time.time(),
                                            "processed": processed_at or time.time(),
                                            "aggregate_s": aggregate_s})
        # Free per-tile data now that the final image exists.
        pipe.delete(job_key(job_id, "tiles"), job_key(job_id, "results"))
        pipe.execute()

    def get_encoded_result(self, job_id: str, fmt: str) -> bytes | None:
        return self.r.get(job_key(job_id, f"result.{fmt}"))

    def cache_encoded_result(self, job_id: str, fmt: str, data: bytes) -> None:
        self.r.set(job_key(job_id, f"result.{fmt}"), data, ex=settings.result_ttl_seconds)

    def delete_job(self, job_id: str) -> bool:
        """Remove a job and all its data (tiles, results, final image, cached encodings)."""
        keys = list(self.r.scan_iter(match=job_key(job_id) + "*", count=100))
        pipe = self.r.pipeline()
        if keys:
            pipe.delete(*keys)
        pipe.zrem(JOBS, job_id)
        pipe.execute()
        return bool(keys)

    def memory_ok(self, extra_bytes: int) -> bool:
        """Admission control: would storing `extra_bytes` more stay within the memory budget?"""
        try:
            maxmem = int(self.r.config_get("maxmemory").get("maxmemory", 0))
            used = int(self.r.info("memory")["used_memory"])
        except Exception:  # CONFIG may be disabled (managed Redis) or unsupported
            return True
        return maxmem == 0 or used + extra_bytes <= settings.redis_memory_budget * maxmem

    # ------------------------------------------------------------- metrics
    def record_worker(self, worker_id: str, tiles: int = 0, busy_s: float = 0.0) -> None:
        key = f"{WORKERS}:{worker_id}"
        pipe = self.r.pipeline()
        pipe.hset(key, "last_seen", time.time())
        if tiles:
            pipe.hincrby(key, "tiles", tiles)
            pipe.hincrbyfloat(key, "busy_s", busy_s)
        pipe.expire(key, 60)
        pipe.sadd(WORKERS, worker_id)
        pipe.execute()

    def metrics(self) -> dict:
        workers = []
        for wid in sorted(_s(w) for w in self.r.smembers(WORKERS)):
            data = self.r.hgetall(f"{WORKERS}:{wid}")
            if not data:
                self.r.srem(WORKERS, wid)
                continue
            d = {_s(k): float(v) for k, v in data.items()}
            workers.append({"id": wid, "tiles": int(d.get("tiles", 0)),
                            "busy_s": round(d.get("busy_s", 0.0), 3),
                            "last_seen_ago_s": round(time.time() - d["last_seen"], 1)})
        for w in workers:
            w["alive"] = w["last_seen_ago_s"] < 10
        stats = {_s(k): int(v) for k, v in self.r.hgetall(STATS).items()}
        return {
            "pending": self.r.llen(PENDING),
            "processing": self.r.llen(PROCESSING),
            "tasks_done": stats.get("tasks_done", 0),
            "retries": stats.get("retries", 0),
            "active_workers": sum(w["alive"] for w in workers),
            "workers": workers,
        }

    def queue_depth(self) -> int:
        return self.r.llen(PENDING) + self.r.llen(PROCESSING)
