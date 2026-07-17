"""Loopback-only job server used by the Chrome extension."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from crawler import AudioAsset, AudioCrawler, sanitize_filename, validate_http_url

HOST = "127.0.0.1"
PORT = 8765
BATCH_SIZE = 200
DOWNLOAD_WORKERS = 4


def default_download_root() -> Path:
    configured = os.getenv("MH_AUDIO_DOWNLOAD_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    drive_j = Path("J:/")
    if os.name == "nt" and drive_j.exists():
        return drive_j / "MH-Audio-Downloads"
    return Path.home() / "Downloads" / "MH-Audio-Downloads"


@dataclass
class Job:
    id: str
    url: str
    status: str = "queued"
    discovered: int = 0
    downloaded: int = 0
    failed: int = 0
    current: str = ""
    output_dir: str = ""
    error: str = ""
    failures: list[str] = field(default_factory=list)

    def public(self) -> dict[str, object]:
        return asdict(self)


JOBS: dict[str, Job] = {}
LOCK = threading.Lock()


def job_folder(url: str, job_id: str) -> Path:
    slug = Path(urlparse(url).path.rstrip("/")).name or "audio"
    slug = sanitize_filename(slug, fallback="audio")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return default_download_root() / f"{slug}-{stamp}-{job_id[:6]}"


def update(job: Job, **values: object) -> None:
    with LOCK:
        for key, value in values.items():
            setattr(job, key, value)


def run_job(job: Job) -> None:
    crawler = AudioCrawler()
    folder = job_folder(job.url, job.id)
    folder.mkdir(parents=True, exist_ok=True)
    update(job, status="discovering", output_dir=str(folder))
    try:
        assets = crawler.discover(job.url)
        update(job, status="downloading", discovered=len(assets))
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
                            if len(job.failures) < 20:
                                job.failures.append(f"{asset.title or asset.url}: {exc}")
                    else:
                        with LOCK:
                            job.downloaded += 1
        final_status = "completed" if job.downloaded else "failed"
        error = "" if job.downloaded else "Không tải được file âm thanh nào"
        update(job, status=final_status, current="", error=error)
    except Exception as exc:
        update(job, status="failed", current="", error=str(exc))


def start_job(url: str) -> Job:
    url = validate_http_url(url)
    job = Job(id=uuid.uuid4().hex, url=url)
    with LOCK:
        JOBS[job.id] = job
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return job


def open_folder(path: str) -> None:
    folder = Path(path)
    if not folder.is_dir() or default_download_root().resolve() not in folder.resolve().parents:
        raise ValueError("Thư mục không hợp lệ")
    if os.name == "nt":
        os.startfile(folder)  # type: ignore[attr-defined]
    elif os.name == "posix":
        command = "open" if subprocess.run(["uname"], capture_output=True, text=True).stdout.strip() == "Darwin" else "xdg-open"
        subprocess.Popen([command, str(folder)])


class Handler(BaseHTTPRequestHandler):
    server_version = "MH-Dow/1.0"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def _headers(self, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def _json(self, payload: object, status: int = 200) -> None:
        self._headers(status)
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 32_768:
            raise ValueError("Dữ liệu gửi lên quá lớn")
        raw = self.rfile.read(length)
        value = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(value, dict):
            raise ValueError("Dữ liệu JSON không hợp lệ")
        return value

    def do_OPTIONS(self) -> None:
        self._headers(204)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json({"ok": True, "download_root": str(default_download_root())})
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
        try:
            body = self._body()
            if self.path == "/jobs":
                job = start_job(str(body.get("url") or ""))
                self._json(job.public(), 202)
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


def main() -> None:
    root = default_download_root()
    root.mkdir(parents=True, exist_ok=True)
    print("MH Dow Extension Server")
    print(f"Server: http://{HOST}:{PORT}")
    print(f"Tai xuong: {root}")
    print("Giu cua so nay mo trong luc tai.")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
