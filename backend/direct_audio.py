"""Fix discovery of large extensionless direct-audio responses.

Unknown binary responses are identified from the first download chunk. Text-like
responses are still bounded by the document scan limit.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

import crawler as _crawler
from crawler import (
    AudioAsset,
    AudioCrawler,
    PublicAudioError,
    detect_audio_suffix,
    is_audio_content_type,
    iso_bmff_contains_video,
    looks_like_non_audio_payload,
)

DOCUMENT_CONTENT_TYPES = getattr(
    _crawler,
    "DOCUMENT_CONTENT_TYPES",
    {
        "application/json",
        "application/javascript",
        "application/ld+json",
        "application/xml",
        "application/xhtml+xml",
    },
)


def _fetch_page_or_direct_asset(
    self: AudioCrawler,
    url: str,
) -> tuple[str | None, AudioAsset | None]:
    response = self._open(url, timeout=45, accept_audio=False)
    with response:
        response_url = response.geturl() or url
        content_type = _crawler._core.normalized_content_type(
            response.headers.get("Content-Type")
        )
        title = unquote(Path(urlparse(response_url).path).stem) or "sample"

        if is_audio_content_type(content_type):
            return None, AudioAsset(response_url, title)

        first_chunk = response.read(_crawler._core.DOWNLOAD_CHUNK_SIZE)
        if not first_chunk:
            raise PublicAudioError("Nguồn trả về dữ liệu rỗng")

        detected_suffix = detect_audio_suffix(first_chunk)
        if detected_suffix is not None and not iso_bmff_contains_video(first_chunk):
            return None, AudioAsset(response_url, title)

        text_like = looks_like_non_audio_payload(first_chunk)
        document_type = (
            content_type.startswith("text/")
            or content_type in DOCUMENT_CONTENT_TYPES
        )
        if not text_like and not document_type:
            raise PublicAudioError(
                f"Nguồn trả về {content_type or 'dữ liệu nhị phân không xác định'}, "
                "không xác minh được là audio hoặc trang chứa audio"
            )

        remaining_limit = max(
            0,
            _crawler._core.MAX_DOCUMENT_BYTES - len(first_chunk) + 1,
        )
        remaining = response.read(remaining_limit)
        raw = first_chunk + remaining
        if len(raw) > _crawler._core.MAX_DOCUMENT_BYTES:
            raise PublicAudioError("Trang nguồn quá lớn để quét an toàn")

        charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace"), None


AudioCrawler._fetch_page_or_direct_asset = _fetch_page_or_direct_asset

__all__ = ["_fetch_page_or_direct_asset"]
