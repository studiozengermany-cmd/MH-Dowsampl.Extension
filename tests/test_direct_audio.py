from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import server  # noqa: F401,E402 - activates runtime compatibility patches
from crawler import AudioAsset, AudioCrawler, PublicAudioError  # noqa: E402


class FakeHeaders:
    def __init__(self, content_type: str) -> None:
        self.content_type = content_type

    def get(self, name: str, default: str = "") -> str:
        if name == "Content-Type":
            return self.content_type
        return default

    def get_content_charset(self) -> str | None:
        return None


class HeaderOnlyResponse:
    def __init__(self, url: str, first_chunk: bytes) -> None:
        self._url = url
        self.headers = FakeHeaders("application/octet-stream")
        self.first_chunk = first_chunk
        self.read_count = 0

    def __enter__(self) -> "HeaderOnlyResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def geturl(self) -> str:
        return self._url

    def read(self, _size: int = -1) -> bytes:
        self.read_count += 1
        if self.read_count > 1:
            raise AssertionError("Không được đọc toàn bộ direct audio ở bước khám phá")
        return self.first_chunk


class DirectAudioDiscoveryTests(unittest.TestCase):
    def test_large_extensionless_audio_is_identified_from_first_chunk(self) -> None:
        crawler = AudioCrawler(retries=1)
        response = HeaderOnlyResponse(
            "https://cdn.test/stream?id=large",
            b"ID3-audio-header",
        )
        with patch.object(crawler, "_open", return_value=response):
            document, asset = crawler._fetch_page_or_direct_asset(
                "https://cdn.test/stream?id=large"
            )

        self.assertIsNone(document)
        self.assertEqual(
            asset,
            AudioAsset("https://cdn.test/stream?id=large", "stream"),
        )
        self.assertEqual(response.read_count, 1)

    def test_unknown_binary_is_not_treated_as_document(self) -> None:
        crawler = AudioCrawler(retries=1)
        response = HeaderOnlyResponse(
            "https://cdn.test/blob",
            b"\x00\x01\x02not-audio",
        )
        with patch.object(crawler, "_open", return_value=response):
            with self.assertRaises(PublicAudioError):
                crawler._fetch_page_or_direct_asset("https://cdn.test/blob")

        self.assertEqual(response.read_count, 1)


if __name__ == "__main__":
    unittest.main()
