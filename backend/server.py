"""Loopback-only job server used by the Chrome extension."""

from __future__ import annotations

import argparse
import base64
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from crawler import (
    AUDIO_SUFFIXES,
    AudioAsset,
    AudioCrawler,
    normalize_audio_format,
    sanitize_filename,
    validate_http_url,
)
from sample_analysis import (
    SampleAnalyzer,
    content_folder,
    organize_sample,
    rename_sample_with_metadata,
)

LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 8765
# Backward-compatible names used by the local launcher and existing tests.
HOST = LOCAL_HOST
PORT = LOCAL_PORT
BATCH_SIZE = 200
DOWNLOAD_WORKERS = 4
APP_VERSION = "1.1.0"
MAX_ASSETS_PER_JOB = 5_000
MAX_ACTIVE_JOBS = 2
MAX_RETAINED_JOBS = 200
MAX_BODY_BYTES = 32_768
DEFAULT_JOB_TTL_SECONDS = 30 * 60
MIN_JOB_TTL_SECONDS = 5 * 60
DEFAULT_ALLOWED_SOURCE_HOSTS = (
    "splice.com",
    ".splice.com",
    "splice-res.cloudinary.com",
)
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}
EXTENSION_ORIGIN = re.compile(r"^chrome-extension://[a-p]{32}$")
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
SETTINGS_FOLDER = "MH-Dowsample"
DOWNLOAD_ROOT_KEY = "download_root"
ASK_EACH_TIME_KEY = "ask_each_time"
UNCONFIGURED_DOWNLOAD_ROOT_LABEL = "Chưa chọn - ứng dụng sẽ hỏi khi bắt đầu tải"
FOLDER_DIALOG_LOCK = threading.Lock()


def remote_mode() -> bool:
    return os.getenv("MH_REMOTE_MODE", "").strip().lower() in {"1", "true", "yes"} or bool(
        os.getenv("RENDER", "").strip()
    )


def runtime_host() -> str:
    return "0.0.0.0" if remote_mode() else LOCAL_HOST


def runtime_port() -> int:
    raw = os.getenv("PORT", "").strip() if remote_mode() else ""
    if not raw:
        return LOCAL_PORT
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError("Biến PORT phải là số nguyên") from exc
    if not 1 <= port <= 65535:
        raise ValueError("Biến PORT nằm ngoài phạm vi hợp lệ")
    return port


def extension_access_key() -> str:
    return os.getenv("MH_EXTENSION_ACCESS_KEY", "").strip()


def job_ttl_seconds() -> int:
    raw = os.getenv("MH_JOB_TTL_SECONDS", str(DEFAULT_JOB_TTL_SECONDS)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_JOB_TTL_SECONDS
    return max(MIN_JOB_TTL_SECONDS, value)


def remote_download_root() -> Path:
    configured = os.getenv("MH_TEMP_DIR", "").strip()
    base = Path(configured).expanduser() if configured else Path(tempfile.gettempdir())
    root = (base / "mh-dowsample-render").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def allowed_source_hosts() -> tuple[str, ...]:
    configured = os.getenv("MH_ALLOWED_SOURCE_HOSTS", "").strip()
    values = configured.split(",") if configured else list(DEFAULT_ALLOWED_SOURCE_HOSTS)
    return tuple(value.strip().lower() for value in values if value.strip())


def hostname_allowed(hostname: str) -> bool:
    hostname = hostname.rstrip(".").lower()
    for rule in allowed_source_hosts():
        if rule == "*":
            return True
        if rule.startswith(".") and hostname.endswith(rule) and hostname != rule[1:]:
            return True
        if hostname == rule:
            return True
    return False


def ensure_public_hostname(hostname: str) -> None:
    lowered = hostname.rstrip(".").lower()
    if lowered in {"localhost", "localhost.localdomain"}:
        raise ValueError("Không cho phép địa chỉ nội bộ")
    try:
        literal = ipaddress.ip_address(lowered.strip("[]"))
        addresses = [literal]
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(lowered, 443, type=socket.SOCK_STREAM)
            }
        except socket.gaierror as exc:
            raise ValueError("Không phân giải được tên miền nguồn") from exc
    for address in addresses:
        if not address.is_global:
            raise ValueError("Không cho phép địa chỉ nội bộ hoặc riêng tư")


def validate_remote_source_url(value: str) -> str:
    value = validate_http_url(value)
    hostname = (urlparse(value).hostname or "").lower()
    if not hostname_allowed(hostname):
        raise ValueError("Tên miền nguồn chưa được cho phép")
    ensure_public_hostname(hostname)
    return value


def settings_path() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    base = Path(local_app_data) if local_app_data else Path.home() / ".config"
    return base / SETTINGS_FOLDER / "settings.json"


def read_settings() -> dict[str, object]:
    path = settings_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def saved_download_root() -> Path | None:
    value = read_settings().get(DOWNLOAD_ROOT_KEY)
    if not isinstance(value, str) or not value.strip():
        return None
    folder = Path(value.strip()).expanduser()
    return folder if folder.is_absolute() else None


def prepare_download_root(value: str | Path) -> Path:
    raw_value = str(value).strip()
    folder = Path(raw_value).expanduser()
    if not raw_value or not folder.is_absolute():
        raise ValueError("Thư mục lưu phải là đường dẫn tuyệt đối")
    folder.mkdir(parents=True, exist_ok=True)
    folder = folder.resolve()
    if not folder.is_dir():
        raise ValueError("Đường dẫn lưu không phải là thư mục")
    return folder


def write_settings(payload: dict[str, object]) -> None:
    destination = settings_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.{uuid.uuid4().hex}.tmp")
    partial.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    partial.replace(destination)


def save_download_root(value: str | Path) -> Path:
    folder = prepare_download_root(value)
    payload = read_settings()
    payload[DOWNLOAD_ROOT_KEY] = str(folder)
    write_settings(payload)
    return folder


def clear_download_root() -> None:
    payload = read_settings()
    payload.pop(DOWNLOAD_ROOT_KEY, None)
    write_settings(payload)


def ask_for_download_root_each_time() -> bool:
    return read_settings().get(ASK_EACH_TIME_KEY) is True


def save_ask_each_time(value: bool) -> bool:
    payload = read_settings()
    payload[ASK_EACH_TIME_KEY] = value
    write_settings(payload)
    return value


def default_download_root() -> Path | None:
    configured = os.getenv("MH_AUDIO_DOWNLOAD_DIR", "").strip()
    if configured:
        folder = Path(configured).expanduser()
        return folder if folder.is_absolute() else None
    return saved_download_root()


class FolderSelectionCancelled(ValueError):
    """Raised when the user closes the native folder selection flow."""


def run_folder_dialog_script(script: str) -> dict[str, object]:
    if os.name != "nt":
        raise ValueError("Chưa có thư mục mặc định. Hãy chọn một đường dẫn lưu tuyệt đối.")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-STA",
            "-EncodedCommand",
            encoded,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creation_flags,
        timeout=None,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError("Không thể mở cửa sổ chọn thư mục lưu")
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Cửa sổ chọn thư mục không trả về kết quả")
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise ValueError("Kết quả chọn thư mục không hợp lệ") from exc
    if not isinstance(payload, dict):
        raise ValueError("Kết quả chọn thư mục không hợp lệ")
    return payload


def choose_download_root() -> Path:
    """Ask for one job's destination without changing the default folder."""

    payload = run_folder_dialog_script(
        r'''
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = "Chọn nơi lưu file âm thanh cho lượt tải này"
$dialog.ShowNewFolderButton = $true
$owner = New-Object System.Windows.Forms.Form
$owner.Text = "MH-Dowsample"
$owner.ShowInTaskbar = $false
$owner.TopMost = $true
$owner.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
$owner.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.Opacity = 0.01
try {
    $owner.Show()
    $owner.Activate()
    $owner.BringToFront()
    $result = $dialog.ShowDialog($owner)
} finally {
    $owner.Close()
    $owner.Dispose()
    $dialog.Dispose()
}
if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
    [Console]::WriteLine('{"cancelled":true}')
    exit 0
}
$payload = @{
    cancelled = $false
    path = $dialog.SelectedPath
}
[Console]::WriteLine(($payload | ConvertTo-Json -Compress))
'''
    )
    if payload.get("cancelled") is True:
        raise FolderSelectionCancelled("Đã hủy chọn thư mục nên chưa bắt đầu tải")
    selected = payload.get("path")
    if not isinstance(selected, str):
        raise ValueError("Cửa sổ chọn thư mục không trả về đường dẫn")
    return prepare_download_root(selected)


def choose_initial_download_root() -> Path:
    """Require a default destination before the server starts for a new user."""

    payload = run_folder_dialog_script(
        r'''
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = "Chọn nơi lưu file âm thanh. Ứng dụng sẽ nhớ thư mục này; bạn có thể đổi lại sau."
$dialog.ShowNewFolderButton = $true
$owner = New-Object System.Windows.Forms.Form
$owner.Text = "MH-Dowsample"
$owner.ShowInTaskbar = $false
$owner.TopMost = $true
$owner.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
$owner.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.Opacity = 0.01
try {
    $owner.Show()
    $owner.Activate()
    $owner.BringToFront()
    $result = $dialog.ShowDialog($owner)
} finally {
    $owner.Close()
    $owner.Dispose()
    $dialog.Dispose()
}
if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
    [Console]::WriteLine('{"cancelled":true}')
    exit 0
}
$payload = @{ cancelled = $false; path = $dialog.SelectedPath }
[Console]::WriteLine(($payload | ConvertTo-Json -Compress))
'''
    )
    if payload.get("cancelled") is True:
        raise FolderSelectionCancelled(
            "Bạn cần chọn nơi lưu trước khi sử dụng MH-Dowsample"
        )
    selected = payload.get("path")
    if not isinstance(selected, str):
        raise ValueError("Cửa sổ chọn thư mục không trả về đường dẫn")
    return prepare_download_root(selected)


def ensure_initial_download_root() -> Path:
    configured = default_download_root()
    if configured is not None:
        return prepare_download_root(configured)
    with FOLDER_DIALOG_LOCK:
        configured = default_download_root()
        if configured is not None:
            return prepare_download_root(configured)
        return save_download_root(choose_initial_download_root())


def resolve_download_root(
    download_dir: str | None = None,
    set_default: bool = False,
) -> tuple[Path, str, bool]:
    """Resolve one job's destination with per-job choice taking precedence."""

    if remote_mode():
        if download_dir is not None or set_default:
            raise ValueError("Render không thể ghi trực tiếp vào ổ đĩa trên máy người dùng")
        return remote_download_root(), "render_temporary", False

    if download_dir is not None:
        selected = prepare_download_root(download_dir)
        if set_default:
            selected = save_download_root(selected)
            return selected, "per_job_default", True
        return selected, "per_job", False

    if ask_for_download_root_each_time():
        with FOLDER_DIALOG_LOCK:
            return choose_download_root(), "prompt_each_time", False

    configured = default_download_root()
    if configured is not None:
        return prepare_download_root(configured), "configured_default", True

    with FOLDER_DIALOG_LOCK:
        configured = default_download_root()
        if configured is not None:
            return prepare_download_root(configured), "configured_default", True
        selected = save_download_root(choose_download_root())
        return selected, "prompt_default", True


@dataclass
class Job:
    id: str
    url: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    status: str = "queued"
    discovered: int = 0
    downloaded: int = 0
    failed: int = 0
    analyzed: int = 0
    loops: int = 0
    one_shots: int = 0
    fx: int = 0
    unknown: int = 0
    audio_errors: int = 0
    rejected: int = 0
    analysis_failed: int = 0
    organization_failed: int = 0
    current: str = ""
    download_root: str = ""
    download_root_source: str = ""
    download_root_remembered: bool = False
    output_dir: str = ""
    error: str = ""
    failures: list[str] = field(default_factory=list)
    provided_assets: list[AudioAsset] = field(default_factory=list, repr=False)
    sample_results: list[dict[str, object]] = field(default_factory=list, repr=False)
    report_path: str = ""
    finished_at: str = ""
    finished_epoch: float = field(default=0.0, repr=False)
    cancel_requested: bool = False

    def public(self) -> dict[str, object]:
        payload = {
            key: value
            for key, value in self.__dict__.items()
            if key not in {"provided_assets", "sample_results", "finished_epoch"}
        }
        if remote_mode():
            payload["download_root"] = "Chrome / Cốc Cốc"
            payload["output_dir"] = "Chrome / Cốc Cốc"
            payload["report_path"] = ""
        if self.finished_epoch:
            payload["expires_at_epoch"] = int(self.finished_epoch + job_ttl_seconds())
        payload["failures"] = list(self.failures)
        payload["sample_results_total"] = len(self.sample_results)
        return payload


JOBS: dict[str, Job] = {}
LOCK = threading.Lock()


def job_folder(root: Path, url: str, job_id: str) -> Path:
    slug = Path(urlparse(url).path.rstrip("/")).name or "audio"
    slug = sanitize_filename(slug, fallback="audio")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return root / f"{slug}-{stamp}-{job_id[:6]}"


def update(job: Job, **values: object) -> None:
    with LOCK:
        if (
            job.cancel_requested
            and "status" in values
            and values["status"] not in {"cancelling", "cancelled"}
        ):
            values.pop("status")
        for key, value in values.items():
            setattr(job, key, value)


def cancellation_requested(job: Job) -> bool:
    with LOCK:
        return job.cancel_requested


def finish_cancelled(job: Job) -> None:
    update(
        job,
        status="cancelled",
        current="",
        error="Đã hủy tác vụ theo yêu cầu.",
        finished_at=datetime.now().isoformat(timespec="seconds"),
        finished_epoch=time.time(),
    )


def cancel_job(job_id: str) -> Job:
    with LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise ValueError("Không tìm thấy tác vụ")
        if job.status in TERMINAL_JOB_STATUSES:
            return job
        job.cancel_requested = True
        job.status = "cancelling"
        job.current = ""
        return job


def record_sample_result(job: Job, result: dict[str, object]) -> None:
    content_type = str(result.get("content_type") or "unknown")
    counter = {
        "loop": "loops",
        "one-shot": "one_shots",
        "fx": "fx",
    }.get(content_type, "unknown")
    with LOCK:
        job.analyzed += 1
        setattr(job, counter, int(getattr(job, counter)) + 1)
        status = str(result.get("status") or "")
        if status == "rejected":
            job.rejected += 1
            job.audio_errors += 1
        elif status == "analysis_failed":
            job.analysis_failed += 1
            job.audio_errors += 1
        elif status == "organization_failed":
            job.organization_failed += 1
        job.sample_results.append(result)


def write_job_report(job: Job, folder: Path) -> Path:
    destination = folder / "sample-report.json"
    partial = destination.with_suffix(".json.part")
    with LOCK:
        payload = {
            "job": job.public(),
            "samples": list(job.sample_results),
        }
    partial.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    partial.replace(destination)
    return destination


def run_job(
    job: Job,
    download_dir: str | None = None,
    set_default: bool = False,
) -> None:
    crawler = AudioCrawler(validate_remote_source_url if remote_mode() else None)
    try:
        if cancellation_requested(job):
            finish_cancelled(job)
            return
        root, root_source, remembered = resolve_download_root(download_dir, set_default)
        if cancellation_requested(job):
            finish_cancelled(job)
            return
        analyzer = SampleAnalyzer()
        folder = job_folder(root, job.url, job.id)
        folder.mkdir(parents=True, exist_ok=True)
        incoming = folder / ".incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        update(
            job,
            status="discovering",
            download_root=str(root),
            download_root_source=root_source,
            download_root_remembered=remembered,
            output_dir=str(folder),
        )
        assets = list(job.provided_assets) if job.provided_assets else crawler.discover(job.url)
        if cancellation_requested(job):
            finish_cancelled(job)
            return
        if remote_mode():
            for asset in assets:
                validate_remote_source_url(asset.url)
        if len(assets) > MAX_ASSETS_PER_JOB:
            raise RuntimeError(
                f"Tìm thấy {len(assets)} file; giới hạn mỗi tác vụ là {MAX_ASSETS_PER_JOB} file"
            )
        update(job, status="downloading", discovered=len(assets))
        downloaded_files: list[tuple[Path, AudioAsset]] = []
        for offset in range(0, len(assets), BATCH_SIZE):
            if cancellation_requested(job):
                finish_cancelled(job)
                return
            batch = assets[offset : offset + BATCH_SIZE]
            with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
                futures = {pool.submit(crawler.download, asset, incoming): asset for asset in batch}
                for future in as_completed(futures):
                    asset = futures[future]
                    update(job, current=asset.title or Path(urlparse(asset.url).path).name)
                    try:
                        downloaded_file = future.result()
                    except Exception as exc:
                        with LOCK:
                            job.failed += 1
                            if len(job.failures) < 20:
                                job.failures.append(f"{asset.title or asset.url}: {exc}")
                    else:
                        with LOCK:
                            job.downloaded += 1
                        downloaded_files.append((downloaded_file, asset))

            if cancellation_requested(job):
                finish_cancelled(job)
                return

        update(job, status="analyzing", current="")
        for downloaded_file, asset in downloaded_files:
            if cancellation_requested(job):
                finish_cancelled(job)
                return
            update(job, current=downloaded_file.name)
            analysis = analyzer.analyze(downloaded_file)
            detected_bpm = analysis.get("bpm", 0)
            detected_key = analysis.get("key", "Unknown")
            effective_bpm = asset.bpm or detected_bpm
            effective_key = asset.musical_key or detected_key
            analysis["detected_bpm"] = detected_bpm
            analysis["detected_key"] = detected_key
            analysis["bpm"] = effective_bpm
            analysis["key"] = effective_key
            analysis["bpm_source"] = (
                "catalogue" if asset.bpm else "analysis" if detected_bpm else "none"
            )
            analysis["key_source"] = (
                "catalogue"
                if asset.musical_key
                else "analysis"
                if str(detected_key).lower() not in {"", "unknown", "none", "n/a"}
                else "none"
            )
            downloaded_file = rename_sample_with_metadata(
                downloaded_file,
                effective_bpm,
                effective_key,
            )
            content_type = str(analysis.get("content_type") or "unknown")
            analysis_error = str(analysis.get("analysis_error") or "")
            status = (
                "analysis_failed"
                if analysis_error
                else "passed"
                if analysis.get("passed") is True
                else "rejected"
            )
            result: dict[str, object] = {
                "file": downloaded_file.name,
                "status": status,
                "content_type": content_type,
                "category": content_folder(content_type),
                "output": "",
                "source_url": redacted_url(asset.url),
                "declared_format": asset.declared_format or "",
                "actual_format": downloaded_file.suffix.lower().lstrip("."),
                "metadata_source": asset.metadata_source,
                "catalogue_bpm": asset.bpm,
                "catalogue_key": asset.musical_key,
                "analysis": analysis,
            }
            try:
                output = organize_sample(downloaded_file, folder, content_type)
                result["output"] = str(output)
            except Exception as exc:
                result["status"] = "organization_failed"
                result["error"] = str(exc)
            record_sample_result(job, result)

        if cancellation_requested(job):
            finish_cancelled(job)
            return

        try:
            incoming.rmdir()
        except OSError:
            pass
        report = write_job_report(job, folder)
        final_status = "completed" if job.downloaded else "failed"
        error = "" if job.downloaded else "Không tải được file âm thanh nào"
        update(
            job,
            status=final_status,
            current="",
            error=error,
            report_path=str(report),
            finished_at=datetime.now().isoformat(timespec="seconds"),
            finished_epoch=time.time(),
        )
    except Exception as exc:
        if cancellation_requested(job):
            finish_cancelled(job)
            return
        update(
            job,
            status="failed",
            current="",
            error=str(exc),
            finished_at=datetime.now().isoformat(timespec="seconds"),
            finished_epoch=time.time(),
        )


def cleanup_jobs_locked() -> None:
    expired: list[tuple[str, str]] = []
    now = time.time()
    for job_id, job in JOBS.items():
        if (
            job.status in TERMINAL_JOB_STATUSES
            and job.finished_epoch
            and now - job.finished_epoch >= job_ttl_seconds()
        ):
            expired.append((job_id, job.output_dir))
    for job_id, output_dir in expired:
        JOBS.pop(job_id, None)
        if remote_mode() and output_dir:
            root = remote_download_root()
            folder = Path(output_dir).resolve()
            if folder.is_relative_to(root) and folder != root:
                shutil.rmtree(folder, ignore_errors=True)

    finished = [
        job_id
        for job_id, job in JOBS.items()
        if job.status in TERMINAL_JOB_STATUSES
    ]
    while len(JOBS) >= MAX_RETAINED_JOBS and finished:
        JOBS.pop(finished.pop(0), None)


def start_job(
    url: str,
    download_dir: str | None = None,
    set_default: bool = False,
    provided_assets: list[AudioAsset] | None = None,
) -> Job:
    url = validate_remote_source_url(url) if remote_mode() else validate_http_url(url)
    assets = list(provided_assets or [])
    if len(assets) > MAX_ASSETS_PER_JOB:
        raise ValueError(f"Giới hạn mỗi tác vụ là {MAX_ASSETS_PER_JOB} file")
    job = Job(id=uuid.uuid4().hex, url=url, provided_assets=assets)
    with LOCK:
        cleanup_jobs_locked()
        active_jobs = sum(
            existing.status not in TERMINAL_JOB_STATUSES
            for existing in JOBS.values()
        )
        if active_jobs >= MAX_ACTIVE_JOBS:
            raise ValueError("Đang có quá nhiều tác vụ. Hãy chờ tác vụ hiện tại hoàn tất.")
        JOBS[job.id] = job
    threading.Thread(
        target=run_job,
        args=(job, download_dir, set_default),
        daemon=True,
    ).start()
    return job


def parse_provided_assets(value: object) -> list[AudioAsset]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Danh sách file gốc phải là mảng")
    if len(value) > MAX_ASSETS_PER_JOB:
        raise ValueError(f"Giới hạn mỗi tác vụ là {MAX_ASSETS_PER_JOB} file")
    assets: list[AudioAsset] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Thông tin file gốc không hợp lệ")
        raw_url = str(item.get("url") or "")
        url = validate_remote_source_url(raw_url) if remote_mode() else validate_http_url(raw_url)
        if url in seen:
            continue
        title = str(item.get("title") or "").strip()[:220] or None
        try:
            bpm = int(item.get("bpm") or 0)
        except (TypeError, ValueError):
            bpm = 0
        bpm_value = bpm if 20 <= bpm <= 400 else None
        musical_key = str(item.get("musical_key") or "").strip()[:32] or None
        declared_format = normalize_audio_format(item.get("declared_format"))
        assets.append(
            AudioAsset(
                url=url,
                title=title,
                bpm=bpm_value,
                musical_key=musical_key,
                declared_format=declared_format,
                metadata_source="extension_page",
            )
        )
        seen.add(url)
    return assets


def open_folder(path: str) -> None:
    folder = Path(path).resolve()
    with LOCK:
        allowed_folders = {
            Path(job.output_dir).resolve()
            for job in JOBS.values()
            if job.output_dir
        }
    if folder not in allowed_folders or not folder.is_dir():
        raise ValueError("Thư mục không hợp lệ")
    if os.name == "nt":
        os.startfile(folder)  # type: ignore[attr-defined]
    elif os.name == "posix":
        command = "open" if subprocess.run(["uname"], capture_output=True, text=True).stdout.strip() == "Darwin" else "xdg-open"
        subprocess.Popen([command, str(folder)])


def download_root_status() -> dict[str, object]:
    if remote_mode():
        return {
            "download_root": "Chrome / Cốc Cốc",
            "download_root_configured": True,
            "ask_each_time": False,
            "delivery": "browser",
        }
    root = default_download_root()
    return {
        "download_root": str(root) if root is not None else UNCONFIGURED_DOWNLOAD_ROOT_LABEL,
        "download_root_configured": root is not None,
        "ask_each_time": ask_for_download_root_each_time(),
    }


def redacted_url(value: str) -> str:
    parsed = urlparse(value)
    return parsed._replace(query="", fragment="").geturl()


def encode_file_id(relative_path: str) -> str:
    return base64.urlsafe_b64encode(relative_path.encode("utf-8")).decode("ascii").rstrip("=")


def decode_file_id(value: str) -> str:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Mã file không hợp lệ")
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Mã file không hợp lệ") from exc


def job_audio_files(job: Job) -> list[Path]:
    if not job.output_dir:
        return []
    root = Path(job.output_dir).resolve()
    if not root.is_dir():
        return []
    return sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES
        ),
        key=lambda path: path.relative_to(root).as_posix().lower(),
    )


def job_files_payload(job: Job) -> dict[str, object]:
    root = Path(job.output_dir).resolve() if job.output_dir else Path()
    items: list[dict[str, object]] = []
    for path in job_audio_files(job):
        relative = path.relative_to(root).as_posix()
        file_id = encode_file_id(relative)
        items.append(
            {
                "id": file_id,
                "name": path.name,
                "relative_path": relative,
                "size": path.stat().st_size,
                "download_url": f"/jobs/{job.id}/download/{quote(file_id)}",
            }
        )
    return {"job_id": job.id, "files": items, "total": len(items)}


def resolve_job_file(job: Job, file_id: str) -> Path:
    if not job.output_dir:
        raise ValueError("Tác vụ chưa có file")
    root = Path(job.output_dir).resolve()
    relative = Path(decode_file_id(unquote(file_id)))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Đường dẫn file không hợp lệ")
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or target.suffix.lower() not in AUDIO_SUFFIXES:
        raise ValueError("File không hợp lệ")
    if not target.is_file():
        raise FileNotFoundError("Không tìm thấy file")
    return target


def files_job_id(path: str) -> str | None:
    parts = urlparse(path).path.strip("/").split("/")
    if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "files" and parts[1].isalnum():
        return parts[1]
    return None


def download_file_request(path: str) -> tuple[str, str] | None:
    parts = urlparse(path).path.strip("/").split("/")
    if (
        len(parts) == 4
        and parts[0] == "jobs"
        and parts[2] == "download"
        and parts[1].isalnum()
    ):
        return parts[1], parts[3]
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = f"MH-Dowsample/{APP_VERSION}"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def _headers(self, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin", "").strip()
        if EXTENSION_ORIGIN.fullmatch(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-MH-Access-Key")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        if self.headers.get("Access-Control-Request-Private-Network", "").lower() == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()

    def _json(self, payload: object, status: int = 200) -> None:
        self._headers(status)
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _file(self, path: Path) -> None:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Disposition",
            f"attachment; filename*=UTF-8''{quote(path.name)}",
        )
        origin = self.headers.get("Origin", "").strip()
        if EXTENSION_ORIGIN.fullmatch(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        with path.open("rb") as handle:
            while chunk := handle.read(256 * 1024):
                self.wfile.write(chunk)

    def _body(self) -> dict[str, object]:
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError as exc:
            raise ValueError("Content-Length không hợp lệ") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("Dữ liệu gửi lên quá lớn")
        raw = self.rfile.read(length)
        value = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(value, dict):
            raise ValueError("Dữ liệu JSON không hợp lệ")
        return value

    def _request_allowed(self) -> bool:
        origin = self.headers.get("Origin", "").strip()
        if remote_mode():
            configured_key = extension_access_key()
            supplied_key = self.headers.get("X-MH-Access-Key", "").strip()
            if not configured_key or not hmac.compare_digest(configured_key, supplied_key):
                return False
            return not origin or EXTENSION_ORIGIN.fullmatch(origin) is not None

        host_header = self.headers.get("Host", "").strip().lower()
        if host_header.startswith("["):
            hostname = host_header.split("]", 1)[0] + "]"
        else:
            hostname = host_header.split(":", 1)[0]
        if hostname not in LOCAL_HOSTS:
            return False
        return not origin or EXTENSION_ORIGIN.fullmatch(origin) is not None

    def _guard_request(self) -> bool:
        if self._request_allowed():
            return True
        self._json({"error": "Nguồn yêu cầu không được phép"}, 403)
        return False

    def do_OPTIONS(self) -> None:
        if remote_mode():
            origin = self.headers.get("Origin", "").strip()
            if EXTENSION_ORIGIN.fullmatch(origin):
                self._headers(204)
            else:
                self._json({"error": "Nguồn yêu cầu không được phép"}, 403)
            return
        if not self._guard_request():
            return
        self._headers(204)

    def do_GET(self) -> None:
        if not self._guard_request():
            return
        with LOCK:
            cleanup_jobs_locked()
        download_request = download_file_request(self.path)
        if download_request:
            job_id, file_id = download_request
            with LOCK:
                job = JOBS.get(job_id)
            if job is None:
                self._json({"error": "Không tìm thấy tác vụ"}, 404)
                return
            try:
                self._file(resolve_job_file(job, file_id))
            except FileNotFoundError as exc:
                self._json({"error": str(exc)}, 404)
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
            return

        files_id = files_job_id(self.path)
        if files_id:
            with LOCK:
                job = JOBS.get(files_id)
            if job is None:
                self._json({"error": "Không tìm thấy tác vụ"}, 404)
                return
            self._json(job_files_payload(job))
            return
        if self.path == "/health":
            self._json(
                {
                    "ok": True,
                    "version": APP_VERSION,
                    **download_root_status(),
                }
            )
            return
        if self.path == "/settings":
            self._json(download_root_status())
            return
        try:
            samples_request = sample_results_request(self.path)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
            return
        if samples_request:
            job_id, offset, limit = samples_request
            with LOCK:
                job = JOBS.get(job_id)
                total = len(job.sample_results) if job else 0
                items = list(job.sample_results[offset : offset + limit]) if job else []
            if job is None:
                self._json({"error": "Không tìm thấy tác vụ"}, 404)
                return
            self._json(
                {
                    "job_id": job_id,
                    "total": total,
                    "offset": offset,
                    "limit": limit,
                    "items": items,
                }
            )
            return
        match = re_job_path(self.path)
        if match:
            with LOCK:
                job = JOBS.get(match)
                payload = job.public() if job else None
            self._json(payload or {"error": "Không tìm thấy tác vụ"}, 200 if payload else 404)
            return
        self._json({"error": "Không tìm thấy endpoint"}, 404)

    def do_POST(self) -> None:
        if not self._guard_request():
            return
        try:
            with LOCK:
                cleanup_jobs_locked()
            body = self._body()
            if self.path == "/jobs":
                raw_download_dir = body.get("download_dir")
                if raw_download_dir is not None and not isinstance(raw_download_dir, str):
                    raise ValueError("Đường dẫn lưu phải là chuỗi")
                set_default = body.get("set_default", False)
                if not isinstance(set_default, bool):
                    raise ValueError("Tùy chọn đặt mặc định phải là true hoặc false")
                job = start_job(
                    str(body.get("url") or ""),
                    raw_download_dir,
                    set_default,
                    parse_provided_assets(body.get("assets")),
                )
                self._json(job.public(), 202)
                return
            cancel_id = re_cancel_job_path(self.path)
            if cancel_id:
                job = cancel_job(cancel_id)
                self._json(job.public(), 202)
                return
            if self.path == "/settings/download-root":
                if remote_mode():
                    raise ValueError("Vị trí lưu do Chrome/Cốc Cốc quản lý khi dùng Render")
                select = body.get("select", False)
                if not isinstance(select, bool):
                    raise ValueError("Tùy chọn chọn thư mục phải là true hoặc false")
                if select:
                    with FOLDER_DIALOG_LOCK:
                        selected = save_download_root(choose_initial_download_root())
                    self._json({"ok": True, **download_root_status()})
                    return

                if "ask_each_time" in body:
                    ask_each_time = body["ask_each_time"]
                    if not isinstance(ask_each_time, bool):
                        raise ValueError("Tùy chọn hỏi nơi lưu phải là true hoặc false")
                    save_ask_each_time(ask_each_time)
                    self._json({"ok": True, **download_root_status()})
                    return

                clear = body.get("clear", False)
                if not isinstance(clear, bool):
                    raise ValueError("Tùy chọn xóa mặc định phải là true hoặc false")
                if clear:
                    clear_download_root()
                    self._json({"ok": True, **download_root_status()})
                    return
                raw_download_dir = body.get("download_dir")
                if not isinstance(raw_download_dir, str):
                    raise ValueError("Thiếu đường dẫn lưu tuyệt đối")
                selected = save_download_root(raw_download_dir)
                self._json(
                    {
                        "ok": True,
                        "download_root": str(selected),
                        "download_root_configured": True,
                    }
                )
                return
            if self.path == "/open-folder":
                if remote_mode():
                    raise ValueError("Render không thể mở thư mục trên máy người dùng")
                job_id = str(body.get("job_id") or "")
                with LOCK:
                    job = JOBS.get(job_id)
                if not job or not job.output_dir:
                    raise ValueError("Chưa có thư mục tải")
                open_folder(job.output_dir)
                self._json({"ok": True})
                return
            self._json({"error": "Không tìm thấy endpoint"}, 404)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)


def re_job_path(path: str) -> str | None:
    prefix = "/jobs/"
    value = path[len(prefix) :] if path.startswith(prefix) else ""
    return value if value and value.isalnum() else None


def re_cancel_job_path(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) != 3 or parts[0] != "jobs" or parts[2] != "cancel":
        return None
    job_id = parts[1]
    return job_id if job_id.isalnum() else None


def sample_results_request(path: str) -> tuple[str, int, int] | None:
    parsed = urlparse(path)
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 3 or parts[0] != "jobs" or parts[2] != "samples":
        return None
    job_id = parts[1]
    if not job_id.isalnum():
        return None
    query = parse_qs(parsed.query)
    try:
        offset = max(0, int(query.get("offset", ["0"])[0]))
        limit = min(500, max(1, int(query.get("limit", ["100"])[0])))
    except ValueError as exc:
        raise ValueError("offset và limit phải là số nguyên") from exc
    return job_id, offset, limit


def cleanup_loop() -> None:
    while True:
        time.sleep(60)
        with LOCK:
            cleanup_jobs_locked()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="MH-Dowsample local server")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--check",
        action="store_true",
        help="kiểm tra bản chạy rồi thoát",
    )
    actions.add_argument(
        "--set-download-dir",
        metavar="PATH",
        help="lưu thư mục tải mặc định rồi thoát",
    )
    actions.add_argument(
        "--get-download-dir",
        action="store_true",
        help="in thư mục tải hiện tại rồi thoát",
    )
    actions.add_argument(
        "--clear-download-dir",
        action="store_true",
        help="xóa thư mục tải mặc định rồi thoát",
    )
    args = parser.parse_args(argv)
    if args.set_download_dir:
        selected = save_download_root(args.set_download_dir)
        print(json.dumps({"ok": True, "download_root": str(selected)}))
        return
    if args.get_download_dir:
        root = default_download_root()
        print(json.dumps({"download_root": str(root) if root is not None else ""}))
        return
    if args.clear_download_dir:
        clear_download_root()
        print(json.dumps({"ok": True, **download_root_status()}))
        return
    if args.check:
        try:
            SampleAnalyzer.check_dependencies()
        except BaseException as exc:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "version": APP_VERSION,
                        "error_type": exc.__class__.__name__,
                        "error": str(exc),
                    },
                    ensure_ascii=True,
                ),
                file=sys.stderr,
                flush=True,
            )
            raise SystemExit(1) from exc
        print(
            json.dumps(
                {
                    "ok": True,
                    "version": APP_VERSION,
                    **download_root_status(),
                },
            )
        )
        return

    if remote_mode():
        if not extension_access_key():
            raise SystemExit("Thiếu biến MH_EXTENSION_ACCESS_KEY cho server Render")
        root = remote_download_root()
    else:
        try:
            root = ensure_initial_download_root()
        except FolderSelectionCancelled as exc:
            print(str(exc).encode("ascii", "backslashreplace").decode("ascii"))
            raise SystemExit(1) from exc
    host = runtime_host()
    port = runtime_port()
    print(f"MH-Dowsample Server {APP_VERSION}")
    print(f"Server: http://{host}:{port}")
    printable_root = str(root).encode("ascii", "backslashreplace").decode("ascii")
    print(f"Tai xuong: {printable_root}")
    print("Giu cua so nay mo trong luc tai.")
    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        raise SystemExit(f"Khong the mo server tai cong {port}: {exc}") from exc
    threading.Thread(target=cleanup_loop, daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nDa dung local server.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
