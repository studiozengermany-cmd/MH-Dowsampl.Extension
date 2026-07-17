"""Lightweight audio discovery and original-file downloads."""

from __future__ import annotations

import html
import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

AUDIO_SUFFIXES = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".aiff", ".aif"}
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
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
NAME_LOCK = threading.Lock()


@dataclass(frozen=True)
class AudioAsset:
    url: str
    title: str | None = None


def validate_http_url(value: str) -> str:
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Liên kết phải bắt đầu bằng http:// hoặc https://")
    return value


def is_audio_url(value: str) -> bool:
    if not value.startswith(("http://", "https://")):
        return False
    return Path(urlparse(value).path).suffix.lower() in AUDIO_SUFFIXES


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
                and is_audio_url(str(item["url"]))
            ]
            preferred = next(
                (item for item in candidates if item.get("asset_file_type_slug") == "preview_mp3"),
                candidates[0] if candidates else None,
            )
            if preferred is not None:
                url = str(preferred["url"])
                if url not in seen:
                    samples.append(AudioAsset(url=url, title=catalogue_title(value)))
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
        if is_audio_url(url) and url not in seen:
            assets.append(AudioAsset(url=url, title=unquote(Path(urlparse(url).path).stem)))
            seen.add(url)
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


class AudioCrawler:
    def discover(self, page_url: str) -> list[AudioAsset]:
        page_url = validate_http_url(page_url)
        if is_audio_url(page_url):
            return [AudioAsset(page_url, unquote(Path(urlparse(page_url).path).stem))]

        hostname = (urlparse(page_url).hostname or "").lower()
        if hostname == "splice.com" or hostname.endswith(".splice.com"):
            return self._discover_splice(page_url)
        return self._discover_generic(page_url)

    def _get_text(self, url: str) -> str:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=45) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")

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
            raise RuntimeError("Không tìm thấy đường dẫn âm thanh công khai trên trang")
        return assets

    def _discover_generic(self, page_url: str) -> list[AudioAsset]:
        document = self._get_text(page_url)
        assets, _, _ = extract_splice_page(document)
        if not assets:
            raise RuntimeError("Không tìm thấy đường dẫn âm thanh công khai trên trang")
        return assets

    def download(self, asset: AudioAsset, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        request = Request(asset.url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=90) as response:
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            response_url = response.geturl() or asset.url
            url_suffix = Path(urlparse(response_url).path).suffix.lower()
            suffix = url_suffix if url_suffix in AUDIO_SUFFIXES else CONTENT_TYPE_SUFFIXES.get(content_type, ".bin")
            response_name = self._response_filename(response)
            if asset.title:
                filename = sanitize_filename(asset.title) + suffix
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
            try:
                with partial.open("wb") as handle:
                    while True:
                        chunk = response.read(1024 * 256)
                        if not chunk:
                            break
                        if chunk:
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
