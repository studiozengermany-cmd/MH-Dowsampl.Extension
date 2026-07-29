from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import server as backend_server  # noqa: E402
from crawler import AudioAsset  # noqa: E402


class BatchCrawler:
    def discover(self, page_url: str) -> list[AudioAsset]:
        return [AudioAsset(page_url + "/sample.wav", Path(page_url).name)]


class ServerHardeningTests(unittest.TestCase):
    def test_accepts_more_than_two_hundred_links(self) -> None:
        urls = [f"https://example.test/{index}" for index in range(1_501)]
        self.assertEqual(len(backend_server.normalize_urls("", urls)), 1_501)

    def test_discovers_sources_in_internal_groups_of_one_thousand(self) -> None:
        urls = [f"https://example.test/{index}" for index in range(1_501)]
        job = backend_server.Job(
            id="batch1501",
            url=urls[0],
            urls=urls,
            source_total=len(urls),
        )
        assets = backend_server.discover_assets(job, BatchCrawler())

        self.assertEqual(len(assets), 1_501)
        self.assertEqual(job.source_batch_total, 2)
        self.assertEqual(job.source_batch_index, 2)
        self.assertEqual(job.source_processed, 1_501)

    def test_classifies_loop_one_shot_fx_and_unknown(self) -> None:
        self.assertEqual(backend_server.classify_audio_name("drum_loop_128bpm.wav"), "loop")
        self.assertEqual(backend_server.classify_audio_name("Kick_01.wav"), "one_shot")
        self.assertEqual(backend_server.classify_audio_name("Huge Impact.wav"), "fx")
        self.assertEqual(backend_server.classify_audio_name("Recorded Audio.wav"), "unknown")

    def test_quality_check_keeps_valid_wav_and_flags_tiny_file(self) -> None:
        valid_wav = b"RIFF" + (b"\x00" * 4) + b"WAVEfmt " + (b"\x00" * 20) + b"data" + (b"\x00" * 5_000)
        with tempfile.TemporaryDirectory() as temp_dir:
            good = Path(temp_dir) / "Good.wav"
            tiny = Path(temp_dir) / "Tiny.wav"
            good.write_bytes(valid_wav)
            tiny.write_bytes(valid_wav[:100])

            self.assertFalse(backend_server.needs_quality_review(good))
            self.assertTrue(backend_server.needs_quality_review(tiny))

    def test_per_file_mode_does_not_open_one_folder_dialog_for_the_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.object(
                backend_server._server,
                "ask_for_download_root_each_time",
                return_value=True,
            ), patch.object(
                backend_server._server,
                "default_download_root",
                return_value=root,
            ), patch.object(
                backend_server._server,
                "choose_download_root",
                side_effect=AssertionError("không được hỏi một thư mục cho cả job"),
            ):
                selected, source, remembered = backend_server.resolve_download_root()

            self.assertEqual(selected, root.resolve())
            self.assertEqual(source, "prompt_per_file")
            self.assertFalse(remembered)

    def test_save_dialog_name_cannot_force_wrong_audio_extension(self) -> None:
        selected = backend_server.enforce_actual_suffix(Path("C:/Audio/Kick.wav"), ".mp3")
        self.assertEqual(selected, Path("C:/Audio/Kick.mp3"))


if __name__ == "__main__":
    unittest.main()
