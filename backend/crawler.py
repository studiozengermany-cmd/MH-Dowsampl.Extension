"""Compatibility module for the MH-Dowsample audio engine.

Existing imports continue to use ``crawler`` while the implementation lives in
``core_engine`` so the server and current extension structure remain unchanged.
"""

from core_engine import (  # noqa: F401
    AUDIO_SUFFIXES,
    CONTENT_TYPE_SUFFIXES,
    AudioAsset,
    AudioCrawler,
    PublicAudioError,
    catalogue_title,
    extract_generic_audio,
    extract_splice_page,
    extract_splice_samples,
    is_audio_content_type,
    is_audio_url,
    sanitize_filename,
    unique_destination,
    validate_http_url,
)

__all__ = [
    "AUDIO_SUFFIXES",
    "CONTENT_TYPE_SUFFIXES",
    "AudioAsset",
    "AudioCrawler",
    "PublicAudioError",
    "catalogue_title",
    "extract_generic_audio",
    "extract_splice_page",
    "extract_splice_samples",
    "is_audio_content_type",
    "is_audio_url",
    "sanitize_filename",
    "unique_destination",
    "validate_http_url",
]
