"""Compatibility and hardening layer for the MH-Dowsample audio engine.

The existing server continues importing ``crawler``. This module keeps that
contract while applying narrowly scoped fixes without changing the popup UI.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import core_engine as _core

AUDIO_SUFFIXES = _core.AUDIO_SUFFIXES
CONTENT_TYPE_SUFFIXES = _core.CONTENT_TYPE_SUFFIXES
AudioAsset = _core.AudioAsset
PublicAudioError = _core.PublicAudioError
catalogue_title = _core.catalogue_title
is_audio_content_type = _core.is_audio_content_type
is_audio_url = _core.is_audio_url
sanitize_filename = _core.sanitize_filename
unique_destination = _core.unique_destination
validate_http_url = _core.validate_http_url

JSON_NAMED_URL = re.compile(
    r"[\"'](?P<key>contentUrl|audioUrl|audio_url|previewUrl|preview_url)[\"']\s*:\s*"
    r"[\"'](?P<url>[^\"']+)[\"']",
    flags=re.IGNORECASE,
)
AUDIO_KIND_TOKENS = {
    "audio",
    "wav",
    "flac",
    "aiff",
    "aif",
    "mp3",
    "m4a",
    "aac",
    "ogg",
    "opus",
    "preview",
    "original",
    "source",
    "lossless",
}
DOCUMENT_CONTENT_TYPES = {
    "application/json",
    "application/javascript",
    "application/ld+json",
    "application/xml",
    "application/xhtml+xml",
}


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
    """Extract public audio candidates without trusting generic JSON blindly.

    Explicit ``audio``/``source`` tags, audio metadata, and audio-named JSON
    fields may contain signed stream URLs without file extensions. Generic
    ``contentUrl`` values must still have a recognized audio suffix because
    they commonly point to images, documents, or videos.
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
    for match in JSON_NAMED_URL.finditer(normalized):
        key = match.group("key").lower()
        _add_candidate(
            assets,
            seen,
            match.group("url"),
            base_url=base_url,
            require_audio_suffix=key == "contenturl",
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


def _splice_candidate(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    raw_url = item.get("url")
    if not isinstance(raw_url, str) or not raw_url.startswith(("http://", "https://")):
        return False
    if is_audio_url(raw_url):
        return True
    kind = str(
        item.get("asset_file_type_slug")
        or item.get("file_type")
        or item.get("format")
        or item.get("type")
        or ""
    ).lower()
    return any(token in kind for token in AUDIO_KIND_TOKENS)


def extract_splice_samples(payload: object) -> list[AudioAsset]:
    """Extract Splice candidates, including signed URLs without suffixes."""

    samples: list[AudioAsset] = []
    seen_primary: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, list):
            for child in value:
                visit(child)
            return
        if not isinstance(value, dict):
            return

        files = value.get("files")
        if isinstance(files, list):
            candidates = [item for item in files if _splice_candidate(item)]
            candidates.sort(key=_core.candidate_quality, reverse=True)
            if candidates:
                primary = str(candidates[0]["url"])
                fallbacks = tuple(
                    str(item["url"])
                    for item in candidates[1:]
                    if str(item["url"]) != primary
                )
                if primary not in seen_primary:
                    samples.append(
                        AudioAsset(
                            url=primary,
                            title=catalogue_title(value),
                            fallback_urls=fallbacks,
                        )
                    )
                    seen_primary.add(primary)

        for child in value.values():
            visit(child)

    visit(payload)
    return samples


# ``core_engine.extract_splice_page`` resolves these globals at call time.
# Patching them here hardens the existing discovery methods without replacing
# the server contract or changing the extension UI.
_core.extract_generic_audio = extract_generic_audio
_core.extract_splice_samples = extract_splice_samples
extract_splice_page = _core.extract_splice_page


def detect_audio_suffix(data: bytes) -> str | None:
    """Infer common sample formats from their initial bytes."""

    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return ".wav"
    if data.startswith(b"fLaC"):
        return ".flac"
    if data.startswith(b"ID3"):
        return ".mp3"
    if len(data) >= 2 and data[0] == 0xFF:
        second = data[1]
        if (second & 0xF6) == 0xF0:
            return ".aac"
        if (second & 0xE0) == 0xE0 and (second & 0x06) != 0:
            return ".mp3"
    if data.startswith(b"OggS"):
        return ".opus" if b"OpusHead" in data[:512] else ".ogg"
    if len(data) >= 12 and data.startswith(b"FORM") and data[8:12] in {
        b"AIFF",
        b"AIFC",
    }:
        return ".aiff"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        sample = data[:65536]
        if b"soun" in sample and b"vide" not in sample:
            return ".m4a"
    return None


def iso_bmff_contains_video(data: bytes) -> bool:
    """Return true when an ISO-BMFF/MP4 header exposes a video handler."""

    return len(data) >= 12 and data[4:8] == b"ftyp" and b"vide" in data[:65536]


def looks_like_non_audio_payload(data: bytes) -> bool:
    """Detect common HTML, JSON and XML error responses."""

    sample = data[:1024].lstrip()
    if sample.startswith(b"\xef\xbb\xbf"):
        sample = sample[3:].lstrip()
    lowered = sample.lower()
    if lowered.startswith((b"<", b"{", b"[")):
        return True
    return any(
        marker in lowered[:512]
        for marker in (
            b"access denied",
            b"request blocked",
            b"not authorized",
        )
    )


class AudioCrawler(_core.AudioCrawler):
    """Hardened discovery and download using the existing retry implementation."""

    def _fetch_page_or_direct_asset(self, url: str) -> tuple[str | None, AudioAsset | None]:
        """Recognize extensionless binary audio before treating it as a webpage."""

        response = self._open(url, timeout=45, accept_audio=False)
        with response:
            response_url = response.geturl() or url
            content_type = _core.normalized_content_type(
                response.headers.get("Content-Type")
            )
            if is_audio_content_type(content_type):
                title = unquote(Path(urlparse(response_url).path).stem) or "sample"
                return None, AudioAsset(response_url, title)

            raw = response.read(_core.MAX_DOCUMENT_BYTES + 1)
            if len(raw) > _core.MAX_DOCUMENT_BYTES:
                raise PublicAudioError("Trang nguồn quá lớn để quét an toàn")
            if not raw:
                raise PublicAudioError("Nguồn trả về dữ liệu rỗng")

            detected_suffix = detect_audio_suffix(raw[: _core.DOWNLOAD_CHUNK_SIZE])
            if detected_suffix is not None and not iso_bmff_contains_video(raw):
                title = unquote(Path(urlparse(response_url).path).stem) or "sample"
                return None, AudioAsset(response_url, title)

            text_like = looks_like_non_audio_payload(raw)
            document_type = content_type.startswith("text/") or content_type in DOCUMENT_CONTENT_TYPES
            if not text_like and not document_type:
                raise PublicAudioError(
                    f"Nguồn trả về {content_type or 'dữ liệu nhị phân không xác định'}, "
                    "không xác minh được là audio hoặc trang chứa audio"
                )

            charset = response.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace"), None

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

            first_chunk = response.read(_core.DOWNLOAD_CHUNK_SIZE)
            if not first_chunk:
                raise PublicAudioError("Nguồn trả về file rỗng")
            if looks_like_non_audio_payload(first_chunk):
                raise PublicAudioError(
                    "Nguồn trả về trang HTML/JSON thay vì file audio"
                )
            if iso_bmff_contains_video(first_chunk):
                raise PublicAudioError("Nguồn trả về MP4 có luồng video, không phải sample audio")

            response_name = self._response_filename(response)
            response_name_suffix = (
                Path(response_name).suffix.lower() if response_name else ""
            )
            detected_suffix = detect_audio_suffix(first_chunk)
            declared_audio = (
                is_audio_content_type(content_type)
                or url_suffix in AUDIO_SUFFIXES
                or response_name_suffix in AUDIO_SUFFIXES
                or detected_suffix is not None
            )
            if not declared_audio:
                raise PublicAudioError(
                    f"Nguồn trả về {content_type or 'dữ liệu không xác định'}, "
                    "không xác minh được là audio"
                )

            # Magic bytes describe the downloaded data more reliably than a
            # stale URL suffix or an incorrect Content-Type header.
            suffix = (
                detected_suffix
                or CONTENT_TYPE_SUFFIXES.get(content_type)
                or (url_suffix if url_suffix in AUDIO_SUFFIXES else None)
                or (
                    response_name_suffix
                    if response_name_suffix in AUDIO_SUFFIXES
                    else None
                )
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
    "iso_bmff_contains_video",
    "looks_like_non_audio_payload",
    "sanitize_filename",
    "unique_destination",
    "validate_http_url",
]
