from __future__ import annotations

import html
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from wildfire_evacuation_risk.firms import (
    AREA_BOUNDS,
    SUPPORTED_SOURCES,
    fetch_hotspots,
)
from wildfire_evacuation_risk.gdacs import fetch_wildfire_events
from wildfire_evacuation_risk.google_news import search_google_news
from wildfire_evacuation_risk.globe import build_globe_html
from wildfire_evacuation_risk.news import build_report_query, search_news

st.set_page_config(
    page_title="WildFireEvac | Global Wildfire Awareness",
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="collapsed",
)

FIRMS_TTL_SECONDS = 15 * 60
GDACS_TTL_SECONDS = 20 * 60
NEWS_TTL_SECONDS = 15 * 60
NEWS_ERROR_TTL_SECONDS = 60
BACKOFF_BASE_SECONDS = 15.0
BACKOFF_CAP_SECONDS = 300.0

ALERT_COLORS = {
    "Red": "#d62728",
    "Orange": "#ff7f0e",
    "Green": "#2ca02c",
    "Unknown": "#6c757d",
}
ALERT_RANK = {"Red": 0, "Orange": 1, "Green": 2, "Unknown": 3}
REFRESH_OPTIONS = {
    "5 minutes": "5m",
    "10 minutes": "10m",
    "15 minutes": "15m",
    "30 minutes": "30m",
}
REPORT_WINDOWS = (7, 14, 30)
PAGE_SIZE = 10

UI_CSS = """
<style>
:root {
  --wf-bg: #0a0a0f;
  --wf-card: #0c0c14;
  --wf-card-2: #12121c;
  --wf-border: rgba(255, 255, 255, 0.08);
  --wf-border-hover: rgba(255, 255, 255, 0.15);
  --wf-text: #e8eaed;
  --wf-muted: rgba(232, 234, 237, 0.5);
  --wf-accent: #00d4ff;
  --wf-accent-dim: rgba(0, 212, 255, 0.15);
  --wf-accent-glow: rgba(0, 212, 255, 0.4);
  --wf-mono: 'JetBrains Mono', 'SF Mono', 'Fira Code', ui-monospace, monospace;
  --wf-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  --panel-radius: 16px;
  --transition: 150ms cubic-bezier(0.4, 0, 0.2, 1);
}

html, body, [data-testid="stAppViewContainer"] {
  background-color: var(--wf-bg);
  color: var(--wf-text);
  font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}
[data-testid="stAppViewContainer"] {
  background-image:
    radial-gradient(900px 480px at 10% -10%, rgba(201, 245, 107, 0.07), transparent 60%),
    radial-gradient(720px 420px at 92% -4%, rgba(182, 146, 246, 0.06), transparent 62%);
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stToolbar"] { right: 0.6rem; }
#MainMenu, footer { visibility: hidden; }
[data-testid="stMainBlockContainer"], .block-container {
  padding: 1.2rem 2rem 3rem;
  max-width: 1440px;
}
a { color: var(--wf-accent); }
hr { border-color: var(--wf-border); }

/* ---------- sidebar ---------- */
[data-testid="stSidebar"] {
  background: #0b0d10;
  border-right: 1px solid var(--wf-border);
}
[data-testid="stSidebar"] > div { background: transparent; }
[data-testid="stSidebar"] h2 {
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.14em;
  color: var(--wf-muted);
  margin-bottom: 0.35rem;
}
.wf-side-brand { display: flex; align-items: center; gap: 10px; margin: 2px 0 18px; }
.wf-side-brand .glyph {
  width: 34px; height: 34px; display: grid; place-items: center;
  border: 1px solid var(--wf-border); border-radius: 12px;
  background: var(--wf-card); font-size: 17px;
}
.wf-side-brand b { display: block; font-size: 14px; letter-spacing: -0.01em; }
.wf-side-brand span { display: block; font-size: 11px; color: var(--wf-muted); }

/* ---------- top bar ---------- */
.wf-topbar {
  display: flex; justify-content: space-between; align-items: flex-start;
  gap: 20px; flex-wrap: wrap; margin: 0 0 20px;
}
.wf-brand { margin: 0; font-size: 22px !important; font-weight: 680; letter-spacing: -0.02em; }
.wf-tagline {
  margin: 5px 0 0; max-width: 72ch; color: var(--wf-muted);
  font-size: 13px !important; line-height: 1.5 !important;
}
.wf-pills { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
.wf-pill {
  display: inline-flex; align-items: center; gap: 7px;
  border: 1px solid var(--wf-border); border-radius: 999px;
  padding: 5px 11px; font-size: 11.5px; color: #cfd6dd;
  background: rgba(255, 255, 255, 0.02); white-space: nowrap;
}
.wf-pill.wf-warn { border-color: rgba(255, 159, 67, 0.35); color: #ffb066; }
.wf-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--wf-accent); display: inline-block;
}

/* ---------- bento ---------- */
.wf-bento {
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px; margin: 0 0 22px;
}
.wf-tile {
  position: relative; min-width: 0; overflow: hidden;
  border: 1px solid var(--wf-border); border-radius: 22px;
  padding: 16px 18px;
  background: linear-gradient(180deg, var(--wf-card-2) 0%, var(--wf-card) 100%);
}
.wf-tile::after {
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.035), transparent 42%);
}
.wf-span2 { grid-column: span 2; }
.wf-span3 { grid-column: span 3; }
@media (max-width: 1200px) {
  .wf-bento { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .wf-span3 { grid-column: span 2; }
}
@media (max-width: 700px) {
  .wf-bento { grid-template-columns: 1fr; }
  .wf-span2, .wf-span3 { grid-column: span 1; }
}

.wf-eyebrow {
  margin: 0 0 12px; font-size: 10.5px !important; line-height: 1.4 !important;
  font-weight: 600; letter-spacing: 0.14em; text-transform: uppercase;
  color: var(--wf-muted);
}
.wf-hero-head {
  display: flex; justify-content: space-between; align-items: flex-start;
  gap: 16px; flex-wrap: wrap;
}
.wf-hero-num {
  margin: 0; font-family: var(--wf-mono); font-size: 46px !important;
  line-height: 1 !important; font-weight: 600; letter-spacing: -0.04em;
  color: #fff;
}
.wf-hero-sub {
  margin: 7px 0 0; font-size: 12.5px !important; line-height: 1.45 !important;
  color: var(--wf-muted);
}
.wf-status {
  display: inline-flex; align-items: center; gap: 6px;
  border-radius: 999px; padding: 3px 10px;
  background: rgba(201, 245, 107, 0.1); color: var(--wf-accent);
  font-size: 11px; font-weight: 600; white-space: nowrap;
}
.wf-strip { display: flex; align-items: flex-end; gap: 3px; height: 72px; margin-top: 18px; }
.wf-strip span { flex: 1; min-width: 0; border-radius: 4px; background: var(--wf-accent); }
.wf-strip-labels {
  display: flex; justify-content: space-between; gap: 8px; margin-top: 8px;
  font-family: var(--wf-mono); font-size: 10.5px; color: var(--wf-muted);
}

.wf-seg {
  display: flex; height: 34px; gap: 2px; overflow: hidden;
  border-radius: 10px; background: #1a1d22;
}
.wf-seg i { display: block; height: 100%; }
.wf-legend {
  list-style: none; margin: 14px 0 0; padding: 0;
  display: grid; grid-template-columns: 1fr 1fr; gap: 9px 14px;
}
.wf-legend li {
  display: flex; align-items: center; justify-content: space-between;
  gap: 8px; font-size: 12px; color: #cfd5db;
}
.wf-legend b { font-family: var(--wf-mono); font-weight: 600; color: #fff; }
.wf-swatch {
  width: 8px; height: 8px; border-radius: 50%;
  display: inline-block; margin-right: 7px;
}

.wf-kv {
  display: flex; justify-content: space-between; gap: 12px;
  padding: 6.5px 0; border-bottom: 1px dashed #1d2025; font-size: 12.5px;
}
.wf-kv:last-child { border-bottom: none; }
.wf-kv span { color: var(--wf-muted); }
.wf-kv b { font-family: var(--wf-mono); font-weight: 600; color: #eef1f4; }

.wf-trend { display: block; width: 100%; height: 148px; margin-top: 2px; }
.wf-trend-labels {
  display: flex; justify-content: space-between; gap: 8px; margin-top: 4px;
  font-family: var(--wf-mono); font-size: 10.5px; color: var(--wf-muted);
}

.wf-rows { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 11px; }
.wf-rows li { display: flex; flex-direction: column; gap: 6px; }
.wf-row { display: flex; justify-content: space-between; gap: 10px; font-size: 12.5px; }
.wf-row b { font-family: var(--wf-mono); font-weight: 600; color: #fff; }
.wf-rows .bar { height: 4px; border-radius: 999px; background: #23262c; overflow: hidden; }
.wf-rows .bar i { display: block; height: 100%; border-radius: 999px; background: var(--wf-accent); opacity: 0.75; }

.wf-big {
  margin: 2px 0 0; font-family: var(--wf-mono); font-size: 38px !important;
  line-height: 1 !important; font-weight: 600; letter-spacing: -0.035em;
  color: #fff;
}
.wf-note {
  margin: 9px 0 0; font-size: 11.5px !important; line-height: 1.55 !important;
  color: var(--wf-muted);
}
.wf-cta {
  display: inline-block; margin-top: 12px; font-size: 12px;
  color: var(--wf-accent); text-decoration: none;
  border-bottom: 1px solid rgba(201, 245, 107, 0.35); padding-bottom: 1px;
}
.wf-cta:hover { color: #e4ff9f; border-color: #e4ff9f; }

/* ---------- report list ---------- */
.wf-section { display: flex; justify-content: space-between; gap: 16px; align-items: baseline; margin: 6px 0 12px; }
.wf-section h2 { margin: 0; font-size: 15px !important; font-weight: 650; letter-spacing: -0.01em; }
.wf-section p { margin: 0; font-size: 12px !important; color: var(--wf-muted); }
.wf-pager-info {
  text-align: center; padding-top: 8px; font-family: var(--wf-mono);
  font-size: 11px; color: var(--wf-muted);
}

/* ---------- widgets ---------- */
[data-testid="stExpander"] details {
  border: 1px solid var(--wf-border) !important;
  border-radius: 18px !important;
  background: linear-gradient(180deg, var(--wf-card-2), var(--wf-card)) !important;
  overflow: hidden;
}
[data-testid="stExpander"] summary { padding: 13px 16px !important; font-size: 13.5px !important; }
[data-testid="stExpander"] summary:hover { background: rgba(255, 255, 255, 0.025); }
[data-testid="stExpander"] [data-testid="stExpanderDetails"] { padding: 2px 16px 16px !important; }

[data-baseweb="tab-list"] { gap: 6px; border-bottom: 1px solid var(--wf-border); }
[data-baseweb="tab"] {
  height: auto; padding: 8px 15px; border-radius: 999px;
  color: var(--wf-muted); background: transparent;
}
[data-baseweb="tab"][aria-selected="true"] {
  color: var(--wf-accent); background: rgba(201, 245, 107, 0.1);
}
[data-baseweb="tab-highlight"], [data-baseweb="tab-border"] { background: transparent; }

[role="tablist"] { gap: 6px; border-bottom: 1px solid var(--wf-border); }
[data-testid="stTab"] {
  padding: 8px 15px !important; border-radius: 999px !important;
  color: var(--wf-muted);
}
[data-testid="stTab"] p { margin: 0 !important; font-size: 13px !important; }
[data-testid="stTab"][aria-selected="true"] {
  background: rgba(201, 245, 107, 0.1) !important;
}
[data-testid="stTab"][aria-selected="true"] p { color: var(--wf-accent) !important; }

[data-testid="stBaseButton-secondary"] {
  border-radius: 999px; border: 1px solid var(--wf-border);
  background: #15181d; color: #dfe4e9;
}
[data-testid="stBaseButton-secondary"]:hover { border-color: #3a3f47; color: #fff; }
[data-testid="stBaseButton-primary"] {
  border-radius: 999px; background: var(--wf-accent);
  border: 1px solid var(--wf-accent); color: #0a0b0d; font-weight: 650;
}
[data-testid="stBaseButton-primary"]:hover {
  background: #d8ff7f; border-color: #d8ff7f; color: #0a0b0d;
}
[data-testid="stAlert"] {
  border-radius: 16px; border: 1px solid var(--wf-border); background: var(--wf-card);
}
[data-testid="stDeckGlJsonChart"], [data-testid="stPydeckChart"],
[data-testid="stIFrame"] {
  min-height: 700px;
  border: 1px solid var(--wf-border); border-radius: 22px; overflow: hidden;
  box-shadow: 0 24px 80px rgba(0, 0, 0, 0.42), 0 0 55px rgba(0, 212, 255, 0.06);
}
[data-testid="stDataFrame"] { border-radius: 14px; overflow: hidden; }
</style>
"""


def inject_theme() -> None:
    st.markdown(UI_CSS, unsafe_allow_html=True)


@dataclass
class FeedSnapshot:
    data: pd.DataFrame | None = None
    fetched_at: float | None = None
    fetched_wall: datetime | None = None
    error: str | None = None
    attempts: int = 0
    backoff_until: float = 0.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_timestamp(value: datetime | None) -> str:
    if value is None:
        return "never"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _safe_url(value) -> str | None:
    text = str(value or "")
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return text
    return None


def load_feed(key: str, ttl_seconds: float, fetcher) -> FeedSnapshot:
    """Load a feed with TTL caching, failure backoff, and stale-data retention."""
    states = st.session_state.setdefault("_feed_states", {})
    snapshot = states.get(key) or FeedSnapshot()
    now = time.monotonic()
    is_fresh = (
        snapshot.data is not None
        and snapshot.fetched_at is not None
        and (now - snapshot.fetched_at) < ttl_seconds
    )
    if not is_fresh and now >= snapshot.backoff_until:
        try:
            data = fetcher()
        except (RuntimeError, ValueError) as exc:
            snapshot.error = str(exc)
            snapshot.attempts += 1
            snapshot.backoff_until = now + min(
                BACKOFF_CAP_SECONDS,
                BACKOFF_BASE_SECONDS * (2 ** (snapshot.attempts - 1)),
            )
        else:
            snapshot.data = data
            snapshot.fetched_at = now
            snapshot.fetched_wall = _utc_now()
            snapshot.error = None
            snapshot.attempts = 0
            snapshot.backoff_until = 0.0
    states[key] = snapshot
    return snapshot


def render_feed_status(label: str, snapshot: FeedSnapshot) -> None:
    if snapshot.data is None and snapshot.error:
        st.error(f"{label} unavailable: {snapshot.error}")
    elif snapshot.error and snapshot.data is not None:
        st.warning(
            f"{label} refresh failed ({snapshot.error}). Showing data from "
            f"{_fmt_timestamp(snapshot.fetched_wall)}."
        )
    elif snapshot.data is not None:
        st.caption(f"{label} retrieved {_fmt_timestamp(snapshot.fetched_wall)}")


def get_report_news(event_id: str, name: str, country: str | None, timespan: str):
    """Fetch related news for one report, cached per report for 15 minutes."""
    cache = st.session_state.setdefault("_news_cache", {})
    entry = cache.get(event_id)
    now = time.monotonic()
    if entry is not None:
        ttl = NEWS_ERROR_TTL_SECONDS if entry["error"] else NEWS_TTL_SECONDS
        if (now - entry["fetched_at"]) < ttl:
            return (
                entry["data"],
                entry["error"],
                entry["fetched_wall"],
                entry["source"],
            )

    try:
        data = search_news(
            build_report_query(name, country), max_records=25, timespan=timespan
        )
    except (RuntimeError, ValueError) as gdelt_exc:
        try:
            data = search_google_news(
                name, country, max_records=25, timespan=timespan
            )
        except (RuntimeError, ValueError) as google_exc:
            entry = {
                "fetched_at": now,
                "fetched_wall": _utc_now(),
                "data": None,
                "error": f"GDELT: {gdelt_exc} Google News: {google_exc}",
                "source": None,
            }
        else:
            entry = {
                "fetched_at": now,
                "fetched_wall": _utc_now(),
                "data": data,
                "error": None,
                "source": "Google News",
            }
    else:
        entry = {
            "fetched_at": now,
            "fetched_wall": _utc_now(),
            "data": data,
            "error": None,
            "source": "GDELT",
        }
    cache[event_id] = entry
    return entry["data"], entry["error"], entry["fetched_wall"], entry["source"]


def _news_timespan(from_date) -> str:
    if pd.isna(from_date):
        return "2w"
    start = pd.Timestamp(from_date)
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
    days = (pd.Timestamp.now(tz="UTC") - start).days + 2
    return f"{min(max(days, 7), 90)}d"


def render_related_news(row, key_prefix: str = "") -> None:
    st.divider()
    st.markdown("**Related news**")
    st.caption(
        "Headlines matched by keyword from GDELT (Google News fallback); "
        "matches are not verification."
    )

    requested = st.session_state.setdefault("_news_requested", set())
    if st.button(
        "Find related news", key=f"{key_prefix}news-button-{row.event_id}"
    ):
        requested.add(row.event_id)
    if row.event_id not in requested:
        st.caption("Select **Find related news** to search for this report.")
        return

    timespan = _news_timespan(row.from_date)
    data, error, fetched_wall, source = get_report_news(
        row.event_id, row.name, row.country or None, timespan
    )

    if error:
        st.warning(f"Could not load related news: {error}")
    if data is None or data.empty:
        if not error:
            st.info("No related English news found for this report.")
        return

    st.caption(
        f"{len(data):,} article(s) from {source} · retrieved "
        f"{_fmt_timestamp(fetched_wall)}"
    )
    for article in data.head(10).itertuples(index=False):
        title = html.escape(article.title or "(untitled)")
        source = html.escape(article.domain or "")
        seen = (
            f"{article.seen_at:%Y-%m-%d %H:%M UTC}"
            if pd.notna(article.seen_at)
            else ""
        )
        meta = " · ".join(part for part in (source, seen) if part)
        safe_url = _safe_url(article.url)
        if safe_url:
            link = (
                f'<a href="{html.escape(safe_url, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer">{title}</a>'
            )
        else:
            link = title
        st.markdown(
            f'{link}<br/><span style="color:#8a8f98;font-size:0.8rem">{meta}</span>',
            unsafe_allow_html=True,
        )


def render_report_detail(row, key_prefix: str = "") -> None:
    color = ALERT_COLORS.get(row.alert_level, ALERT_COLORS["Unknown"])
    level = html.escape(str(row.alert_level))
    st.markdown(
        f'<span style="background:{color};color:white;padding:2px 10px;'
        f'border-radius:10px;font-size:0.85rem">{level}</span>',
        unsafe_allow_html=True,
    )

    meta = []
    if pd.notna(row.from_date):
        meta.append(f"from {pd.Timestamp(row.from_date):%Y-%m-%d}")
    if pd.notna(row.to_date):
        meta.append(f"to {pd.Timestamp(row.to_date):%Y-%m-%d}")
    meta.append("current" if bool(row.is_current) else "ended")
    if row.country:
        meta.append(html.escape(str(row.country)))
    st.caption(" · ".join(meta))

    if row.description:
        description = str(row.description)
        st.write(description[:800] + ("…" if len(description) > 800 else ""))

    links = []
    for label, url in (
        ("Official GDACS report", row.report_url),
        ("Event details", row.details_url),
    ):
        safe_url = _safe_url(url)
        if safe_url:
            links.append(
                f'<a href="{html.escape(safe_url, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer">{html.escape(label)}</a>'
            )
    if links:
        st.markdown(" · ".join(links), unsafe_allow_html=True)

    render_related_news(row, key_prefix)


def _date_label(days_ago: int) -> str:
    day = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=days_ago)
    return f"{day:%b} {day.day}"


def _daily_counts(reports: pd.DataFrame, column: str, days: int) -> list[int]:
    end = pd.Timestamp.now(tz="UTC").normalize().tz_localize(None)
    index = pd.date_range(end=end, periods=days, freq="D")
    if reports.empty or column not in reports.columns:
        return [0] * days
    values = pd.to_datetime(reports[column], errors="coerce")
    if getattr(values.dt, "tz", None) is not None:
        values = values.dt.tz_localize(None)
    counts = values.dt.normalize().value_counts().reindex(index, fill_value=0)
    return [int(count) for count in counts.tolist()]


def _strip_bars(values: list[int], accent: str = "#c9f56b") -> str:
    high = max(values) or 1
    total = len(values)
    bars = []
    for index, value in enumerate(values):
        height = max(4, round(value / high * 100))
        label = _date_label(total - 1 - index)
        if value == 0:
            bars.append(
                f'<span style="height:{height}%;background:#23262c" '
                f'title="{label}: 0 reports"></span>'
            )
        else:
            opacity = 0.3 + 0.7 * (value / high)
            bars.append(
                f'<span style="height:{height}%;background:{accent};'
                f'opacity:{opacity:.2f}" title="{label}: {value} report(s)"></span>'
            )
    return f'<div class="wf-strip">{"".join(bars)}</div>'


def _trend_svg(values: list[int], accent: str = "#c9f56b") -> str:
    width, height, pad = 620, 148, 6
    high = max(values) or 1
    span = width - 2 * pad
    step = span / max(1, len(values) - 1)
    points = [
        (pad + index * step, height - pad - (value / high) * (height - 2 * pad))
        for index, value in enumerate(values)
    ]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area = (
        f"{pad},{height - pad} "
        + line
        + f" {points[-1][0]:.1f},{height - pad}"
    )
    grid = "".join(
        f'<line x1="{pad}" x2="{width - pad}" y1="{height * fraction:.0f}" '
        f'y2="{height * fraction:.0f}" stroke="#1e2227" stroke-width="1" '
        f'stroke-dasharray="2 6"/>'
        for fraction in (0.3, 0.55, 0.8)
    )
    return (
        f'<svg class="wf-trend" viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="none" role="img" aria-label="Daily report starts">'
        f'<defs><linearGradient id="wf-trend-fill" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{accent}" stop-opacity="0.30"/>'
        f'<stop offset="1" stop-color="{accent}" stop-opacity="0"/>'
        f"</linearGradient></defs>"
        f"{grid}"
        f'<polygon points="{area}" fill="url(#wf-trend-fill)"/>'
        f'<polyline points="{line}" fill="none" stroke="{accent}" '
        f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
        f"</svg>"
    )


def _tile(title: str, body: str, span: int = 1) -> str:
    span_class = " wf-span2" if span == 2 else " wf-span3" if span == 3 else ""
    return (
        f'<section class="wf-tile{span_class}">'
        f'<p class="wf-eyebrow">{title}</p>{body}</section>'
    )


def render_bento(*tiles: str) -> None:
    st.markdown(
        f'<div class="wf-bento">{"".join(tiles)}</div>', unsafe_allow_html=True
    )


def render_report_bento(
    reports: pd.DataFrame,
    counts,
    window_days: int,
    include_ended: bool,
    alert_levels: list[str],
    snapshot: FeedSnapshot,
    has_key: bool,
) -> None:
    total = len(reports)
    daily = _daily_counts(reports, "from_date", window_days)
    countries = 0
    if "country" in reports.columns:
        countries = int(
            reports["country"].dropna().astype(str).str.strip().replace("", pd.NA).dropna().nunique()
        )

    hero = _tile(
        "Global wildfire reports",
        (
            '<div class="wf-hero-head">'
            "<div>"
            f'<p class="wf-hero-num">{total:,}</p>'
            f'<p class="wf-hero-sub">reports match the current filters · last {window_days} days</p>'
            "</div>"
            '<span class="wf-status"><span class="wf-dot"></span>GDACS live</span>'
            "</div>"
            f"{_strip_bars(daily)}"
            '<div class="wf-strip-labels">'
            f"<span>{_date_label(window_days - 1)}</span>"
            "<span>new reports per day</span>"
            "<span>today</span>"
            "</div>"
        ),
        span=2,
    )

    levels = ("Red", "Orange", "Green")
    segments = "".join(
        f'<i style="width:{((counts.get(level, 0) / total) * 100) if total else 0:.2f}%;'
        f'background:{ALERT_COLORS[level]}"></i>'
        for level in levels
    )
    legend = "".join(
        f'<li><span><i class="wf-swatch" style="background:{ALERT_COLORS[level]}"></i>'
        f"{level}</span><b>{int(counts.get(level, 0)):,}</b></li>"
        for level in levels
    )
    alert_tile = _tile(
        "Alert mix",
        f'<div class="wf-seg">{segments}</div><ul class="wf-legend">{legend}</ul>',
    )

    filter_label = (
        "All levels"
        if set(alert_levels) >= {"Red", "Orange", "Green"}
        else (", ".join(alert_levels) or "none")
    )
    data_tile = _tile(
        "Data",
        (
            f'<div class="wf-kv"><span>Updated</span>'
            f"<b>{_fmt_timestamp(snapshot.fetched_wall)}</b></div>"
            f'<div class="wf-kv"><span>Window</span><b>{window_days}d</b></div>'
            f'<div class="wf-kv"><span>Alerts</span>'
            f"<b>{html.escape(filter_label)}</b></div>"
            f'<div class="wf-kv"><span>Ended</span>'
            f'<b>{"shown" if include_ended else "hidden"}</b></div>'
            f'<div class="wf-kv"><span>Countries</span><b>{countries:,}</b></div>'
        ),
    )

    trend_tile = _tile(
        "Reports per day",
        (
            f"{_trend_svg(daily)}"
            '<div class="wf-trend-labels">'
            f"<span>{_date_label(window_days - 1)}</span>"
            f"<span>{sum(daily):,} report starts</span>"
            "<span>today</span>"
            "</div>"
        ),
        span=2,
    )

    cached = st.session_state.get("_feed_states", {}).get("firms")
    hotspots = cached.data if cached is not None else None
    if hotspots is not None and len(hotspots):
        latest = (
            str(hotspots["acq_date"].max()) if "acq_date" in hotspots.columns else "—"
        )
        sat_body = (
            f'<p class="wf-big">{len(hotspots):,}</p>'
            f'<p class="wf-note">hotspot detections cached from the Live Globe · '
            f"latest observation {html.escape(latest)}</p>"
        )
    elif has_key:
        sat_body = (
            '<p class="wf-big">Ready</p>'
            '<p class="wf-note">FIRMS key detected. Open the Live Globe to load '
            "satellite detections.</p>"
        )
    else:
        sat_body = (
            '<p class="wf-big">Locked</p>'
            '<p class="wf-note">Satellite hotspots need a free NASA FIRMS key.</p>'
            '<a class="wf-cta" href="https://firms.modaps.eosdis.nasa.gov/api/map_key/" '
            'target="_blank" rel="noopener noreferrer">Get a map key →</a>'
        )
    satellite_tile = _tile("Satellite detections", sat_body)

    top_countries = pd.Series(dtype=int)
    if "country" in reports.columns:
        top_countries = (
            reports["country"]
            .dropna()
            .astype(str)
            .str.strip()
            .replace("", pd.NA)
            .dropna()
            .value_counts()
            .head(4)
        )
    if top_countries.empty:
        countries_body = '<p class="wf-note">No country data for this selection.</p>'
    else:
        peak = int(top_countries.max()) or 1
        rows = "".join(
            f'<li><div class="wf-row"><span>{html.escape(str(country))}</span>'
            f'<b>{int(count):,}</b></div>'
            f'<div class="bar"><i style="width:{count / peak * 100:.0f}%"></i></div></li>'
            for country, count in top_countries.items()
        )
        countries_body = f'<ul class="wf-rows">{rows}</ul>'
    countries_tile = _tile("Top countries", countries_body)

    render_bento(
        hero, alert_tile, data_tile, trend_tile, satellite_tile, countries_tile
    )


def render_reports(
    snapshot: FeedSnapshot,
    include_ended: bool,
    window_days: int,
    alert_levels: list[str],
) -> None:
    render_feed_status("GDACS reports", snapshot)
    if snapshot.data is None:
        return

    if not alert_levels:
        st.info("Select at least one alert level in the sidebar.")
        return

    reports = filter_reports(snapshot.data, include_ended, alert_levels)
    counts = reports["alert_level"].value_counts() if not reports.empty else {}
    render_report_bento(
        reports,
        counts,
        window_days,
        include_ended,
        alert_levels,
        snapshot,
        bool(os.environ.get("FIRMS_MAP_KEY", "").strip()),
    )

    if reports.empty:
        st.info(
            "No wildfire reports matched. Try including ended reports, a "
            "longer window, or more alert levels."
        )
        return

    ordered = reports.assign(
        _rank=reports["alert_level"].map(ALERT_RANK)
    ).sort_values(["_rank", "from_date"], ascending=[True, False], kind="stable")

    st.markdown(
        '<div class="wf-section"><h2>Reports</h2>'
        "<p>Newest first · open a card for details and related news</p></div>",
        unsafe_allow_html=True,
    )

    pages = max(1, (len(ordered) + PAGE_SIZE - 1) // PAGE_SIZE)
    signature = (window_days, tuple(alert_levels), include_ended)
    if st.session_state.get("_report_filter_sig") != signature:
        st.session_state["_report_filter_sig"] = signature
        st.session_state["_report_page"] = 0
    page = min(int(st.session_state.get("_report_page", 0)), pages - 1)
    st.session_state["_report_page"] = page
    start = page * PAGE_SIZE

    for row in ordered.iloc[start : start + PAGE_SIZE].itertuples(index=False):
        label = " · ".join(part for part in (row.name, row.country) if part)
        with st.expander(f"{label} ({row.alert_level})"):
            render_report_detail(row)

    if pages > 1:
        prev_col, info_col, next_col = st.columns([1, 2, 1])
        if prev_col.button(
            "← Newer",
            key="reports_prev",
            disabled=page == 0,
            use_container_width=True,
        ):
            st.session_state["_report_page"] = page - 1
            st.rerun()
        info_col.markdown(
            f'<div class="wf-pager-info">Reports {start + 1:,}–'
            f"{min(start + PAGE_SIZE, len(ordered)):,} of {len(ordered):,} · "
            f"page {page + 1:,} of {pages:,}</div>",
            unsafe_allow_html=True,
        )
        if next_col.button(
            "Older →",
            key="reports_next",
            disabled=page >= pages - 1,
            use_container_width=True,
        ):
            st.session_state["_report_page"] = page + 1
            st.rerun()


def _rgb(color: str) -> list[int]:
    value = color.lstrip("#")
    return [int(value[index : index + 2], 16) for index in (0, 2, 4)]


def filter_reports(
    reports: pd.DataFrame, include_ended: bool, alert_levels: list[str]
) -> pd.DataFrame:
    filtered = reports[reports["alert_level"].isin(alert_levels)]
    if not include_ended and "is_current" in filtered.columns:
        filtered = filtered[filtered["is_current"]]
    return filtered


def _map_camera(area_name: str) -> tuple[float, float, float]:
    if area_name == "Global":
        return 12, 14, 2.4
    elif area_name == "Indonesia":
        return 118, -2, 1.45
    return 114, -1, 0.85


def _hotspot_tooltips(hotspots: pd.DataFrame) -> pd.Series:
    detail = (
        "Observed " + hotspots["acq_date"].astype(str) + " "
        + hotspots["acq_time"].astype(str) + " UTC"
    )
    parts = []
    for prefix, column, suffix in (
        ("Satellite ", "satellite", ""),
        ("Confidence ", "confidence", ""),
        ("FRP ", "frp", " MW"),
    ):
        if column in hotspots.columns:
            parts.append(prefix + hotspots[column].astype(str) + suffix)
    if parts:
        detail = detail + " · " + " · ".join(parts)
    return detail.map(html.escape)


def render_live_map(
    snapshot: FeedSnapshot | None,
    area_name: str,
    events: pd.DataFrame,
    has_key: bool,
    feed_note: str = "",
) -> None:
    hotspots = snapshot.data if has_key and snapshot is not None else None
    if has_key and snapshot is not None:
        render_feed_status("FIRMS hotspots", snapshot)

    mapped = events
    if {"latitude", "longitude"}.issubset(events.columns):
        mapped = events.dropna(subset=["latitude", "longitude"]).copy()
    else:
        mapped = events.head(0).copy()

    coverage_note = feed_note or area_name
    if hotspots is not None:
        latest = (
            str(hotspots["acq_date"].max())
            if len(hotspots) and "acq_date" in hotspots
            else "—"
        )
        render_bento(
            _tile(
                "Satellite detections",
                f'<p class="wf-big">{len(hotspots):,}</p>'
                '<p class="wf-note">NASA FIRMS near-real-time detections</p>',
            ),
            _tile(
                "Latest observation",
                f'<p class="wf-big">{html.escape(latest)}</p>'
                '<p class="wf-note">acquisition date (UTC)</p>',
            ),
            _tile(
                "Reports on map",
                f'<p class="wf-big">{len(mapped):,}</p>'
                '<p class="wf-note">GDACS reports with coordinates</p>',
            ),
            _tile(
                "Coverage",
                f'<p class="wf-big">{html.escape(area_name)}</p>'
                f'<p class="wf-note">{html.escape(coverage_note)}</p>',
            ),
        )
        if hotspots.empty:
            st.warning("No satellite detections were returned for this selection.")
    else:
        render_bento(
            _tile(
                "Reports on map",
                f'<p class="wf-big">{len(mapped):,}</p>'
                '<p class="wf-note">GDACS reports with coordinates</p>',
            ),
            _tile(
                "Coverage",
                f'<p class="wf-big">{html.escape(area_name)}</p>'
                f'<p class="wf-note">{html.escape(coverage_note)}</p>',
            ),
            _tile(
                "Satellite detections",
                '<p class="wf-big">Locked</p>'
                '<p class="wf-note">Add a free NASA FIRMS map key to overlay '
                "satellite hotspots on the report layer.</p>"
                '<a class="wf-cta" href="https://firms.modaps.eosdis.nasa.gov/api/map_key/" '
                'target="_blank" rel="noopener noreferrer">Get a map key →</a>',
                span=2,
            ),
        )

    if has_key and hotspots is None and snapshot is not None and snapshot.error:
        st.caption("Showing GDACS reports only while the satellite feed is down.")

    report_features = []
    if not mapped.empty:
        ordered = mapped.assign(_rank=mapped["alert_level"].map(ALERT_RANK))
        ordered = ordered.sort_values("_rank", ascending=False, kind="stable")
        detail = []
        for row in ordered.itertuples(index=False):
            parts = [str(row.country or "")] if row.country else []
            if pd.notna(row.from_date):
                parts.append(f"from {pd.Timestamp(row.from_date):%Y-%m-%d}")
            if pd.notna(row.to_date):
                parts.append(f"to {pd.Timestamp(row.to_date):%Y-%m-%d}")
            parts.append("current" if bool(row.is_current) else "ended")
            detail.append(html.escape(" · ".join(parts)))
        event_data = pd.DataFrame(
            {
                "longitude": pd.to_numeric(ordered["longitude"], errors="coerce"),
                "latitude": pd.to_numeric(ordered["latitude"], errors="coerce"),
                "label": (
                    ordered["name"].astype(str)
                    + " ("
                    + ordered["alert_level"].astype(str)
                    + ")"
                ).map(html.escape),
                "detail": detail,
                "fill_color": ordered["alert_level"].map(
                    lambda level: _rgb(
                        ALERT_COLORS.get(level, ALERT_COLORS["Unknown"])
                    )
                ),
            }
        )
        event_data = event_data.dropna(subset=["longitude", "latitude"])
        report_features = [
            {
                "longitude": float(row.longitude),
                "latitude": float(row.latitude),
                "label": str(row.label),
                "detail": str(row.detail),
                "color": [int(channel) for channel in row.fill_color],
            }
            for row in event_data.itertuples(index=False)
        ]

    hotspot_features = []
    if hotspots is not None and not hotspots.empty:
        hotspot_columns = {
            "longitude": pd.to_numeric(hotspots["longitude"], errors="coerce"),
            "latitude": pd.to_numeric(hotspots["latitude"], errors="coerce"),
            "label": "Satellite hotspot",
            "detail": _hotspot_tooltips(hotspots),
        }
        for column in ("acq_date", "acq_time", "satellite", "confidence", "frp"):
            if column in hotspots.columns:
                hotspot_columns[column] = hotspots[column]
        hotspot_data = pd.DataFrame(hotspot_columns)
        hotspot_data = hotspot_data.dropna(subset=["longitude", "latitude"])
        hotspot_features = [
            {
                "longitude": float(row.longitude),
                "latitude": float(row.latitude),
                "label": str(row.label),
                "detail": str(row.detail),
            }
            for row in hotspot_data.itertuples(index=False)
        ]

    longitude, latitude, camera_height = _map_camera(area_name)
    components.html(
        build_globe_html(
            reports=report_features,
            hotspots=hotspot_features,
            longitude=longitude,
            latitude=latitude,
            camera_altitude=camera_height,
        ),
        height=760,
        scrolling=False,
    )

    st.caption(
        "Interactive satellite globe · drag to rotate · scroll to zoom. "
        "Large points are GDACS wildfire reports; small points are NASA FIRMS "
        "satellite detections. Click a point for details. Static base imagery "
        "is NASA Blue Marble; hotspots are not verified fire perimeters."
    )
    if not report_features and not hotspot_features:
        st.info("No reports or satellite detections are available; the globe remains visible.")

    if hotspots is not None and not hotspots.empty:
        display_columns = [
            column
            for column in (
                "latitude",
                "longitude",
                "acq_date",
                "acq_time",
                "satellite",
                "confidence",
                "frp",
            )
            if column in hotspots.columns
        ]
        with st.expander("View detection records"):
            st.dataframe(
                hotspots[display_columns].sort_values(
                    ["acq_date", "acq_time"],
                    ascending=False,
                    kind="stable",
                ),
                use_container_width=True,
                hide_index=True,
            )

    if not mapped.empty:
        report_columns = [
            column
            for column in (
                "name",
                "country",
                "alert_level",
                "from_date",
                "to_date",
                "latitude",
                "longitude",
            )
            if column in mapped.columns
        ]
        with st.expander("View mapped reports"):
            st.dataframe(
                mapped[report_columns], use_container_width=True, hide_index=True
            )


def main() -> None:
    inject_theme()
    map_key = os.environ.get("FIRMS_MAP_KEY", "").strip()

    key_pill = (
        '<span class="wf-pill"><i class="wf-dot"></i>FIRMS key active</span>'
        if map_key
        else '<span class="wf-pill wf-warn">FIRMS key not set</span>'
    )
    st.markdown(
        '<div class="wf-topbar">'
        "<div>"
        '<p class="wf-brand">🔥 WildFireEvac</p>'
        '<p class="wf-tagline">Live global wildfire awareness: GDACS reports, '
        "related English news, and satellite detections. Not an emergency "
        "decision tool.</p>"
        "</div>"
        '<div class="wf-pills">'
        '<span class="wf-pill"><i class="wf-dot"></i>GDACS live</span>'
        '<span class="wf-pill">English headlines</span>'
        f"{key_pill}"
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.markdown(
            '<div class="wf-side-brand"><span class="glyph">🔥</span>'
            "<div><b>WildFireEvac</b><span>Global awareness</span></div></div>",
            unsafe_allow_html=True,
        )
        st.header("Refresh")
        auto_refresh = st.toggle("Auto-refresh", value=True)
        interval_label = st.selectbox(
            "Refresh interval",
            list(REFRESH_OPTIONS),
            index=1,
            disabled=not auto_refresh,
        )
        if st.button("Refresh now", type="primary"):
            st.session_state["_feed_states"] = {}
            st.session_state["_news_cache"] = {}
            st.session_state["_news_requested"] = set()
            st.rerun()
        st.caption(
            "Feeds refresh automatically at the selected interval; cached data "
            "is reused while it is fresh."
        )

        st.divider()
        st.header("Reports")
        reports_days = st.selectbox("Report window (days)", REPORT_WINDOWS, index=1)
        alert_levels = st.multiselect(
            "Alert levels",
            ["Red", "Orange", "Green"],
            default=["Red", "Orange", "Green"],
            help=(
                "Green events are numerous and usually low impact; deselect "
                "them to focus on Orange and Red reports."
            ),
        )
        include_ended = st.checkbox("Include ended reports", value=False)

        st.divider()
        st.header("Satellite feed")
        area_name = st.selectbox("Coverage", list(AREA_BOUNDS))
        source = st.selectbox("Satellite feed", SUPPORTED_SOURCES, index=2)
        day_range = st.slider("Look-back (days)", min_value=1, max_value=5, value=1)
        st.caption(
            "The Live Globe plots GDACS reports without a key; NASA FIRMS "
            "hotspot detections need a free FIRMS map key."
        )

    refresh_interval = REFRESH_OPTIONS[interval_label] if auto_refresh else None

    map_tab, reports_tab, forecast_tab = st.tabs(
        ["Live Globe", "Reports", "Forecast"]
    )

    with reports_tab:

        @st.fragment(run_every=refresh_interval)
        def reports_view() -> None:
            snapshot = load_feed(
                "gdacs",
                GDACS_TTL_SECONDS,
                lambda: fetch_wildfire_events(days=reports_days),
            )
            render_reports(snapshot, include_ended, reports_days, alert_levels)

        reports_view()

    with map_tab:

        @st.fragment(run_every=refresh_interval)
        def map_view() -> None:
            snapshot = load_feed(
                "firms",
                FIRMS_TTL_SECONDS,
                lambda: fetch_hotspots(
                    map_key=map_key,
                    source=source,
                    area=AREA_BOUNDS[area_name],
                    day_range=day_range,
                ),
            ) if map_key else None

            events_snapshot = load_feed(
                "gdacs",
                GDACS_TTL_SECONDS,
                lambda: fetch_wildfire_events(days=reports_days),
            )
            render_feed_status("GDACS reports", events_snapshot)

            events = events_snapshot.data
            if events is None:
                events = pd.DataFrame()
            else:
                events = filter_reports(
                    events,
                    include_ended,
                    alert_levels,
                )

            if not map_key:
                st.caption(
                    "Satellite hotspots are optional and need a free NASA FIRMS "
                    "map key; the GDACS report layer works without one."
                )
            render_live_map(
                snapshot,
                area_name,
                events,
                bool(map_key),
                f"{area_name} · {source} · last {day_range}d",
            )

        map_view()

    with forecast_tab:
        render_bento(
            _tile(
                "Forecast roadmap",
                '<p class="wf-note">Forecast panels arrive in later phases: '
                "detection analytics from the current feeds, 72-hour fire-weather "
                "context for active clusters, and optional survival-model "
                "probabilities.</p>",
                span=2,
            ),
            _tile(
                "Available now",
                '<p class="wf-note">Global reports, related English headlines, '
                "and the GDACS report map need no credentials.</p>",
            ),
            _tile(
                "Needs a FIRMS key",
                '<p class="wf-note">Satellite detections and the detection '
                "analytics they feed.</p>",
            ),
            _tile(
                "Model estimate",
                '<p class="wf-note">Optional research estimate from survival '
                "models. Caveated, and never an evacuation instruction.</p>",
                span=2,
            ),
            _tile(
                "Stay grounded",
                '<p class="wf-note">Reports, headlines, and detections are '
                "third-party data. Hotspots are not verified fire perimeters. "
                "Do not use this app for emergency decisions.</p>",
                span=2,
            ),
        )

    st.divider()
    st.caption(
        "Sources: NASA FIRMS (hotspots) · GDACS (reports) · GDELT and Google "
        "News (headlines). Weather data planned for a later phase."
    )
    st.warning(
        "Situational awareness only. Satellite hotspots are not verified fire "
        "perimeters, and reports and news are third-party data. Do not use this "
        "app for emergency decisions."
    )


main()
