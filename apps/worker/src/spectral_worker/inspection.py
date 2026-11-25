"""Defensive metadata extraction for untrusted image inputs."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from warnings import catch_warnings, simplefilter
from zipfile import BadZipFile, ZipFile, ZipInfo

import numpy as np
import rasterio
from spectral_engine.calibration import parse_raster_metadata
from rasterio.enums import Resampling
from rasterio.errors import NotGeoreferencedWarning, RasterioError
from rasterio.io import MemoryFile
from rasterio.windows import Window

SCIENTIFIC_EXTENSIONS = {".tif", ".tiff"}
AUXILIARY_EXTENSIONS = {".png", ".jpg", ".jpeg"}
ALLOWED_IMAGE_EXTENSIONS = SCIENTIFIC_EXTENSIONS | AUXILIARY_EXTENSIONS
EXPECTED_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".tif": (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"),
    ".tiff": (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
}
EXPECTED_DRIVERS = {
    ".tif": "GTiff",
    ".tiff": "GTiff",
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
}
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_SAFE_FILENAME_CHARACTER = re.compile(r"[^\w.()\[\] -]", flags=re.UNICODE)


class InvalidAssetError(ValueError):
    """A user-provided object is invalid and must not be retried automatically."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RasterMetadata:
    filename: str
    extension: str
    width: int
    height: int
    dtype: str
    band_count: int
    nodata: str | None
    crs: str | None
    transform: list[float] | None
    metadata: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def sanitize_display_filename(path: str, *, fallback: str = "upload") -> str:
    """Return a normalized basename suitable for display and metadata."""
    name = PurePosixPath(unicodedata.normalize("NFC", path.replace("\\", "/"))).name
    cleaned = _SAFE_FILENAME_CHARACTER.sub("_", name).strip(" .")
    if not cleaned:
        cleaned = fallback
    suffix = PurePosixPath(cleaned).suffix[:32]
    stem_budget = max(1, 255 - len(suffix.encode("utf-8")))
    stem = (
        PurePosixPath(cleaned).stem.encode("utf-8")[:stem_budget].decode("utf-8", errors="ignore")
    )
    return f"{stem or fallback}{suffix}"


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_raster(
    path: Path,
    display_name: str | None = None,
    *,
    max_pixels: int = 500_000_000,
    max_bands: int = 64,
    max_decoded_bytes: int = 64 * 1024**3,
    validation_window_size: int = 1024,
) -> RasterMetadata:
    extension = path.suffix.lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise InvalidAssetError("invalid_extension", "Unsupported image extension")
    try:
        with path.open("rb") as source_bytes:
            signature = source_bytes.read(8)
    except OSError as error:
        raise InvalidAssetError("unreadable_image", "Image cannot be read") from error
    if not any(signature.startswith(expected) for expected in EXPECTED_SIGNATURES[extension]):
        raise InvalidAssetError(
            "content_type_mismatch", "File signature does not match the declared image type"
        )
    try:
        with rasterio.open(path) as source:
            if source.width <= 0 or source.height <= 0 or source.count <= 0:
                raise InvalidAssetError("invalid_dimensions", "Raster dimensions are invalid")
            if source.driver != EXPECTED_DRIVERS[extension]:
                raise InvalidAssetError(
                    "content_type_mismatch", "Raster driver does not match the declared image type"
                )
            pixels = source.width * source.height
            if pixels > max_pixels:
                raise InvalidAssetError("raster_pixel_limit", "Raster has too many pixels")
            if source.count > max_bands:
                raise InvalidAssetError("raster_band_limit", "Raster has too many bands")
            decoded_bytes = pixels * sum(np.dtype(dtype).itemsize for dtype in source.dtypes)
            if decoded_bytes > max_decoded_bytes:
                raise InvalidAssetError(
                    "raster_decoded_size_limit", "Decoded raster exceeds the configured limit"
                )
            # Decode every block with bounded memory so corruption outside the first tile
            # cannot be accepted silently.
            if validation_window_size < 1 or validation_window_size > 4096:
                raise ValueError("validation_window_size must be between 1 and 4096")
            for band_number in range(1, source.count + 1):
                for row_offset in range(0, source.height, validation_window_size):
                    window_height = min(validation_window_size, source.height - row_offset)
                    for column_offset in range(0, source.width, validation_window_size):
                        window_width = min(validation_window_size, source.width - column_offset)
                        source.read(
                            band_number,
                            window=Window(
                                column_offset,
                                row_offset,
                                window_width,
                                window_height,
                            ),
                            masked=True,
                        )
            transform = list(source.transform) if source.transform else None
            tags: dict[str, object] = dict(source.tags())
            if len(tags) > 128 or any(
                len(str(value).encode("utf-8")) > 4096 for value in tags.values()
            ):
                raise InvalidAssetError("metadata_limit", "Raster metadata exceeds safe limits")
            tags.update(
                driver=source.driver,
                color_interpretation=[value.name for value in source.colorinterp],
                descriptions=list(source.descriptions),
                calibration=parse_raster_metadata(
                    source.tags(),
                    source.tags(ns="EXIF"),
                    source.tags(ns="xml:XMP").get("xml:XMP"),
                    dtype=source.dtypes[0],
                    count=source.count,
                ),
            )
            return RasterMetadata(
                filename=display_name or path.name,
                extension=extension,
                width=source.width,
                height=source.height,
                dtype=",".join(source.dtypes),
                band_count=source.count,
                nodata=None if source.nodata is None else str(source.nodata),
                crs=None if source.crs is None else source.crs.to_string(),
                transform=transform,
                metadata=tags,
            )
    except InvalidAssetError:
        raise
    except (RasterioError, OSError, ValueError) as error:
        raise InvalidAssetError("corrupt_image", "Image cannot be decoded safely") from error


def build_preview_png(path: Path, max_dimension: int = 1600) -> tuple[bytes, float]:
    """Render a bounded grayscale preview and estimate the source valid-pixel fraction."""
    try:
        with rasterio.open(path) as source:
            scale = min(1.0, max_dimension / max(source.width, source.height))
            width = max(1, round(source.width * scale))
            height = max(1, round(source.height * scale))
            sampled = source.read(
                1,
                out_shape=(height, width),
                masked=True,
                resampling=Resampling.nearest,
            )
            values = np.asarray(sampled.data, dtype=np.float64)
            invalid = np.ma.getmaskarray(sampled) | ~np.isfinite(values)
            valid = values[~invalid]
            valid_fraction = float(valid.size / values.size)
            rendered = np.zeros(values.shape, dtype=np.uint8)
            if valid.size:
                low, high = np.percentile(valid, [2.0, 98.0])
                if not math.isfinite(float(low)) or not math.isfinite(float(high)):
                    raise InvalidAssetError("invalid_samples", "Raster has non-finite samples")
                if high <= low:
                    rendered[~invalid] = 127
                else:
                    scaled = np.clip((values - low) / (high - low), 0, 1)
                    rendered[~invalid] = np.asarray(scaled[~invalid] * 255, dtype=np.uint8)
            with MemoryFile() as memory:
                with catch_warnings():
                    simplefilter("ignore", NotGeoreferencedWarning)
                    with memory.open(
                        driver="PNG",
                        width=width,
                        height=height,
                        count=1,
                        dtype="uint8",
                    ) as preview:
                        preview.write(rendered, 1)
                return memory.read(), valid_fraction
    except InvalidAssetError:
        raise
    except (RasterioError, OSError, ValueError) as error:
        raise InvalidAssetError("preview_failed", "Raster preview cannot be generated") from error


def validate_zip_members(
    archive: ZipFile,
    *,
    max_entries: int = 10_000,
    max_uncompressed_bytes: int = 100 * 1024**3,
    max_entry_bytes: int = 50 * 1024**3,
    max_compression_ratio: float = 200.0,
    max_path_depth: int = 16,
) -> list[ZipInfo]:
    try:
        members = [member for member in archive.infolist() if not member.is_dir()]
    except BadZipFile as error:
        raise InvalidAssetError("corrupt_zip", "ZIP central directory is invalid") from error
    if len(members) > max_entries:
        raise InvalidAssetError("zip_too_many_entries", "ZIP has too many entries")
    total_size = 0
    accepted: list[ZipInfo] = []
    normalized_names: set[str] = set()
    for member in members:
        normalized_name = unicodedata.normalize("NFC", member.filename.replace("\\", "/"))
        raw_parts = normalized_name.split("/")
        path = PurePosixPath(normalized_name)
        if (
            path.is_absolute()
            or _WINDOWS_DRIVE.match(normalized_name)
            or any(part in {"", ".", ".."} for part in raw_parts)
            or not path.name
            or len(path.parts) > max_path_depth
            or any(not part or len(part.encode("utf-8")) > 255 for part in path.parts)
            or any(unicodedata.category(character).startswith("C") for character in normalized_name)
        ):
            raise InvalidAssetError("zip_unsafe_path", "ZIP contains an unsafe path")
        folded_name = normalized_name.casefold()
        if folded_name in normalized_names:
            raise InvalidAssetError("zip_duplicate_path", "ZIP contains duplicate paths")
        normalized_names.add(folded_name)
        if member.flag_bits & 0x1:
            raise InvalidAssetError("zip_encrypted", "Encrypted ZIP entries are unsupported")
        unix_type = (member.external_attr >> 16) & 0o170000
        if unix_type not in {0, 0o100000}:
            raise InvalidAssetError("zip_special_entry", "ZIP contains a link or special entry")
        if member.file_size > max_entry_bytes:
            raise InvalidAssetError("zip_entry_size_limit", "ZIP entry exceeds the size limit")
        total_size += member.file_size
        if total_size > max_uncompressed_bytes:
            raise InvalidAssetError(
                "zip_expansion_limit", "ZIP expands beyond the configured limit"
            )
        ratio = member.file_size / max(member.compress_size, 1)
        if not math.isfinite(ratio) or ratio > max_compression_ratio:
            raise InvalidAssetError("zip_compression_ratio", "ZIP compression ratio is unsafe")
        accepted.append(member)
    return accepted


def open_validated_zip(
    path: Path,
    *,
    max_entries: int = 10_000,
    max_uncompressed_bytes: int = 100 * 1024**3,
    max_entry_bytes: int = 50 * 1024**3,
    max_compression_ratio: float = 200.0,
    max_path_depth: int = 16,
) -> tuple[ZipFile, list[ZipInfo]]:
    try:
        archive = ZipFile(path)
        return archive, validate_zip_members(
            archive,
            max_entries=max_entries,
            max_uncompressed_bytes=max_uncompressed_bytes,
            max_entry_bytes=max_entry_bytes,
            max_compression_ratio=max_compression_ratio,
            max_path_depth=max_path_depth,
        )
    except InvalidAssetError:
        raise
    except (BadZipFile, OSError) as error:
        raise InvalidAssetError("corrupt_zip", "ZIP cannot be opened") from error


def extract_zip_member(
    archive: ZipFile,
    member: ZipInfo,
    target: Path,
    *,
    max_bytes: int,
    chunk_size: int = 1024 * 1024,
) -> int:
    """Extract one regular member while enforcing the limit on actual streamed bytes."""
    written = 0
    try:
        with archive.open(member) as source, target.open("xb") as destination:
            while chunk := source.read(chunk_size):
                written += len(chunk)
                if written > max_bytes or written > member.file_size:
                    raise InvalidAssetError(
                        "zip_entry_size_limit", "ZIP entry expanded beyond its declared limit"
                    )
                destination.write(chunk)
    except InvalidAssetError:
        target.unlink(missing_ok=True)
        raise
    except (BadZipFile, OSError, RuntimeError) as error:
        target.unlink(missing_ok=True)
        raise InvalidAssetError(
            "corrupt_zip_entry", "ZIP entry cannot be decoded safely"
        ) from error
    if written != member.file_size:
        target.unlink(missing_ok=True)
        raise InvalidAssetError("zip_size_mismatch", "ZIP entry size differs from its header")
    return written
