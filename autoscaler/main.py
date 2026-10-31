"""Autoscaler for the worker Deployment.

POLICY=work (default): scale on predicted compute-seconds, not task count.
    W = predicted seconds of work queued or in progress (Redis imgframe:work_seconds)
    λ = arrival rate of work in compute-seconds per second (EMA of d(work_added)/dt)
    D = LATENCY_TARGET_S minus POD_STARTUP_S, u = TARGET_UTILIZATION
    replicas = clamp(max(ceil(W / (D * u)), ceil(λ / u)), MIN_WORKERS, MAX_WORKERS)
The first term clears the backlog within the latency target; the second keeps
up with the incoming rate (Little's law on work), so pods are added before a
backlog builds.

POLICY=queue: the original rule, replicas = clamp(ceil(depth / TASKS_PER_WORKER), MIN, MAX).

Every POLL_SECONDS the policy is evaluated. Scale-ups apply immediately;
scale-downs wait for SCALE_DOWN_DELAY seconds of lower demand so pods are not
churned between jobs.
"""
import logging
import math
import os
import time

from imgframe.store import Store

log = logging.getLogger("autoscaler")

NAMESPACE = os.environ.get("NAMESPACE", "imgframe")
DEPLOYMENT = os.environ.get("WORKER_DEPLOYMENT", "worker")
POLICY = os.environ.get("POLICY", "work")
MIN_WORKERS = int(os.environ.get("MIN_WORKERS", "1"))
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "8"))
TASKS_PER_WORKER = int(os.environ.get("TASKS_PER_WORKER", "16"))
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "3"))
SCALE_DOWN_DELAY = float(os.environ.get("SCALE_DOWN_DELAY", "30"))
LATENCY_TARGET_S = float(os.environ.get("LATENCY_TARGET_S", "30"))
TARGET_UTILIZATION = float(os.environ.get("TARGET_UTILIZATION", "0.8"))
POD_STARTUP_S = float(os.environ.get("POD_STARTUP_S", "0"))
RATE_EMA = float(os.environ.get("RATE_EMA", "0.3"))


def desired_replicas(depth: int, min_w: int = MIN_WORKERS, max_w: int = MAX_WORKERS,
                     per_worker: int = TASKS_PER_WORKER) -> int:
    """Queue-length rule (original policy, also what KEDA's Redis list scaler does)."""
    return max(min_w, min(max_w, math.ceil(depth / per_worker)))


def work_replicas(work_s: float, rate: float, latency_target: float = LATENCY_TARGET_S,
                  utilization: float = TARGET_UTILIZATION, min_w: int = MIN_WORKERS,
                  max_w: int = MAX_WORKERS, startup_s: float = POD_STARTUP_S) -> int:
    """Work-aware rule: enough workers to clear `work_s` within the latency target and to
    keep up with `rate` compute-seconds of new work per second."""
    d = max(1e-3, latency_target - startup_s)
    backlog = math.ceil(max(0.0, work_s) / (d * utilization) - 1e-9)
    arrival = math.ceil(max(0.0, rate) / utilization - 1e-9)
    return max(min_w, min(max_w, max(backlog, arrival)))


class Autoscaler:
    def __init__(self, store: Store, apps_api, policy: str = POLICY):
        self.store = store
        self.apps = apps_api
        self.policy = policy
        self.low_since: float | None = None
        self.rate = 0.0
        self._last: tuple[float, float] | None = None  # (time, work_added)

    def current(self) -> int:
        return self.apps.read_namespaced_deployment_scale(DEPLOYMENT, NAMESPACE).spec.replicas or 0

    def scale(self, replicas: int) -> None:
        self.apps.patch_namespaced_deployment_scale(DEPLOYMENT, NAMESPACE,
                                                    {"spec": {"replicas": replicas}})

    def _update_rate(self, now: float) -> None:
        added = self.store.work_added()
        if self._last is not None and now > self._last[0]:
            inst = max(0.0, added - self._last[1]) / (now - self._last[0])
            self.rate = RATE_EMA * inst + (1 - RATE_EMA) * self.rate
        self._last = (now, added)

    def want(self, now: float) -> tuple[int, str]:
        if self.policy == "queue":
            depth = self.store.queue_depth()
            return desired_replicas(depth), f"queue depth {depth}"
        self._update_rate(now)
        work = self.store.work_seconds()
        return work_replicas(work, self.rate), f"work {work:.1f}s, rate {self.rate:.2f}s/s"

    def tick(self, now: float | None = None) -> int:
        now = now or time.time()
        want, why = self.want(now)
        have = self.current()
        if want > have:
            log.info("%s: scaling up %d -> %d", why, have, want)
            self.scale(want)
            self.low_since = None
            return want
        if want < have:
            self.low_since = self.low_since or now
            if now - self.low_since >= SCALE_DOWN_DELAY:
                log.info("%s: scaling down %d -> %d", why, have, want)
                self.scale(want)
                self.low_since = None
                return want
        else:
            self.low_since = None
        return have


def main() -> None:
    from kubernetes import client, config

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    scaler = Autoscaler(Store(), client.AppsV1Api())
    if POLICY == "queue":
        log.info("autoscaling %s/%s between %d and %d replicas (queue length, %d tasks per worker)",
                 NAMESPACE, DEPLOYMENT, MIN_WORKERS, MAX_WORKERS, TASKS_PER_WORKER)
    else:
        log.info("autoscaling %s/%s between %d and %d replicas (work-aware: latency target %.0fs, "
                 "utilization %.2f)", NAMESPACE, DEPLOYMENT, MIN_WORKERS, MAX_WORKERS,
                 LATENCY_TARGET_S, TARGET_UTILIZATION)
    while True:
        try:
            scaler.tick()
        except Exception:
            log.exception("autoscaler tick failed")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
