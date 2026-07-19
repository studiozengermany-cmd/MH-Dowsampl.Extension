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
    def discover(self, page_url: str) -> list[AudioAsset]:
        return [AudioAsset("https://cdn.test/sample.mp3", "Integration sample")]

    def download(self, asset: AudioAsset, folder: Path) -> Path:
        destination = folder / "Integration sample.mp3"
        destination.write_bytes(b"ID3-integration")
        return destination


class ProvidedAssetCrawler(FakeCrawler):
    def discover(self, page_url: str) -> list[AudioAsset]:
        raise AssertionError("Provided extension assets must bypass server page discovery")


class CatalogueCrawler(FakeCrawler):
    def discover(self, page_url: str) -> list[AudioAsset]:
        return [
            AudioAsset(
                "https://cdn.test/sample.mp3",
                "Integration sample",
                bpm=98,
                musical_key="D min",
            )
        ]


class FakeAnalyzer:
    def analyze(self, filepath: Path) -> dict[str, object]:
        return {
            "passed": True,
            "content_type": "one-shot",
            "loop_score": 0.0,
            "duration_sec": 0.5,
            "bpm": 0,
            "key": "Unknown",
            "issues": [],
            "analysis_error": "",
        }


class MetadataAnalyzer(FakeAnalyzer):
    def analyze(self, filepath: Path) -> dict[str, object]:
        result = super().analyze(filepath)
        result.update(bpm=124, key="F# min")
        return result


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
        connection = http.client.HTTPConnection(backend_server.HOST, self.port, timeout=2)
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        raw = response.read()
        response_headers = {key: value for key, value in response.getheaders()}
        connection.close()
        data = json.loads(raw.decode("utf-8")) if raw else {}
        return response.status, data, response_headers

    def request_raw(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        connection = http.client.HTTPConnection(backend_server.HOST, self.port, timeout=2)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        raw = response.read()
        response_headers = {key: value for key, value in response.getheaders()}
        connection.close()
        return response.status, raw, response_headers

    def wait_for_job(self, job_id: str) -> dict[str, object]:
        deadline = time.monotonic() + 2
        job: dict[str, object] = {}
        while time.monotonic() < deadline:
            _, job, _ = self.request(
                "GET",
                f"/jobs/{job_id}",
                headers={"Origin": EXTENSION_ORIGIN},
            )
            if job["status"] in {"completed", "failed"}:
                return job
            time.sleep(0.01)
        self.fail(f"Job {job_id} did not finish before the test deadline")

    def test_health_endpoint_is_available_locally(self) -> None:
        status, payload, headers = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_allows_chrome_extension_origin(self) -> None:
        status, _, headers = self.request(
            "GET",
            "/health",
            headers={"Origin": EXTENSION_ORIGIN},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["Access-Control-Allow-Origin"], EXTENSION_ORIGIN)

    def test_rejects_regular_website_origin(self) -> None:
        status, payload, headers = self.request(
            "GET",
            "/health",
            headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(status, 403)
        self.assertIn("không được phép", str(payload["error"]))
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_private_network_preflight(self) -> None:
        status, _, headers = self.request(
            "OPTIONS",
            "/jobs",
            headers={
                "Origin": EXTENSION_ORIGIN,
                "Access-Control-Request-Private-Network": "true",
            },
        )
        self.assertEqual(status, 204)
        self.assertEqual(headers["Access-Control-Allow-Private-Network"], "true")

    def test_remote_mode_requires_extension_access_key(self) -> None:
        environment = {
            "MH_REMOTE_MODE": "true",
            "MH_EXTENSION_ACCESS_KEY": "test-secret",
        }
        with patch.dict(os.environ, environment, clear=False):
            status, _, _ = self.request(
                "GET",
                "/health",
                headers={"Origin": EXTENSION_ORIGIN},
            )
            self.assertEqual(status, 403)

            status, payload, headers = self.request(
                "GET",
                "/health",
                headers={
                    "Origin": EXTENSION_ORIGIN,
                    "X-MH-Access-Key": "test-secret",
                },
            )
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["delivery"], "browser")
            self.assertIn("X-MH-Access-Key", headers["Access-Control-Allow-Headers"])

            status, _, headers = self.request(
                "OPTIONS",
                "/jobs",
                headers={
                    "Origin": EXTENSION_ORIGIN,
                    "Access-Control-Request-Headers": "X-MH-Access-Key, Content-Type",
                },
            )
            self.assertEqual(status, 204)
            self.assertEqual(headers["Access-Control-Allow-Origin"], EXTENSION_ORIGIN)

    def test_remote_source_validation_blocks_private_and_unapproved_hosts(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MH_REMOTE_MODE": "true",
                "MH_ALLOWED_SOURCE_HOSTS": "allowed.example",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "chưa được cho phép"):
                backend_server.validate_remote_source_url("https://other.example/file.wav")
            with patch.object(
                backend_server.socket,
                "getaddrinfo",
                return_value=[(2, 1, 6, "", ("127.0.0.1", 443))],
            ):
                with self.assertRaisesRegex(ValueError, "nội bộ"):
                    backend_server.validate_remote_source_url(
                        "https://allowed.example/file.wav"
                    )

    def test_remote_source_wildcard_allows_public_hosts_but_blocks_private_ips(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MH_REMOTE_MODE": "true",
                "MH_ALLOWED_SOURCE_HOSTS": "*",
            },
            clear=False,
        ):
            with patch.object(
                backend_server.socket,
                "getaddrinfo",
                return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
            ):
                self.assertEqual(
                    backend_server.validate_remote_source_url(
                        "https://samples.example/file.wav"
                    ),
                    "https://samples.example/file.wav",
                )
            with patch.object(
                backend_server.socket,
                "getaddrinfo",
                return_value=[(2, 1, 6, "", ("10.0.0.8", 443))],
            ):
                with self.assertRaisesRegex(ValueError, "nội bộ"):
                    backend_server.validate_remote_source_url(
                        "https://private.example/file.wav"
                    )

    def test_rejects_invalid_job_url(self) -> None:
        status, payload, _ = self.request(
            "POST",
            "/jobs",
            payload={"url": "not-a-url"},
            headers={"Origin": EXTENSION_ORIGIN},
        )
        self.assertEqual(status, 400)
        self.assertIn("http", str(payload["error"]))

    def test_saves_and_reuses_user_selected_download_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            config_file = temp_root / "config" / "settings.json"
            selected_folder = temp_root / "My Audio Library"
            with (
                patch.object(backend_server, "settings_path", return_value=config_file),
                patch.dict(os.environ, {"MH_AUDIO_DOWNLOAD_DIR": ""}),
            ):
                saved = backend_server.save_download_root(str(selected_folder))
                self.assertEqual(saved, selected_folder.resolve())
                self.assertEqual(
                    backend_server.default_download_root(),
                    selected_folder.resolve(),
                )
                payload = json.loads(config_file.read_text(encoding="utf-8"))
                self.assertEqual(payload["download_root"], str(selected_folder.resolve()))

    def test_no_hard_coded_folder_is_used_before_user_selects_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "config" / "settings.json"
            with (
                patch.object(backend_server, "settings_path", return_value=config_file),
                patch.dict(os.environ, {"MH_AUDIO_DOWNLOAD_DIR": ""}),
            ):
                self.assertIsNone(backend_server.default_download_root())
                status = backend_server.download_root_status()
                self.assertFalse(status["download_root_configured"])
                self.assertFalse(status["ask_each_time"])
                self.assertIn("sẽ hỏi", str(status["download_root"]))

    def test_per_job_folder_is_used_once_without_becoming_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            config_file = temp_root / "config" / "settings.json"
            selected_folder = temp_root / "Only This Download"
            with (
                patch.object(backend_server, "settings_path", return_value=config_file),
                patch.dict(os.environ, {"MH_AUDIO_DOWNLOAD_DIR": ""}),
            ):
                root, source, remembered = backend_server.resolve_download_root(
                    str(selected_folder),
                    False,
                )
                self.assertEqual(root, selected_folder.resolve())
                self.assertEqual(source, "per_job")
                self.assertFalse(remembered)
                self.assertIsNone(backend_server.saved_download_root())

    def test_changing_default_only_changes_new_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            config_file = temp_root / "config" / "settings.json"
            first_folder = temp_root / "First Library"
            second_folder = temp_root / "Second Library"
            with (
                patch.object(backend_server, "settings_path", return_value=config_file),
                patch.dict(os.environ, {"MH_AUDIO_DOWNLOAD_DIR": ""}),
            ):
                first_job_root, _, _ = backend_server.resolve_download_root(
                    str(first_folder),
                    True,
                )
                backend_server.save_download_root(second_folder)
                second_job_root, source, remembered = backend_server.resolve_download_root()

                self.assertEqual(first_job_root, first_folder.resolve())
                self.assertEqual(second_job_root, second_folder.resolve())
                self.assertEqual(source, "configured_default")
                self.assertTrue(remembered)

    def test_missing_default_prompts_once_and_saves_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            config_file = temp_root / "config" / "settings.json"
            selected_folder = temp_root / "Prompt Selection"
            with (
                patch.object(backend_server, "settings_path", return_value=config_file),
                patch.dict(os.environ, {"MH_AUDIO_DOWNLOAD_DIR": ""}),
                patch.object(
                    backend_server,
                    "choose_download_root",
                    return_value=selected_folder,
                ) as chooser,
            ):
                root, source, remembered = backend_server.resolve_download_root()
                self.assertEqual(root, selected_folder.resolve())
                self.assertEqual(source, "prompt_default")
                self.assertTrue(remembered)
                self.assertEqual(backend_server.saved_download_root(), selected_folder.resolve())
                chooser.assert_called_once_with()

    def test_ask_each_time_prompts_without_changing_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            config_file = temp_root / "config" / "settings.json"
            default_folder = temp_root / "Default Library"
            selected_folder = temp_root / "This Download"
            with (
                patch.object(backend_server, "settings_path", return_value=config_file),
                patch.dict(os.environ, {"MH_AUDIO_DOWNLOAD_DIR": ""}),
                patch.object(
                    backend_server,
                    "choose_download_root",
                    return_value=selected_folder,
                ) as chooser,
            ):
                backend_server.save_download_root(default_folder)
                backend_server.save_ask_each_time(True)

                root, source, remembered = backend_server.resolve_download_root()

                self.assertEqual(root, selected_folder)
                self.assertEqual(source, "prompt_each_time")
                self.assertFalse(remembered)
                self.assertEqual(backend_server.saved_download_root(), default_folder.resolve())
                chooser.assert_called_once_with()

    def test_first_server_start_requires_and_remembers_selected_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            config_file = temp_root / "config" / "settings.json"
            selected_folder = temp_root / "First Start Library"
            with (
                patch.object(backend_server, "settings_path", return_value=config_file),
                patch.dict(os.environ, {"MH_AUDIO_DOWNLOAD_DIR": ""}),
                patch.object(
                    backend_server,
                    "choose_initial_download_root",
                    return_value=selected_folder,
                ) as chooser,
            ):
                root = backend_server.ensure_initial_download_root()
                self.assertEqual(root, selected_folder.resolve())
                self.assertEqual(backend_server.saved_download_root(), selected_folder.resolve())
                chooser.assert_called_once_with()

    def test_existing_default_skips_first_start_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            config_file = temp_root / "config" / "settings.json"
            selected_folder = temp_root / "Existing Library"
            with (
                patch.object(backend_server, "settings_path", return_value=config_file),
                patch.dict(os.environ, {"MH_AUDIO_DOWNLOAD_DIR": ""}),
                patch.object(backend_server, "choose_initial_download_root") as chooser,
            ):
                backend_server.save_download_root(selected_folder)
                root = backend_server.ensure_initial_download_root()
                self.assertEqual(root, selected_folder.resolve())
                chooser.assert_not_called()

    def test_first_start_cannot_continue_when_folder_selection_is_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "config" / "settings.json"
            with (
                patch.object(backend_server, "settings_path", return_value=config_file),
                patch.dict(os.environ, {"MH_AUDIO_DOWNLOAD_DIR": ""}),
                patch.object(
                    backend_server,
                    "choose_initial_download_root",
                    side_effect=backend_server.FolderSelectionCancelled(
                        "Bạn cần chọn nơi lưu trước khi sử dụng MH-Dowsample"
                    ),
                ),
            ):
                with self.assertRaises(backend_server.FolderSelectionCancelled):
                    backend_server.ensure_initial_download_root()
                self.assertIsNone(backend_server.saved_download_root())

    def test_settings_api_changes_and_clears_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            config_file = temp_root / "config" / "settings.json"
            selected_folder = temp_root / "API Library"
            with (
                patch.object(backend_server, "settings_path", return_value=config_file),
                patch.dict(os.environ, {"MH_AUDIO_DOWNLOAD_DIR": ""}),
            ):
                status, payload, _ = self.request(
                    "POST",
                    "/settings/download-root",
                    payload={"download_dir": str(selected_folder)},
                    headers={"Origin": EXTENSION_ORIGIN},
                )
                self.assertEqual(status, 200)
                self.assertTrue(payload["download_root_configured"])
                self.assertEqual(Path(str(payload["download_root"])), selected_folder.resolve())

                status, payload, _ = self.request(
                    "POST",
                    "/settings/download-root",
                    payload={"ask_each_time": True},
                    headers={"Origin": EXTENSION_ORIGIN},
                )
                self.assertEqual(status, 200)
                self.assertTrue(payload["ask_each_time"])

                status, payload, _ = self.request(
                    "GET",
                    "/settings",
                    headers={"Origin": EXTENSION_ORIGIN},
                )
                self.assertEqual(status, 200)
                self.assertTrue(payload["ask_each_time"])

                status, payload, _ = self.request(
                    "POST",
                    "/settings/download-root",
                    payload={"clear": True},
                    headers={"Origin": EXTENSION_ORIGIN},
                )
                self.assertEqual(status, 200)
                self.assertFalse(payload["download_root_configured"])

    def test_settings_api_selects_a_new_default_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            config_file = temp_root / "config" / "settings.json"
            selected_folder = temp_root / "Selected From Popup"
            with (
                patch.object(backend_server, "settings_path", return_value=config_file),
                patch.dict(os.environ, {"MH_AUDIO_DOWNLOAD_DIR": ""}),
                patch.object(
                    backend_server,
                    "choose_initial_download_root",
                    return_value=selected_folder,
                ) as chooser,
            ):
                status, payload, _ = self.request(
                    "POST",
                    "/settings/download-root",
                    payload={"select": True},
                    headers={"Origin": EXTENSION_ORIGIN},
                )

                self.assertEqual(status, 200)
                self.assertEqual(Path(str(payload["download_root"])), selected_folder.resolve())
                self.assertEqual(backend_server.saved_download_root(), selected_folder.resolve())
                chooser.assert_called_once_with()

    def test_job_runs_from_api_to_downloaded_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                patch.object(backend_server, "AudioCrawler", return_value=FakeCrawler()),
                patch.object(backend_server, "SampleAnalyzer", return_value=FakeAnalyzer()),
            ):
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
                job_id = str(created["id"])
                job = self.wait_for_job(job_id)

                self.assertEqual(job["status"], "completed")
                self.assertEqual(job["downloaded"], 1)
                self.assertEqual(job["analyzed"], 1)
                self.assertEqual(job["one_shots"], 1)
                self.assertEqual(job["loops"], 0)
                self.assertEqual(job["audio_errors"], 0)
                self.assertEqual(job["sample_results_total"], 1)
                self.assertEqual(job["download_root"], str(root.resolve()))
                self.assertEqual(job["download_root_source"], "per_job")
                self.assertFalse(job["download_root_remembered"])
                output = (
                    Path(str(job["output_dir"]))
                    / "One-Shots"
                    / "Integration sample.mp3"
                )
                self.assertEqual(output.read_bytes(), b"ID3-integration")
                self.assertTrue(Path(str(job["report_path"])).is_file())

                status, samples, _ = self.request(
                    "GET",
                    f"/jobs/{job_id}/samples?offset=0&limit=10",
                    headers={"Origin": EXTENSION_ORIGIN},
                )
                self.assertEqual(status, 200)
                self.assertEqual(samples["total"], 1)
                self.assertEqual(samples["items"][0]["content_type"], "one-shot")
                self.assertEqual(samples["items"][0]["category"], "One-Shots")

                status, files, _ = self.request(
                    "GET",
                    f"/jobs/{job_id}/files",
                    headers={"Origin": EXTENSION_ORIGIN},
                )
                self.assertEqual(status, 200)
                self.assertEqual(files["total"], 1)
                item = files["files"][0]
                self.assertEqual(item["name"], "Integration sample.mp3")
                status, raw, headers = self.request_raw(
                    "GET",
                    str(item["download_url"]),
                    headers={"Origin": EXTENSION_ORIGIN},
                )
                self.assertEqual(status, 200)
                self.assertEqual(raw, b"ID3-integration")
                self.assertIn("attachment", headers["Content-Disposition"])

    def test_sample_results_api_validates_pagination(self) -> None:
        job = backend_server.Job(id="abc123", url="https://example.com")
        with backend_server.LOCK:
            backend_server.JOBS[job.id] = job
        status, payload, _ = self.request(
            "GET",
            "/jobs/abc123/samples?offset=bad",
            headers={"Origin": EXTENSION_ORIGIN},
        )
        self.assertEqual(status, 400)
        self.assertIn("số nguyên", str(payload["error"]))

    def test_job_can_be_cancelled_through_api(self) -> None:
        job = backend_server.Job(id="cancel123", url="https://example.com")
        with backend_server.LOCK:
            backend_server.JOBS[job.id] = job
        status, payload, _ = self.request(
            "POST",
            "/jobs/cancel123/cancel",
            payload={},
            headers={"Origin": EXTENSION_ORIGIN},
        )
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "cancelling")
        self.assertTrue(job.cancel_requested)

    def test_expired_render_job_removes_temporary_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            folder = root / "expired-job"
            folder.mkdir()
            (folder / "sample.wav").write_bytes(b"RIFF0000WAVE")
            job = backend_server.Job(
                id="expired123",
                url="https://splice.com/example",
                status="completed",
                output_dir=str(folder),
                finished_epoch=time.time() - 10,
            )
            with backend_server.LOCK:
                backend_server.JOBS[job.id] = job
                with (
                    patch.object(backend_server, "remote_mode", return_value=True),
                    patch.object(backend_server, "remote_download_root", return_value=root),
                    patch.object(backend_server, "job_ttl_seconds", return_value=1),
                ):
                    backend_server.cleanup_jobs_locked()
            self.assertFalse(folder.exists())
            self.assertNotIn(job.id, backend_server.JOBS)

    def test_job_adds_analyzed_tempo_and_key_to_downloaded_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                patch.object(backend_server, "AudioCrawler", return_value=FakeCrawler()),
                patch.object(backend_server, "SampleAnalyzer", return_value=MetadataAnalyzer()),
            ):
                status, created, _ = self.request(
                    "POST",
                    "/jobs",
                    payload={"url": "https://example.com/samples", "download_dir": str(root)},
                    headers={"Origin": EXTENSION_ORIGIN},
                )
                self.assertEqual(status, 202)
                job = self.wait_for_job(str(created["id"]))
                output = (
                    Path(str(job["output_dir"]))
                    / "One-Shots"
                    / "Integration sample [124 BPM] [F# Minor].mp3"
                )
                self.assertEqual(output.read_bytes(), b"ID3-integration")

    def test_extension_can_submit_original_asset_without_sending_session_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                patch.object(
                    backend_server,
                    "AudioCrawler",
                    return_value=ProvidedAssetCrawler(),
                ),
                patch.object(backend_server, "SampleAnalyzer", return_value=FakeAnalyzer()),
            ):
                status, created, _ = self.request(
                    "POST",
                    "/jobs",
                    payload={
                        "url": "https://splice.com/sounds/packs/example",
                        "download_dir": str(root),
                        "assets": [
                            {
                                "url": "https://cdn.test/original.wav?signature=temporary",
                                "title": "Original Kick",
                                "bpm": 128,
                                "musical_key": "C# minor",
                                "declared_format": "wav",
                            }
                        ],
                    },
                    headers={"Origin": EXTENSION_ORIGIN},
                )
                self.assertEqual(status, 202)
                job_id = str(created["id"])
                job = self.wait_for_job(job_id)
                self.assertEqual(job["status"], "completed")
                _, samples, _ = self.request(
                    "GET",
                    f"/jobs/{job_id}/samples",
                    headers={"Origin": EXTENSION_ORIGIN},
                )
                item = samples["items"][0]
                self.assertEqual(item["metadata_source"], "extension_page")
                self.assertEqual(item["source_url"], "https://cdn.test/original.wav")
                self.assertNotIn("signature", str(item["source_url"]))

    def test_catalogue_tempo_and_key_take_priority_over_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                patch.object(backend_server, "AudioCrawler", return_value=CatalogueCrawler()),
                patch.object(backend_server, "SampleAnalyzer", return_value=MetadataAnalyzer()),
            ):
                _, created, _ = self.request(
                    "POST",
                    "/jobs",
                    payload={"url": "https://example.com/samples", "download_dir": str(root)},
                    headers={"Origin": EXTENSION_ORIGIN},
                )
                job_id = str(created["id"])
                job = self.wait_for_job(job_id)
                output = (
                    Path(str(job["output_dir"]))
                    / "One-Shots"
                    / "Integration sample [98 BPM] [D Minor].mp3"
                )
                self.assertTrue(output.is_file())
                _, samples, _ = self.request(
                    "GET",
                    f"/jobs/{job_id}/samples",
                    headers={"Origin": EXTENSION_ORIGIN},
                )
                analysis = samples["items"][0]["analysis"]
                self.assertEqual(analysis["bpm_source"], "catalogue")
                self.assertEqual(analysis["key_source"], "catalogue")

    def test_cancelled_folder_prompt_stops_before_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "config" / "settings.json"
            crawler = FakeCrawler()
            with (
                patch.object(backend_server, "settings_path", return_value=config_file),
                patch.dict(os.environ, {"MH_AUDIO_DOWNLOAD_DIR": ""}),
                patch.object(backend_server, "AudioCrawler", return_value=crawler),
                patch.object(
                    backend_server,
                    "choose_download_root",
                    side_effect=backend_server.FolderSelectionCancelled(
                        "Đã hủy chọn thư mục nên chưa bắt đầu tải"
                    ),
                ),
                patch.object(crawler, "discover", wraps=crawler.discover) as discover,
            ):
                status, created, _ = self.request(
                    "POST",
                    "/jobs",
                    payload={"url": "https://example.com/samples"},
                    headers={"Origin": EXTENSION_ORIGIN},
                )
                self.assertEqual(status, 202)
                job = self.wait_for_job(str(created["id"]))
                self.assertEqual(job["status"], "failed")
                self.assertIn("hủy chọn thư mục", str(job["error"]))
                discover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
