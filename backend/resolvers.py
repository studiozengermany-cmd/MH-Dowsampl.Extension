"""Universal resolver: turn any public link into downloadable sample audio.

The engine intentionally handles only publicly reachable audio. It does not
bypass authentication, paywalls, DRM, or access controls: those streams have
no public URL to fetch, so they are simply not supported.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from urllib.parse import urlparse

from core_engine import PublicAudioError

try:
    import yt_dlp
    from yt_dlp.utils import DownloadError
except ImportError:  # pragma: no cover - optional dependency probe
    yt_dlp = None
    DownloadError = Exception

# Prefer lossless; ffmpeg transcodes from the best available audio stream.
PREFERRED_CODEC = "flac"
SOCKET_TIMEOUT = 30


def ytdlp_available() -> bool:
    """True when both yt-dlp and ffmpeg are usable on this machine."""

    return yt_dlp is not None and shutil.which("ffmpeg") is not None


def probe_entries(url: str) -> list[str]:
    """List audio item titles without downloading (used during discovery)."""

    if not ytdlp_available():
        raise PublicAudioError("Chua cai yt-dlp/ffmpeg (xem requirements.txt)")
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "socket_timeout": SOCKET_TIMEOUT,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    entries = info.get("entries") if isinstance(info, dict) else None
    if entries:
        return [
            str(entry.get("title") or entry.get("id") or "audio")
            for entry in entries
            if entry
        ]
    title = info.get("title") if isinstance(info, dict) else None
    return [str(title or "audio")]


def download_audio(url: str, folder: Path) -> list[Path]:
    """Download and extract audio via ffmpeg. Return the files produced.

    Output is written to a unique subfolder so concurrent download workers
    sharing ``folder`` never claim each other's files. The caller is expected
    to move the returned files to their final destination.
    """

    if not ytdlp_available():
        raise PublicAudioError("Chua cai yt-dlp/ffmpeg (xem requirements.txt)")
    work = folder / f".resolve-{uuid.uuid4().hex}"
    work.mkdir(parents=True, exist_ok=True)
    opts = {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": "only_download",
        "retries": 3,
        "socket_timeout": SOCKET_TIMEOUT,
        "windowsfilenames": True,
        "outtmpl": str(work / "%(title).180B [%(id)s].%(ext)s"),
        "format": "bestaudio/best",
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": PREFERRED_CODEC},
        ],
        "allow_unplayable_formats": False,  # never touch DRM-protected streams
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            ydl.download([url])
        except DownloadError as exc:
            host = urlparse(url).hostname or url
            raise PublicAudioError(f"Khong tai duoc tu {host}: {exc}") from exc
    produced = [
        path
        for path in work.glob("*")
        if path.is_file() and path.suffix.lower() != ".part"
    ]
    if not produced:
        raise PublicAudioError(
            "Khong lay duoc luong audio cong khai nao tu lien ket"
        )
    return produced


__all__ = ["ytdlp_available", "probe_entries", "download_audio", "PREFERRED_CODEC"]
