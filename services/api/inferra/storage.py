"""Object storage for uploaded datasets.

Files are always queried from local disk by DuckDB. With the S3 backend, the bucket is the source
of truth and local disk acts as a cache that is filled on demand."""

from pathlib import Path

from inferra.config import get_settings


def _local_root() -> Path:
    root = get_settings().data_dir.resolve() / "objects"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _s3_client():  # type: ignore[no-untyped-def]
    import boto3

    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint_url,
        aws_access_key_id=s.s3_access_key_id,
        aws_secret_access_key=s.s3_secret_access_key,
        region_name=s.s3_region,
    )


def workspace_dir(workspace_id: str) -> Path:
    path = _local_root() / "workspaces" / workspace_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def local_path(key: str) -> Path:
    return _local_root() / key


def put_file(key: str, source: Path) -> None:
    """Store a file that already sits at its local cache path (`local_path(key)`)."""
    s = get_settings()
    if s.storage_backend == "s3":
        _s3_client().upload_file(str(source), s.s3_bucket, key)


def ensure_local(key: str) -> Path:
    path = local_path(key)
    if path.exists():
        return path
    s = get_settings()
    if s.storage_backend != "s3":
        raise FileNotFoundError(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    _s3_client().download_file(s.s3_bucket, key, str(path))
    return path


def delete_file(key: str) -> None:
    local_path(key).unlink(missing_ok=True)
    s = get_settings()
    if s.storage_backend == "s3":
        _s3_client().delete_object(Bucket=s.s3_bucket, Key=key)
