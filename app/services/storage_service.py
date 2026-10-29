"""Plant image storage: AWS S3 in production, local disk as a fallback.

Both backends implement the same small protocol, so nothing above this module
knows which one is live.  The selection rule is deliberately forgiving — a
fresh clone with no AWS account still runs, with uploads landing under
``instance/uploads``.

Only the storage *key* is ever persisted (e.g. ``plants/6f2a….webp``).  URLs
are derived at render time, because an S3 presigned URL expires and must never
be written into the database.
"""

from __future__ import annotations

import io
import logging
import mimetypes
import os
import threading
import time
import uuid
from pathlib import Path
from typing import IO, Any, Protocol, runtime_checkable

from flask import Flask, current_app, url_for
from PIL import Image, UnidentifiedImageError

from ..errors import ExternalServiceError, ValidationError

logger = logging.getLogger(__name__)

# Normalising every upload to WebP keeps the bucket small and sidesteps the
# "is this really a PNG?" question entirely — Pillow has to decode it first.
OUTPUT_FORMAT = "WEBP"
OUTPUT_EXTENSION = "webp"
OUTPUT_CONTENT_TYPE = "image/webp"
MAX_DIMENSION = 1400
WEBP_QUALITY = 82

ALLOWED_INPUT_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP"}


@runtime_checkable
class Storage(Protocol):
    """What the rest of the app may assume about a storage backend."""

    name: str

    def save(self, data: bytes, key: str, content_type: str) -> str: ...

    def url_for_key(self, key: str) -> str | None: ...

    def delete(self, key: str) -> bool: ...

    def exists(self, key: str) -> bool: ...


# ---------------------------------------------------------------------------
# Image normalisation
# ---------------------------------------------------------------------------
def process_image(file_obj: IO[bytes], *, max_bytes: int | None = None) -> bytes:
    """Validate an upload and re-encode it as a bounded WebP.

    Decoding with Pillow is the actual validation: a file that claims to be a
    PNG but is a script fails here, before it can be stored or served.
    """
    raw = file_obj.read()
    if not raw:
        raise ValidationError("The uploaded file is empty.")
    if max_bytes and len(raw) > max_bytes:
        raise ValidationError(
            f"Image is too large ({len(raw) // 1024} KB). "
            f"The limit is {max_bytes // (1024 * 1024)} MB."
        )

    try:
        with Image.open(io.BytesIO(raw)) as img:
            if img.format not in ALLOWED_INPUT_FORMATS:
                raise ValidationError(
                    f"Unsupported image type: {img.format or 'unknown'}. "
                    "Use JPEG, PNG, WebP, GIF or BMP."
                )
            img.load()
            # Flatten transparency/palette onto white; WebP handles RGB(A) only.
            if img.mode in ("RGBA", "LA", "P"):
                flat = Image.new("RGB", img.size, (255, 255, 255))
                converted = img.convert("RGBA")
                flat.paste(converted, mask=converted.split()[-1])
            elif img.mode != "RGB":
                flat = img.convert("RGB")
            else:
                flat = img.copy()

            # Image.LANCZOS is a deprecated alias; the enum is the current API.
            flat.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)

            out = io.BytesIO()
            flat.save(out, format=OUTPUT_FORMAT, quality=WEBP_QUALITY, method=4)
            return out.getvalue()
    except ValidationError:
        raise
    except UnidentifiedImageError as exc:
        raise ValidationError("That file is not a readable image.") from exc
    except Exception as exc:
        logger.warning("Image processing failed", exc_info=True)
        raise ValidationError("That image could not be processed.") from exc


def build_key(prefix: str = "plants") -> str:
    """A collision-free object key. Never derived from the user's filename."""
    return f"{prefix}/{uuid.uuid4().hex}.{OUTPUT_EXTENSION}"


# ---------------------------------------------------------------------------
# Local disk backend
# ---------------------------------------------------------------------------
class LocalStorage:
    """Writes under ``LOCAL_UPLOAD_DIR`` and serves via the ``media`` blueprint."""

    name = "local"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Resolve and confirm containment: a key like "../../etc/passwd" must
        # not escape the upload root even if one ever reached this far.
        candidate = (self.root / key).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValidationError("Invalid storage key.")
        return candidate

    def save(self, data: bytes, key: str, content_type: str) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def url_for_key(self, key: str) -> str | None:
        if not key:
            return None
        return url_for("media.serve_upload", key=key)

    def delete(self, key: str) -> bool:
        try:
            path = self._path(key)
        except ValidationError:
            return False
        if path.exists():
            path.unlink()
            return True
        return False

    def exists(self, key: str) -> bool:
        try:
            return self._path(key).exists()
        except ValidationError:
            return False

    def open(self, key: str) -> tuple[bytes, str]:
        path = self._path(key)
        if not path.exists():
            raise FileNotFoundError(key)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return path.read_bytes(), content_type


# ---------------------------------------------------------------------------
# AWS S3 backend
# ---------------------------------------------------------------------------
class S3Storage:
    """Stores objects in S3 and serves them through short-lived presigned URLs.

    The bucket stays private: no public-read ACL, no bucket policy opening it to
    the world.  Presigned URLs are cached in-process for most of their lifetime
    so rendering a 30-plant dashboard doesn't mean 30 signature computations per
    request.
    """

    name = "s3"

    def __init__(
        self,
        bucket: str,
        *,
        region: str = "us-east-1",
        endpoint_url: str | None = None,
        presign_expiry: int = 3600,
    ) -> None:
        import boto3  # imported lazily so the dependency is optional at runtime
        from botocore.config import Config

        self.bucket = bucket
        self.presign_expiry = presign_expiry
        self._client = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=5,
                read_timeout=10,
            ),
        )
        self._url_cache: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def save(self, data: bytes, key: str, content_type: str) -> str:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self._client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                CacheControl="public, max-age=31536000, immutable",
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error("S3 upload failed for key=%s: %s", key, exc)
            raise ExternalServiceError("Could not store the image. Please try again.") from exc
        with self._lock:
            self._url_cache.pop(key, None)
        return key

    def url_for_key(self, key: str) -> str | None:
        if not key:
            return None
        now = time.time()
        with self._lock:
            cached = self._url_cache.get(key)
            if cached and cached[1] > now:
                return cached[0]

        from botocore.exceptions import BotoCoreError, ClientError

        try:
            url = self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=self.presign_expiry,
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error("S3 presign failed for key=%s: %s", key, exc)
            return None

        # Expire the cache entry a minute early so a URL handed to a browser is
        # never already dead.
        with self._lock:
            self._url_cache[key] = (url, now + max(self.presign_expiry - 60, 30))
        return url

    def delete(self, key: str) -> bool:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            logger.error("S3 delete failed for key=%s: %s", key, exc)
            return False
        with self._lock:
            self._url_cache.pop(key, None)
        return True

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
def _build_backend(app: Flask) -> Storage:
    mode = (app.config.get("STORAGE_BACKEND") or "auto").lower()
    bucket = app.config.get("AWS_S3_BUCKET")

    if mode == "local" or (mode == "auto" and not bucket):
        root = app.config.get("LOCAL_UPLOAD_DIR", "instance/uploads")
        if not os.path.isabs(root):
            root = os.path.join(app.root_path, "..", root)
        backend: Storage = LocalStorage(root)
        if mode == "auto":
            app.logger.info(
                "Storage: local disk at %s (set AWS_S3_BUCKET to use S3)", backend.root  # type: ignore[attr-defined]
            )
        return backend

    if not bucket:
        raise RuntimeError("STORAGE_BACKEND=s3 requires AWS_S3_BUCKET to be set.")

    try:
        backend = S3Storage(
            bucket,
            region=app.config.get("AWS_REGION", "us-east-1"),
            endpoint_url=app.config.get("AWS_S3_ENDPOINT_URL"),
            presign_expiry=app.config.get("S3_PRESIGN_EXPIRY_SECONDS", 3600),
        )
        app.logger.info("Storage: S3 bucket %s", bucket)
        return backend
    except Exception:
        if mode == "s3":
            raise
        app.logger.warning(
            "S3 storage unavailable, falling back to local disk", exc_info=True
        )
        root = app.config.get("LOCAL_UPLOAD_DIR", "instance/uploads")
        if not os.path.isabs(root):
            root = os.path.join(app.root_path, "..", root)
        return LocalStorage(root)


def init_app(app: Flask) -> None:
    app.extensions["storage"] = _build_backend(app)


def get_storage() -> Storage:
    storage = current_app.extensions.get("storage")
    if storage is None:  # pragma: no cover - misconfiguration guard
        raise RuntimeError("Storage not initialised; call storage_service.init_app(app).")
    return storage


# --- Convenience wrappers used by blueprints --------------------------------
def store_upload(file_obj: IO[bytes], *, prefix: str = "plants") -> str:
    """Validate, normalise and store an uploaded image. Returns the key."""
    max_bytes = current_app.config.get("MAX_CONTENT_LENGTH")
    data = process_image(file_obj, max_bytes=max_bytes)
    key = build_key(prefix)
    return get_storage().save(data, key, OUTPUT_CONTENT_TYPE)


def image_url(key: str | None, *, fallback: str | None = None) -> str | None:
    """Resolve a stored key to a URL, or a placeholder when there is none."""
    if key and key.startswith("https://"):
        return key
    if key:
        resolved = get_storage().url_for_key(key)
        if resolved:
            return resolved
    if fallback is not None:
        return fallback
    return url_for("static", filename="img/plant-placeholder.svg")


def delete_image(key: str | None) -> bool:
    if not key or key.startswith("https://"):
        return False
    return get_storage().delete(key)


def resolve_image_urls(items: list[Any], attr: str = "image_key") -> dict[str, str | None]:
    """Resolve many keys at once, keyed by the key itself (deduplicated)."""
    keys = {getattr(item, attr, None) for item in items}
    storage = get_storage()
    return {k: k if k.startswith("https://") else storage.url_for_key(k) for k in keys if k}
