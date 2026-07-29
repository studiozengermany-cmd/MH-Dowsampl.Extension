"""Compatibility and hardening layer for the MH-Dowsample audio engine.

The existing server continues importing ``crawler``. This module keeps that
contract while applying narrowly scoped fixes without changing the popup UI.
"""

from __future__ import annotations

import html
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import core_engine as _core

AUDIO_SUFFIXES = _core.AUDIO_SUFFIXES
CONTENT_TYPE_SUFFIXES = _core.CONTENT_TYPE_SUFFIXES
AudioAsset = _core.AudioAsset
PublicAudioError = _core.PublicAudioError
catalogue_title = _core.catalogue_title
extract_splice_samples = _core.extract_splice_samples
is_audio_content_type = _core.is_audio_content_type
is_audio_url = _core.is_audio_url
sanitize_filename = _core.sanitize_filename
unique_destination = _core.unique_destination
validate_http_url = _core.validate_http_url


def _decode_embedded_url(value: str) -> str:
    return html.unescape(value).replace(r"\/", "/").replace(r"\u0026", "&").strip()


def _add_candidate(
    assets: list[AudioAsset],
    seen: set[str],
    raw_url: str,
    *,
    base_url: str | None,
    require_audio_suffix: bool,
) -> None:
    decoded = _decode_embedded_url(raw_url).rstrip(".,);]")
    absolute = urljoin(base_url, decoded) if base_url else decoded
    if not absolute.startswith(("http://", "https://")):
        return
    if require_audio_suffix and not is_audio_url(absolute):
        return
    if absolute in seen:
        return

    title = unquote(Path(urlparse(absolute).path).stem) or "sample"
    assets.append(AudioAsset(url=absolute, title=title))
    seen.add(absolute)


def extract_generic_audio(
    document: str,
    base_url: str | None = None,
) -> list[AudioAsset]:
    """Extract audio candidates while accepting signed URLs without extensions.

    URLs found in explicit audio tags and audio metadata are trusted candidates
    even when their path has no suffix. Arbitrary URLs found elsewhere still
    require a recognized audio suffix to avoid collecting unrelated resources.
    """

    assets: list[AudioAsset] = []
    seen: set[str] = set()
    normalized = html.unescape(document).replace(r"\/", "/").replace(r"\u0026", "&")

    for match in _core.TAG_AUDIO_SOURCE.finditer(normalized):
        _add_candidate(
            assets,
            seen,
            match.group(2),
            base_url=base_url,
            require_audio_suffix=False,
        )
    for match in _core.META_AUDIO.finditer(normalized):
        _add_candidate(
            assets,
            seen,
            match.group(3),
            base_url=base_url,
            require_audio_suffix=False,
        )
    for match in _core.JSON_AUDIO_URL.finditer(normalized):
        _add_candidate(
            assets,
            seen,
            match.group(1),
            base_url=base_url,
            require_audio_suffix=False,
        )
    for raw_url in _core.ABSOLUTE_URL.findall(normalized):
        _add_candidate(
            assets,
            seen,
            raw_url,
            base_url=base_url,
            require_audio_suffix=True,
        )

    return assets


# ``core_engine.extract_splice_page`` resolves this global at call time.
# Patching it here also hardens the generic fallback used by Splice pages.
_core.extract_generic_audio = extract_generic_audio
extract_splice_page = _core.extract_splice_page


def detect_audio_suffix(data: bytes) -> str | None:
    """Infer common sample formats from their initial bytes."""

    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return ".wav"
    if data.startswith(b"fLaC"):
        return ".flac"
    if data.startswith(b"ID3") or (
        len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0
    ):
        return ".mp3"
    if data.startswith(b"OggS"):
        return ".ogg"
    if len(data) >= 12 and data.startswith(b"FORM") and data[8:12] in {
        b"AIFF",
        b"AIFC",
    }:
        return ".aiff"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return ".m4a"
    return None


def looks_like_non_audio_payload(data: bytes) -> bool:
    """Detect common HTML, JSON and XML error responses."""

    sample = data[:1024].lstrip().lower()
    if sample.startswith((b"<!doctype html", b"<html", b"<?xml", b"{", b"[")):
        return True
    return any(
        marker in sample[:512]
        for marker in (
            b"<body",
            b"<head",
            b"access denied",
            b"request blocked",
            b"not authorized",
        )
    )


class AudioCrawler(_core.AudioCrawler):
    """Hardened downloader using the existing discovery/retry implementation."""

    def _download_one(self, url: str, title: str | None, folder: Path) -> Path:
        response = self._open(url, timeout=90, accept_audio=True)
        with response:
            content_type = _core.normalized_content_type(
                response.headers.get("Content-Type")
            )
            response_url = response.geturl() or url
            url_suffix = _core.audio_suffix_from_url(response_url)

            if content_type.startswith("text/") or content_type in {
                "application/json",
                "application/xml",
                "application/xhtml+xml",
            }:
                raise PublicAudioError(
                    f"Nguồn trả về {content_type}, không phải dữ liệu audio"
                )
            if not is_audio_content_type(content_type) and url_suffix not in AUDIO_SUFFIXES:
                raise PublicAudioError(
                    f"Nguồn trả về {content_type or 'dữ liệu không xác định'}, "
                    "không phải audio"
                )

            first_chunk = response.read(_core.DOWNLOAD_CHUNK_SIZE)
            if not first_chunk:
                raise PublicAudioError("Nguồn trả về file rỗng")
            if looks_like_non_audio_payload(first_chunk):
                raise PublicAudioError(
                    "Nguồn trả về trang HTML/JSON thay vì file audio"
                )

            response_name = self._response_filename(response)
            response_name_suffix = (
                Path(response_name).suffix.lower() if response_name else ""
            )
            detected_suffix = detect_audio_suffix(first_chunk)
            suffix = (
                CONTENT_TYPE_SUFFIXES.get(content_type)
                or (url_suffix if url_suffix in AUDIO_SUFFIXES else None)
                or (
                    response_name_suffix
                    if response_name_suffix in AUDIO_SUFFIXES
                    else None
                )
                or detected_suffix
                or ".bin"
            )

            if title:
                clean_name = sanitize_filename(title)
            elif response_name:
                clean_name = sanitize_filename(response_name)
            else:
                clean_name = "sample"

            clean_path = Path(clean_name)
            if clean_path.suffix.lower() in AUDIO_SUFFIXES:
                clean_name = clean_path.stem
            filename = sanitize_filename(clean_name) + suffix

            folder.mkdir(parents=True, exist_ok=True)
            with _core.NAME_LOCK:
                destination = unique_destination(folder, filename)
                partial = destination.with_suffix(destination.suffix + ".part")
                partial.touch(exist_ok=False)

            written = 0
            try:
                with partial.open("wb") as handle:
                    handle.write(first_chunk)
                    written += len(first_chunk)
                    while True:
                        chunk = response.read(_core.DOWNLOAD_CHUNK_SIZE)
                        if not chunk:
                            break
                        handle.write(chunk)
                        written += len(chunk)
                if written <= 0:
                    raise PublicAudioError("Nguồn trả về file rỗng")
                partial.replace(destination)
            except Exception:
                partial.unlink(missing_ok=True)
                raise
            return destination


__all__ = [
    "AUDIO_SUFFIXES",
    "CONTENT_TYPE_SUFFIXES",
    "AudioAsset",
    "AudioCrawler",
    "PublicAudioError",
    "catalogue_title",
    "detect_audio_suffix",
    "extract_generic_audio",
    "extract_splice_page",
    "extract_splice_samples",
    "is_audio_content_type",
    "is_audio_url",
    "looks_like_non_audio_payload",
    "sanitize_filename",
    "unique_destination",
    "validate_http_url",
]
