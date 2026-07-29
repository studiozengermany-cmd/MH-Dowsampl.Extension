from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from crawler import (  # noqa: E402
    AudioAsset,
    AudioCrawler,
    PublicAudioError,
    extract_generic_audio,
)


class FakeHeaders:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def get(self, name: str, default: str = "") -> str:
        return self.values.get(name, default)

    def get_content_charset(self) -> str | None:
        return None


class FakeResponse:
    def __init__(self, url: str, content_type: str, chunks: list[bytes]) -> None:
        self._url = url
        self.headers = FakeHeaders({"Content-Type": content_type})
        self._chunks = list(chunks)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def geturl(self) -> str:
        return self._url

    def read(self, _size: int = -1) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


class CoreEngineHardeningTests(unittest.TestCase):
    def test_accepts_audio_tag_without_file_extension(self) -> None:
        assets = extract_generic_audio(
            '<audio src="/stream?id=123"></audio>',
            base_url="https://example.test/page",
        )
        self.assertEqual(
            assets,
            [AudioAsset("https://example.test/stream?id=123", "stream")],
        )

    def test_fallback_extension_matches_downloaded_format(self) -> None:
        crawler = AudioCrawler(retries=1)
        response = FakeResponse(
            "https://cdn.test/preview.mp3",
            "audio/mpeg",
            [b"ID3-test-audio", b""],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(crawler, "_open", return_value=response):
                result = crawler._download_one(
                    "https://cdn.test/preview.mp3",
                    "Kick.wav",
                    Path(temp_dir),
                )
            self.assertEqual(result.name, "Kick.mp3")
            self.assertEqual(result.read_bytes(), b"ID3-test-audio")

    def test_octet_stream_without_extension_uses_magic_bytes(self) -> None:
        crawler = AudioCrawler(retries=1)
        response = FakeResponse(
            "https://cdn.test/stream?id=123",
            "application/octet-stream",
            [b"ID3-stream-audio", b""],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(crawler, "_open", return_value=response):
                result = crawler._download_one(
                    "https://cdn.test/stream?id=123",
                    "Stream",
                    Path(temp_dir),
                )
            self.assertEqual(result.name, "Stream.mp3")

    def test_rejects_html_from_audio_looking_url(self) -> None:
        crawler = AudioCrawler(retries=1)
        response = FakeResponse(
            "https://cdn.test/sample.mp3",
            "text/html",
            [b"<!doctype html><html>Access denied</html>", b""],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(crawler, "_open", return_value=response):
                with self.assertRaises(PublicAudioError):
                    crawler._download_one(
                        "https://cdn.test/sample.mp3",
                        "Sample",
                        Path(temp_dir),
                    )
            self.assertEqual(list(Path(temp_dir).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
