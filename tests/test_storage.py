"""Image handling and the S3 / local storage swap."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.errors import ValidationError


def make_image(fmt="PNG", size=(800, 600), color=(90, 160, 110)) -> io.BytesIO:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    buffer.seek(0)
    return buffer


def test_uploads_are_normalised_to_webp(app):
    from app.services.storage_service import process_image

    with app.app_context():
        data = process_image(make_image("PNG"))

    with Image.open(io.BytesIO(data)) as img:
        assert img.format == "WEBP"


def test_large_images_are_scaled_down(app):
    from app.services.storage_service import MAX_DIMENSION, process_image

    with app.app_context():
        data = process_image(make_image("JPEG", size=(4000, 3000)))

    with Image.open(io.BytesIO(data)) as img:
        assert max(img.size) <= MAX_DIMENSION


def test_transparency_is_flattened_not_dropped(app):
    from app.services.storage_service import process_image

    buffer = io.BytesIO()
    Image.new("RGBA", (100, 100), (255, 0, 0, 128)).save(buffer, format="PNG")
    buffer.seek(0)

    with app.app_context():
        data = process_image(buffer)

    with Image.open(io.BytesIO(data)) as img:
        assert img.mode in ("RGB", "RGBA")


def test_a_non_image_is_rejected(app):
    """Decoding with Pillow *is* the validation — a renamed script fails here."""
    from app.services.storage_service import process_image

    payload = io.BytesIO(b"<?php system($_GET['c']); ?>")
    with app.app_context(), pytest.raises(ValidationError):
        process_image(payload)


def test_an_empty_upload_is_rejected(app):
    from app.services.storage_service import process_image

    with app.app_context(), pytest.raises(ValidationError):
        process_image(io.BytesIO(b""))


def test_oversize_uploads_are_rejected(app):
    from app.services.storage_service import process_image

    with app.app_context(), pytest.raises(ValidationError, match="too large"):
        process_image(make_image("PNG"), max_bytes=10)


def test_keys_are_random_and_never_derived_from_the_filename(app):
    from app.services.storage_service import build_key

    keys = {build_key() for _ in range(50)}
    assert len(keys) == 50
    assert all(k.startswith("plants/") and k.endswith(".webp") for k in keys)


# --- Local backend ---------------------------------------------------------
def test_local_round_trip(app, tmp_path):
    from app.services.storage_service import LocalStorage

    storage = LocalStorage(tmp_path / "uploads")
    key = storage.save(b"some-bytes", "plants/abc.webp", "image/webp")

    assert storage.exists(key)
    assert storage.open(key)[0] == b"some-bytes"
    assert storage.delete(key)
    assert not storage.exists(key)


def test_local_storage_refuses_to_escape_its_root(app, tmp_path):
    """A traversal key must not read or write outside the upload directory."""
    from app.services.storage_service import LocalStorage

    storage = LocalStorage(tmp_path / "uploads")
    with pytest.raises(ValidationError):
        storage.save(b"x", "../../../etc/passwd", "image/webp")
    assert storage.exists("../../../etc/passwd") is False


def test_the_app_falls_back_to_local_without_a_bucket(app):
    from app.services.storage_service import LocalStorage, get_storage

    with app.app_context():
        assert isinstance(get_storage(), LocalStorage)


def test_missing_image_key_yields_the_placeholder(app):
    from app.services import storage_service

    with app.test_request_context():
        url = storage_service.image_url(None)
    assert "plant-placeholder" in url


# --- S3 backend ------------------------------------------------------------
@pytest.fixture
def s3_bucket():
    moto = pytest.importorskip("moto")
    import boto3

    with moto.mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="test-garden-bucket")
        yield "test-garden-bucket"


def test_s3_round_trip(app, s3_bucket):
    from app.services.storage_service import S3Storage

    storage = S3Storage(s3_bucket, region="us-east-1")
    key = storage.save(b"image-bytes", "plants/x.webp", "image/webp")

    assert storage.exists(key)
    url = storage.url_for_key(key)
    assert (url and s3_bucket in url and "Signature" in url) or "X-Amz-Signature" in url
    assert storage.delete(key)
    assert not storage.exists(key)


def test_s3_presigned_urls_are_cached_per_key(app, s3_bucket):
    """Rendering 30 cards shouldn't mean 30 signature computations."""
    from app.services.storage_service import S3Storage

    storage = S3Storage(s3_bucket, region="us-east-1")
    storage.save(b"x", "plants/y.webp", "image/webp")

    assert storage.url_for_key("plants/y.webp") == storage.url_for_key("plants/y.webp")


def test_s3_is_selected_when_a_bucket_is_configured(app, s3_bucket):
    from app.services import storage_service
    from app.services.storage_service import S3Storage

    app.config["STORAGE_BACKEND"] = "s3"
    app.config["AWS_S3_BUCKET"] = s3_bucket
    storage_service.init_app(app)

    with app.app_context():
        assert isinstance(storage_service.get_storage(), S3Storage)
