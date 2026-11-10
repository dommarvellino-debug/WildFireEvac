"""Backend-only client for the GDELT 2.0 DOC news search API."""

from __future__ import annotations

import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
ARTICLE_COLUMNS = ("title", "url", "domain", "language", "source_country", "seen_at")
MAX_RECORDS = 250
MIN_REQUEST_INTERVAL_SECONDS = 5.0
_SORT_MODES = {"DateDesc", "DateAsc", "ToneDesc", "ToneAsc", "HybridRel"}
_TIMESPAN_RE = re.compile(r"\d+(?:min|[hdwm])")
_CONTROL_RE = re.compile(r'["\\\x00-\x1f]')
_WHITESPACE_RE = re.compile(r"\s+")
_PREFIX_RE = re.compile(
    r"^(?:wildfires?|forest\s+fires?|bush\s?fires?|fires?)\b"
    r"\s*(?:[-\u2013\u2014:]|\bin\b)?\s*",
    re.IGNORECASE,
)

_last_request_at: float | None = None


def _sanitize_phrase(value) -> str:
    text = _CONTROL_RE.sub(" ", str(value or ""))
    return _WHITESPACE_RE.sub(" ", text).strip()[:120]


def build_report_query(name: str, country: str | None = None) -> str:
    """Build an English-language GDELT query from a report name and country."""
    cleaned = _sanitize_phrase(name)
    if not cleaned:
        raise ValueError("A report name is required to search related news.")
    place = _PREFIX_RE.sub("", cleaned).split(",")[0].strip() or cleaned
    terms = [f'"{place}"']
    country_text = _sanitize_phrase(country).split(",")[0].strip() if country else ""
    if country_text and country_text.casefold() not in place.casefold():
        terms.append(f'"{country_text}"')
    terms.append("sourcelang:english")
    return " ".join(terms)


def _throttle(min_interval: float) -> None:
    """Pace calls so GDELT's one-request-every-five-seconds rule is respected."""
    global _last_request_at
    now = time.monotonic()
    if min_interval > 0 and _last_request_at is not None:
        wait = min_interval - (now - _last_request_at)
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
    _last_request_at = now


def search_news(
    query: str,
    *,
    max_records: int = 25,
    timespan: str = "7d",
    sort: str = "DateDesc",
    timeout: float = 30,
    min_interval: float | None = None,
) -> pd.DataFrame:
    """Search world news through GDELT and return recent matching articles."""
    text = _sanitize_phrase(query)
    if not text:
        raise ValueError("A news search query is required.")
    if not isinstance(max_records, int) or not 1 <= max_records <= MAX_RECORDS:
        raise ValueError("GDELT max records must be an integer from 1 to 250.")
    if not isinstance(timespan, str) or not _TIMESPAN_RE.fullmatch(timespan):
        raise ValueError("GDELT timespan must look like '24h', '7d', '2w', or '3m'.")
    if sort not in _SORT_MODES:
        raise ValueError(f"Unsupported GDELT sort mode: {sort!r}")

    _throttle(MIN_REQUEST_INTERVAL_SECONDS if min_interval is None else min_interval)

    params = {
        "query": text,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(max_records),
        "timespan": timespan,
        "sort": sort,
    }
    url = f"{GDELT_DOC_URL}?{urlencode(params)}"
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
                "GDELT rate limit reached; wait a few minutes and try again."
            ) from None
        raise RuntimeError(f"GDELT request failed with HTTP {exc.code}.") from None
    except URLError:
        raise RuntimeError("Could not connect to the GDELT service.") from None
    except TimeoutError:
        raise RuntimeError("GDELT request timed out.") from None

    content = content.strip()
    if not content:
        return pd.DataFrame(columns=ARTICLE_COLUMNS)
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        # GDELT reports rate limits and bad queries as plain text with HTTP 200.
        snippet = _WHITESPACE_RE.sub(" ", content)[:160]
        raise RuntimeError(f"GDELT returned a non-JSON response: {snippet}") from None

    articles = payload.get("articles") if isinstance(payload, dict) else None
    if not isinstance(articles, list) or not articles:
        return pd.DataFrame(columns=ARTICLE_COLUMNS)

    records = []
    for article in articles:
        if not isinstance(article, dict):
            continue
        url_value = str(article.get("url") or "").strip()
        title = _WHITESPACE_RE.sub(" ", str(article.get("title") or "")).strip()
        if not url_value and not title:
            continue
        records.append(
            {
                "title": title,
                "url": url_value,
                "domain": str(article.get("domain") or "").strip(),
                "language": str(article.get("language") or "").strip(),
                "source_country": str(article.get("sourcecountry") or "").strip(),
                "seen_at": pd.to_datetime(
                    article.get("seendate"),
                    format="%Y%m%dT%H%M%SZ",
                    errors="coerce",
                    utc=True,
                ),
            }
        )

    if not records:
        return pd.DataFrame(columns=ARTICLE_COLUMNS)

    frame = pd.DataFrame.from_records(records)
    frame = frame[frame["url"] != ""]
    frame = frame.drop_duplicates(subset=["url"], keep="first")
    frame = frame.sort_values(
        "seen_at", ascending=False, na_position="last", kind="stable"
    )
    return frame.loc[:, ARTICLE_COLUMNS].reset_index(drop=True)
