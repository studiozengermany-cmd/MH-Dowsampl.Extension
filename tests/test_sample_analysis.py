from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sample_analysis import (  # noqa: E402
    SampleAnalyzer,
    content_folder,
    metadata_filename,
    organize_sample,
    rename_sample_with_metadata,
)


class SampleLayoutTests(unittest.TestCase):
    def test_metadata_filename_exposes_bpm_and_key_without_changing_extension(self) -> None:
        source = Path("Warm Loop.mp3")
        self.assertEqual(
            metadata_filename(source, 128, "C# min"),
            "Warm Loop [128 BPM] [C# Minor].mp3",
        )

    def test_metadata_filename_does_not_duplicate_existing_metadata(self) -> None:
        source = Path("Warm Loop 128 BPM C# min.wav")
        self.assertEqual(metadata_filename(source, 128, "C# min"), source.name)

    def test_rename_with_metadata_preserves_audio_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "Loop.wav"
            payload = b"RIFF-original-audio"
            source.write_bytes(payload)

            output = rename_sample_with_metadata(source, 120, "A min")

            self.assertEqual(output.name, "Loop [120 BPM] [A Minor].wav")
            self.assertEqual(output.read_bytes(), payload)
            self.assertFalse(source.exists())

    def test_content_types_use_the_mh_dowsample_layout(self) -> None:
        self.assertEqual(content_folder("loop"), "Loops")
        self.assertEqual(content_folder("one-shot"), "One-Shots")
        self.assertEqual(content_folder("fx"), "FX")
        self.assertEqual(content_folder("unknown"), "Unsorted")
        self.assertEqual(content_folder("unexpected"), "Unsorted")

    def test_organize_sample_moves_original_into_category(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            incoming = root / ".incoming"
            incoming.mkdir()
            source = incoming / "Kick.wav"
            source.write_bytes(b"RIFF-test")

            output = organize_sample(source, root, "one-shot")

            self.assertEqual(output, (root / "One-Shots" / "Kick.wav").resolve())
            self.assertEqual(output.read_bytes(), b"RIFF-test")
            self.assertFalse(source.exists())

    def test_organize_sample_rejects_source_outside_job_root(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            source = Path(first) / "sample.wav"
            source.write_bytes(b"test")
            with self.assertRaisesRegex(ValueError, "ngoài thư mục"):
                organize_sample(source, Path(second), "loop")


class SampleAnalyzerTests(unittest.TestCase):
    def analyzer_or_skip(self) -> SampleAnalyzer:
        try:
            return SampleAnalyzer()
        except RuntimeError as exc:
            self.skipTest(str(exc))

    def test_decodes_silent_wav_and_reports_quality_issue(self) -> None:
        analyzer = self.analyzer_or_skip()

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "short.wav"
            sample_rate = 44_100
            frames = int(sample_rate * 0.5)
            with wave.open(str(source), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(sample_rate)
                handle.writeframes(b"\x00\x00" * frames)

            result = analyzer.analyze(source)

            self.assertEqual(result["content_type"], "unknown")
            self.assertFalse(result["passed"])
            self.assertEqual(result["analysis_error"], "")
            self.assertIn("khoảng lặng", " ".join(result["issues"]))

    def test_signal_classifier_marks_subsecond_audio_as_one_shot(self) -> None:
        analyzer = self.analyzer_or_skip()
        signal = analyzer.np.zeros(int(44_100 * 0.5), dtype="float32")
        result = analyzer._classify_content(signal, 44_100)
        self.assertEqual(result["content_type"], "one-shot")

    def test_signal_classifier_marks_regular_drum_pattern_as_loop(self) -> None:
        analyzer = self.analyzer_or_skip()
        np = analyzer.np
        sample_rate = 44_100
        bpm = 128
        duration = 4 * (4 * 60.0 / bpm)
        frame_count = int(sample_rate * duration)
        signal = np.zeros(frame_count, dtype="float32")
        beat_interval = int(sample_rate * 60.0 / bpm)
        for index in range(int(duration * bpm / 60)):
            start = index * beat_interval
            end = min(start + int(sample_rate * 0.05), frame_count)
            signal[start:end] += 0.9 * np.exp(-np.linspace(0, 10, end - start))
        timeline = np.arange(frame_count) / sample_rate
        signal += 0.2 * np.sin(2 * np.pi * 80 * timeline)
        signal += 0.1 * np.sin(2 * np.pi * 8_000 * timeline)
        result = analyzer._classify_content(np.clip(signal, -1, 1), sample_rate)
        self.assertEqual(result["content_type"], "loop")


if __name__ == "__main__":
    unittest.main()
