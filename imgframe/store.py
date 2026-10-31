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

Storage modes:
  * S3 mode (S3_BUCKET set): tiles, results and the final image live in object
    storage (see objstore.py); Redis holds only task ids, tile specs, object
    keys and job metadata, a few hundred bytes per tile.
  * Redis mode: the original design with tile bytes in Redis hashes.

Work accounting (for the work-aware autoscaler): every tile carries a
predicted cost in compute-seconds (job:<id>:costs). `work_seconds` is the sum
over tiles not yet completed; the Lua scripts that complete, discard or give
up on a tile subtract its cost atomically, so retries never double count.
`work_added` is a cumulative counter the autoscaler differentiates to get the
arrival rate of work.
"""
import json
import time
import uuid
from dataclasses import dataclass

import redis
from redis.backoff import ExponentialBackoff
from redis.retry import Retry

from .config import settings
from .objstore import ObjStore, default_objstore, job_prefix, result_key, tile_key
from .tiling import TileSpec

P = "imgframe:"
PENDING = P + "queue:pending"
PROCESSING = P + "queue:processing"
LEASES = P + "queue:leases"
ATTEMPTS = P + "queue:attempts"
JOBS = P + "jobs"
WORKERS = P + "workers"
STATS = P + "stats"
WORK = P + "work_seconds"
WORK_ADDED = P + "work_added"


def profile_key(op: str) -> str:
    return f"{P}profile:{op}"


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

# Subtract a tile's predicted cost from the global work counter, exactly once.
_UNCOST = """
local function uncost(job, idx, work)
  local c = redis.call('HGET', job .. ':costs', idx)
  if c and redis.call('HDEL', job .. ':costs', idx) == 1 then
    redis.call('INCRBYFLOAT', work, -tonumber(c))
  end
end
"""

# KEYS: pending, processing, leases, attempts, stats, work
# ARGV: task, idx, result (bytes, or object key in S3 mode), prefix
# Returns {done, total} for a newly counted tile, {-1, total} for a duplicate.
_COMPLETE = _UNCOST + """
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
uncost(job, ARGV[2], KEYS[6])
return {redis.call('HINCRBY', job, 'done', 1), total}
"""

# KEYS: pending, processing, leases, attempts, stats, work   ARGV: now, max_retries, prefix
# Returns {requeued, failed}
_REAP = _UNCOST + """
local expired = redis.call('ZRANGEBYSCORE', KEYS[3], '-inf', ARGV[1])
local requeued, failed = 0, 0
for _, task in ipairs(expired) do
  redis.call('ZREM', KEYS[3], task)
  redis.call('LREM', KEYS[2], 0, task)
  local job = ARGV[3] .. 'job:' .. string.match(task, '^(.*):%d+$')
  local attempts = tonumber(redis.call('HGET', KEYS[4], task) or '0')
  if attempts >= tonumber(ARGV[2]) then
    redis.call('HDEL', KEYS[4], task)
    uncost(job, string.match(task, ':(%d+)$'), KEYS[6])
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
# KEYS: pending, processing, leases, attempts, work   ARGV: task, prefix
_DISCARD = _UNCOST + """
redis.call('LREM', KEYS[2], 0, ARGV[1])
redis.call('ZREM', KEYS[3], ARGV[1])
redis.call('HDEL', KEYS[4], ARGV[1])
local job = ARGV[2] .. 'job:' .. string.match(ARGV[1], '^(.*):%d+$')
uncost(job, string.match(ARGV[1], ':(%d+)$'), KEYS[5])
return 1
"""

# Online profiling: exponential moving average of compute seconds per pixel.
# KEYS: profile   ARGV: sample (s/pixel), weight
_EMA = """
local old = tonumber(redis.call('HGET', KEYS[1], 'c_op') or '0')
local new = tonumber(ARGV[1])
if old > 0 then new = old * (1 - tonumber(ARGV[2])) + new * tonumber(ARGV[2]) end
redis.call('HSET', KEYS[1], 'c_op', string.format('%.6e', new))
redis.call('HINCRBY', KEYS[1], 'samples', 1)
return tostring(new)
"""


@dataclass
class TaskPayload:
    task_id: str
    job_id: str
    op: str
    spec: TileSpec
    tile: bytes
    fetch_s: float = 0.0   # time to fetch the tile (S3 GET or Redis HGET)


def _s(v) -> str:
    return v.decode() if isinstance(v, bytes) else v


_NO_OBJ = object()


class Store:
    def __init__(self, client: redis.Redis | None = None, url: str | None = None,
                 obj: ObjStore | None | object = _NO_OBJ):
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
        self._ema = self.r.register_script(_EMA)
        # Object store for pixels; None = all-in-Redis mode.
        self.obj = default_objstore() if obj is _NO_OBJ else obj

    # ---------------------------------------------------------------- jobs
    @property
    def storage(self) -> str:
        return "s3" if self.obj is not None else "redis"

    @staticmethod
    def new_job_id() -> str:
        return uuid.uuid4().hex[:12]

    def create_job(self, *, op: str, height: int, width: int, tile_size: int,
                   tiles: list[tuple[TileSpec, bytes]], filename: str = "",
                   partition_s: float = 0.0, job_id: str | None = None, scale: int = 1,
                   costs: dict[int, float] | None = None, plan: dict | None = None) -> str:
        """Register a job and enqueue one task per tile. In S3 mode the tile bytes are
        uploaded first, so a worker never claims a task whose tile is missing."""
        job_id = job_id or self.new_job_id()
        upload_s = 0.0
        if self.obj is not None:
            t0 = time.perf_counter()
            self.obj.put_many((tile_key(job_id, s.index), data) for s, data in tiles)
            upload_s = time.perf_counter() - t0
        now = time.time()
        ttl = settings.job_ttl_seconds
        meta = {
            "id": job_id, "status": "queued", "op": op, "filename": filename,
            "height": height, "width": width, "tile_size": tile_size, "scale": scale,
            "total": len(tiles), "done": 0, "retries": 0, "storage": self.storage,
            "created": now, "partition_s": partition_s + upload_s, "upload_s": upload_s,
        }
        if plan:
            meta.update({"plan_mode": plan["mode"], "planned_workers": plan["workers"],
                         "predicted_s": plan["predicted_s"], "plan": json.dumps(plan)})
        total_cost = sum(costs.values()) if costs else 0.0
        if costs:
            meta["work_s"] = total_cost
        pipe = self.r.pipeline(transaction=True)
        pipe.hset(job_key(job_id), mapping=meta)
        pipe.hset(job_key(job_id, "specs"),
                  mapping={s.index: json.dumps(s.to_dict()) for s, _ in tiles})
        parts = ["", "specs"]
        if self.obj is None:
            pipe.hset(job_key(job_id, "tiles"), mapping={s.index: data for s, data in tiles})
            parts.append("tiles")
        if costs:
            pipe.hset(job_key(job_id, "costs"), mapping=costs)
            pipe.incrbyfloat(WORK, total_cost)
            pipe.incrbyfloat(WORK_ADDED, total_cost)
            parts.append("costs")
        for part in parts:
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
        job["scale"] = int(job.get("scale", 1))
        if "planned_workers" in job:
            job["planned_workers"] = int(job["planned_workers"])
        for k in ("created", "started", "processed", "finished", "partition_s", "aggregate_s",
                  "compute_s", "upload_s", "predicted_s", "work_s", "io_s"):
            if k in job:
                job[k] = float(job[k])
        return job

    def list_jobs(self, limit: int = 20) -> list[dict]:
        ids = [_s(j) for j in self.r.zrevrange(JOBS, 0, limit - 1)]
        return [j for j in (self.get_job(i) for i in ids) if j]

    def get_result(self, job_id: str) -> bytes | None:
        """Final image in raw tile format (None if absent or streamed to TIFF)."""
        if self.obj is not None:
            return self.obj.get(job_prefix(job_id) + "output.raw")
        return self.r.get(job_key(job_id, "result"))

    # --------------------------------------------------------------- queue
    def claim(self, now: float | None = None) -> str | None:
        task = self._claim(keys=[PENDING, PROCESSING, LEASES, ATTEMPTS],
                           args=[now or time.time(), settings.lease_seconds, P])
        return _s(task) if task else None

    def load_task(self, task_id: str) -> TaskPayload | None:
        """Fetch what a worker needs; None if the job is gone or no longer runnable."""
        job_id, idx = split_task(task_id)
        t0 = time.perf_counter()
        pipe = self.r.pipeline()
        pipe.hmget(job_key(job_id), "status", "op")
        pipe.hget(job_key(job_id, "specs"), idx)
        if self.obj is None:
            pipe.hget(job_key(job_id, "tiles"), idx)
            (status, op), spec, tile = pipe.execute()
        else:
            (status, op), spec = pipe.execute()
            tile = None
            if status is not None and _s(status) not in ("failed", "done"):
                tile = self.obj.get(tile_key(job_id, idx))
        if status is None or _s(status) in ("failed", "done") or spec is None or tile is None:
            return None
        return TaskPayload(task_id, job_id, _s(op), TileSpec.from_dict(json.loads(spec)), tile,
                           time.perf_counter() - t0)

    def heartbeat(self, task_id: str, now: float | None = None) -> bool:
        return bool(self.r.zadd(LEASES, {task_id: (now or time.time()) + settings.lease_seconds},
                                xx=True, ch=True))

    def expire_lease(self, task_id: str) -> None:
        """Mark a claimed task as failed now; the reaper retries it (counting the attempt)."""
        self.r.zadd(LEASES, {task_id: 0}, xx=True)

    def complete(self, task_id: str, result: bytes, compute_s: float = 0.0) -> tuple[int, int]:
        """Store a tile result. Returns (done, total); done == -1 for a duplicate."""
        job_id, idx = split_task(task_id)
        if self.obj is not None:
            # Write first, then record: a retried tile overwrites the same key, which is safe.
            key = result_key(job_id, idx)
            self.obj.put(key, result)
            result = key
        done, total = self._complete(keys=[PENDING, PROCESSING, LEASES, ATTEMPTS, STATS, WORK],
                                     args=[task_id, idx, result, P])
        if done != -1:
            self.r.hincrbyfloat(job_key(job_id), "compute_s", compute_s)
            self.r.expire(job_key(job_id, "results"), settings.job_ttl_seconds)
            self.r.expire(job_key(job_id, "completed"), settings.job_ttl_seconds)
        return int(done), int(total)

    def release(self, task_id: str) -> bool:
        return bool(self._release(keys=[PENDING, PROCESSING, LEASES, ATTEMPTS], args=[task_id]))

    def discard(self, task_id: str) -> None:
        self._discard(keys=[PENDING, PROCESSING, LEASES, ATTEMPTS, WORK], args=[task_id, P])

    def fail_job(self, job_id: str, error: str) -> None:
        self.r.hset(job_key(job_id), mapping={"status": "failed", "error": error,
                                              "finished": time.time()})

    def reap(self, now: float | None = None) -> tuple[int, int]:
        """Requeue tasks whose lease expired. Returns (requeued, failed)."""
        requeued, failed = self._reap(keys=[PENDING, PROCESSING, LEASES, ATTEMPTS, STATS, WORK],
                                      args=[now or time.time(), settings.max_retries, P])
        return int(requeued), int(failed)

    # --------------------------------------------------------- aggregation
    def iter_results(self, job_id: str, batch: int = 64):
        """Yield (spec, result bytes) in tile-index order, fetching S3 objects in batches
        so at most `batch` results are in memory (used by streaming aggregation)."""
        specs = {int(k): TileSpec.from_dict(json.loads(v))
                 for k, v in self.r.hgetall(job_key(job_id, "specs")).items()}
        refs = {int(k): v for k, v in self.r.hgetall(job_key(job_id, "results")).items()}
        order = sorted(refs)
        for i in range(0, len(order), batch):
            chunk = order[i : i + batch]
            if self.obj is not None:
                datas = self.obj.get_many([_s(refs[k]) for k in chunk])
            else:
                datas = [refs[k] for k in chunk]
            for k, data in zip(chunk, datas):
                if data is None:
                    raise RuntimeError(f"result of tile {k} missing")
                yield specs[k], data

    def load_results(self, job_id: str) -> list[tuple[TileSpec, bytes]]:
        return list(self.iter_results(job_id, batch=1 << 30))

    def finish_job(self, job_id: str, result: bytes | None, aggregate_s: float,
                   processed_at: float | None = None, output_key: str | None = None) -> None:
        """Store the reconstructed image (raw tile format) and mark the job done.
        `output_key`: the image was already written to object storage under this key."""
        if self.obj is not None and result is not None:
            output_key = job_prefix(job_id) + "output.raw"
            self.obj.put(output_key, result)
        pipe = self.r.pipeline(transaction=True)
        if self.obj is None:
            pipe.set(job_key(job_id, "result"), result, ex=settings.result_ttl_seconds)
        done = {"status": "done", "finished": time.time(),
                "processed": processed_at or time.time(), "aggregate_s": aggregate_s}
        if output_key:
            done["output_key"] = output_key
        pipe.hset(job_key(job_id), mapping=done)
        # Free per-tile data now that the final image exists.
        pipe.delete(job_key(job_id, "tiles"), job_key(job_id, "results"))
        pipe.execute()
        if self.obj is not None:  # tiles/results are no longer needed either
            self.obj.delete_prefix(job_prefix(job_id) + "tiles/")
            self.obj.delete_prefix(job_prefix(job_id) + "results/")

    def get_encoded_result(self, job_id: str, fmt: str) -> bytes | None:
        if self.obj is not None:
            return self.obj.get(job_prefix(job_id) + f"result.{fmt}")
        return self.r.get(job_key(job_id, f"result.{fmt}"))

    def cache_encoded_result(self, job_id: str, fmt: str, data: bytes) -> None:
        if self.obj is not None:
            ctype = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp",
                     "preview": "image/jpeg"}.get(fmt.split(".")[0], "application/octet-stream")
            self.obj.put(job_prefix(job_id) + f"result.{fmt}", data, ctype)
            return
        self.r.set(job_key(job_id, f"result.{fmt}"), data, ex=settings.result_ttl_seconds)

    def delete_job(self, job_id: str) -> bool:
        """Remove a job and all its data (tiles, results, final image, cached encodings)."""
        remaining = [float(v) for v in self.r.hvals(job_key(job_id, "costs"))]
        keys = list(self.r.scan_iter(match=job_key(job_id) + "*", count=100))
        pipe = self.r.pipeline()
        if keys:
            pipe.delete(*keys)
        if remaining:
            pipe.incrbyfloat(WORK, -sum(remaining))
        pipe.zrem(JOBS, job_id)
        pipe.execute()
        if self.obj is not None:
            self.obj.delete_prefix(job_prefix(job_id))
        return bool(keys)

    def job_memory(self, job_id: str) -> int:
        """Bytes Redis uses for one job's keys (MEMORY USAGE), for the S3 before/after study."""
        total = 0
        for k in self.r.scan_iter(match=job_key(job_id) + "*", count=100):
            try:
                total += int(self.r.memory_usage(k, samples=0) or 0)
            except redis.ResponseError:  # fakeredis has no MEMORY USAGE
                total += 0
        return total

    def queue_ok(self, new_tiles: int) -> bool:
        """Back-pressure in S3 mode: Redis holds only control data, so the limit is queue depth."""
        return self.queue_depth() + new_tiles <= settings.max_queued_tiles

    def memory_ok(self, extra_bytes: int) -> bool:
        """Admission control: would storing `extra_bytes` more stay within the memory budget?"""
        try:
            maxmem = int(self.r.config_get("maxmemory").get("maxmemory", 0))
            used = int(self.r.info("memory")["used_memory"])
        except Exception:  # CONFIG may be disabled (managed Redis) or unsupported
            return True
        return maxmem == 0 or used + extra_bytes <= settings.redis_memory_budget * maxmem

    # ------------------------------------------------------------ profiles
    def update_profile(self, op: str, compute_s: float, pixels: int) -> float | None:
        """Fold one tile's measured compute time into the op's online c_op estimate."""
        if pixels <= 0 or compute_s <= 0:
            return None
        return float(self._ema(keys=[profile_key(op)], args=[compute_s / pixels, settings.profile_ema]))

    def profile_c_op(self, op: str) -> float | None:
        v = self.r.hget(profile_key(op), "c_op")
        return float(v) if v else None

    def work_seconds(self) -> float:
        return max(0.0, float(self.r.get(WORK) or 0.0))

    def work_added(self) -> float:
        return float(self.r.get(WORK_ADDED) or 0.0)

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
        try:
            redis_mem = int(self.r.info("memory")["used_memory"])
        except Exception:
            redis_mem = None
        return {
            "storage": self.storage,
            "work_seconds": round(self.work_seconds(), 3),
            "work_added": round(self.work_added(), 3),
            "redis_used_memory": redis_mem,
            "pending": self.r.llen(PENDING),
            "processing": self.r.llen(PROCESSING),
            "tasks_done": stats.get("tasks_done", 0),
            "retries": stats.get("retries", 0),
            "active_workers": sum(w["alive"] for w in workers),
            "workers": workers,
        }

    def queue_depth(self) -> int:
        return self.r.llen(PENDING) + self.r.llen(PROCESSING)
