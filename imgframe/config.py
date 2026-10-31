"""Runtime settings, read from environment variables so the same image works in
Docker Compose, Kubernetes and local runs."""
import os
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


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

    # Object storage (Upgrade 1). With S3_BUCKET set, pixels live in S3/MinIO and Redis
    # holds only control data; without it the original all-in-Redis mode is used.
    s3_bucket: str = os.environ.get("S3_BUCKET", "")
    s3_endpoint: str = os.environ.get("S3_ENDPOINT", "")            # empty = AWS S3
    # Host used in presigned URLs (the browser must reach it); defaults to S3_ENDPOINT.
    s3_public_endpoint: str = os.environ.get("S3_PUBLIC_ENDPOINT", "")
    s3_region: str = os.environ.get("S3_REGION", "us-east-1")
    s3_sse: str = os.environ.get("S3_SSE", "")                       # "", "AES256" or "aws:kms"
    s3_kms_key_id: str = os.environ.get("S3_KMS_KEY_ID", "")
    presign_seconds: int = _int("PRESIGN_SECONDS", 300)
    # Create the bucket and its lifecycle rule at startup (MinIO). On AWS the bucket is
    # provisioned by deploy/aws/setup_s3.sh and the service roles cannot create it.
    s3_manage_bucket: bool = os.environ.get("S3_MANAGE_BUCKET", "1") not in ("0", "false", "no")
    # "redirect": GET /jobs/{id}/result answers 307 -> presigned URL; "proxy": stream via API.
    result_delivery: str = os.environ.get("RESULT_DELIVERY", "redirect")
    # Back-pressure in S3 mode: refuse new jobs while this many tiles are queued.
    max_queued_tiles: int = _int("MAX_QUEUED_TILES", 20000)
    # Final images above this many megapixels are streamed to a tiled TIFF, never held whole.
    stream_output_mp: float = _float("STREAM_OUTPUT_MP", 150.0)

    # Neural operations (Upgrade 2).
    model_dir: str = os.environ.get("MODEL_DIR", str(_ROOT / "models"))

    # Cost-aware planner (Upgrade 3).
    planner: bool = os.environ.get("PLANNER", "1") not in ("0", "false", "no")
    planner_margin: float = _float("PLANNER_MARGIN", 0.2)       # distribute only if >= 20% faster
    planner_workers: int = _int("PLANNER_WORKERS", 0)           # 0 = live worker count
    profile_ema: float = _float("PROFILE_EMA", 0.05)            # weight of each new tile sample


settings = Settings()
