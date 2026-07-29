"""Runtime hardening for the existing MH-Dowsample local server.

This module patches the existing ``server_engine`` contract instead of replacing
its HTTP API or popup layout. It adds large-list batching, per-file save prompts,
lightweight quality review, and file classification.
"""

from __future__ import annotations

import base64
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import server_engine as _server
from crawler import (
    AUDIO_SUFFIXES,
    AudioAsset,
    AudioCrawler,
    detect_audio_suffix,
    sanitize_filename,
    unique_destination,
    validate_http_url,
)

APP_VERSION = "1.3.0"
SOURCE_BATCH_SIZE = 1_000
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_DISCOVERED_ASSETS = 100_000
QUALITY_REVIEW_FOLDER = "Cần kiểm tra chất lượng"
CATEGORY_FOLDERS = {
    "loop": "Loop",
    "one_shot": "One-Shot",
    "fx": "FX",
    "unknown": "Chưa xác định",
}
CATEGORY_FIELDS = {
    "loop": "classified_loop",
    "one_shot": "classified_one_shot",
    "fx": "classified_fx",
    "unknown": "classified_unknown",
}

FX_PATTERN = re.compile(
    r"\b(?:fx|sfx|riser|impact|sweep|whoosh|uplifter|downlifter|transition|"
    r"ambience|ambient|foley|texture|noise|reverse)\b",
    flags=re.IGNORECASE,
)
LOOP_PATTERN = re.compile(r"\bloop\b|\b\d{2,3}\s*bpm\b", flags=re.IGNORECASE)
ONE_SHOT_PATTERN = re.compile(
    r"\b(?:one[\s_-]*shot|oneshot|kick|snare|clap|snap|rim|tom|hihat|hi[\s_-]*hat|"
    r"hat|cymbal|crash|ride|perc|percussion|stab|pluck|chord|vocal[\s_-]*chop)\b",
    flags=re.IGNORECASE,
)


@dataclass
class Job:
    id: str
    url: str
    urls: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    status: str = "queued"
    source_total: int = 0
    source_processed: int = 0
    source_failed: int = 0
    source_batch_index: int = 0
    source_batch_total: int = 0
    discovered: int = 0
    downloaded: int = 0
    failed: int = 0
    cancelled: int = 0
    quality_review: int = 0
    classified_loop: int = 0
    classified_one_shot: int = 0
    classified_fx: int = 0
    classified_unknown: int = 0
    current: str = ""
    download_root: str = ""
    download_root_source: str = ""
    download_root_remembered: bool = False
    output_dir: str = ""
    error: str = ""
    failures: list[str] = field(default_factory=list)
    finished_at: str = ""

    def public(self) -> dict[str, object]:
        return asdict(self)


def normalize_urls(url: str | None = None, urls: object = None) -> list[str]:
    """Normalize any practical number of public HTTP(S) URLs.

    The previous 200-link product limit is removed. Request size and discovered
    asset limits remain as safety boundaries for the loopback server.
    """

    if urls is None:
        raw_values: list[object] = [url or ""]
    elif isinstance(urls, list):
        raw_values = urls
    else:
        raise ValueError("Danh sách liên kết phải là một mảng")

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        if not isinstance(raw, str):
            raise ValueError("Mỗi liên kết phải là chuỗi")
        for line in raw.splitlines():
            candidate = line.strip()
            if not candidate:
                continue
            validated = validate_http_url(candidate)
            if validated not in seen:
                normalized.append(validated)
                seen.add(validated)
    if not normalized:
        raise ValueError("Chưa có liên kết để tải")
    return normalized


def _fallback_prompt_root() -> Path:
    configured = _server.default_download_root()
    if configured is not None:
        return _server.prepare_download_root(configured)
    downloads = Path.home() / "Downloads"
    return _server.prepare_download_root(downloads if downloads.exists() else Path.home())


def resolve_download_root(
    download_dir: str | None = None,
    set_default: bool = False,
) -> tuple[Path, str, bool]:
    if download_dir is not None:
        selected = _server.prepare_download_root(download_dir)
        if set_default:
            selected = _server.save_download_root(selected)
            return selected, "per_job_default", True
        return selected, "per_job", False

    if _server.ask_for_download_root_each_time():
        return _fallback_prompt_root(), "prompt_per_file", False

    configured = _server.default_download_root()
    if configured is not None:
        return _server.prepare_download_root(configured), "configured_default", True

    with _server.FOLDER_DIALOG_LOCK:
        configured = _server.default_download_root()
        if configured is not None:
            return _server.prepare_download_root(configured), "configured_default", True
        selected = _server.save_download_root(_server.choose_download_root())
        return selected, "prompt_default", True


def _dialog_b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def choose_download_file(default_name: str, initial_directory: Path) -> Path:
    """Open a native Windows SaveFileDialog for one downloaded asset."""

    safe_name = sanitize_filename(default_name, fallback="sample")
    suffix = Path(safe_name).suffix.lower()
    default_ext = suffix.lstrip(".") if suffix in AUDIO_SUFFIXES else ""
    name_b64 = _dialog_b64(safe_name)
    directory_b64 = _dialog_b64(str(initial_directory))
    extension_b64 = _dialog_b64(default_ext)
    payload = _server.run_folder_dialog_script(
        rf'''
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
$name = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("{name_b64}"))
$initial = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("{directory_b64}"))
$extension = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("{extension_b64}"))
$dialog = New-Object System.Windows.Forms.SaveFileDialog
$dialog.Title = "Chọn nơi lưu file âm thanh"
$dialog.FileName = $name
$dialog.InitialDirectory = $initial
$dialog.OverwritePrompt = $true
$dialog.AddExtension = $true
if ($extension) {{
    $dialog.DefaultExt = $extension
    $dialog.Filter = "Audio file (*.$extension)|*.$extension|All files (*.*)|*.*"
}} else {{
    $dialog.Filter = "Audio file|*.wav;*.flac;*.aiff;*.aif;*.mp3;*.m4a;*.aac;*.ogg;*.opus|All files (*.*)|*.*"
}}
$result = $dialog.ShowDialog()
if ($result -ne [System.Windows.Forms.DialogResult]::OK) {{
    [Console]::WriteLine('{{"cancelled":true}}')
    exit 0
}}
$payload = @{{ cancelled = $false; path = $dialog.FileName }}
[Console]::WriteLine(($payload | ConvertTo-Json -Compress))
'''
    )
    if payload.get("cancelled") is True:
        raise _server.FolderSelectionCancelled("Đã hủy lưu file này")
    selected = payload.get("path")
    if not isinstance(selected, str) or not selected.strip():
        raise ValueError("Cửa sổ lưu file không trả về đường dẫn")
    destination = Path(selected.strip()).expanduser()
    if not destination.is_absolute():
        raise ValueError("Đường dẫn lưu file phải là đường dẫn tuyệt đối")
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination


def enforce_actual_suffix(destination: Path, actual_suffix: str) -> Path:
    suffix = actual_suffix.lower()
    if suffix not in AUDIO_SUFFIXES:
        raise ValueError("Định dạng file tải xuống không được hỗ trợ")
    return destination if destination.suffix.lower() == suffix else destination.with_suffix(suffix)


def classify_audio_name(name: str) -> str:
    normalized = re.sub(r"[_\-.]+", " ", Path(name).stem.lower())
    if FX_PATTERN.search(normalized):
        return "fx"
    if LOOP_PATTERN.search(normalized):
        return "loop"
    if ONE_SHOT_PATTERN.search(normalized):
        return "one_shot"
    return "unknown"


def needs_quality_review(path: Path) -> bool:
    """Apply a deliberately light quality check without deleting uncertain files."""

    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            header = handle.read(65_536)
    except OSError:
        return True
    if size < 4_096:
        return True
    detected = detect_audio_suffix(header)
    if detected is None or detected != path.suffix.lower():
        return True
    if detected == ".wav" and (
        len(header) < 44 or b"fmt " not in header or b"data" not in header
    ):
        return True
    return False


def _record_download(job: Job, category: str, review: bool) -> None:
    with _server.LOCK:
        job.downloaded += 1
        setattr(job, CATEGORY_FIELDS[category], getattr(job, CATEGORY_FIELDS[category]) + 1)
        if review:
            job.quality_review += 1


def _organize_download(path: Path, output_root: Path, category: str, review: bool) -> Path:
    target = output_root
    if review:
        target = target / QUALITY_REVIEW_FOLDER
    target = target / CATEGORY_FOLDERS[category]
    target.mkdir(parents=True, exist_ok=True)
    destination = unique_destination(target, path.name)
    shutil.move(str(path), str(destination))
    return destination


def _staging_folder(job_id: str) -> Path:
    return _server.settings_path().parent / "staging" / job_id


def discover_assets(job: Job, crawler: AudioCrawler) -> list[AudioAsset]:
    assets: list[AudioAsset] = []
    seen: set[str] = set()
    batch_total = max(1, (len(job.urls) + SOURCE_BATCH_SIZE - 1) // SOURCE_BATCH_SIZE)
    _server.update(job, source_batch_total=batch_total)

    for batch_index, offset in enumerate(range(0, len(job.urls), SOURCE_BATCH_SIZE), start=1):
        _server.update(job, source_batch_index=batch_index)
        for source_url in job.urls[offset : offset + SOURCE_BATCH_SIZE]:
            _server.update(
                job,
                current=f"Đang quét nhóm {batch_index}/{batch_total}: {source_url}",
            )
            try:
                found = crawler.discover(source_url)
            except Exception as exc:
                with _server.LOCK:
                    job.source_failed += 1
                    job.source_processed += 1
                _server.append_failure(job, f"{source_url}: {exc}")
                continue
            for asset in found:
                if asset.url not in seen:
                    assets.append(asset)
                    seen.add(asset.url)
            with _server.LOCK:
                job.source_processed += 1
                job.discovered = len(assets)
            if len(assets) > MAX_DISCOVERED_ASSETS:
                raise RuntimeError(
                    f"Tìm thấy hơn {MAX_DISCOVERED_ASSETS} file; hãy chia nguồn thành nhiều lượt"
                )
    return assets


def _download_prompt_per_file(
    job: Job,
    crawler: AudioCrawler,
    assets: list[AudioAsset],
    staging: Path,
    initial_root: Path,
) -> None:
    for asset in assets:
        _server.update(job, current=asset.title or Path(urlparse(asset.url).path).name)
        downloaded: Path | None = None
        try:
            downloaded = crawler.download(asset, staging)
            category = classify_audio_name(downloaded.name)
            review = needs_quality_review(downloaded)
            with _server.FOLDER_DIALOG_LOCK:
                selected = choose_download_file(downloaded.name, initial_root)
            destination = enforce_actual_suffix(selected, downloaded.suffix)
            if destination.exists():
                if destination == selected:
                    destination.unlink()
                else:
                    destination = unique_destination(destination.parent, destination.name)
            shutil.move(str(downloaded), str(destination))
            initial_root = destination.parent
            _server.update(job, output_dir=str(destination.parent))
            _record_download(job, category, review)
        except _server.FolderSelectionCancelled:
            if downloaded is not None:
                downloaded.unlink(missing_ok=True)
            with _server.LOCK:
                job.cancelled += 1
        except Exception as exc:
            if downloaded is not None:
                downloaded.unlink(missing_ok=True)
            with _server.LOCK:
                job.failed += 1
            _server.append_failure(job, f"{asset.title or asset.url}: {exc}")


def _download_to_job_folder(
    job: Job,
    crawler: AudioCrawler,
    assets: list[AudioAsset],
    output_root: Path,
) -> None:
    for offset in range(0, len(assets), _server.BATCH_SIZE):
        batch = assets[offset : offset + _server.BATCH_SIZE]
        with ThreadPoolExecutor(max_workers=_server.DOWNLOAD_WORKERS) as pool:
            futures = {
                pool.submit(crawler.download, asset, output_root): asset for asset in batch
            }
            for future in as_completed(futures):
                asset = futures[future]
                _server.update(
                    job,
                    current=asset.title or Path(urlparse(asset.url).path).name,
                )
                try:
                    downloaded = future.result()
                    category = classify_audio_name(downloaded.name)
                    review = needs_quality_review(downloaded)
                    _organize_download(downloaded, output_root, category, review)
                except Exception as exc:
                    with _server.LOCK:
                        job.failed += 1
                    _server.append_failure(job, f"{asset.title or asset.url}: {exc}")
                else:
                    _record_download(job, category, review)


def run_job(
    job: Job,
    download_dir: str | None = None,
    set_default: bool = False,
) -> None:
    crawler = AudioCrawler()
    staging: Path | None = None
    try:
        root, root_source, remembered = resolve_download_root(download_dir, set_default)
        prompt_per_file = root_source == "prompt_per_file"
        if prompt_per_file:
            staging = _staging_folder(job.id)
            folder = staging
        else:
            folder = _server.job_folder(root, job.urls, job.id)
        folder.mkdir(parents=True, exist_ok=True)
        _server.update(
            job,
            status="discovering",
            download_root=str(root),
            download_root_source=root_source,
            download_root_remembered=remembered,
            output_dir=str(root if prompt_per_file else folder),
        )
        assets = discover_assets(job, crawler)
        if not assets:
            raise RuntimeError("Không tìm thấy đường dẫn âm thanh công khai từ các liên kết đã nhập")

        _server.update(job, status="downloading", discovered=len(assets), current="")
        if prompt_per_file:
            _download_prompt_per_file(job, crawler, assets, folder, root)
        else:
            _download_to_job_folder(job, crawler, assets, folder)

        completed_by_user = job.cancelled > 0 and not job.failed and not job.downloaded
        final_status = "completed" if job.downloaded or completed_by_user else "failed"
        if job.downloaded or completed_by_user:
            error = ""
        elif job.failures:
            error = "Không tải được file nào. " + job.failures[0]
        else:
            error = "Không tải được file âm thanh nào"
        _server.update(
            job,
            status=final_status,
            current="",
            error=error,
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )
    except Exception as exc:
        _server.update(
            job,
            status="failed",
            current="",
            error=str(exc),
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


# Patch the original module. Its Handler and start_job functions resolve these
# globals at runtime, so the existing HTTP API remains unchanged.
_server.APP_VERSION = APP_VERSION
_server.MAX_BODY_BYTES = MAX_REQUEST_BYTES
_server.MAX_ASSETS_PER_JOB = MAX_DISCOVERED_ASSETS
_server.MAX_URLS_PER_JOB = 0
_server.Job = Job
_server.normalize_urls = normalize_urls
_server.resolve_download_root = resolve_download_root
_server.discover_assets = discover_assets
_server.run_job = run_job
_server.Handler.server_version = f"MH-Dowsample/{APP_VERSION}"

# Public compatibility surface used by server.py and the existing tests.
Handler = _server.Handler
JOBS = _server.JOBS
LOCK = _server.LOCK
FolderSelectionCancelled = _server.FolderSelectionCancelled
append_failure = _server.append_failure
ask_for_download_root_each_time = _server.ask_for_download_root_each_time
clear_download_root = _server.clear_download_root
default_download_root = _server.default_download_root
download_root_status = _server.download_root_status
main = _server.main
open_folder = _server.open_folder
prepare_download_root = _server.prepare_download_root
save_ask_each_time = _server.save_ask_each_time
save_download_root = _server.save_download_root
settings_path = _server.settings_path
start_job = _server.start_job
update = _server.update

__all__ = [
    "APP_VERSION",
    "CATEGORY_FOLDERS",
    "FolderSelectionCancelled",
    "Handler",
    "JOBS",
    "Job",
    "LOCK",
    "QUALITY_REVIEW_FOLDER",
    "SOURCE_BATCH_SIZE",
    "choose_download_file",
    "classify_audio_name",
    "discover_assets",
    "download_root_status",
    "enforce_actual_suffix",
    "main",
    "needs_quality_review",
    "normalize_urls",
    "resolve_download_root",
    "run_job",
    "start_job",
]
