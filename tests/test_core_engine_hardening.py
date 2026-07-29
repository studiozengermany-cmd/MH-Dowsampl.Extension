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
    detect_audio_suffix,
    extract_generic_audio,
    extract_splice_samples,
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

    def test_accepts_audio_named_json_without_extension(self) -> None:
        assets = extract_generic_audio(
            '{"audioUrl":"https://cdn.test/stream?id=456"}',
        )
        self.assertEqual(
            assets,
            [AudioAsset("https://cdn.test/stream?id=456", "stream")],
        )

    def test_ignores_generic_content_url_without_audio_extension(self) -> None:
        assets = extract_generic_audio(
            '{"contentUrl":"https://cdn.test/video?id=789"}',
        )
        self.assertEqual(assets, [])

    def test_splice_accepts_declared_audio_url_without_extension(self) -> None:
        payload = {
            "items": [
                {
                    "name": "Signed WAV",
                    "files": [
                        {
                            "url": "https://cdn.test/download?token=abc",
                            "asset_file_type_slug": "wav",
                        },
                        {
                            "url": "https://cdn.test/preview.mp3",
                            "asset_file_type_slug": "preview_mp3",
                        },
                    ],
                }
            ]
        }
        assets = extract_splice_samples(payload)
        self.assertEqual(assets[0].url, "https://cdn.test/download?token=abc")
        self.assertEqual(assets[0].fallback_urls, ("https://cdn.test/preview.mp3",))

    def test_discover_recognizes_extensionless_octet_stream_audio(self) -> None:
        crawler = AudioCrawler(retries=1)
        response = FakeResponse(
            "https://cdn.test/stream?id=direct",
            "application/octet-stream",
            [b"ID3-direct-audio", b""],
        )
        with patch.object(crawler, "_open", return_value=response):
            assets = crawler.discover("https://cdn.test/stream?id=direct")
        self.assertEqual(
            assets,
            [AudioAsset("https://cdn.test/stream?id=direct", "stream")],
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

    def test_magic_bytes_override_wrong_declared_format(self) -> None:
        crawler = AudioCrawler(retries=1)
        wav_bytes = b"RIFF" + (b"\x00" * 4) + b"WAVEfmt "
        response = FakeResponse(
            "https://cdn.test/wrong.mp3",
            "audio/mpeg",
            [wav_bytes, b""],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(crawler, "_open", return_value=response):
                result = crawler._download_one(
                    "https://cdn.test/wrong.mp3",
                    "Recorded.mp3",
                    Path(temp_dir),
                )
            self.assertEqual(result.name, "Recorded.wav")

    def test_detects_opus_and_aac_signatures(self) -> None:
        self.assertEqual(detect_audio_suffix(b"OggS" + b"\x00" * 20 + b"OpusHead"), ".opus")
        self.assertEqual(detect_audio_suffix(bytes([0xFF, 0xF1, 0x50, 0x80])), ".aac")

    def test_rejects_octet_stream_mp4_video_as_m4a(self) -> None:
        crawler = AudioCrawler(retries=1)
        video_bytes = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 20 + b"vide"
        response = FakeResponse(
            "https://cdn.test/content?id=video",
            "application/octet-stream",
            [video_bytes, b""],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(crawler, "_open", return_value=response):
                with self.assertRaises(PublicAudioError):
                    crawler._download_one(
                        "https://cdn.test/content?id=video",
                        "Not Audio",
                        Path(temp_dir),
                    )
            self.assertEqual(list(Path(temp_dir).iterdir()), [])

    def test_rejects_video_even_when_url_looks_like_m4a(self) -> None:
        crawler = AudioCrawler(retries=1)
        video_bytes = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 20 + b"vide"
        response = FakeResponse(
            "https://cdn.test/fake.m4a",
            "audio/mp4",
            [video_bytes, b""],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(crawler, "_open", return_value=response):
                with self.assertRaises(PublicAudioError):
                    crawler._download_one(
                        "https://cdn.test/fake.m4a",
                        "Fake M4A",
                        Path(temp_dir),
                    )
            self.assertEqual(list(Path(temp_dir).iterdir()), [])

    def test_accepts_octet_stream_m4a_with_audio_handler(self) -> None:
        crawler = AudioCrawler(retries=1)
        audio_bytes = b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 20 + b"soun"
        response = FakeResponse(
            "https://cdn.test/content?id=audio",
            "application/octet-stream",
            [audio_bytes, b""],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(crawler, "_open", return_value=response):
                result = crawler._download_one(
                    "https://cdn.test/content?id=audio",
                    "Audio Stream",
                    Path(temp_dir),
                )
            self.assertEqual(result.name, "Audio Stream.m4a")

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
