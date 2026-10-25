"""Queue-length autoscaler for the worker Deployment.

Every POLL_SECONDS it reads the Redis queue depth and sets
    replicas = clamp(ceil(depth / TASKS_PER_WORKER), MIN_WORKERS, MAX_WORKERS)
Scale-ups apply immediately; scale-downs wait for SCALE_DOWN_DELAY seconds of
lower demand so pods are not churned between jobs.
"""
import logging
import math
import os
import time

from imgframe.store import Store

log = logging.getLogger("autoscaler")

NAMESPACE = os.environ.get("NAMESPACE", "imgframe")
DEPLOYMENT = os.environ.get("WORKER_DEPLOYMENT", "worker")
MIN_WORKERS = int(os.environ.get("MIN_WORKERS", "1"))
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "8"))
TASKS_PER_WORKER = int(os.environ.get("TASKS_PER_WORKER", "16"))
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "3"))
SCALE_DOWN_DELAY = float(os.environ.get("SCALE_DOWN_DELAY", "30"))


def desired_replicas(depth: int, min_w: int = MIN_WORKERS, max_w: int = MAX_WORKERS,
                     per_worker: int = TASKS_PER_WORKER) -> int:
    return max(min_w, min(max_w, math.ceil(depth / per_worker)))


class Autoscaler:
    def __init__(self, store: Store, apps_api):
        self.store = store
        self.apps = apps_api
        self.low_since: float | None = None

    def current(self) -> int:
        return self.apps.read_namespaced_deployment_scale(DEPLOYMENT, NAMESPACE).spec.replicas or 0

    def scale(self, replicas: int) -> None:
        self.apps.patch_namespaced_deployment_scale(DEPLOYMENT, NAMESPACE,
                                                    {"spec": {"replicas": replicas}})

    def tick(self, now: float | None = None) -> int:
        now = now or time.time()
        depth = self.store.queue_depth()
        want, have = desired_replicas(depth), self.current()
        if want > have:
            log.info("queue depth %d: scaling up %d -> %d", depth, have, want)
            self.scale(want)
            self.low_since = None
            return want
        if want < have:
            self.low_since = self.low_since or now
            if now - self.low_since >= SCALE_DOWN_DELAY:
                log.info("queue depth %d: scaling down %d -> %d", depth, have, want)
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
    log.info("autoscaling %s/%s between %d and %d replicas (%d tasks per worker)",
             NAMESPACE, DEPLOYMENT, MIN_WORKERS, MAX_WORKERS, TASKS_PER_WORKER)
    while True:
        try:
            scaler.tick()
        except Exception:
            log.exception("autoscaler tick failed")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
