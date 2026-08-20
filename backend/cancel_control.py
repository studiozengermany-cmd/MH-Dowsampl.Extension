"""Cancellation and simple save-flow control for MH-Dowsample.

This layer restores the practical workflow: choose one folder for a job, download
without one Save As dialog per asset, and allow the popup to stop an active job.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import server_hardening as _hard

APP_VERSION = "1.3.1"
_CANCEL_PATH = re.compile(r"^/jobs/([A-Za-z0-9]+)/cancel$")


def _is_cancel_requested(job: _hard.Job) -> bool:
    return bool(getattr(job, "cancel_requested", False))


def _job_public(self: _hard.Job) -> dict[str, object]:
    payload = _ORIGINAL_JOB_PUBLIC(self)
    payload["cancel_requested"] = _is_cancel_requested(self)
    payload["cancelled_by_user"] = bool(getattr(self, "cancelled_by_user", False))
    return payload


def cancel_job(job_id: str) -> dict[str, object] | None:
    """Request cancellation without killing the local server process."""

    with _hard._server.LOCK:
        job = _hard._server.JOBS.get(job_id)
        if job is None:
            return None
        if job.status not in {"completed", "failed"}:
            setattr(job, "cancel_requested", True)
            job.current = "\u0110ang d\u1eebng t\u00e1c v\u1ee5..."
        return job.public()


def resolve_download_root(
    download_dir: str | None = None,
    set_default: bool = False,
) -> tuple[Path, str, bool]:
    """Use one destination folder per job; never open Save As for every asset."""

    if download_dir is not None:
        selected = _hard._server.prepare_download_root(download_dir)
        if set_default:
            selected = _hard._server.save_download_root(selected)
            return selected, "per_job_default", True
        return selected, "per_job", False

    if _hard._server.ask_for_download_root_each_time():
        with _hard._server.FOLDER_DIALOG_LOCK:
            return _hard._server.choose_download_root(), "prompt_each_time", False

    configured = _hard._server.default_download_root()
    if configured is not None:
        return _hard._server.prepare_download_root(configured), "configured_default", True

    with _hard._server.FOLDER_DIALOG_LOCK:
        configured = _hard._server.default_download_root()
        if configured is not None:
            return _hard._server.prepare_download_root(configured), "configured_default", True
        selected = _hard._server.save_download_root(_hard._server.choose_download_root())
        return selected, "prompt_default", True


def discover_assets(job: _hard.Job, crawler: _hard.AudioCrawler) -> list[_hard.AudioAsset]:
    assets: list[_hard.AudioAsset] = []
    seen: set[str] = set()
    batch_total = max(
        1,
        (len(job.urls) + _hard.SOURCE_BATCH_SIZE - 1) // _hard.SOURCE_BATCH_SIZE,
    )
    _hard._server.update(job, source_batch_total=batch_total)

    for batch_index, offset in enumerate(
        range(0, len(job.urls), _hard.SOURCE_BATCH_SIZE),
        start=1,
    ):
        if _is_cancel_requested(job):
            break
        _hard._server.update(job, source_batch_index=batch_index)
        for source_url in job.urls[offset : offset + _hard.SOURCE_BATCH_SIZE]:
            if _is_cancel_requested(job):
                break
            _hard._server.update(
                job,
                current=f"\u0110ang qu\u00e9t nh\u00f3m {batch_index}/{batch_total}: {source_url}",
            )
            try:
                found = crawler.discover(source_url)
            except Exception as exc:
                with _hard._server.LOCK:
                    job.source_failed += 1
                    job.source_processed += 1
                _hard._server.append_failure(job, f"{source_url}: {exc}")
                continue
            for asset in found:
                if asset.url not in seen:
                    assets.append(asset)
                    seen.add(asset.url)
            with _hard._server.LOCK:
                job.source_processed += 1
                job.discovered = len(assets)
            if len(assets) > _hard.MAX_DISCOVERED_ASSETS:
                raise RuntimeError(
                    f"T\u00ecm th\u1ea5y h\u01a1n {_hard.MAX_DISCOVERED_ASSETS} file; h\u00e3y chia ngu\u1ed3n th\u00e0nh nhi\u1ec1u l\u01b0\u1ee3t"
                )
    return assets


def _organize_and_record(job: _hard.Job, downloaded: Path, output_root: Path) -> None:
    """Classify, file into a category folder, and count one produced file."""

    category = _hard.classify_audio_name(downloaded.name)
    review = _hard.needs_quality_review(downloaded)
    _hard._organize_download(downloaded, output_root, category, review)
    _hard._record_download(job, category, review)


def _download_to_job_folder(
    job: _hard.Job,
    crawler: _hard.AudioCrawler,
    assets: list[_hard.AudioAsset],
    output_root: Path,
) -> None:
    """Submit only one worker-wave at a time so cancel remains responsive.

    ``crawler.download`` returns a single Path for direct assets and a list of
    Paths for resolver assets (playlists / albums). Both shapes are handled.
    """

    wave_size = max(1, int(_hard._server.DOWNLOAD_WORKERS))
    for offset in range(0, len(assets), wave_size):
        if _is_cancel_requested(job):
            return
        wave = assets[offset : offset + wave_size]
        with ThreadPoolExecutor(max_workers=wave_size) as pool:
            futures = {
                pool.submit(crawler.download, asset, output_root): asset for asset in wave
            }
            for future in as_completed(futures):
                asset = futures[future]
                _hard._server.update(
                    job,
                    current=asset.title or Path(urlparse(asset.url).path).name,
                )
                try:
                    result = future.result()
                    produced = result if isinstance(result, list) else [result]
                    for downloaded in produced:
                        _organize_and_record(job, downloaded, output_root)
                except Exception as exc:
                    with _hard._server.LOCK:
                        job.failed += 1
                    _hard._server.append_failure(job, f"{asset.title or asset.url}: {exc}")


def _finish_cancelled(job: _hard.Job) -> None:
    with _hard._server.LOCK:
        remaining = max(0, int(job.discovered) - int(job.downloaded) - int(job.failed))
        job.cancelled = max(int(job.cancelled), remaining)
        setattr(job, "cancelled_by_user", True)
    _hard._server.update(
        job,
        status="completed",
        current="",
        error="",
        finished_at=datetime.now().isoformat(timespec="seconds"),
    )


def run_job(
    job: _hard.Job,
    download_dir: str | None = None,
    set_default: bool = False,
) -> None:
    crawler = _hard.AudioCrawler()
    try:
        root, root_source, remembered = _hard.resolve_download_root(
            download_dir,
            set_default,
        )
        folder = _hard._server.job_folder(root, job.urls, job.id)
        folder.mkdir(parents=True, exist_ok=True)
        _hard._server.update(
            job,
            status="discovering",
            download_root=str(root),
            download_root_source=root_source,
            download_root_remembered=remembered,
            output_dir=str(folder),
        )

        assets = discover_assets(job, crawler)
        if _is_cancel_requested(job):
            _finish_cancelled(job)
            return
        if not assets:
            raise RuntimeError(
                "Kh\u00f4ng t\u00ecm th\u1ea5y \u0111\u01b0\u1eddng d\u1eabn \u00e2m thanh c\u00f4ng khai t\u1eeb c\u00e1c li\u00ean k\u1ebft \u0111\u00e3 nh\u1eadp"
            )

        _hard._server.update(job, status="downloading", discovered=len(assets), current="")
        _download_to_job_folder(job, crawler, assets, folder)
        if _is_cancel_requested(job):
            _finish_cancelled(job)
            return

        final_status = "completed" if job.downloaded else "failed"
        if job.downloaded:
            error = ""
        elif job.failures:
            error = "Kh\u00f4ng t\u1ea3i \u0111\u01b0\u1ee3c file n\u00e0o. " + job.failures[0]
        else:
            error = "Kh\u00f4ng t\u1ea3i \u0111\u01b0\u1ee3c file \u00e2m thanh n\u00e0o"
        _hard._server.update(
            job,
            status=final_status,
            current="",
            error=error,
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )
    except Exception as exc:
        if _is_cancel_requested(job):
            _finish_cancelled(job)
            return
        _hard._server.update(
            job,
            status="failed",
            current="",
            error=str(exc),
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )


_ORIGINAL_JOB_PUBLIC = _hard.Job.public
_ORIGINAL_DO_POST = _hard.Handler.do_POST


def _do_post_with_cancel(self: object) -> None:
    match = _CANCEL_PATH.fullmatch(getattr(self, "path", ""))
    if match is None:
        _ORIGINAL_DO_POST(self)
        return
    if not self._guard_request():
        return
    payload = cancel_job(match.group(1))
    if payload is None:
        self._json({"error": "Kh\u00f4ng t\u00ecm th\u1ea5y t\u00e1c v\u1ee5"}, 404)
        return
    self._json(payload, 202)


_hard.Job.public = _job_public
_hard.APP_VERSION = APP_VERSION
_hard.resolve_download_root = resolve_download_root
_hard.discover_assets = discover_assets
_hard._download_to_job_folder = _download_to_job_folder
_hard.run_job = run_job
_hard._server.APP_VERSION = APP_VERSION
_hard._server.resolve_download_root = resolve_download_root
_hard._server.discover_assets = discover_assets
_hard._server.run_job = run_job
_hard.Handler.do_POST = _do_post_with_cancel
_hard.Handler.server_version = f"MH-Dowsample/{APP_VERSION}"

for _name in (
    "HOST",
    "PORT",
    "ThreadingHTTPServer",
    "choose_download_root",
    "choose_initial_download_root",
    "ensure_initial_download_root",
    "saved_download_root",
):
    setattr(_hard, _name, getattr(_hard._server, _name))

try:
    if _hard._server.ask_for_download_root_each_time():
        _hard._server.save_ask_each_time(False)
except OSError:
    pass

__all__ = [
    "APP_VERSION",
    "cancel_job",
    "discover_assets",
    "resolve_download_root",
    "run_job",
]
