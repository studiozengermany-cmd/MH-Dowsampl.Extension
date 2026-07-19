from __future__ import annotations

import json
import io
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from crawler import (  # noqa: E402
    MAX_DOWNLOAD_BYTES,
    AudioAsset,
    AudioCrawler,
    detect_audio_format,
    extract_splice_page,
    extract_splice_samples,
    sanitize_filename,
    validate_http_url,
)


class FakeResponse:
    def __init__(self, payload: bytes, *, content_type: str, url: str, length: int | None = None) -> None:
        self._stream = io.BytesIO(payload)
        self._url = url
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(payload) if length is None else length)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def geturl(self) -> str:
        return self._url


class CrawlerTests(unittest.TestCase):
    def test_prefers_original_wav_over_preview_mp3(self) -> None:
        payload = {
            "data": {
                "items": [
                    {
                        "name": "Warm Kick 128 BPM",
                        "bpm": 128,
                        "key": "C# minor",
                        "files": [
                            {"url": "https://cdn.test/preview.mp3", "asset_file_type_slug": "preview_mp3"},
                            {"url": "https://cdn.test/full.flac", "asset_file_type_slug": "flac"},
                            {"url": "https://cdn.test/full.wav", "asset_file_type_slug": "wav"},
                        ],
                    }
                ]
            }
        }
        self.assertEqual(
            extract_splice_samples(payload),
            [
                AudioAsset(
                    "https://cdn.test/full.wav",
                    "Warm Kick 128 BPM",
                    128,
                    "C# Minor",
                    "wav",
                    "catalogue",
                )
            ],
        )

    def test_preview_only_catalogue_is_not_returned_as_original(self) -> None:
        payload = {
            "items": [
                {
                    "name": "Preview only",
                    "files": [
                        {
                            "url": "https://cdn.test/preview.mp3",
                            "asset_file_type_slug": "preview_mp3",
                        }
                    ],
                }
            ]
        }
        self.assertEqual(extract_splice_samples(payload), [])

    def test_extracts_server_rendered_splice_page_and_pagination(self) -> None:
        body = {
            "items": [
                {
                    "title": "Deep Bass",
                    "files": [
                        {"url": "https://cdn.test/deep-bass.wav", "asset_file_type_slug": "wav"}
                    ],
                }
            ],
            "pagination_metadata": {"currentPage": 2, "totalPages": 4},
        }
        envelope = {"body": json.dumps(body)}
        document = f'<script data-sveltekit-fetched>{json.dumps(envelope)}</script>'
        assets, current, total = extract_splice_page(document)
        self.assertEqual(
            assets,
            [
                AudioAsset(
                    "https://cdn.test/deep-bass.wav",
                    "Deep Bass",
                    declared_format="wav",
                    metadata_source="catalogue",
                )
            ],
        )
        self.assertEqual((current, total), (2, 4))

    def test_sanitizes_windows_filename_without_renaming_words(self) -> None:
        self.assertEqual(sanitize_filename('Kick: 128 BPM / C#m'), "Kick_ 128 BPM _ C#m")

    def test_protects_windows_reserved_filenames(self) -> None:
        self.assertEqual(sanitize_filename("CON"), "_CON")
        self.assertEqual(sanitize_filename("LPT1.wav"), "_LPT1.wav")

    def test_rejects_credentials_and_whitespace_in_urls(self) -> None:
        with self.assertRaises(ValueError):
            validate_http_url("https://user:secret@example.com/sample.mp3")
        with self.assertRaises(ValueError):
            validate_http_url("https://example.com/bad link.mp3")

    def test_downloads_audio_atomically(self) -> None:
        response = FakeResponse(
            b"ID3-test-audio",
            content_type="audio/mpeg",
            url="https://cdn.test/sample.mp3",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            with patch("crawler.urlopen", return_value=response):
                destination = AudioCrawler().download(
                    AudioAsset("https://cdn.test/sample.mp3", "Safe sample"),
                    folder,
                )
            self.assertEqual(destination.name, "Safe sample.mp3")
            self.assertEqual(destination.read_bytes(), b"ID3-test-audio")
            self.assertFalse((folder / "Safe sample.mp3.part").exists())

    def test_uses_real_wav_signature_instead_of_url_extension(self) -> None:
        payload = b"RIFF" + (b"\x00" * 4) + b"WAVEfmt " + (b"\x00" * 32)
        response = FakeResponse(
            payload,
            content_type="audio/mpeg",
            url="https://cdn.test/wrong-name.mp3",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("crawler.urlopen", return_value=response):
                destination = AudioCrawler().download(
                    AudioAsset("https://cdn.test/wrong-name.mp3", "Original sample"),
                    Path(temp_dir),
                )
            self.assertEqual(destination.name, "Original sample.wav")
            self.assertEqual(destination.read_bytes(), payload)

    def test_rejects_mp3_disguised_as_declared_wav(self) -> None:
        response = FakeResponse(
            b"ID3-not-a-wave",
            content_type="audio/wav",
            url="https://cdn.test/fake.wav",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("crawler.urlopen", return_value=response):
                with self.assertRaisesRegex(RuntimeError, "khai báo WAV"):
                    AudioCrawler().download(
                        AudioAsset(
                            "https://cdn.test/fake.wav",
                            "Fake WAV",
                            declared_format="wav",
                        ),
                        Path(temp_dir),
                    )

    def test_detects_supported_audio_signatures(self) -> None:
        self.assertEqual(detect_audio_format(b"RIFF0000WAVEfmt "), "wav")
        self.assertEqual(detect_audio_format(b"ID3audio"), "mp3")
        self.assertEqual(detect_audio_format(b"fLaCaudio"), "flac")
        self.assertEqual(detect_audio_format(b"OggSaudio"), "ogg")
        self.assertEqual(detect_audio_format(b"0000ftypM4A "), "m4a")

    def test_rejects_oversized_download_before_writing(self) -> None:
        response = FakeResponse(
            b"",
            content_type="audio/mpeg",
            url="https://cdn.test/huge.mp3",
            length=MAX_DOWNLOAD_BYTES + 1,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            with patch("crawler.urlopen", return_value=response):
                with self.assertRaisesRegex(RuntimeError, "512 MB"):
                    AudioCrawler().download(
                        AudioAsset("https://cdn.test/huge.mp3"),
                        folder,
                    )
            self.assertEqual(list(folder.iterdir()), [])

    def test_rejects_html_disguised_as_audio(self) -> None:
        response = FakeResponse(
            b"<html>not audio</html>",
            content_type="text/html",
            url="https://cdn.test/fake.mp3",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("crawler.urlopen", return_value=response):
                with self.assertRaisesRegex(RuntimeError, "trang web"):
                    AudioCrawler().download(
                        AudioAsset("https://cdn.test/fake.mp3"),
                        Path(temp_dir),
                    )


if __name__ == "__main__":
    unittest.main()
