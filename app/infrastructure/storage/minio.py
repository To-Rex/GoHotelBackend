import asyncio
import io
import logging
from datetime import timedelta
from typing import BinaryIO

from minio import Minio
from minio.error import S3Error

from app.core.config import settings

logger = logging.getLogger(__name__)

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
        try:
            for bucket in [settings.MINIO_BUCKET_DOCUMENTS, settings.MINIO_BUCKET_GUESTS]:
                if not _client.bucket_exists(bucket):
                    _client.make_bucket(bucket)
        except S3Error as e:
            logger.error("MinIO bucket check failed: %s", e)
            raise
    return _client


async def upload_file(bucket: str, object_path: str, data: bytes, content_type: str) -> str:
    client = get_minio_client()
    file_stream = io.BytesIO(data)
    try:
        await asyncio.to_thread(
            client.put_object,
            bucket,
            object_path,
            file_stream,
            len(data),
            content_type=content_type,
        )
    except S3Error as e:
        logger.error("MinIO upload failed for %s/%s: %s", bucket, object_path, e)
        raise
    return object_path


def get_presigned_url(bucket: str, object_path: str, expires: int = 3600) -> str:
    client = get_minio_client()
    return client.presigned_get_object(bucket, object_path, expires=timedelta(seconds=expires))


async def delete_file(bucket: str, object_path: str) -> bool:
    client = get_minio_client()
    try:
        await asyncio.to_thread(client.remove_object, bucket, object_path)
        return True
    except S3Error:
        return False


async def download_file(bucket: str, object_path: str) -> bytes:
    client = get_minio_client()
    response = await asyncio.to_thread(client.get_object, bucket, object_path)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()
