from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from crawler import (  # noqa: E402
    AudioAsset,
    extract_splice_page,
    extract_splice_samples,
    sanitize_filename,
)


class CrawlerTests(unittest.TestCase):
    def test_extracts_preferred_splice_preview_and_title(self) -> None:
        payload = {
            "data": {
                "items": [
                    {
                        "name": "Warm Kick 128 BPM",
                        "files": [
                            {"url": "https://cdn.test/full.wav", "asset_file_type_slug": "wav"},
                            {"url": "https://cdn.test/preview.mp3", "asset_file_type_slug": "preview_mp3"},
                        ],
                    }
                ]
            }
        }
        self.assertEqual(
            extract_splice_samples(payload),
            [AudioAsset("https://cdn.test/preview.mp3", "Warm Kick 128 BPM")],
        )

    def test_extracts_server_rendered_splice_page_and_pagination(self) -> None:
        body = {
            "items": [
                {
                    "title": "Deep Bass",
                    "files": [
                        {"url": "https://cdn.test/deep-bass.mp3", "asset_file_type_slug": "preview_mp3"}
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

    def test_sanitizes_windows_filename_without_renaming_words(self) -> None:
        self.assertEqual(sanitize_filename('Kick: 128 BPM / C#m'), "Kick_ 128 BPM _ C#m")


if __name__ == "__main__":
    unittest.main()
