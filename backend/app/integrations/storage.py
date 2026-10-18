"""Private object storage.

User content is stored under server-generated keys and only ever exposed via
short-lived signed URLs. Backends:

* ``S3Storage``: Cloudflare R2 / any S3-compatible store (presigned GET).
* ``LocalStorage``: filesystem for development; URLs are HMAC-signed and served
  by ``GET /v1/files/{key}``, which verifies signature and expiry.
* ``MemoryStorage``: tests.
"""

import asyncio
import hashlib
import hmac
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from urllib.parse import quote, urlencode

import boto3
from botocore.config import Config

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9/_.-]{0,299}$")
CONTENT_TYPES = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg"}


class StorageError(Exception):
    pass


def validate_key(key: str) -> str:
    if not _KEY_RE.fullmatch(key) or ".." in key or "//" in key:
        raise StorageError("invalid storage key")
    return key


def content_type_for(key: str) -> str:
    return CONTENT_TYPES.get(Path(key).suffix, "application/octet-stream")


class ObjectStorage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def signed_url(self, key: str, ttl_seconds: int) -> str: ...


class UrlSigner:
    def __init__(self, base_url: str, secret: bytes) -> None:
        self._base_url = base_url.rstrip("/")
        self._secret = secret

    def _signature(self, key: str, expires: int) -> str:
        message = f"{key}\n{expires}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def sign(self, key: str, ttl_seconds: int, *, now: float | None = None) -> str:
        expires = int((now if now is not None else time.time()) + ttl_seconds)
        query = urlencode({"exp": expires, "sig": self._signature(key, expires)})
        return f"{self._base_url}/v1/files/{quote(key)}?{query}"

    def verify(self, key: str, expires: int, signature: str, *, now: float | None = None) -> bool:
        if expires < (now if now is not None else time.time()):
            return False
        return hmac.compare_digest(self._signature(key, expires), signature)


class MemoryStorage:
    def __init__(self, signer: UrlSigner) -> None:
        self.signer = signer
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[validate_key(key)] = (data, content_type)

    async def get(self, key: str) -> bytes:
        try:
            return self.objects[validate_key(key)][0]
        except KeyError:
            raise StorageError("object not found") from None

    async def delete(self, key: str) -> None:
        self.objects.pop(validate_key(key), None)

    async def signed_url(self, key: str, ttl_seconds: int) -> str:
        return self.signer.sign(validate_key(key), ttl_seconds)


class LocalStorage:
    def __init__(self, root: Path, signer: UrlSigner) -> None:
        self.root = root.resolve()
        self.signer = signer

    def path_for(self, key: str) -> Path:
        path = (self.root / validate_key(key)).resolve()
        if not path.is_relative_to(self.root):
            raise StorageError("invalid storage key")
        return path

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self.path_for(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)

        await asyncio.to_thread(write)

    async def get(self, key: str) -> bytes:
        path = self.path_for(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError:
            raise StorageError("object not found") from None

    async def delete(self, key: str) -> None:
        path = self.path_for(key)
        await asyncio.to_thread(path.unlink, missing_ok=True)

    async def signed_url(self, key: str, ttl_seconds: int) -> str:
        return self.signer.sign(validate_key(key), ttl_seconds)


class S3Storage:
    def __init__(
        self,
        *,
        endpoint_url: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
    ) -> None:
        self._bucket = bucket
        self._client: S3Client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=5,
                read_timeout=30,
            ),
        )

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=validate_key(key),
            Body=data,
            ContentType=content_type,
            CacheControl="private, max-age=0",
        )

    async def get(self, key: str) -> bytes:
        response = await asyncio.to_thread(
            self._client.get_object, Bucket=self._bucket, Key=validate_key(key)
        )
        return await asyncio.to_thread(response["Body"].read)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(
            self._client.delete_object, Bucket=self._bucket, Key=validate_key(key)
        )

    async def signed_url(self, key: str, ttl_seconds: int) -> str:
        return await asyncio.to_thread(
            self._client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self._bucket, "Key": validate_key(key)},
            ExpiresIn=ttl_seconds,
        )
