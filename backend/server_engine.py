"""Loopback-only server for the existing MH-Dowsample extension popup."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as BaseThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from crawler import AudioAsset, AudioCrawler, sanitize_filename, validate_http_url

HOST = "127.0.0.1"
PORT = 8765
BATCH_SIZE = 1_000
DOWNLOAD_WORKERS = 4
APP_VERSION = "1.2.0"
MAX_URLS_PER_JOB = 200
MAX_ASSETS_PER_JOB = 20_000
MAX_ACTIVE_JOBS = 2
MAX_RETAINED_JOBS = 200
MAX_BODY_BYTES = 128 * 1024
EXTENSION_ORIGIN = re.compile(r"^chrome-extension://[a-p]{32}$")
FIREFOX_EXTENSION_ORIGIN = re.compile(r"^moz-extension://[0-9a-f-]{32,36}$", re.IGNORECASE)
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
SETTINGS_FOLDER = "MH-Dowsample"
DOWNLOAD_ROOT_KEY = "download_root"
ASK_EACH_TIME_KEY = "ask_each_time"
UNCONFIGURED_DOWNLOAD_ROOT_LABEL = "Chưa chọn thư mục lưu"
FOLDER_DIALOG_LOCK = threading.Lock()
SERVER_STARTED_AT = time.time()


class ThreadingHTTPServer(BaseThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


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
    """Raised when the user closes a native folder selection flow."""


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
    payload = run_folder_dialog_script(
        r'''
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = "Chọn nơi lưu file âm thanh cho lượt tải này"
$dialog.ShowNewFolderButton = $true
$result = $dialog.ShowDialog()
if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
    [Console]::WriteLine('{"cancelled":true}')
    exit 0
}
$payload = @{ cancelled = $false; path = $dialog.SelectedPath }
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
    payload = run_folder_dialog_script(
        r'''
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = "Chọn nơi lưu file âm thanh. Ứng dụng sẽ nhớ thư mục này; bạn có thể đổi lại sau."
$dialog.ShowNewFolderButton = $true
$result = $dialog.ShowDialog()
if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
    [Console]::WriteLine('{"cancelled":true}')
    exit 0
}
$payload = @{ cancelled = $false; path = $dialog.SelectedPath }
[Console]::WriteLine(($payload | ConvertTo-Json -Compress))
'''
    )
    if payload.get("cancelled") is True:
        raise FolderSelectionCancelled("Bạn cần chọn nơi lưu trước khi sử dụng MH-Dowsample")
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


def normalize_urls(url: str | None = None, urls: object = None) -> list[str]:
    raw_values: list[object]
    if urls is None:
        raw_values = [url or ""]
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
    if len(normalized) > MAX_URLS_PER_JOB:
        raise ValueError(f"Mỗi lượt tối đa {MAX_URLS_PER_JOB} liên kết")
    return normalized


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
    discovered: int = 0
    downloaded: int = 0
    failed: int = 0
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


JOBS: dict[str, Job] = {}
LOCK = threading.Lock()


def job_folder(root: Path, urls: list[str], job_id: str) -> Path:
    if len(urls) == 1:
        slug = Path(urlparse(urls[0]).path.rstrip("/")).name or "audio"
    else:
        slug = f"batch-{len(urls)}-links"
    slug = sanitize_filename(slug, fallback="audio")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return root / f"{slug}-{stamp}-{job_id[:6]}"


def update(job: Job, **values: object) -> None:
    with LOCK:
        for key, value in values.items():
            setattr(job, key, value)


def append_failure(job: Job, message: str) -> None:
    with LOCK:
        if len(job.failures) < 50:
            job.failures.append(message)


def discover_assets(job: Job, crawler: AudioCrawler) -> list[AudioAsset]:
    assets: list[AudioAsset] = []
    seen: set[str] = set()
    for source_url in job.urls:
        update(job, current=f"Đang quét: {source_url}")
        try:
            found = crawler.discover(source_url)
        except Exception as exc:
            with LOCK:
                job.source_failed += 1
                job.source_processed += 1
            append_failure(job, f"{source_url}: {exc}")
            continue
        for asset in found:
            if asset.url not in seen:
                assets.append(asset)
                seen.add(asset.url)
        with LOCK:
            job.source_processed += 1
            job.discovered = len(assets)
        if len(assets) > MAX_ASSETS_PER_JOB:
            raise RuntimeError(
                f"Tìm thấy hơn {MAX_ASSETS_PER_JOB} file; hãy chia danh sách thành nhiều lượt"
            )
    return assets


def run_job(
    job: Job,
    download_dir: str | None = None,
    set_default: bool = False,
) -> None:
    crawler = AudioCrawler()
    try:
        root, root_source, remembered = resolve_download_root(download_dir, set_default)
        folder = job_folder(root, job.urls, job.id)
        folder.mkdir(parents=True, exist_ok=True)
        update(
            job,
            status="discovering",
            download_root=str(root),
            download_root_source=root_source,
            download_root_remembered=remembered,
            output_dir=str(folder),
        )
        assets = discover_assets(job, crawler)
        if not assets:
            raise RuntimeError("Không tìm thấy đường dẫn âm thanh công khai từ các liên kết đã nhập")

        update(job, status="downloading", discovered=len(assets), current="")
        for offset in range(0, len(assets), BATCH_SIZE):
            batch = assets[offset : offset + BATCH_SIZE]
            with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
                futures = {pool.submit(crawler.download, asset, folder): asset for asset in batch}
                for future in as_completed(futures):
                    asset = futures[future]
                    update(job, current=asset.title or Path(urlparse(asset.url).path).name)
                    try:
                        future.result()
                    except Exception as exc:
                        with LOCK:
                            job.failed += 1
                        append_failure(job, f"{asset.title or asset.url}: {exc}")
                    else:
                        with LOCK:
                            job.downloaded += 1

        final_status = "completed" if job.downloaded else "failed"
        if job.downloaded:
            error = ""
        elif job.failures:
            error = "Không tải được file nào. " + job.failures[0]
        else:
            error = "Không tải được file âm thanh nào"
        update(
            job,
            status=final_status,
            current="",
            error=error,
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )
    except Exception as exc:
        update(
            job,
            status="failed",
            current="",
            error=str(exc),
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )


def cleanup_jobs_locked() -> None:
    finished = [
        job_id
        for job_id, job in JOBS.items()
        if job.status in {"completed", "failed"}
    ]
    while len(JOBS) >= MAX_RETAINED_JOBS and finished:
        JOBS.pop(finished.pop(0), None)


def start_job(
    url: str,
    download_dir: str | None = None,
    set_default: bool = False,
    *,
    urls: object = None,
) -> Job:
    normalized = normalize_urls(url, urls)
    job = Job(
        id=uuid.uuid4().hex,
        url=normalized[0],
        urls=normalized,
        source_total=len(normalized),
    )
    with LOCK:
        cleanup_jobs_locked()
        active_jobs = sum(
            existing.status not in {"completed", "failed"}
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
        probe = subprocess.run(["uname"], capture_output=True, text=True, check=False)
        command = "open" if probe.stdout.strip() == "Darwin" else "xdg-open"
        subprocess.Popen([command, str(folder)])


def download_root_status() -> dict[str, object]:
    root = default_download_root()
    return {
        "download_root": str(root) if root is not None else UNCONFIGURED_DOWNLOAD_ROOT_LABEL,
        "download_root_configured": root is not None,
        "ask_each_time": ask_for_download_root_each_time(),
    }


def origin_allowed(origin: str) -> bool:
    return (
        not origin
        or EXTENSION_ORIGIN.fullmatch(origin) is not None
        or FIREFOX_EXTENSION_ORIGIN.fullmatch(origin) is not None
    )


class Handler(BaseHTTPRequestHandler):
    server_version = f"MH-Dowsample/{APP_VERSION}"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def _headers(self, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-MH-Dowsample-Version", APP_VERSION)
        origin = self.headers.get("Origin", "").strip()
        if origin_allowed(origin) and origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        if self.headers.get("Access-Control-Request-Private-Network", "").lower() == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()

    def _json(self, payload: object, status: int = 200) -> None:
        self._headers(status)
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

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
        host_header = self.headers.get("Host", "").strip().lower()
        if host_header.startswith("["):
            hostname = host_header.split("]", 1)[0] + "]"
        else:
            hostname = host_header.split(":", 1)[0]
        if hostname not in LOCAL_HOSTS:
            return False
        return origin_allowed(self.headers.get("Origin", "").strip())

    def _guard_request(self) -> bool:
        if self._request_allowed():
            return True
        self._json({"error": "Nguồn yêu cầu không được phép"}, 403)
        return False

    def do_OPTIONS(self) -> None:
        if self._guard_request():
            self._headers(204)

    def do_GET(self) -> None:
        if not self._guard_request():
            return
        if self.path == "/health":
            self._json(
                {
                    "ok": True,
                    "version": APP_VERSION,
                    "uptime_seconds": max(0, int(time.time() - SERVER_STARTED_AT)),
                    "capabilities": {
                        "multi_url": True,
                        "lossless_preference": True,
                        "preview_fallback": True,
                        "retry": True,
                    },
                    **download_root_status(),
                }
            )
            return
        if self.path == "/settings":
            self._json(download_root_status())
            return
        job_id = re_job_path(self.path)
        if job_id:
            with LOCK:
                job = JOBS.get(job_id)
                payload = job.public() if job else None
            self._json(payload or {"error": "Không tìm thấy tác vụ"}, 200 if payload else 404)
            return
        self._json({"error": "Không tìm thấy endpoint"}, 404)

    def do_POST(self) -> None:
        if not self._guard_request():
            return
        try:
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
                    urls=body.get("urls"),
                )
                self._json(job.public(), 202)
                return

            if self.path == "/settings/download-root":
                select = body.get("select", False)
                if not isinstance(select, bool):
                    raise ValueError("Tùy chọn chọn thư mục phải là true hoặc false")
                if select:
                    with FOLDER_DIALOG_LOCK:
                        save_download_root(choose_initial_download_root())
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
                        "ask_each_time": ask_for_download_root_each_time(),
                    }
                )
                return

            if self.path == "/open-folder":
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="MH-Dowsample local server")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true", help="kiểm tra bản chạy rồi thoát")
    actions.add_argument("--set-download-dir", metavar="PATH", help="lưu thư mục tải mặc định rồi thoát")
    actions.add_argument("--get-download-dir", action="store_true", help="in thư mục tải hiện tại rồi thoát")
    actions.add_argument("--clear-download-dir", action="store_true", help="xóa thư mục tải mặc định rồi thoát")
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
        print(json.dumps({"ok": True, "version": APP_VERSION, **download_root_status()}))
        return

    root = default_download_root()
    print(f"MH-Dowsample Server {APP_VERSION}")
    print(f"Server: http://{HOST}:{PORT}")
    if root is None:
        print("Tai xuong: chua chon; ung dung se hoi khi bat dau tai.")
    else:
        printable_root = str(root).encode("ascii", "backslashreplace").decode("ascii")
        print(f"Tai xuong: {printable_root}")
    print("Giu cua so nay mo trong luc tai.")
    try:
        httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        raise SystemExit(
            f"Khong the mo local server tai cong {PORT}. "
            "Co the server da chay san hoac cong dang bi chiem. "
            f"Chi tiet: {exc}"
        ) from exc
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nDa dung local server.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
