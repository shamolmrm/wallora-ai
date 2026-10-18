"""Image validation and re-encoding.

Uploads are never stored as received: the format is sniffed from magic bytes
(the client's Content-Type is ignored), dimensions are checked before pixel
data is decoded (decompression-bomb guard), and the image is re-encoded from
raw pixels, which drops EXIF/GPS and any appended payload.
"""

import hashlib
import io
from dataclasses import dataclass

from PIL import Image, ImageOps

THUMB_BOX = (512, 1024)
RIFF_HEADER_LEN = 12
_ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}


class InvalidImageError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SanitizedImage:
    data: bytes
    mime_type: str
    width: int
    height: int
    sha256: str


@dataclass(frozen=True)
class ProcessedOutput:
    data: bytes
    mime_type: str
    width: int
    height: int
    thumb: bytes


def sniff_format(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if len(data) >= RIFF_HEADER_LEN and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    return None


def _open_checked(data: bytes, max_pixels: int) -> Image.Image:
    sniffed = sniff_format(data)
    if sniffed is None:
        raise InvalidImageError("unsupported_format", "Only PNG, JPEG and WebP are supported.")
    try:
        image = Image.open(io.BytesIO(data))
        # Image.open is lazy: size is known before any pixel data is decoded.
        width, height = image.size
        if width * height > max_pixels:
            raise InvalidImageError("image_too_large", "The image has too many pixels.")
        if image.format != sniffed or image.format not in _ALLOWED_FORMATS:
            raise InvalidImageError("unsupported_format", "The file content is not a valid image.")
        if getattr(image, "n_frames", 1) > 1:
            raise InvalidImageError("animated_image", "Animated images are not supported.")
        image.load()
    except InvalidImageError:
        raise
    except Image.DecompressionBombError as exc:
        # Pillow refuses absurd dimensions inside Image.open, before our own size check.
        raise InvalidImageError("image_too_large", "The image has too many pixels.") from exc
    except (OSError, SyntaxError, ValueError) as exc:
        raise InvalidImageError("corrupt_image", "The image could not be decoded.") from exc
    return image


def sanitize_upload(
    data: bytes, *, max_bytes: int, max_pixels: int, max_side: int
) -> SanitizedImage:
    if len(data) > max_bytes:
        raise InvalidImageError("file_too_large", "The file is too large.")
    image = _open_checked(data, max_pixels)
    image = ImageOps.exif_transpose(image)
    has_alpha = image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info
    clean = image.convert("RGBA" if has_alpha else "RGB")
    if max(clean.size) > max_side:
        clean.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    clean.save(buffer, format="PNG")  # fresh image: no EXIF, ICC or text chunks copied
    out = buffer.getvalue()
    return SanitizedImage(
        data=out,
        mime_type="image/png",
        width=clean.width,
        height=clean.height,
        sha256=hashlib.sha256(out).hexdigest(),
    )


def process_output(data: bytes, *, max_pixels: int) -> ProcessedOutput:
    image = _open_checked(data, max_pixels)
    thumb_source = image.convert("RGB")
    thumb_source.thumbnail(THUMB_BOX, Image.Resampling.LANCZOS)
    thumb = io.BytesIO()
    thumb_source.save(thumb, format="WEBP", quality=80)
    mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}[image.format or "PNG"]
    return ProcessedOutput(
        data=data, mime_type=mime, width=image.width, height=image.height, thumb=thumb.getvalue()
    )
