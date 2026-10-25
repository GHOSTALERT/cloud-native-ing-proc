"""Runtime settings, read from environment variables so the same image works in
Docker Compose, Kubernetes and local runs."""
import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    redis_url: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    tile_size: int = _int("TILE_SIZE", 512)
    lease_seconds: float = _float("LEASE_SECONDS", 15.0)
    heartbeat_seconds: float = _float("HEARTBEAT_SECONDS", 3.0)
    max_retries: int = _int("MAX_RETRIES", 3)
    reaper_interval: float = _float("REAPER_INTERVAL", 2.0)
    worker_procs: int = _int("WORKER_PROCS", 1)
    job_ttl_seconds: int = _int("JOB_TTL_SECONDS", 3600)
    # Final images are large (raw pixels); keep them only long enough to download.
    result_ttl_seconds: int = _int("RESULT_TTL_SECONDS", 900)
    # Admission control: refuse new jobs (HTTP 503) beyond this fraction of Redis maxmemory.
    redis_memory_budget: float = _float("REDIS_MEMORY_BUDGET", 0.9)
    max_concurrent_uploads: int = _int("MAX_CONCURRENT_UPLOADS", 2)


settings = Settings()
