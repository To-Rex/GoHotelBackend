from datetime import timedelta

from minio import Minio
from minio.error import S3Error

from app.core.config import settings

_client: Minio | None = None


def get_minio_client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            endpoint=settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        for bucket in [settings.MINIO_BUCKET_DOCUMENTS, settings.MINIO_BUCKET_GUESTS]:
            if not _client.bucket_exists(bucket):
                _client.make_bucket(bucket)
    return _client


async def upload_file(bucket: str, object_path: str, data: bytes, content_type: str) -> str:
    client = get_minio_client()
    client.put_object(bucket, object_path, data, len(data), content_type=content_type)
    return object_path


def get_presigned_url(bucket: str, object_path: str, expires: int = 3600) -> str:
    client = get_minio_client()
    return client.presigned_get_object(bucket, object_path, expires=timedelta(seconds=expires))


async def delete_file(bucket: str, object_path: str) -> bool:
    client = get_minio_client()
    try:
        client.remove_object(bucket, object_path)
        return True
    except S3Error:
        return False
