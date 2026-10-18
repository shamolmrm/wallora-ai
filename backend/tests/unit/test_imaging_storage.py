import io
import struct
import zlib
from pathlib import Path

import pytest
from PIL import Image

from app.core.imaging import InvalidImageError, process_output, sanitize_upload
from app.integrations.storage import LocalStorage, StorageError, UrlSigner, validate_key

LIMITS = {"max_bytes": 5_000_000, "max_pixels": 20_000_000, "max_side": 1024}
PNG_SIGNATURE = bytes([0x89]) + b"PNG\r\n" + bytes([0x1A]) + b"\n"


def _image_bytes(fmt: str, size: tuple[int, int] = (64, 48), **save: object) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 30, 90)).save(buffer, format=fmt, **save)
    return buffer.getvalue()


def _png_header_only(width: int, height: int) -> bytes:
    """A structurally valid PNG whose IHDR claims huge dimensions but holds almost no data."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        return struct.pack(">I", len(payload)) + kind + payload + crc

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(1)))
        + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_sanitize_accepts_supported_formats_and_reencodes_png(fmt: str) -> None:
    result = sanitize_upload(_image_bytes(fmt), **LIMITS)
    assert result.mime_type == "image/png"
    assert result.data.startswith(PNG_SIGNATURE)
    assert (result.width, result.height) == (64, 48)


def test_sanitize_strips_exif() -> None:
    image = Image.new("RGB", (32, 32))
    exif = Image.Exif()
    exif[0x010E] = "secret description"  # ImageDescription
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif.tobytes())
    assert b"secret description" in buffer.getvalue()

    result = sanitize_upload(buffer.getvalue(), **LIMITS)

    assert b"secret description" not in result.data
    assert not Image.open(io.BytesIO(result.data)).getexif()


def test_sanitize_downscales_large_images() -> None:
    result = sanitize_upload(_image_bytes("PNG", (3000, 1500)), **LIMITS)
    assert max(result.width, result.height) == 1024


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"GIF89a" + bytes(100), "unsupported_format"),
        (PNG_SIGNATURE + b"garbage" * 10, "corrupt_image"),
        # Caught by Pillow's own bomb guard inside Image.open.
        (_png_header_only(60_000, 60_000), "image_too_large"),
        # Below Pillow's threshold but above our 20 MP cap: caught before decoding.
        (_png_header_only(5_000, 5_000), "image_too_large"),
    ],
)
def test_sanitize_rejects_bad_input(data: bytes, code: str) -> None:
    with pytest.raises(InvalidImageError) as info:
        sanitize_upload(data, **LIMITS)
    assert info.value.code == code


def test_sanitize_rejects_mismatched_magic() -> None:
    # JPEG bytes behind a PNG signature are caught by format sniffing, not the filename.
    jpeg = _image_bytes("JPEG")
    with pytest.raises(InvalidImageError):
        sanitize_upload(PNG_SIGNATURE + jpeg[8:], **LIMITS)


def test_sanitize_rejects_oversized_file() -> None:
    with pytest.raises(InvalidImageError) as info:
        sanitize_upload(_image_bytes("PNG"), max_bytes=10, max_pixels=10**7, max_side=1024)
    assert info.value.code == "file_too_large"


def test_process_output_builds_thumbnail() -> None:
    output = process_output(_image_bytes("WEBP", (1072, 2320)), max_pixels=10**8)
    assert (output.width, output.height) == (1072, 2320)
    thumb = Image.open(io.BytesIO(output.thumb))
    assert thumb.format == "WEBP"
    assert thumb.height <= 1024
    assert thumb.width <= 512


def test_url_signer_round_trip_expiry_and_tamper() -> None:
    signer = UrlSigner("http://api", b"secret")
    url = signer.sign("u/a/gen/x.webp", 60, now=1000)
    query = dict(part.split("=") for part in url.split("?", 1)[1].split("&"))
    exp, sig = int(query["exp"]), query["sig"]
    assert signer.verify("u/a/gen/x.webp", exp, sig, now=1030)
    assert not signer.verify("u/a/gen/x.webp", exp, sig, now=1061)  # expired
    assert not signer.verify("u/b/gen/x.webp", exp, sig, now=1030)  # other key
    assert not UrlSigner("http://api", b"other").verify("u/a/gen/x.webp", exp, sig, now=1030)


@pytest.mark.parametrize("key", ["../etc/passwd", "u/../../x", "/abs/path", "U/Upper", "a//b", ""])
def test_invalid_storage_keys(key: str) -> None:
    with pytest.raises(StorageError):
        validate_key(key)


async def test_local_storage_round_trip(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path, UrlSigner("http://api", b"s"))
    await storage.put("u/1/ref/a.png", b"data", "image/png")
    assert await storage.get("u/1/ref/a.png") == b"data"
    await storage.delete("u/1/ref/a.png")
    with pytest.raises(StorageError):
        await storage.get("u/1/ref/a.png")
