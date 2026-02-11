"""Async HTML downloader and crypto address extractor."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

import aiohttp
from bs4 import BeautifulSoup
import pandas as pd

from utils import chain_candidates, detect_chain


class ParseError(Exception):
    """Raised when page downloading/parsing fails."""


@dataclass(frozen=True)
class AddressHit:
    """Single parsed address with context."""

    source_id: str
    address: str
    chain: str
    context_snippet: str


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ParseError("Невалидный URL. Нужна ссылка в формате http(s)://...")


def _clean_text_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    # Remove non-content tags to reduce noise.
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator=" ")
    return re.sub(r"\s+", " ", text).strip()


def _snippet(text: str, start: int, end: int, window: int = 50) -> str:
    left = max(0, start - window)
    right = min(len(text), end + window)
    return text[left:right].strip()


def _request_headers() -> dict[str, str]:
    """Build browser-like headers to reduce 403 blocks on strict websites."""

    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru,en-US;q=0.9,en;q=0.8",
    }


def _fallback_url(url: str) -> str | None:
    """Return alternative URL for websites that frequently block bot-like requests.

    For Reddit, we can often read content via old.reddit.com when www.reddit.com
    returns HTTP 403.
    """

    parsed = urlparse(url)
    if parsed.netloc.lower() != "www.reddit.com":
        return None

    return urlunparse(parsed._replace(netloc="old.reddit.com"))


async def _download_once(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url, allow_redirects=True, headers=_request_headers()) as response:
        response.raise_for_status()
        return await response.text(errors="ignore")


async def fetch_html(url: str, timeout_sec: int = 15) -> str:
    """Download HTML asynchronously with robust network error handling."""

    _validate_url(url)
    timeout = aiohttp.ClientTimeout(total=timeout_sec)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                return await _download_once(session, url)
            except aiohttp.ClientResponseError as exc:
                # Retry known strict websites through alternate route.
                alt_url = _fallback_url(url)
                if exc.status == 403 and alt_url:
                    return await _download_once(session, alt_url)
                raise
    except aiohttp.InvalidURL as exc:
        raise ParseError("URL содержит ошибку.") from exc
    except aiohttp.ClientResponseError as exc:
        raise ParseError(f"HTTP ошибка {exc.status} при загрузке страницы.") from exc
    except aiohttp.ClientConnectorError as exc:
        raise ParseError("Не удалось подключиться к хосту.") from exc
    except aiohttp.ClientError as exc:
        raise ParseError(f"Сетевая ошибка: {exc}") from exc
    except TimeoutError as exc:
        raise ParseError("Таймаут при загрузке страницы.") from exc


async def parse_crypto_addresses(url: str) -> pd.DataFrame:
    """Fetch page by URL and extract deduplicated crypto addresses.

    Returns:
        pandas.DataFrame with columns:
        source_id, address, chain, context_snippet
    """

    html = await fetch_html(url)
    text = _clean_text_from_html(html)

    hits: list[AddressHit] = []
    seen: set[tuple[str, str]] = set()

    for chain_pattern in chain_candidates():
        for match in chain_pattern.regex.finditer(text):
            address = match.group(0)
            chain = detect_chain(address)
            key = (address, chain)
            if key in seen:
                continue

            seen.add(key)
            hits.append(
                AddressHit(
                    source_id=url,
                    address=address,
                    chain=chain,
                    context_snippet=_snippet(text, match.start(), match.end()),
                )
            )

    rows: list[dict[str, Any]] = [hit.__dict__ for hit in hits]
    df = pd.DataFrame(rows, columns=["source_id", "address", "chain", "context_snippet"])

    if not df.empty:
        df = df.sort_values(["chain", "address"]).reset_index(drop=True)

    return df
