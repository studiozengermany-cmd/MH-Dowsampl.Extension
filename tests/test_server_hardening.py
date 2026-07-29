from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import server as backend_server  # noqa: E402
import server_streaming  # noqa: E402
from crawler import AudioAsset  # noqa: E402


class BatchCrawler:
    def discover(self, page_url: str) -> list[AudioAsset]:
        return [AudioAsset(page_url + "/sample.wav", Path(page_url).name)]


def valid_wav_bytes(size: int = 5_000) -> bytes:
    return b"RIFF" + (b"\x00" * 4) + b"WAVEfmt " + (b"\x00" * 20) + b"data" + (b"\x00" * size)


class OrganizingCrawler:
    def discover(self, _page_url: str) -> list[AudioAsset]:
        return [
            AudioAsset("https://cdn.test/loop.wav", "Drum Loop 128 BPM"),
            AudioAsset("https://cdn.test/kick.wav", "Kick 01"),
        ]

    def download(self, asset: AudioAsset, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{asset.title}.wav"
        payload = valid_wav_bytes() if "Loop" in str(asset.title) else valid_wav_bytes()[:100]
        path.write_bytes(payload)
        return path


def selected_download(_crawler: object, asset: AudioAsset, selector: object) -> Path:
    destination = selector(f"{asset.title}.wav", ".wav")
    if destination is None:
        raise backend_server.DestinationSelectionCancelled("Đã hủy lưu file này")
    path = Path(destination)
    payload = valid_wav_bytes() if "Loop" in str(asset.title) else valid_wav_bytes()[:100]
    path.write_bytes(payload)
    return path


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
        valid_wav = valid_wav_bytes()
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

    def test_per_file_mode_opens_one_save_dialog_for_each_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            staging = root / "staging"
            job = backend_server.Job(
                id="prompt",
                url="https://example.test/source",
                urls=["https://example.test/source"],
                source_total=1,
            )
            destinations = [root / "Saved Loop.wav", root / "Saved Kick.wav"]
            with patch.object(backend_server, "AudioCrawler", return_value=OrganizingCrawler()), patch.object(
                backend_server,
                "resolve_download_root",
                return_value=(root, "prompt_per_file", False),
            ), patch.object(
                backend_server,
                "_staging_folder",
                return_value=staging,
            ), patch.object(
                server_streaming,
                "download_with_selector",
                side_effect=selected_download,
            ), patch.object(
                backend_server,
                "choose_download_file",
                side_effect=destinations,
            ) as save_dialog:
                backend_server.run_job(job)

            self.assertEqual(save_dialog.call_count, 2)
            self.assertEqual(job.status, "completed")
            self.assertEqual(job.downloaded, 2)
            self.assertTrue((root / "Saved Loop.wav").is_file())
            self.assertTrue((root / "Saved Kick.wav").is_file())
            self.assertFalse(staging.exists())

    def test_run_job_organizes_categories_and_keeps_uncertain_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = backend_server.Job(
                id="organize",
                url="https://example.test/source",
                urls=["https://example.test/source"],
                source_total=1,
            )
            with patch.object(backend_server, "AudioCrawler", return_value=OrganizingCrawler()), patch.object(
                backend_server,
                "resolve_download_root",
                return_value=(root, "per_job", False),
            ):
                backend_server.run_job(job)

            output = Path(job.output_dir)
            self.assertEqual(job.status, "completed")
            self.assertEqual(job.downloaded, 2)
            self.assertEqual(job.classified_loop, 1)
            self.assertEqual(job.classified_one_shot, 1)
            self.assertEqual(job.quality_review, 1)
            self.assertEqual(len(list((output / "Loop").glob("*.wav"))), 1)
            self.assertEqual(
                len(list((output / "Cần kiểm tra chất lượng" / "One-Shot").glob("*.wav"))),
                1,
            )

    def test_save_dialog_name_cannot_force_wrong_audio_extension(self) -> None:
        selected = backend_server.enforce_actual_suffix(Path("C:/Audio/Kick.wav"), ".mp3")
        self.assertEqual(selected, Path("C:/Audio/Kick.mp3"))


if __name__ == "__main__":
    unittest.main()
