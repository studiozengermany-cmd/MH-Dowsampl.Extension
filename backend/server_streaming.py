"""Stream per-file downloads only after the native Save As destination is chosen.

This module patches the compatibility layer in ``server_hardening``. The popup
and HTTP API remain unchanged.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import crawler as _crawler
import server_hardening as _hard
from crawler import (
    AUDIO_SUFFIXES,
    CONTENT_TYPE_SUFFIXES,
    AudioAsset,
    AudioCrawler,
    PublicAudioError,
    detect_audio_suffix,
    is_audio_content_type,
    iso_bmff_contains_video,
    looks_like_non_audio_payload,
    sanitize_filename,
    unique_destination,
)


class DestinationSelectionCancelled(Exception):
    """The user cancelled Save As for one asset."""


class DestinationWriteError(Exception):
    """The selected destination could not be written safely."""


def _download_one_with_selector(
    crawler: AudioCrawler,
    url: str,
    title: str | None,
    selector: Callable[[str, str], Path | None],
) -> Path:
    response = crawler._open(url, timeout=90, accept_audio=True)
    with response:
        content_type = _crawler._core.normalized_content_type(
            response.headers.get("Content-Type")
        )
        response_url = response.geturl() or url
        url_suffix = _crawler._core.audio_suffix_from_url(response_url)

        if content_type.startswith("text/") or content_type in {
            "application/json",
            "application/xml",
            "application/xhtml+xml",
        }:
            raise PublicAudioError(
                f"Nguồn trả về {content_type}, không phải dữ liệu audio"
            )

        first_chunk = response.read(_crawler._core.DOWNLOAD_CHUNK_SIZE)
        if not first_chunk:
            raise PublicAudioError("Nguồn trả về file rỗng")
        if looks_like_non_audio_payload(first_chunk):
            raise PublicAudioError("Nguồn trả về trang HTML/JSON thay vì file audio")
        if iso_bmff_contains_video(first_chunk):
            raise PublicAudioError(
                "Nguồn trả về MP4 có luồng video, không phải sample audio"
            )

        response_name = crawler._response_filename(response)
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
        if suffix not in AUDIO_SUFFIXES:
            raise PublicAudioError("Không xác định được định dạng audio an toàn")

        clean_name = sanitize_filename(title or response_name or "sample")
        clean_path = Path(clean_name)
        if clean_path.suffix.lower() in AUDIO_SUFFIXES:
            clean_name = clean_path.stem
        filename = sanitize_filename(clean_name) + suffix

        selected = selector(filename, suffix)
        if selected is None:
            raise DestinationSelectionCancelled("Đã hủy lưu file này")
        destination = Path(selected)
        destination.parent.mkdir(parents=True, exist_ok=True)

        with _crawler._core.NAME_LOCK:
            partial = destination.with_name(
                destination.name + f".{uuid.uuid4().hex}.part"
            )
            partial.touch(exist_ok=False)

        try:
            with partial.open("wb") as handle:
                handle.write(first_chunk)
                while True:
                    chunk = response.read(_crawler._core.DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
            partial.replace(destination)
        except Exception as exc:
            partial.unlink(missing_ok=True)
            raise DestinationWriteError(str(exc)) from exc
        return destination


def download_with_selector(
    crawler: AudioCrawler,
    asset: AudioAsset,
    selector: Callable[[str, str], Path | None],
) -> Path:
    errors: list[str] = []
    candidates = (asset.url, *asset.fallback_urls)
    for index, url in enumerate(candidates):
        try:
            return _download_one_with_selector(crawler, url, asset.title, selector)
        except (DestinationSelectionCancelled, DestinationWriteError):
            raise
        except Exception as exc:
            readable = (
                exc
                if isinstance(exc, PublicAudioError)
                else _crawler._core.readable_network_error(exc, url)
            )
            errors.append(str(readable))
            if index + 1 >= len(candidates):
                break
    raise PublicAudioError("; fallback thất bại: ".join(errors))


def _download_prompt_per_file(
    job: _hard.Job,
    crawler: AudioCrawler,
    assets: list[AudioAsset],
    initial_root: Path,
) -> None:
    for asset in assets:
        _hard._server.update(
            job,
            current=asset.title or Path(urlparse(asset.url).path).name,
        )

        def select_destination(filename: str, suffix: str) -> Path | None:
            nonlocal initial_root
            try:
                with _hard._server.FOLDER_DIALOG_LOCK:
                    selected = _hard.choose_download_file(filename, initial_root)
            except _hard._server.FolderSelectionCancelled:
                return None
            destination = _hard.enforce_actual_suffix(selected, suffix)
            # If correcting the extension points to a different existing file,
            # the user did not confirm overwriting that file in SaveFileDialog.
            if destination.exists() and destination != selected:
                destination = unique_destination(
                    destination.parent,
                    destination.name,
                )
            initial_root = destination.parent
            return destination

        try:
            destination = download_with_selector(
                crawler,
                asset,
                select_destination,
            )
            category = _hard.classify_audio_name(destination.name)
            review = _hard.needs_quality_review(destination)
            _hard._server.update(job, output_dir=str(destination.parent))
            _hard._record_download(job, category, review)
        except DestinationSelectionCancelled:
            with _hard._server.LOCK:
                job.cancelled += 1
        except Exception as exc:
            with _hard._server.LOCK:
                job.failed += 1
            _hard._server.append_failure(
                job,
                f"{asset.title or asset.url}: {exc}",
            )


# ``server_hardening.run_job`` resolves this global when each job runs.
_hard.DestinationSelectionCancelled = DestinationSelectionCancelled
_hard.DestinationWriteError = DestinationWriteError
_hard.download_with_selector = download_with_selector
_hard._download_prompt_per_file = _download_prompt_per_file

__all__ = [
    "DestinationSelectionCancelled",
    "DestinationWriteError",
    "download_with_selector",
]
