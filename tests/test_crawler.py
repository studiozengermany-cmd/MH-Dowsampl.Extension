from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from crawler import (  # noqa: E402
    AudioAsset,
    extract_generic_audio,
    extract_splice_page,
    extract_splice_samples,
    sanitize_filename,
)


class CrawlerTests(unittest.TestCase):
    def test_prefers_public_lossless_and_keeps_preview_as_fallback(self) -> None:
        payload = {
            "data": {
                "items": [
                    {
                        "name": "Warm Kick 128 BPM",
                        "files": [
                            {
                                "url": "https://cdn.test/full.wav",
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
        }
        assets = extract_splice_samples(payload)
        self.assertEqual(assets, [AudioAsset("https://cdn.test/full.wav", "Warm Kick 128 BPM")])
        self.assertEqual(assets[0].fallback_urls, ("https://cdn.test/preview.mp3",))

    def test_extracts_server_rendered_splice_page_and_pagination(self) -> None:
        body = {
            "items": [
                {
                    "title": "Deep Bass",
                    "files": [
                        {
                            "url": "https://cdn.test/deep-bass.mp3",
                            "asset_file_type_slug": "preview_mp3",
                        }
                    ],
                }
            ],
            "pagination_metadata": {"currentPage": 2, "totalPages": 4},
        }
        envelope = {"body": json.dumps(body)}
        document = f'<script data-sveltekit-fetched>{json.dumps(envelope)}</script>'
        assets, current, total = extract_splice_page(document)
        self.assertEqual(assets, [AudioAsset("https://cdn.test/deep-bass.mp3", "Deep Bass")])
        self.assertEqual((current, total), (2, 4))

    def test_extracts_audio_source_meta_json_and_relative_urls(self) -> None:
        document = """
        <audio src="/media/loop.wav"></audio>
        <source src='https://cdn.test/bass.flac'>
        <meta property="og:audio" content="/media/preview.mp3">
        <script type="application/ld+json">
          {"contentUrl": "https:\\/\\/cdn.test\\/voice.ogg"}
        </script>
        """
        assets = extract_generic_audio(document, base_url="https://example.test/product/1")
        self.assertEqual(
            [asset.url for asset in assets],
            [
                "https://example.test/media/loop.wav",
                "https://cdn.test/bass.flac",
                "https://example.test/media/preview.mp3",
                "https://cdn.test/voice.ogg",
            ],
        )

    def test_sanitizes_windows_filename_without_renaming_words(self) -> None:
        self.assertEqual(sanitize_filename('Kick: 128 BPM / C#m'), "Kick_ 128 BPM _ C#m")


if __name__ == "__main__":
    unittest.main()
