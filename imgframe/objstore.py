"""Object storage for pixels (S3 on AWS, MinIO locally).

Redis keeps only control data (task ids, tile specs, job metadata); every input
tile, result tile and final image lives here under

    jobs/<job_id>/input.png           original upload
    jobs/<job_id>/tiles/<idx>.raw     halo tile (raw tile format)
    jobs/<job_id>/results/<idx>.raw   processed tile
    jobs/<job_id>/output.raw|.tif     reconstructed image
    jobs/<job_id>/result.<variant>    encoded downloads, created on first request

Writes are idempotent (a retried tile overwrites the same key), so the queue's
at-least-once delivery needs no extra coordination here.
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable

from .config import settings


def job_prefix(job_id: str) -> str:
    return f"jobs/{job_id}/"


def tile_key(job_id: str, idx: int) -> str:
    return f"jobs/{job_id}/tiles/{idx}.raw"


def result_key(job_id: str, idx: int) -> str:
    return f"jobs/{job_id}/results/{idx}.raw"


class ObjStore:
    """Thin boto3 wrapper: put / get / presign / delete_prefix."""

    def __init__(self, bucket: str | None = None, endpoint: str | None = None,
                 public_endpoint: str | None = None):
        import boto3
        from botocore.config import Config

        self.bucket = bucket or settings.s3_bucket
        endpoint = endpoint if endpoint is not None else settings.s3_endpoint
        public = public_endpoint or settings.s3_public_endpoint or endpoint
        cfg = Config(signature_version="s3v4", max_pool_connections=64,
                     retries={"max_attempts": 5, "mode": "standard"},
                     s3={"addressing_style": "path" if endpoint else "auto"})
        self.s3 = boto3.client("s3", endpoint_url=endpoint or None, region_name=settings.s3_region,
                               config=cfg)
        # Presigning is a local computation, but the signed host must be the one the
        # browser will use, so it gets its own client.
        self._signer = boto3.client("s3", endpoint_url=public or None, region_name=settings.s3_region,
                                    config=cfg)
        self._extra = {}
        if settings.s3_sse:
            self._extra["ServerSideEncryption"] = settings.s3_sse
            if settings.s3_kms_key_id:
                self._extra["SSEKMSKeyId"] = settings.s3_kms_key_id
        self._pool = None
        self._pool_lock = threading.Lock()

    def ensure_bucket(self, lifecycle_days: int = 1) -> None:
        """Create the bucket if needed, block public access and expire job objects."""
        from botocore.exceptions import ClientError

        if not settings.s3_manage_bucket:
            return
        try:
            self.s3.head_bucket(Bucket=self.bucket)
        except ClientError:
            try:
                self.s3.create_bucket(Bucket=self.bucket)
            except ClientError as exc:  # created concurrently by another service
                if "BucketAlready" not in str(exc):
                    raise
        try:
            self.s3.put_bucket_lifecycle_configuration(Bucket=self.bucket, LifecycleConfiguration={
                "Rules": [{"ID": "expire-jobs", "Status": "Enabled", "Filter": {"Prefix": "jobs/"},
                           "Expiration": {"Days": lifecycle_days}}]})
        except ClientError:
            pass  # lifecycle is managed outside the app on some deployments
        try:
            self.s3.put_public_access_block(Bucket=self.bucket, PublicAccessBlockConfiguration={
                "BlockPublicAcls": True, "IgnorePublicAcls": True,
                "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
        except ClientError:
            pass  # MinIO has no public-access-block API; its buckets are private by default

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type,
                           **self._extra)

    def get(self, key: str) -> bytes | None:
        try:
            return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.s3.exceptions.NoSuchKey:
            return None

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def upload_file(self, key: str, path: str, content_type: str) -> None:
        self.s3.upload_file(path, self.bucket, key, ExtraArgs={"ContentType": content_type, **self._extra})

    def presign(self, key: str, filename: str | None = None, seconds: int | None = None) -> str:
        params = {"Bucket": self.bucket, "Key": key}
        if filename:
            params["ResponseContentDisposition"] = f'inline; filename="{filename}"'
        return self._signer.generate_presigned_url("get_object", Params=params,
                                                   ExpiresIn=seconds or settings.presign_seconds)

    def delete_prefix(self, prefix: str) -> int:
        n = 0
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": keys, "Quiet": True})
                n += len(keys)
        return n

    # ------------------------------------------------------------ batches
    def _executor(self) -> ThreadPoolExecutor:
        with self._pool_lock:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(32, thread_name_prefix="s3")
            return self._pool

    def put_many(self, items: Iterable[tuple[str, bytes]]) -> None:
        list(self._executor().map(lambda kv: self.put(*kv), items))

    def get_many(self, keys: list[str]) -> list[bytes | None]:
        return list(self._executor().map(self.get, keys))


class MemoryObjStore(ObjStore):
    """In-process stand-in used by the tests (same interface, no network)."""

    def __init__(self):
        self.bucket = "memory"
        self.objects: dict[str, bytes] = {}
        self._pool = None
        self._pool_lock = threading.Lock()

    def ensure_bucket(self, lifecycle_days: int = 1) -> None:
        pass

    def put(self, key, data, content_type="application/octet-stream"):
        self.objects[key] = bytes(data)

    def get(self, key):
        return self.objects.get(key)

    def exists(self, key):
        return key in self.objects

    def upload_file(self, key, path, content_type):
        with open(path, "rb") as f:
            self.objects[key] = f.read()

    def presign(self, key, filename=None, seconds=None):
        return f"memory://{key}?expires={seconds or settings.presign_seconds}"

    def delete_prefix(self, prefix):
        keys = [k for k in self.objects if k.startswith(prefix)]
        for k in keys:
            del self.objects[k]
        return len(keys)


_default: ObjStore | None = None


def default_objstore() -> ObjStore | None:
    """The configured object store, or None in all-in-Redis mode (S3_BUCKET unset)."""
    global _default
    if _default is None and settings.s3_bucket:
        _default = ObjStore()
    return _default
