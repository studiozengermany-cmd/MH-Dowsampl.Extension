"""Analyze and organize downloaded samples using the MH-Dowsample contract.

The signal classification constants and rules mirror the frozen QualityGate in
the sibling MH-Dowsample project. Decoding uses bundled libsndfile so the
packaged Windows server does not require Python or FFmpeg to be installed.
"""

from __future__ import annotations

import importlib
import os
import re
from pathlib import Path
from typing import Any

CONTENT_FOLDERS = {
    "loop": "Loops",
    "one-shot": "One-Shots",
    "fx": "FX",
    "unknown": "Unsorted",
}
ONSET_WEIGHT = 0.35
REGULARITY_WEIGHT = 0.30
BEAT_WEIGHT = 0.25
ZCR_WEIGHT = 0.10
LOOP_THRESHOLD = 0.58
MIN_BITRATE_KBPS = 96
MIN_DURATION_SEC = 1.0
MIN_ONESHOT_DURATION_SEC = 0.1
MAX_SILENCE_RATIO = 0.8
MAX_ANALYSIS_SECONDS = 300
KEY_TOKEN = re.compile(
    r"(?i)(?<![A-Za-z0-9])([A-G])\s*([#b]?)\s*(major|maj|minor|min|m)(?![A-Za-z0-9])"
)


def content_folder(content_type: str) -> str:
    """Return the public library folder for a classifier content type."""

    return CONTENT_FOLDERS.get(str(content_type or "").lower(), "Unsorted")


def _available_path(target: Path) -> Path:
    if not target.exists():
        return target
    for index in range(2, 10_000):
        candidate = target.with_stem(f"{target.stem} ({index})")
        if not candidate.exists():
            return candidate
    raise RuntimeError("Không thể tạo tên file phân loại không trùng")


def organize_sample(source: Path, output_root: Path, content_type: str) -> Path:
    """Atomically publish one downloaded file into its content folder."""

    root = output_root.resolve()
    source = source.resolve()
    if not source.is_relative_to(root):
        raise ValueError("File tải nằm ngoài thư mục của tác vụ")
    folder = (root / content_folder(content_type)).resolve()
    if not folder.is_relative_to(root):
        raise ValueError("Thư mục phân loại nằm ngoài thư mục của tác vụ")
    folder.mkdir(parents=True, exist_ok=True)
    destination = _available_path(folder / source.name)
    os.replace(source, destination)
    return destination


def normalize_musical_key(value: object) -> str:
    """Normalize catalogue/analyzed key labels to e.g. ``C# Minor``."""

    text = re.sub(r"\s+", " ", str(value or "").strip()).replace("♯", "#").replace("♭", "b")
    if text.lower() in {"", "unknown", "none", "n/a"}:
        return ""
    match = re.fullmatch(
        r"(?i)([A-G])\s*([#b]?)\s*(major|maj|minor|min|m)?",
        text,
    )
    if not match:
        return ""
    root = match.group(1).upper() + match.group(2)
    mode = (match.group(3) or "").lower()
    if mode in {"minor", "min", "m"}:
        return f"{root} Minor"
    if mode in {"major", "maj"}:
        return f"{root} Major"
    return root


def metadata_filename(path: Path, bpm: object = 0, musical_key: object = "") -> str:
    """Build a portable filename that exposes tempo/key in any sample browser."""

    stem = path.stem
    tokens: list[str] = []
    try:
        bpm_value = int(round(float(bpm)))
    except (TypeError, ValueError):
        bpm_value = 0
    if bpm_value > 0 and not re.search(r"(?i)(?<!\d)\d{2,3}\s*BPM(?!\w)", stem):
        tokens.append(f"{bpm_value} BPM")

    key_value = normalize_musical_key(musical_key)
    existing_key = KEY_TOKEN.search(stem)
    if key_value and not existing_key:
        tokens.append(key_value)

    if not tokens:
        return path.name
    suffix_text = " " + " ".join(f"[{token}]" for token in tokens)
    maximum_stem = max(1, 220 - len(path.suffix) - len(suffix_text))
    stem = stem[:maximum_stem].rstrip(" .") or "sample"
    return f"{stem}{suffix_text}{path.suffix}"


def rename_sample_with_metadata(
    source: Path,
    bpm: object = 0,
    musical_key: object = "",
) -> Path:
    """Rename only; audio bytes and source codec remain unchanged."""

    filename = metadata_filename(source, bpm, musical_key)
    if filename == source.name:
        return source
    destination = _available_path(source.with_name(filename))
    os.replace(source, destination)
    return destination


class SampleAnalyzer:
    """Local audio decoder and MH-Dowsample-compatible signal analyzer."""

    def __init__(self) -> None:
        try:
            self.np = importlib.import_module("numpy")
            self.soundfile = importlib.import_module("soundfile")
        except ImportError as exc:
            raise RuntimeError(
                "Bản server thiếu thư viện phân tích âm thanh được đóng gói"
            ) from exc

    @classmethod
    def check_dependencies(cls) -> None:
        cls()

    def _load_audio(self, path: Path) -> tuple[Any, int, int, int]:
        info = self.soundfile.info(str(path))
        sample_rate = int(info.samplerate)
        channels = int(info.channels)
        frame_limit = sample_rate * MAX_ANALYSIS_SECONDS
        data, sample_rate = self.soundfile.read(
            str(path),
            dtype="float32",
            always_2d=True,
            frames=frame_limit,
        )
        if data.size == 0:
            raise ValueError("Không giải mã được dữ liệu âm thanh")
        mono = self.np.mean(data, axis=1, dtype="float32")
        duration = len(mono) / sample_rate
        bitrate = round(path.stat().st_size * 8 / duration / 1000) if duration else 0
        return mono, int(sample_rate), channels, bitrate

    def _classify_content(self, y: Any, sample_rate: int) -> dict[str, Any]:
        """Mirror MH-Dowsample QualityGate._classify_content."""

        np = self.np
        duration = len(y) / sample_rate
        if duration < 0.8:
            return {"content_type": "one-shot", "loop_score": 0.0}
        frame_length = min(2_048, len(y))
        hop_length = max(1, frame_length // 4)
        if len(y) < frame_length:
            frames = y.reshape(1, -1)
        else:
            frame_count = 1 + (len(y) - frame_length) // hop_length
            frames = np.lib.stride_tricks.as_strided(
                y,
                shape=(frame_count, frame_length),
                strides=(y.strides[0] * hop_length, y.strides[0]),
                writeable=False,
            )
        rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
        onset = np.maximum(np.diff(rms, prepend=rms[0]), 0.0)
        if onset.size < 3 or not np.any(onset > 0):
            return {"content_type": "fx", "loop_score": 0.0}
        threshold = float(np.mean(onset) + np.std(onset) * 0.5)
        candidates = np.flatnonzero(
            (onset[1:-1] > onset[:-2])
            & (onset[1:-1] >= onset[2:])
            & (onset[1:-1] > threshold)
        ) + 1
        minimum_gap = max(1, int(0.08 * sample_rate / hop_length))
        peaks: list[int] = []
        for candidate in candidates.tolist():
            if not peaks or candidate - peaks[-1] >= minimum_gap:
                peaks.append(candidate)
            elif onset[candidate] > onset[peaks[-1]]:
                peaks[-1] = candidate
        peak_values = onset[peaks] if peaks else np.array([], dtype=float)
        onset_score = (
            1.0 / (1.0 + float(np.std(peak_values) / (np.mean(peak_values) + 1e-9)))
            if peak_values.size >= 2
            else 0.0
        )
        intervals = np.diff(peaks)
        regularity = (
            1.0 / (1.0 + float(np.std(intervals) / (np.mean(intervals) + 1e-9)))
            if intervals.size >= 2
            else 0.0
        )
        beat_score = min(1.0, len(peaks) / max(duration * 1.5, 1.0))
        signs = frames >= 0
        zero_crossing = np.mean(signs[:, 1:] != signs[:, :-1], axis=1)
        zero_crossing_variance = float(np.var(zero_crossing))
        zero_crossing_score = 1.0 / (1.0 + zero_crossing_variance * 1000)
        loop_score = (
            onset_score * ONSET_WEIGHT
            + regularity * REGULARITY_WEIGHT
            + beat_score * BEAT_WEIGHT
            + zero_crossing_score * ZCR_WEIGHT
        )
        is_loop = duration >= 2.0 and loop_score >= LOOP_THRESHOLD and regularity >= 0.72
        is_one_shot = (
            not is_loop
            and duration < 3.0
            and (zero_crossing_variance > 0.005 or onset_score < 0.3 or len(peaks) <= 2)
        )
        content_type = "loop" if is_loop else "one-shot" if is_one_shot else "fx"
        return {"content_type": content_type, "loop_score": round(loop_score, 3)}

    def _detect_key(self, y: Any, sample_rate: int) -> str:
        try:
            np = self.np
            signal = y[: sample_rate * 60]
            window = np.hanning(len(signal))
            spectrum = np.abs(np.fft.rfft(signal * window)) ** 2
            frequencies = np.fft.rfftfreq(len(signal), 1 / sample_rate)
            mask = (frequencies >= 40) & (frequencies <= 5_000) & (spectrum > 0)
            profile = np.zeros(12, dtype=float)
            midi = np.rint(69 + 12 * np.log2(frequencies[mask] / 440.0)).astype(int)
            for pitch_class in range(12):
                profile[pitch_class] = float(np.sum(spectrum[mask][midi % 12 == pitch_class]))
            if not np.any(profile):
                return "Unknown"
            major = np.array(
                [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
            )
            minor = np.array(
                [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
            )
            scores = [
                np.corrcoef(profile, np.roll(template, shift))[0, 1]
                for template in (major, minor)
                for shift in range(12)
            ]
            index = int(np.nanargmax(scores))
            names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
            mode, root = divmod(index, 12)
            return f"{names[root]}{'maj' if mode == 0 else 'min'}"
        except Exception:  # Optional key metadata must not invalidate decoded audio.
            return "Unknown"

    def _detect_bpm(
        self,
        y: Any,
        sample_rate: int,
        content: dict[str, Any],
        duration: float,
    ) -> tuple[int, str]:
        if content["content_type"] != "loop" or duration < 2:
            return 0, "none"
        try:
            np = self.np
            frame_length = min(2_048, len(y))
            hop_length = max(1, frame_length // 4)
            frame_count = 1 + max(0, (len(y) - frame_length) // hop_length)
            frames = np.lib.stride_tricks.as_strided(
                y,
                shape=(frame_count, frame_length),
                strides=(y.strides[0] * hop_length, y.strides[0]),
                writeable=False,
            )
            rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
            onset = np.maximum(np.diff(rms, prepend=rms[0]), 0.0)
            threshold = float(np.mean(onset) + np.std(onset) * 0.5)
            peaks = np.flatnonzero(
                (onset[1:-1] > onset[:-2])
                & (onset[1:-1] >= onset[2:])
                & (onset[1:-1] > threshold)
            ) + 1
            intervals = np.diff(peaks) * hop_length / sample_rate
            if intervals.size == 0:
                return 0, "none"
            bpm = int(round(60.0 / float(np.median(intervals))))
            while bpm < 60:
                bpm *= 2
            while bpm > 200:
                bpm //= 2
            confidence = "high" if intervals.size >= max(4, duration) else "low"
            return bpm, confidence
        except Exception:
            return 0, "none"

    @staticmethod
    def _genre_hint(bpm: int, centroid: float, content_type: str) -> str:
        if content_type == "one-shot":
            return "one-shot"
        if content_type == "fx":
            return "fx"
        if not bpm:
            return "ambient" if centroid < 2000 else "other"
        if bpm >= 160:
            return "dnb" if centroid > 3000 else "trap"
        if bpm >= 135:
            return "trap" if centroid < 3000 else "techno"
        if bpm >= 118:
            return "house" if centroid > 2000 else "deep-house"
        if bpm >= 80:
            return "hip-hop" if centroid < 2500 else "pop"
        return "lo-fi"

    def analyze(self, filepath: Path | str) -> dict[str, Any]:
        path = Path(filepath)
        result: dict[str, Any] = {
            "passed": False,
            "content_type": "unknown",
            "loop_score": 0.0,
            "bitrate_kbps": 0,
            "duration_sec": 0.0,
            "silence_ratio": 1.0,
            "sample_rate": 0,
            "channels": 0,
            "bpm": 0,
            "bpm_confidence": "none",
            "key": "Unknown",
            "genre_hint": "other",
            "issues": [],
            "analysis_error": "",
        }
        try:
            y, sample_rate, channels, bitrate = self._load_audio(path)
            duration = len(y) / sample_rate
            result.update(
                bitrate_kbps=bitrate,
                duration_sec=round(duration, 3),
                sample_rate=sample_rate,
                channels=channels,
            )
            frame_length = min(2_048, len(y))
            hop_length = max(1, frame_length // 4)
            frame_count = 1 + max(0, (len(y) - frame_length) // hop_length)
            frames = self.np.lib.stride_tricks.as_strided(
                y,
                shape=(frame_count, frame_length),
                strides=(y.strides[0] * hop_length, y.strides[0]),
                writeable=False,
            )
            rms = self.np.sqrt(self.np.mean(frames * frames, axis=1) + 1e-12)
            peak_rms = float(self.np.max(rms))
            silence_ratio = (
                1.0
                if peak_rms <= 1e-6
                else float(self.np.mean(rms < peak_rms * (10 ** (-45 / 20))))
            )
            result["silence_ratio"] = round(silence_ratio, 3)
            if bitrate and bitrate < MIN_BITRATE_KBPS:
                result["issues"].append(f"Bitrate dưới {MIN_BITRATE_KBPS} kbps")
            if duration < MIN_ONESHOT_DURATION_SEC:
                result["issues"].append("Âm thanh quá ngắn")
                return result
            if silence_ratio > MAX_SILENCE_RATIO:
                result["issues"].append("Âm thanh có quá nhiều khoảng lặng")
                return result
            content = self._classify_content(y, sample_rate)
            content_type = str(content["content_type"])
            result.update(content_type=content_type, loop_score=content["loop_score"])
            if content_type != "one-shot" and duration < MIN_DURATION_SEC:
                result["issues"].append("Âm thanh quá ngắn")
            signal = y[: sample_rate * 60]
            spectrum = self.np.abs(self.np.fft.rfft(signal))
            frequencies = self.np.fft.rfftfreq(len(signal), 1 / sample_rate)
            centroid = float(
                self.np.sum(frequencies * spectrum) / (self.np.sum(spectrum) + 1e-12)
            )
            result["key"] = self._detect_key(y, sample_rate)
            result["bpm"], result["bpm_confidence"] = self._detect_bpm(
                y,
                sample_rate,
                content,
                duration,
            )
            result["genre_hint"] = self._genre_hint(
                int(result["bpm"]),
                centroid,
                content_type,
            )
            result["passed"] = not result["issues"]
            return result
        except Exception as exc:  # Decoder and DSP libraries expose typed native errors.
            message = str(exc) or exc.__class__.__name__
            result["analysis_error"] = message
            result["issues"].append(f"Không phân tích được âm thanh: {message}")
            return result
