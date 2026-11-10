"""Backend-only client for GDACS wildfire event reports."""

from __future__ import annotations

import html
import json
import math
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

GDACS_SEARCH_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
WILDFIRE_EVENT_TYPE = "WF"
ALERT_LEVELS = ("green", "orange", "red")

REPORT_COLUMNS = (
    "event_id",
    "event_type",
    "name",
    "description",
    "alert_level",
    "alert_score",
    "country",
    "iso3",
    "latitude",
    "longitude",
    "from_date",
    "to_date",
    "modified_at",
    "is_current",
    "details_url",
    "report_url",
)

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _empty_reports() -> pd.DataFrame:
    return pd.DataFrame(columns=REPORT_COLUMNS)


def _strip_html(value) -> str:
    """Flatten GDACS' HTML descriptions into plain text."""
    if not isinstance(value, str):
        return ""
    text = _TAG_RE.sub(" ", value)
    return _WHITESPACE_RE.sub(" ", html.unescape(text)).strip()


def _first(props: dict, keys: tuple[str, ...]):
    for key in keys:
        value = props.get(key)
        if value not in (None, ""):
            return value
    return None


def _parse_timestamp(value):
    if value in (None, ""):
        return pd.NaT
    return pd.to_datetime(value, errors="coerce", utc=True)


def _normalize_alert(value) -> str:
    text = str(value or "").strip().title()
    return text if text in {"Red", "Orange", "Green"} else "Unknown"


def _extract_urls(value) -> tuple[str, str]:
    if isinstance(value, dict):
        details = str(value.get("details") or "").strip()
        report = str(value.get("report") or value.get("details") or "").strip()
        return details, report
    if isinstance(value, str):
        return value.strip(), value.strip()
    return "", ""


def _extract_point(geometry) -> tuple[float, float]:
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        return math.nan, math.nan
    coordinates = geometry.get("coordinates")
    if not (isinstance(coordinates, (list, tuple)) and len(coordinates) >= 2):
        return math.nan, math.nan
    try:
        longitude = float(coordinates[0])
        latitude = float(coordinates[1])
    except (TypeError, ValueError):
        return math.nan, math.nan
    if not (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    ):
        return math.nan, math.nan
    return latitude, longitude


def _fetch_page(params: dict, timeout: float) -> list:
    """Fetch one GDACS result page, treating a 404 window as empty."""
    url = f"{GDACS_SEARCH_URL}?{urlencode(params, safe=';')}"
    request = Request(
        url,
        headers={"User-Agent": "WildFireEvac/0.1 (wildfire reports)"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            content = response.read().decode("utf-8-sig", errors="replace")
    except HTTPError as exc:
        # GDACS answers quiet windows with a 404 problem document.
        if exc.code == 404:
            return []
        raise RuntimeError(f"GDACS request failed with HTTP {exc.code}.") from None
    except URLError:
        raise RuntimeError("Could not connect to the GDACS service.") from None
    except TimeoutError:
        raise RuntimeError("GDACS request timed out.") from None

    if not content.strip():
        return []
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        raise RuntimeError("GDACS response was not valid JSON.") from None

    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        return []
    return features


def _collect_pages(
    params: dict,
    *,
    page_size: int,
    max_pages: int,
    timeout: float,
    stop_before=None,
) -> list:
    """Collect GDACS pages until a short page, the cap, or the cutoff date."""
    features: list = []
    for page_number in range(1, max_pages + 1):
        page = _fetch_page(
            {
                **params,
                "pageSize": str(page_size),
                "pageNumber": str(page_number),
            },
            timeout,
        )
        features.extend(page)
        if len(page) < page_size:
            break
        if stop_before is not None and _page_is_older_than(page, stop_before):
            break
    return features


def _page_is_older_than(page: list, cutoff) -> bool:
    """True when every dated event on a page ends before the cutoff.

    GDACS orders results by ``todate`` descending, so once a full page is
    entirely older than the cutoff, later pages are older too.
    """
    oldest = None
    for feature in page:
        props = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(props, dict):
            continue
        stamp = _parse_timestamp(props.get("todate"))
        if pd.isna(stamp):
            continue
        stamp = pd.Timestamp(stamp)
        oldest = stamp if oldest is None else min(oldest, stamp)
    return oldest is not None and oldest < cutoff


def fetch_wildfire_events(
    *,
    days: int = 30,
    page_size: int = 100,
    max_pages: int = 10,
    timeout: float = 30,
) -> pd.DataFrame:
    """Fetch current and recent GDACS wildfire events for a look-back window."""
    if not isinstance(days, int) or not 1 <= days <= 180:
        raise ValueError("GDACS look-back must be an integer from 1 to 180 days.")
    if not isinstance(page_size, int) or not 1 <= page_size <= 100:
        raise ValueError("GDACS page size must be an integer from 1 to 100.")
    if not isinstance(max_pages, int) or not 1 <= max_pages <= 20:
        raise ValueError("GDACS max pages must be an integer from 1 to 20.")

    # GDACS filters "fromdate"/"todate" by event start date, which hides
    # long-running events. Fetch the most recent events instead and apply the
    # look-back window to each event's last report date below.
    cutoff = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=days)
    features = _collect_pages(
        {
            "eventlist": WILDFIRE_EVENT_TYPE,
            "alertlevel": ";".join(ALERT_LEVELS),
        },
        page_size=page_size,
        max_pages=max_pages,
        timeout=timeout,
        stop_before=cutoff,
    )

    if not features:
        return _empty_reports()

    records = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        raw_properties = feature.get("properties")
        if not isinstance(raw_properties, dict):
            continue
        props = {str(key).lower(): value for key, value in raw_properties.items()}

        event_type = str(props.get("eventtype") or "").strip().upper()
        if event_type and event_type != WILDFIRE_EVENT_TYPE:
            continue
        event_id = props.get("eventid")
        if event_id in (None, ""):
            continue

        latitude, longitude = _extract_point(feature.get("geometry"))
        details_url, report_url = _extract_urls(props.get("url"))
        country = _strip_html(_first(props, ("country", "countryname")))
        name = _strip_html(_first(props, ("eventname", "name", "episodename")))
        if not name:
            name = f"Wildfire {country}".strip()

        records.append(
            {
                "event_id": str(event_id),
                "event_type": event_type or WILDFIRE_EVENT_TYPE,
                "name": name,
                "description": _strip_html(
                    _first(props, ("description", "htmldescription", "episodedescription"))
                )[:1200],
                "alert_level": _normalize_alert(
                    _first(props, ("alertlevel", "episodealertlevel"))
                ),
                "alert_score": pd.to_numeric(props.get("alertscore"), errors="coerce"),
                "country": country,
                "iso3": str(_first(props, ("iso3", "countrycode")) or "").strip().upper(),
                "latitude": latitude,
                "longitude": longitude,
                "from_date": _parse_timestamp(_first(props, ("fromdate", "from_date"))),
                "to_date": _parse_timestamp(_first(props, ("todate", "to_date"))),
                "modified_at": _parse_timestamp(
                    _first(props, ("datemodified", "modified"))
                ),
                "is_current": str(_first(props, ("iscurrent",)) or "").strip().lower()
                == "true",
                "details_url": details_url,
                "report_url": report_url,
            }
        )

    if not records:
        return _empty_reports()

    frame = pd.DataFrame.from_records(records)
    frame = frame.dropna(subset=["event_id"])
    # One row per event: keep the most recently modified episode.
    frame = frame.sort_values(
        "modified_at", ascending=False, na_position="last", kind="stable"
    )
    frame = frame.drop_duplicates(subset=["event_id"], keep="first")
    # Keep events still active or last reported inside the look-back window.
    recent = frame["to_date"].notna() & (frame["to_date"] >= cutoff)
    frame = frame[frame["is_current"] | recent]
    return frame.loc[:, REPORT_COLUMNS].reset_index(drop=True)
