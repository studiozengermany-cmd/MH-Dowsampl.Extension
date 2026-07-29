from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import server as backend_server  # noqa: E402
from crawler import AudioAsset, AudioCrawler  # noqa: E402


class FakeHeaders:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def get(self, name: str, default: str = "") -> str:
        return self.values.get(name, default)


class FakeResponse:
    def __init__(
        self,
        url: str,
        chunks: list[bytes],
        *,
        fail_after_reads: int | None = None,
    ) -> None:
        self._url = url
        self.headers = FakeHeaders({"Content-Type": "audio/mpeg"})
        self._chunks = list(chunks)
        self.fail_after_reads = fail_after_reads
        self.read_count = 0

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def geturl(self) -> str:
        return self._url

    def read(self, _size: int = -1) -> bytes:
        self.read_count += 1
        if self.fail_after_reads is not None and self.read_count > self.fail_after_reads:
            raise OSError("simulated stream failure")
        return self._chunks.pop(0) if self._chunks else b""


class StreamingSaveTests(unittest.TestCase):
    def test_destination_is_selected_after_header_validation_before_full_stream(self) -> None:
        crawler = AudioCrawler(retries=1)
        response = FakeResponse(
            "https://cdn.test/stream",
            [b"ID3-first", b"-second", b""],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "Kick.mp3"

            def selector(filename: str, suffix: str) -> Path:
                self.assertEqual(response.read_count, 1)
                self.assertEqual(filename, "Kick.mp3")
                self.assertEqual(suffix, ".mp3")
                return destination

            with patch.object(crawler, "_open", return_value=response):
                result = backend_server.download_with_selector(
                    crawler,
                    AudioAsset("https://cdn.test/stream", "Kick.wav"),
                    selector,
                )

            self.assertEqual(result, destination)
            self.assertEqual(destination.read_bytes(), b"ID3-first-second")

    def test_cancelled_save_creates_no_file(self) -> None:
        crawler = AudioCrawler(retries=1)
        response = FakeResponse(
            "https://cdn.test/stream",
            [b"ID3-first", b"-second", b""],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            with patch.object(crawler, "_open", return_value=response):
                with self.assertRaises(backend_server.DestinationSelectionCancelled):
                    backend_server.download_with_selector(
                        crawler,
                        AudioAsset("https://cdn.test/stream", "Kick"),
                        lambda _filename, _suffix: None,
                    )
            self.assertEqual(list(folder.iterdir()), [])

    def test_stream_failure_preserves_existing_destination(self) -> None:
        crawler = AudioCrawler(retries=1)
        response = FakeResponse(
            "https://cdn.test/stream",
            [b"ID3-first", b"-second"],
            fail_after_reads=1,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            destination = folder / "Kick.mp3"
            destination.write_bytes(b"old-file")

            with patch.object(crawler, "_open", return_value=response):
                with self.assertRaises(backend_server.DestinationWriteError):
                    backend_server.download_with_selector(
                        crawler,
                        AudioAsset("https://cdn.test/stream", "Kick"),
                        lambda _filename, _suffix: destination,
                    )

            self.assertEqual(destination.read_bytes(), b"old-file")
            self.assertEqual(list(folder.glob("*.part")), [])


if __name__ == "__main__":
    unittest.main()
