"""ObjectStore adapters: S3-compatible (MinIO) and a local-directory fallback for dev."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from pathlib import Path


class S3ObjectStore:
    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str) -> None:
        import aioboto3

        self._session = aioboto3.Session()
        self._kw = {
            "endpoint_url": endpoint,
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
            "region_name": "us-east-1",
        }
        self._bucket = bucket

    async def ensure_bucket(self) -> None:
        async with self._session.client("s3", **self._kw) as s3:
            try:
                await s3.head_bucket(Bucket=self._bucket)
            except Exception:
                await s3.create_bucket(Bucket=self._bucket)

    async def put(self, key: str, data: AsyncIterator[bytes] | bytes, content_type: str) -> None:
        async with self._session.client("s3", **self._kw) as s3:
            if isinstance(data, bytes):
                await s3.put_object(
                    Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
                )
                return
            upload = await s3.create_multipart_upload(
                Bucket=self._bucket, Key=key, ContentType=content_type
            )
            parts, buf, n = [], b"", 0
            try:
                async for chunk in data:
                    buf += chunk
                    if len(buf) >= 8 * 1024 * 1024:
                        n += 1
                        r = await s3.upload_part(
                            Bucket=self._bucket,
                            Key=key,
                            PartNumber=n,
                            UploadId=upload["UploadId"],
                            Body=buf,
                        )
                        parts.append({"PartNumber": n, "ETag": r["ETag"]})
                        buf = b""
                n += 1
                r = await s3.upload_part(
                    Bucket=self._bucket,
                    Key=key,
                    PartNumber=n,
                    UploadId=upload["UploadId"],
                    Body=buf,
                )
                parts.append({"PartNumber": n, "ETag": r["ETag"]})
                await s3.complete_multipart_upload(
                    Bucket=self._bucket,
                    Key=key,
                    UploadId=upload["UploadId"],
                    MultipartUpload={"Parts": parts},
                )
            except Exception:
                await s3.abort_multipart_upload(
                    Bucket=self._bucket, Key=key, UploadId=upload["UploadId"]
                )
                raise

    async def get_url(self, key: str, expires_s: int = 3600) -> str:
        async with self._session.client("s3", **self._kw) as s3:
            return await s3.generate_presigned_url(
                "get_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=expires_s
            )

    async def download_to(self, key: str, path: str) -> None:
        async with self._session.client("s3", **self._kw) as s3:
            await s3.download_file(self._bucket, key, path)

    async def read(self, key: str) -> bytes:
        return (await self.open(key))[0]

    async def list_keys(self, prefix: str) -> list[str]:
        async with self._session.client("s3", **self._kw) as s3:
            keys: list[str] = []
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                keys += [o["Key"] for o in page.get("Contents", [])]
            return keys

    async def delete(self, key: str) -> None:
        async with self._session.client("s3", **self._kw) as s3:
            await s3.delete_object(Bucket=self._bucket, Key=key)


class LocalObjectStore:
    """Dev/test: objects are files under a directory; URLs are served by the API."""

    def __init__(self, root: str, url_prefix: str = "/api/v1/objects") -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._prefix = url_prefix

    def path_of(self, key: str) -> Path:
        p = (self._root / key).resolve()
        if not str(p).startswith(str(self._root.resolve())):
            raise ValueError("invalid object key")
        return p

    async def put(self, key: str, data: AsyncIterator[bytes] | bytes, content_type: str) -> None:
        p = self.path_of(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            await asyncio.to_thread(p.write_bytes, data)
            return
        chunks = [chunk async for chunk in data]  # uploads are bounded by the request limit
        await asyncio.to_thread(p.write_bytes, b"".join(chunks))

    async def get_url(self, key: str, expires_s: int = 3600) -> str:
        return f"{self._prefix}/{key}"

    async def download_to(self, key: str, path: str) -> None:
        await asyncio.to_thread(shutil.copyfile, self.path_of(key), path)

    async def read(self, key: str) -> bytes:
        return await asyncio.to_thread(self.path_of(key).read_bytes)

    async def list_keys(self, prefix: str) -> list[str]:
        base = self._root / prefix
        if not base.is_dir():
            return []
        return [str(p.relative_to(self._root)) for p in base.rglob("*") if p.is_file()]

    async def delete(self, key: str) -> None:
        self.path_of(key).unlink(missing_ok=True)
