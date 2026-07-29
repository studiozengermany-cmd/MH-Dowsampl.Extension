from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import server as backend_server  # noqa: E402
from crawler import AudioAsset  # noqa: E402


class PartialCrawler:
    def discover(self, page_url: str) -> list[AudioAsset]:
        if "broken" in page_url:
            raise RuntimeError("nguồn thử nghiệm bị lỗi")
        return [AudioAsset("https://cdn.test/good.wav", "Good sample")]


class BatchServerTests(unittest.TestCase):
    def test_normalizes_multiline_urls_and_removes_duplicates(self) -> None:
        urls = backend_server.normalize_urls(
            "",
            [
                "https://example.test/one\nhttps://example.test/two",
                "https://example.test/one",
            ],
        )
        self.assertEqual(
            urls,
            ["https://example.test/one", "https://example.test/two"],
        )

    def test_one_broken_source_does_not_kill_the_batch(self) -> None:
        job = backend_server.Job(
            id="batchtest",
            url="https://example.test/broken",
            urls=[
                "https://example.test/broken",
                "https://example.test/good",
            ],
            source_total=2,
        )
        assets = backend_server.discover_assets(job, PartialCrawler())

        self.assertEqual(assets, [AudioAsset("https://cdn.test/good.wav", "Good sample")])
        self.assertEqual(job.source_processed, 2)
        self.assertEqual(job.source_failed, 1)
        self.assertEqual(job.discovered, 1)
        self.assertEqual(len(job.failures), 1)


if __name__ == "__main__":
    unittest.main()
