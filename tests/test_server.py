from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import server as backend_server  # noqa: E402
from crawler import AudioAsset  # noqa: E402

EXTENSION_ORIGIN = "chrome-extension://" + ("a" * 32)


class FakeCrawler:
    def discover(self, _page_url: str) -> list[AudioAsset]:
        return [AudioAsset("https://cdn.test/sample.mp3", "Integration sample")]

    def download(self, _asset: AudioAsset, folder: Path) -> Path:
        destination = folder / "Integration sample.mp3"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"ID3-integration")
        return destination


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.httpd = backend_server.ThreadingHTTPServer(
            (backend_server.HOST, 0),
            backend_server.Handler,
        )
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def setUp(self) -> None:
        with backend_server.LOCK:
            backend_server.JOBS.clear()

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object], dict[str, str]]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request_headers = dict(headers or {})
        if body is not None:
            request_headers["Content-Type"] = "application/json"
            request_headers["Content-Length"] = str(len(body))
        connection = http.client.HTTPConnection(backend_server.HOST, self.port, timeout=3)
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        raw = response.read()
        response_headers = {key: value for key, value in response.getheaders()}
        connection.close()
        data = json.loads(raw.decode("utf-8")) if raw else {}
        return response.status, data, response_headers

    def wait_for_job(self, job_id: str) -> dict[str, object]:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            _, job, _ = self.request(
                "GET",
                f"/jobs/{job_id}",
                headers={"Origin": EXTENSION_ORIGIN},
            )
            if job["status"] in {"completed", "failed"}:
                return job
            time.sleep(0.02)
        self.fail(f"Job {job_id} did not finish before the test deadline")

    def test_health_and_extension_cors(self) -> None:
        status, payload, headers = self.request(
            "GET",
            "/health",
            headers={"Origin": EXTENSION_ORIGIN},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(headers["Access-Control-Allow-Origin"], EXTENSION_ORIGIN)
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_rejects_regular_website_origin(self) -> None:
        status, payload, headers = self.request(
            "GET",
            "/health",
            headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(status, 403)
        self.assertIn("không được phép", str(payload["error"]))
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_rejects_invalid_job_url(self) -> None:
        status, payload, _ = self.request(
            "POST",
            "/jobs",
            payload={"url": "not-a-url"},
            headers={"Origin": EXTENSION_ORIGIN},
        )
        self.assertEqual(status, 400)
        self.assertIn("http", str(payload["error"]))

    def test_saves_and_reuses_selected_download_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            config_file = temp_root / "config" / "settings.json"
            selected_folder = temp_root / "My Audio Library"
            with patch.object(
                backend_server._server,
                "settings_path",
                return_value=config_file,
            ), patch.dict(os.environ, {"MH_AUDIO_DOWNLOAD_DIR": ""}):
                saved = backend_server.save_download_root(str(selected_folder))
                self.assertEqual(saved, selected_folder.resolve())
                self.assertEqual(
                    backend_server.default_download_root(),
                    selected_folder.resolve(),
                )
                payload = json.loads(config_file.read_text(encoding="utf-8"))
                self.assertEqual(payload["download_root"], str(selected_folder.resolve()))

    def test_ask_each_time_opens_one_folder_dialog_for_whole_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            selected_folder = Path(temp_dir).resolve()
            with patch.object(
                backend_server._server,
                "ask_for_download_root_each_time",
                return_value=True,
            ), patch.object(
                backend_server._server,
                "choose_download_root",
                return_value=selected_folder,
            ) as chooser:
                root, source, remembered = backend_server.resolve_download_root()

            chooser.assert_called_once_with()
            self.assertEqual(root, selected_folder)
            self.assertEqual(source, "prompt_each_time")
            self.assertFalse(remembered)

    def test_job_downloads_into_one_job_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.object(backend_server, "AudioCrawler", return_value=FakeCrawler()):
                status, created, _ = self.request(
                    "POST",
                    "/jobs",
                    payload={
                        "url": "https://example.com/samples",
                        "download_dir": str(root),
                        "set_default": False,
                    },
                    headers={"Origin": EXTENSION_ORIGIN},
                )
                self.assertEqual(status, 202)
                job = self.wait_for_job(str(created["id"]))

            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["downloaded"], 1)
            self.assertEqual(job["download_root"], str(root.resolve()))
            output_root = Path(str(job["output_dir"]))
            files = list(output_root.rglob("Integration sample.mp3"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_bytes(), b"ID3-integration")

    def test_cancel_endpoint_marks_active_job(self) -> None:
        job = backend_server.Job(
            id="cancelapi",
            url="https://example.test/source",
            urls=["https://example.test/source"],
            source_total=1,
        )
        with backend_server.LOCK:
            backend_server.JOBS[job.id] = job

        status, payload, _ = self.request(
            "POST",
            f"/jobs/{job.id}/cancel",
            payload={},
            headers={"Origin": EXTENSION_ORIGIN},
        )
        self.assertEqual(status, 202)
        self.assertTrue(payload["cancel_requested"])
        self.assertEqual(payload["current"], "Đang dừng tác vụ...")

    def test_cancel_unknown_job_returns_404(self) -> None:
        status, payload, _ = self.request(
            "POST",
            "/jobs/notfound/cancel",
            payload={},
            headers={"Origin": EXTENSION_ORIGIN},
        )
        self.assertEqual(status, 404)
        self.assertIn("Không tìm thấy", str(payload["error"]))


if __name__ == "__main__":
    unittest.main()
