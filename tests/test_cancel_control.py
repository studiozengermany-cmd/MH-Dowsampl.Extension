from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import server as backend_server  # noqa: E402
import cancel_control  # noqa: E402


class MustNotDiscover:
    def discover(self, _url: str):
        raise AssertionError("crawler must not run after cancellation")


class CancelControlTests(unittest.TestCase):
    def test_cancel_job_sets_public_flags(self) -> None:
        job = backend_server.Job(
            id="canceltest",
            url="https://example.test/source",
            urls=["https://example.test/source"],
            source_total=1,
        )
        with backend_server.LOCK:
            backend_server.JOBS[job.id] = job
        try:
            payload = cancel_control.cancel_job(job.id)
            self.assertIsNotNone(payload)
            self.assertTrue(payload["cancel_requested"])
            self.assertFalse(payload["cancelled_by_user"])
            self.assertEqual(job.current, "Đang dừng tác vụ...")
        finally:
            with backend_server.LOCK:
                backend_server.JOBS.pop(job.id, None)

    def test_discovery_stops_before_next_source(self) -> None:
        job = backend_server.Job(
            id="cancel-discovery",
            url="https://example.test/source",
            urls=["https://example.test/source"],
            source_total=1,
        )
        job.cancel_requested = True
        assets = cancel_control.discover_assets(job, MustNotDiscover())
        self.assertEqual(assets, [])
        self.assertEqual(job.source_processed, 0)

    def test_old_per_file_setting_is_migrated_to_one_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            with patch.object(
                cancel_control._hard._server,
                "ask_for_download_root_each_time",
                return_value=True,
            ), patch.object(
                cancel_control._hard._server,
                "save_ask_each_time",
            ) as save_setting, patch.object(
                cancel_control._hard._server,
                "default_download_root",
                return_value=root,
            ), patch.object(
                cancel_control._hard._server,
                "prepare_download_root",
                return_value=root,
            ):
                selected, source, remembered = cancel_control.resolve_download_root()

            save_setting.assert_called_once_with(False)
            self.assertEqual(selected, root)
            self.assertEqual(source, "configured_default")
            self.assertTrue(remembered)


if __name__ == "__main__":
    unittest.main()
