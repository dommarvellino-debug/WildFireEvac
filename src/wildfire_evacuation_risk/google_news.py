"""Fallback client for Google News RSS when GDELT is rate limited."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from .news import ARTICLE_COLUMNS, _sanitize_phrase

GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
_TIMESPAN_RE = re.compile(r"(\d+)(min|[hdwm])")
_MAX_ITEMS = 100


def build_google_query(name: str, country: str | None = None) -> str:
    """Build an English Google News query from a report name and country."""
    cleaned = _sanitize_phrase(name)
    if not cleaned:
        raise ValueError("A report name is required to search related news.")
    place = re.sub(
        r"^(?:wildfires?|forest\s+fires?|bush\s?fires?|fires?)\b"
        r"\s*(?:[-\u2013\u2014:]|\bin\b)?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).split(",")[0].strip() or cleaned

    terms = [f'"{place}"']
    country_text = _sanitize_phrase(country).split(",")[0].strip() if country else ""
    if country_text and country_text.casefold() not in place.casefold():
        terms.append(f'"{country_text}"')
    terms.append("wildfire")
    return " ".join(terms)


def _when_window(timespan: str) -> str:
    match = _TIMESPAN_RE.fullmatch(timespan)
    if not match:
        raise ValueError("Timespan must look like '24h', '7d', '2w', or '3m'.")
    value, unit = int(match.group(1)), match.group(2)
    if unit == "min":
        days = 1
    elif unit == "h":
        days = max(1, (value + 23) // 24)
    elif unit == "d":
        days = value
    elif unit == "w":
        days = value * 7
    else:
        days = value * 30
    return f"when:{days}d"


def search_google_news(
    name: str,
    country: str | None = None,
    *,
    max_records: int = 25,
    timespan: str = "7d",
    timeout: float = 30,
) -> pd.DataFrame:
    """Search Google News RSS for English headlines about a report."""
    if not isinstance(max_records, int) or not 1 <= max_records <= _MAX_ITEMS:
        raise ValueError(f"Google News max records must be from 1 to {_MAX_ITEMS}.")

    query = f"{build_google_query(name, country)} {_when_window(timespan)}"

    params = {
        "q": query,
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    }
    url = f"{GOOGLE_NEWS_RSS_URL}?{urlencode(params)}"
    request = Request(
        url,
        headers={"User-Agent": "WildFireEvac/0.1 (report news)"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            content = response.read().decode("utf-8-sig", errors="replace")
    except HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError(
                "Google News rate limit reached; wait a few minutes and try again."
            ) from None
        raise RuntimeError(f"Google News request failed with HTTP {exc.code}.") from None
    except URLError:
        raise RuntimeError("Could not connect to the Google News service.") from None
    except TimeoutError:
        raise RuntimeError("Google News request timed out.") from None

    if not content.strip():
        return pd.DataFrame(columns=ARTICLE_COLUMNS)
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise RuntimeError("Google News returned an unreadable RSS feed.") from None

    records = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        source = item.find("source")
        records.append(
            {
                "title": title,
                "url": link,
                "domain": (source.text or "").strip() if source is not None else "",
                "language": "English",
                "source_country": "",
                "seen_at": pd.to_datetime(
                    item.findtext("pubDate"), errors="coerce", utc=True
                ),
            }
        )

    if not records:
        return pd.DataFrame(columns=ARTICLE_COLUMNS)

    frame = pd.DataFrame.from_records(records)
    frame = frame.drop_duplicates(subset=["url"], keep="first")
    frame = frame.sort_values(
        "seen_at", ascending=False, na_position="last", kind="stable"
    )
    return frame.loc[:, ARTICLE_COLUMNS].head(max_records).reset_index(drop=True)
