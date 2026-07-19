"""Discover original audio assets and preserve their real codecs on download."""

from __future__ import annotations

import html
import json
import re
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, unquote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

AUDIO_SUFFIXES = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".aiff", ".aif"}
FORMAT_SUFFIXES = {
    "wav": ".wav",
    "mp3": ".mp3",
    "flac": ".flac",
    "ogg": ".ogg",
    "m4a": ".m4a",
    "aiff": ".aiff",
}
CONTENT_TYPE_SUFFIXES = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/flac": ".flac",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/aiff": ".aiff",
    "audio/x-aiff": ".aiff",
}
FETCHED_SCRIPT = re.compile(
    r"<script\b[^>]*\bdata-sveltekit-fetched\b[^>]*>(.*?)</script>",
    flags=re.IGNORECASE | re.DOTALL,
)
MAX_PAGES = 100
MAX_PAGE_BYTES = 10 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 256 * 1024
BLOCKED_CONTENT_TYPES = {
    "application/json",
    "application/xml",
    "text/html",
    "text/xml",
}
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
NAME_LOCK = threading.Lock()


@dataclass(frozen=True)
class AudioAsset:
    url: str
    title: str | None = None
    bpm: int | None = None
    musical_key: str | None = None
    declared_format: str | None = None
    metadata_source: str = "page"


def validate_http_url(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 4096 or any(character.isspace() for character in value):
        raise ValueError("Liên kết HTTP không hợp lệ")
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Liên kết phải bắt đầu bằng http:// hoặc https://")
    if parsed.username or parsed.password:
        raise ValueError("Liên kết không được chứa tên đăng nhập hoặc mật khẩu")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("Cổng trong liên kết không hợp lệ") from exc
    return value


def is_audio_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return False
    return Path(parsed.path).suffix.lower() in AUDIO_SUFFIXES


def normalize_audio_format(value: object) -> str | None:
    text = str(value or "").strip().lower().lstrip(".")
    aliases = {
        "wave": "wav",
        "x-wav": "wav",
        "mpeg": "mp3",
        "audio/mpeg": "mp3",
        "mp4": "m4a",
        "aac": "m4a",
        "aif": "aiff",
        "aifc": "aiff",
        "vorbis": "ogg",
        "opus": "ogg",
    }
    text = aliases.get(text, text)
    return text if text in FORMAT_SUFFIXES else None


def declared_file_format(item: dict[str, object]) -> str | None:
    for field in (
        "asset_file_type_slug",
        "file_type",
        "fileType",
        "format",
        "extension",
        "mime_type",
        "mimeType",
    ):
        raw = str(item.get(field) or "").lower()
        if "preview" in raw:
            continue
        candidate = raw.split("/", 1)[-1]
        normalized = normalize_audio_format(candidate)
        if normalized:
            return normalized
    url = str(item.get("url") or "")
    return normalize_audio_format(Path(urlparse(url).path).suffix)


def is_preview_asset(item: dict[str, object]) -> bool:
    labels = " ".join(
        str(item.get(field) or "")
        for field in (
            "asset_file_type_slug",
            "file_type",
            "fileType",
            "kind",
            "role",
            "name",
            "url",
        )
    ).lower()
    return (
        item.get("preview") is True
        or item.get("is_preview") is True
        or item.get("isPreview") is True
        or "preview" in labels
    )


def original_candidate_rank(item: dict[str, object]) -> tuple[int, int]:
    audio_format = declared_file_format(item)
    format_rank = {
        "wav": 0,
        "aiff": 1,
        "flac": 2,
        "ogg": 3,
        "m4a": 4,
        "mp3": 5,
    }.get(audio_format, 50)
    label = str(item.get("asset_file_type_slug") or item.get("role") or "").lower()
    original_rank = 0 if any(token in label for token in ("original", "full", "source")) else 1
    return format_rank, original_rank


def looks_like_preview_url(value: str) -> bool:
    lowered = unquote(value).lower()
    return any(token in lowered for token in ("preview_mp3", "/preview/", "-preview.", "_preview."))


def detect_audio_format(payload: bytes) -> str | None:
    """Identify a supported codec from container bytes, never from a filename."""

    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WAVE":
        return "wav"
    if payload.startswith(b"fLaC"):
        return "flac"
    if payload.startswith(b"OggS"):
        return "ogg"
    if len(payload) >= 12 and payload[4:8] == b"ftyp":
        return "m4a"
    if len(payload) >= 12 and payload[:4] == b"FORM" and payload[8:12] in {b"AIFF", b"AIFC"}:
        return "aiff"
    if payload.startswith(b"ID3"):
        return "mp3"
    scan = payload[:4096]
    for index in range(max(0, len(scan) - 1)):
        if scan[index] == 0xFF and scan[index + 1] & 0xE0 == 0xE0:
            return "mp3"
    return None


def catalogue_title(item: object) -> str | None:
    if not isinstance(item, dict):
        return None
    candidates = [item, *(value for value in item.values() if isinstance(value, dict))]
    for candidate in candidates:
        for field in ("name", "title", "display_name", "displayName"):
            value = candidate.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def catalogue_bpm(item: object) -> int | None:
    """Read a plausible catalogue tempo without mistaking unrelated numbers for BPM."""

    if not isinstance(item, dict):
        return None
    candidates = [item, *(value for value in item.values() if isinstance(value, dict))]
    for candidate in candidates:
        for field in ("bpm", "tempo"):
            value = candidate.get(field)
            match = re.search(r"\d+(?:\.\d+)?", str(value)) if value is not None else None
            if match:
                bpm = int(round(float(match.group(0))))
                if 20 <= bpm <= 400:
                    return bpm
    return None


def _musical_key_text(value: object) -> str | None:
    if isinstance(value, dict):
        for field in ("name", "title", "display_name", "displayName", "value"):
            nested = value.get(field)
            if isinstance(nested, str) and nested.strip():
                value = nested
                break
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"\s+", " ", value.strip()).replace("♯", "#").replace("♭", "b")
    match = re.fullmatch(
        r"([A-Ga-g])\s*([#b]?)\s*(?:(maj(?:or)?|min(?:or)?|m))?",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    root = match.group(1).upper() + match.group(2)
    mode = (match.group(3) or "").lower()
    if mode.startswith("maj"):
        return f"{root} Major"
    if mode == "m" or mode.startswith("min"):
        return f"{root} Minor"
    return root


def catalogue_musical_key(item: object) -> str | None:
    """Read the musical key exposed by a catalogue item, when present."""

    if not isinstance(item, dict):
        return None
    candidates = [item, *(value for value in item.values() if isinstance(value, dict))]
    for candidate in candidates:
        for field in (
            "musical_key",
            "musicalKey",
            "key_name",
            "keyName",
            "tonality",
            "key",
        ):
            musical_key = _musical_key_text(candidate.get(field))
            if musical_key:
                mode = str(candidate.get("mode") or "").strip().lower()
                if " " not in musical_key and mode in {"major", "maj", "minor", "min"}:
                    musical_key += " Minor" if mode.startswith("min") else " Major"
                return musical_key
    return None


def extract_splice_samples(payload: object) -> list[AudioAsset]:
    samples: list[AudioAsset] = []
    seen: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, list):
            for child in value:
                visit(child)
            return
        if not isinstance(value, dict):
            return

        files = value.get("files")
        if isinstance(files, list):
            candidates = [
                item
                for item in files
                if isinstance(item, dict)
                and isinstance(item.get("url"), str)
                and (is_audio_url(str(item["url"])) or declared_file_format(item) is not None)
                and not is_preview_asset(item)
            ]
            preferred = min(candidates, key=original_candidate_rank) if candidates else None
            if preferred is not None:
                url = str(preferred["url"])
                if url not in seen:
                    samples.append(
                        AudioAsset(
                            url=url,
                            title=catalogue_title(value),
                            bpm=catalogue_bpm(value),
                            musical_key=catalogue_musical_key(value),
                            declared_format=declared_file_format(preferred),
                            metadata_source="catalogue",
                        )
                    )
                    seen.add(url)

        for child in value.values():
            visit(child)

    visit(payload)
    return samples


def extract_splice_page(document: str) -> tuple[list[AudioAsset], int, int]:
    assets: list[AudioAsset] = []
    seen: set[str] = set()
    current_page = 1
    total_pages = 1

    def add(items: list[AudioAsset]) -> None:
        for item in items:
            if item.url not in seen:
                assets.append(item)
                seen.add(item.url)

    def read_pagination(value: object) -> None:
        nonlocal current_page, total_pages
        if isinstance(value, list):
            for child in value:
                read_pagination(child)
            return
        if not isinstance(value, dict):
            return
        metadata = value.get("pagination_metadata")
        if isinstance(metadata, dict):
            try:
                current_page = max(1, int(metadata.get("currentPage") or current_page))
                total_pages = max(current_page, int(metadata.get("totalPages") or total_pages))
            except (TypeError, ValueError):
                pass
        for child in value.values():
            read_pagination(child)

    for match in FETCHED_SCRIPT.finditer(document):
        try:
            envelope = json.loads(html.unescape(match.group(1)).strip())
            body = envelope.get("body") if isinstance(envelope, dict) else None
            payload = json.loads(body) if isinstance(body, str) else body
        except (json.JSONDecodeError, TypeError):
            continue
        add(extract_splice_samples(payload))
        read_pagination(payload)

    if assets:
        return assets, current_page, total_pages

    normalized = html.unescape(document).replace(r"\/", "/").replace(r"\u0026", "&")
    for raw_url in re.findall(r"https?://[^\s\"'<>\\]+", normalized):
        url = raw_url.rstrip(".,);]")
        if is_audio_url(url) and not looks_like_preview_url(url) and url not in seen:
            assets.append(
                AudioAsset(
                    url=url,
                    title=unquote(Path(urlparse(url).path).stem),
                    declared_format=normalize_audio_format(Path(urlparse(url).path).suffix),
                    metadata_source="embedded_url",
                )
            )
            seen.add(url)
    return assets, current_page, total_pages


def sanitize_filename(value: str, fallback: str = "sample") -> str:
    value = unicodedata.normalize("NFKC", unquote(value)).replace("/", "_").replace("\\", "_")
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).strip(" ._")
    value = value[:180].rstrip(" .") or fallback
    if value.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        value = f"_{value}"
    return value


def content_length(headers: object) -> int | None:
    raw = getattr(headers, "get")("Content-Length", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def read_limited(response: object, limit: int, error_message: str) -> bytes:
    expected = content_length(getattr(response, "headers"))
    if expected is not None and expected > limit:
        raise RuntimeError(error_message)
    payload = getattr(response, "read")(limit + 1)
    if len(payload) > limit:
        raise RuntimeError(error_message)
    return payload


def unique_destination(folder: Path, filename: str) -> Path:
    candidate = folder / filename
    if not candidate.exists() and not candidate.with_suffix(candidate.suffix + ".part").exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for index in range(2, 10_000):
        candidate = folder / f"{stem} ({index}){suffix}"
        if not candidate.exists() and not candidate.with_suffix(candidate.suffix + ".part").exists():
            return candidate
    raise RuntimeError("Không thể tạo tên file không trùng")


class AudioCrawler:
    def __init__(self, url_validator: Callable[[str], str] | None = None) -> None:
        self.url_validator = url_validator

    def _open(self, request: Request, timeout: int):
        if self.url_validator is None:
            return urlopen(request, timeout=timeout)

        validator = self.url_validator

        class ValidatingRedirectHandler(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
                validator(newurl)
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        validator(request.full_url)
        return build_opener(ValidatingRedirectHandler()).open(request, timeout=timeout)

    def discover(self, page_url: str) -> list[AudioAsset]:
        page_url = validate_http_url(page_url)
        if is_audio_url(page_url):
            if looks_like_preview_url(page_url):
                raise RuntimeError("Liên kết chỉ trỏ tới MP3 nghe thử, không phải file gốc")
            return [
                AudioAsset(
                    page_url,
                    unquote(Path(urlparse(page_url).path).stem),
                    declared_format=normalize_audio_format(Path(urlparse(page_url).path).suffix),
                    metadata_source="direct_url",
                )
            ]

        hostname = (urlparse(page_url).hostname or "").lower()
        if hostname == "splice.com" or hostname.endswith(".splice.com"):
            return self._discover_splice(page_url)
        return self._discover_generic(page_url)

    def _get_text(self, url: str) -> str:
        url = validate_http_url(url)
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with self._open(request, timeout=45) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            payload = read_limited(
                response,
                MAX_PAGE_BYTES,
                "Trang nguồn quá lớn để xử lý an toàn",
            )
            return payload.decode(charset, errors="replace")

    @staticmethod
    def _with_page(url: str, page: int) -> str:
        parsed = urlparse(url)
        query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "page"]
        query.append(("page", str(page)))
        return parsed._replace(query=urlencode(query)).geturl()

    def _discover_splice(self, page_url: str) -> list[AudioAsset]:
        first_document = self._get_text(page_url)
        first_assets, current_page, total_pages = extract_splice_page(first_document)
        if total_pages > MAX_PAGES:
            raise RuntimeError(f"Trang có {total_pages} phần; giới hạn an toàn là {MAX_PAGES}")

        assets = list(first_assets)
        seen = {item.url for item in assets}
        for page in range(1, total_pages + 1):
            if page == current_page:
                continue
            document = self._get_text(self._with_page(page_url, page))
            page_assets, _, _ = extract_splice_page(document)
            for item in page_assets:
                if item.url not in seen:
                    assets.append(item)
                    seen.add(item.url)
        if not assets:
            combined = first_document.lower()
            if "preview_mp3" in combined or "preview.mp3" in combined:
                raise RuntimeError(
                    "Không truy cập được file WAV gốc bằng phiên đăng nhập hiện tại."
                )
            raise RuntimeError("Không tìm thấy đường dẫn file âm thanh gốc trên trang")
        return assets

    def _discover_generic(self, page_url: str) -> list[AudioAsset]:
        document = self._get_text(page_url)
        assets, _, _ = extract_splice_page(document)
        if not assets:
            if "preview_mp3" in document.lower() or "preview.mp3" in document.lower():
                raise RuntimeError(
                    "Không truy cập được file WAV gốc bằng phiên đăng nhập hiện tại."
                )
            raise RuntimeError("Không tìm thấy đường dẫn file âm thanh gốc trên trang")
        return assets

    def download(self, asset: AudioAsset, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        asset_url = validate_http_url(asset.url)
        request = Request(asset_url, headers={"User-Agent": USER_AGENT})
        with self._open(request, timeout=90) as response:
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if content_type in BLOCKED_CONTENT_TYPES:
                raise RuntimeError("Máy chủ trả về trang web thay vì file âm thanh")
            expected = content_length(response.headers)
            if expected is not None and expected > MAX_DOWNLOAD_BYTES:
                raise RuntimeError("File vượt quá giới hạn 512 MB")
            response_url = validate_http_url(response.geturl() or asset_url)
            first_chunk = response.read(DOWNLOAD_CHUNK_BYTES)
            actual_format = detect_audio_format(first_chunk)
            if actual_format is None:
                raise RuntimeError("Không nhận dạng được định dạng âm thanh thật của file")
            declared_format = asset.declared_format or normalize_audio_format(
                Path(urlparse(response_url).path).suffix
            ) or normalize_audio_format(content_type.split("/", 1)[-1])
            if declared_format == "wav" and actual_format != "wav":
                raise RuntimeError(
                    f"File được khai báo WAV nhưng dữ liệu thật là {actual_format.upper()}; đã từ chối"
                )
            suffix = FORMAT_SUFFIXES[actual_format]
            response_name = self._response_filename(response)
            if asset.title:
                title = Path(asset.title).stem if Path(asset.title).suffix.lower() in AUDIO_SUFFIXES else asset.title
                filename = sanitize_filename(title) + suffix
            elif response_name:
                filename = sanitize_filename(Path(response_name).stem) + suffix
            else:
                filename = "sample" + suffix
            with NAME_LOCK:
                destination = unique_destination(folder, filename)
                partial = destination.with_suffix(destination.suffix + ".part")
                partial.touch(exist_ok=False)
            try:
                downloaded_bytes = len(first_chunk)
                with partial.open("wb") as handle:
                    handle.write(first_chunk)
                    while True:
                        chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                        if not chunk:
                            break
                        downloaded_bytes += len(chunk)
                        if downloaded_bytes > MAX_DOWNLOAD_BYTES:
                            raise RuntimeError("File vượt quá giới hạn 512 MB")
                        handle.write(chunk)
                partial.replace(destination)
            except Exception:
                partial.unlink(missing_ok=True)
                raise
            return destination

    @staticmethod
    def _response_filename(response: object) -> str | None:
        headers = getattr(response, "headers")
        disposition = response.headers.get("Content-Disposition", "")
        utf8 = re.search(r"filename\*=UTF-8''([^;]+)", disposition, flags=re.IGNORECASE)
        plain = re.search(r'filename="?([^";]+)', disposition, flags=re.IGNORECASE)
        if utf8:
            return unquote(utf8.group(1))
        if plain:
            return plain.group(1)
        response_url = getattr(response, "geturl")()
        path_name = unquote(Path(urlparse(response_url).path).name)
        return path_name or None
