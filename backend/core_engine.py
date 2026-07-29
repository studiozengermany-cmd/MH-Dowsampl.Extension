"""Resilient discovery and download engine for public audio resources.

The engine intentionally handles only audio URLs that are publicly reachable.
It does not bypass authentication, paywalls, DRM, or access controls.
"""

from __future__ import annotations

import html
import json
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

AUDIO_SUFFIXES = {
    ".mp3",
    ".wav",
    ".flac",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
    ".aiff",
    ".aif",
}
CONTENT_TYPE_SUFFIXES = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/vnd.wave": ".wav",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/aiff": ".aiff",
    "audio/x-aiff": ".aiff",
}
LOSSLESS_SUFFIXES = {".wav", ".flac", ".aiff", ".aif"}
FETCHED_SCRIPT = re.compile(
    r"<script\b[^>]*\bdata-sveltekit-fetched\b[^>]*>(.*?)</script>",
    flags=re.IGNORECASE | re.DOTALL,
)
TAG_AUDIO_SOURCE = re.compile(
    r"<(?:audio|source)\b[^>]*?\bsrc\s*=\s*([\"'])(.*?)\1",
    flags=re.IGNORECASE | re.DOTALL,
)
META_AUDIO = re.compile(
    r"<meta\b(?=[^>]*(?:property|name)\s*=\s*([\"'])(?:og:audio(?::url)?|twitter:player:stream)\1)"
    r"[^>]*?content\s*=\s*([\"'])(.*?)\2[^>]*>",
    flags=re.IGNORECASE | re.DOTALL,
)
JSON_AUDIO_URL = re.compile(
    r"[\"'](?:contentUrl|audioUrl|audio_url|previewUrl|preview_url)[\"']\s*:\s*"
    r"[\"']([^\"']+)[\"']",
    flags=re.IGNORECASE,
)
ABSOLUTE_URL = re.compile(r"https?://[^\s\"'<>\\]+", flags=re.IGNORECASE)
MAX_PAGES = 100
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
DOWNLOAD_CHUNK_SIZE = 256 * 1024
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
RETRYABLE_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}
NAME_LOCK = threading.Lock()


class PublicAudioError(RuntimeError):
    """Readable error raised for discovery or download failures."""


@dataclass(frozen=True)
class AudioAsset:
    url: str
    title: str | None = None
    fallback_urls: tuple[str, ...] = field(default_factory=tuple, compare=False)


def validate_http_url(value: str) -> str:
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Liên kết phải bắt đầu bằng http:// hoặc https://")
    return value


def normalized_content_type(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def audio_suffix_from_url(value: str) -> str:
    return Path(urlparse(value).path).suffix.lower()


def is_audio_url(value: str) -> bool:
    if not value.startswith(("http://", "https://")):
        return False
    return audio_suffix_from_url(value) in AUDIO_SUFFIXES


def is_audio_content_type(value: str | None) -> bool:
    return normalized_content_type(value).startswith("audio/")


def catalogue_title(item: object) -> str | None:
    if not isinstance(item, dict):
        return None
    candidates = [item, *(value for value in item.values() if isinstance(value, dict))]
    for candidate in candidates:
        for field_name in ("name", "title", "display_name", "displayName"):
            value = candidate.get(field_name)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def candidate_quality(item: dict[str, object]) -> tuple[int, int]:
    url = str(item.get("url") or "")
    suffix = audio_suffix_from_url(url)
    kind = str(
        item.get("asset_file_type_slug")
        or item.get("file_type")
        or item.get("format")
        or ""
    ).lower()

    if suffix in LOSSLESS_SUFFIXES or any(token in kind for token in ("wav", "flac", "aiff", "lossless")):
        return (100, 1)
    if "original" in kind or "source" in kind:
        return (90, 1)
    if suffix in {".m4a", ".aac", ".ogg", ".opus"}:
        return (60, 1)
    if suffix == ".mp3" and "preview" not in kind:
        return (50, 1)
    if "preview" in kind or suffix == ".mp3":
        return (20, 1)
    return (0, 0)


def extract_splice_samples(payload: object) -> list[AudioAsset]:
    """Extract one best public candidate per catalogue item.

    Lossless/original URLs are preferred. Lower-quality public previews remain as
    fallbacks so an expired or denied original URL does not kill that sample.
    """

    samples: list[AudioAsset] = []
    seen_primary: set[str] = set()

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
                and is_audio_url(str(item["url"]))
            ]
            candidates.sort(key=candidate_quality, reverse=True)
            if candidates:
                primary = str(candidates[0]["url"])
                fallbacks = tuple(
                    str(item["url"])
                    for item in candidates[1:]
                    if str(item["url"]) != primary
                )
                if primary not in seen_primary:
                    samples.append(
                        AudioAsset(
                            url=primary,
                            title=catalogue_title(value),
                            fallback_urls=fallbacks,
                        )
                    )
                    seen_primary.add(primary)

        for child in value.values():
            visit(child)

    visit(payload)
    return samples


def _decode_embedded_url(value: str) -> str:
    return html.unescape(value).replace(r"\/", "/").replace(r"\u0026", "&").strip()


def _add_audio_candidate(
    assets: list[AudioAsset],
    seen: set[str],
    raw_url: str,
    *,
    base_url: str | None = None,
    title: str | None = None,
) -> None:
    decoded = _decode_embedded_url(raw_url).rstrip(".,);]")
    absolute = urljoin(base_url, decoded) if base_url else decoded
    if not absolute.startswith(("http://", "https://")) or not is_audio_url(absolute):
        return
    if absolute in seen:
        return
    inferred_title = title or unquote(Path(urlparse(absolute).path).stem)
    assets.append(AudioAsset(url=absolute, title=inferred_title or None))
    seen.add(absolute)


def extract_generic_audio(document: str, base_url: str | None = None) -> list[AudioAsset]:
    """Extract public audio URLs from common HTML and embedded JSON patterns."""

    assets: list[AudioAsset] = []
    seen: set[str] = set()
    normalized = html.unescape(document).replace(r"\/", "/").replace(r"\u0026", "&")

    for match in TAG_AUDIO_SOURCE.finditer(normalized):
        _add_audio_candidate(assets, seen, match.group(2), base_url=base_url)
    for match in META_AUDIO.finditer(normalized):
        _add_audio_candidate(assets, seen, match.group(3), base_url=base_url)
    for match in JSON_AUDIO_URL.finditer(normalized):
        _add_audio_candidate(assets, seen, match.group(1), base_url=base_url)
    for raw_url in ABSOLUTE_URL.findall(normalized):
        _add_audio_candidate(assets, seen, raw_url, base_url=base_url)

    return assets


def extract_splice_page(
    document: str,
    base_url: str | None = None,
) -> tuple[list[AudioAsset], int, int]:
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

    if not assets:
        add(extract_generic_audio(document, base_url=base_url))
    return assets, current_page, total_pages


def sanitize_filename(value: str, fallback: str = "sample") -> str:
    value = unquote(value).replace("/", "_").replace("\\", "_")
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).strip(" ._")
    return (value[:180].rstrip(" .") or fallback)


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


def readable_network_error(exc: BaseException, url: str) -> PublicAudioError:
    host = urlparse(url).hostname or url
    if isinstance(exc, HTTPError):
        if exc.code in {401, 403}:
            return PublicAudioError(f"Nguồn {host} từ chối truy cập công khai (HTTP {exc.code})")
        if exc.code == 404:
            return PublicAudioError(f"File không còn tồn tại trên {host} (HTTP 404)")
        if exc.code == 429:
            return PublicAudioError(f"Nguồn {host} đang giới hạn lượt tải (HTTP 429)")
        return PublicAudioError(f"Nguồn {host} trả về HTTP {exc.code}")
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return PublicAudioError(f"Kết nối tới {host} bị quá thời gian")
    if isinstance(exc, URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, socket.timeout):
            return PublicAudioError(f"Kết nối tới {host} bị quá thời gian")
        return PublicAudioError(f"Không kết nối được tới {host}: {reason or exc}")
    return PublicAudioError(str(exc))


class AudioCrawler:
    def __init__(self, *, retries: int = 3, retry_delay: float = 0.6) -> None:
        self.retries = max(1, retries)
        self.retry_delay = max(0.0, retry_delay)

    @staticmethod
    def _headers(*, accept_audio: bool = False) -> dict[str, str]:
        return {
            "User-Agent": USER_AGENT,
            "Accept": (
                "audio/*,application/octet-stream;q=0.9,*/*;q=0.1"
                if accept_audio
                else "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.5"
            ),
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.7,en;q=0.6",
            "Cache-Control": "no-cache",
        }

    def _open(self, url: str, *, timeout: float, accept_audio: bool = False):
        last_error: BaseException | None = None
        for attempt in range(self.retries):
            request = Request(url, headers=self._headers(accept_audio=accept_audio))
            try:
                return urlopen(request, timeout=timeout)
            except HTTPError as exc:
                last_error = exc
                if exc.code not in RETRYABLE_HTTP_CODES or attempt + 1 >= self.retries:
                    break
            except (URLError, socket.timeout, TimeoutError) as exc:
                last_error = exc
                if attempt + 1 >= self.retries:
                    break
            time.sleep(self.retry_delay * (2**attempt))
        assert last_error is not None
        raise readable_network_error(last_error, url) from last_error

    def _read_document(self, response: BinaryIO) -> str:
        raw = response.read(MAX_DOCUMENT_BYTES + 1)
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise PublicAudioError("Trang nguồn quá lớn để quét an toàn")
        headers = getattr(response, "headers")
        charset = headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")

    def _fetch_page_or_direct_asset(self, url: str) -> tuple[str | None, AudioAsset | None]:
        response = self._open(url, timeout=45, accept_audio=False)
        with response:
            response_url = response.geturl() or url
            content_type = normalized_content_type(response.headers.get("Content-Type"))
            if is_audio_content_type(content_type):
                title = unquote(Path(urlparse(response_url).path).stem) or "sample"
                return None, AudioAsset(response_url, title)
            return self._read_document(response), None

    def discover(self, page_url: str) -> list[AudioAsset]:
        page_url = validate_http_url(page_url)
        if is_audio_url(page_url):
            return [AudioAsset(page_url, unquote(Path(urlparse(page_url).path).stem))]

        hostname = (urlparse(page_url).hostname or "").lower()
        if hostname == "splice.com" or hostname.endswith(".splice.com"):
            return self._discover_splice(page_url)
        return self._discover_generic(page_url)

    @staticmethod
    def _with_page(url: str, page: int) -> str:
        parsed = urlparse(url)
        query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "page"]
        query.append(("page", str(page)))
        return parsed._replace(query=urlencode(query)).geturl()

    def _discover_splice(self, page_url: str) -> list[AudioAsset]:
        first_document, direct = self._fetch_page_or_direct_asset(page_url)
        if direct is not None:
            return [direct]
        assert first_document is not None
        first_assets, current_page, total_pages = extract_splice_page(
            first_document,
            base_url=page_url,
        )
        if total_pages > MAX_PAGES:
            raise PublicAudioError(
                f"Trang có {total_pages} phần; giới hạn an toàn là {MAX_PAGES}"
            )

        assets = list(first_assets)
        seen = {item.url for item in assets}
        for page in range(1, total_pages + 1):
            if page == current_page:
                continue
            page_address = self._with_page(page_url, page)
            document, direct = self._fetch_page_or_direct_asset(page_address)
            page_assets = [direct] if direct is not None else extract_splice_page(
                document or "",
                base_url=page_address,
            )[0]
            for item in page_assets:
                if item is not None and item.url not in seen:
                    assets.append(item)
                    seen.add(item.url)
        if not assets:
            raise PublicAudioError("Không tìm thấy đường dẫn âm thanh công khai trên trang")
        return assets

    def _discover_generic(self, page_url: str) -> list[AudioAsset]:
        document, direct = self._fetch_page_or_direct_asset(page_url)
        if direct is not None:
            return [direct]
        assets = extract_generic_audio(document or "", base_url=page_url)
        if not assets:
            raise PublicAudioError("Không tìm thấy đường dẫn âm thanh công khai trên trang")
        return assets

    def _download_one(self, url: str, title: str | None, folder: Path) -> Path:
        response = self._open(url, timeout=90, accept_audio=True)
        with response:
            content_type = normalized_content_type(response.headers.get("Content-Type"))
            response_url = response.geturl() or url
            url_suffix = audio_suffix_from_url(response_url)
            if not is_audio_content_type(content_type) and url_suffix not in AUDIO_SUFFIXES:
                raise PublicAudioError(
                    f"Nguồn trả về {content_type or 'dữ liệu không xác định'}, không phải audio"
                )

            suffix = (
                url_suffix
                if url_suffix in AUDIO_SUFFIXES
                else CONTENT_TYPE_SUFFIXES.get(content_type, ".bin")
            )
            response_name = self._response_filename(response)
            if title:
                title_path = Path(sanitize_filename(title))
                filename = title_path.name
                if title_path.suffix.lower() not in AUDIO_SUFFIXES:
                    filename += suffix
            elif response_name:
                filename = sanitize_filename(response_name)
                if Path(filename).suffix.lower() not in AUDIO_SUFFIXES:
                    filename += suffix
            else:
                filename = "sample" + suffix

            with NAME_LOCK:
                destination = unique_destination(folder, filename)
                partial = destination.with_suffix(destination.suffix + ".part")
                partial.touch(exist_ok=False)

            written = 0
            try:
                with partial.open("wb") as handle:
                    while True:
                        chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                        if not chunk:
                            break
                        handle.write(chunk)
                        written += len(chunk)
                if written <= 0:
                    raise PublicAudioError("Nguồn trả về file rỗng")
                partial.replace(destination)
            except Exception:
                partial.unlink(missing_ok=True)
                raise
            return destination

    def download(self, asset: AudioAsset, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []
        candidates = (asset.url, *asset.fallback_urls)
        for index, url in enumerate(candidates):
            try:
                return self._download_one(url, asset.title, folder)
            except Exception as exc:
                readable = exc if isinstance(exc, PublicAudioError) else readable_network_error(exc, url)
                errors.append(str(readable))
                if index + 1 >= len(candidates):
                    break
        raise PublicAudioError("; fallback thất bại: ".join(errors))

    @staticmethod
    def _response_filename(response: object) -> str | None:
        headers = getattr(response, "headers")
        disposition = headers.get("Content-Disposition", "")
        utf8 = re.search(r"filename\*=UTF-8''([^;]+)", disposition, flags=re.IGNORECASE)
        plain = re.search(r'filename="?([^";]+)', disposition, flags=re.IGNORECASE)
        if utf8:
            return unquote(utf8.group(1))
        if plain:
            return plain.group(1)
        response_url = getattr(response, "geturl")()
        path_name = unquote(Path(urlparse(response_url).path).name)
        return path_name or None
