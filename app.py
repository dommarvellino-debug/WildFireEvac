"""Wildfire surveillance console: GOD'S EYE VIEW.

A single-screen HUD over a 3D globe. GDACS wildfire reports and optional NASA
FIRMS detections are plotted worldwide; selecting a marker opens its report and
situation panel.
"""

from __future__ import annotations

import html
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
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
from wildfire_evacuation_risk.news import build_report_query, search_news

FIRMS_TTL_SECONDS = 15 * 60
GDACS_TTL_SECONDS = 20 * 60
NEWS_TTL_SECONDS = 15 * 60
NEWS_ERROR_TTL_SECONDS = 60
BACKOFF_BASE_SECONDS = 15.0
BACKOFF_CAP_SECONDS = 300.0
REFRESH_SECONDS = "5m"
REPORT_WINDOWS = (7, 14, 30)
HOTSPOT_RENDER_LIMIT = 6000

ALERT_LEVELS = ("Red", "Orange", "Green")
ALERT_COLORS = {
    "Red": "#ff3b3b",
    "Orange": "#ff9f1c",
    "Green": "#3ddc84",
    "Unknown": "#8fa3ad",
}
ALERT_RANK = {"Red": 0, "Orange": 1, "Green": 2, "Unknown": 3}

_COMPONENT_DIR = Path(__file__).parent / "components" / "globe"
_wildfire_globe = components.declare_component(
    "wildfire_globe", path=str(_COMPONENT_DIR)
)


HUD_CSS = """
<style>
:root {
  --hud-bg: #04070b;
  --hud-cyan: #4ff3ff;
  --hud-dim: #7fa6b4;
  --hud-line: rgba(79, 243, 255, 0.16);
  --hud-red: #ff3b3b;
  --hud-orange: #ff9f1c;
  --hud-green: #3ddc84;
  --hud-mono: ui-monospace, "Cascadia Mono", "SFMono-Regular", Consolas, monospace;
}

html, body, [data-testid="stAppViewContainer"] {
  background-color: var(--hud-bg);
  color: #cfe3ea;
}
[data-testid="stAppViewContainer"] {
  background-image:
    radial-gradient(1100px 720px at 50% 46%, rgba(79, 243, 255, 0.075), transparent 66%),
    radial-gradient(760px 420px at 6% -6%, rgba(79, 243, 255, 0.05), transparent 62%);
}
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stHeader"],
[data-testid="stDecoration"], [data-testid="stStatusWidget"] {
  display: none !important;
}
[data-testid="stSidebar"], [data-testid="collapsedControl"] { display: none !important; }
[data-testid="stMainBlockContainer"], .block-container {
  padding: 10px 18px 4px;
  max-width: 100%;
}
[data-testid="stAppViewBlockContainer"] { padding-top: 0; }
[data-testid="stVerticalBlock"] { gap: 0.55rem; }
a { color: var(--hud-cyan); }

/* ---------- viewport decoration ---------- */
.hud-vignette {
  position: fixed; inset: 0; pointer-events: none; z-index: 0;
  background:
    repeating-linear-gradient(0deg, rgba(79, 243, 255, 0.028) 0 1px, transparent 1px 3px),
    radial-gradient(120% 90% at 50% 50%, transparent 55%, rgba(0, 0, 0, 0.72) 100%);
}
.hud-corners {
  position: fixed; inset: 8px; pointer-events: none; z-index: 0;
  background:
    linear-gradient(var(--hud-cyan), var(--hud-cyan)) left top / 26px 1px no-repeat,
    linear-gradient(var(--hud-cyan), var(--hud-cyan)) left top / 1px 26px no-repeat,
    linear-gradient(var(--hud-cyan), var(--hud-cyan)) right top / 26px 1px no-repeat,
    linear-gradient(var(--hud-cyan), var(--hud-cyan)) right top / 1px 26px no-repeat,
    linear-gradient(var(--hud-cyan), var(--hud-cyan)) left bottom / 26px 1px no-repeat,
    linear-gradient(var(--hud-cyan), var(--hud-cyan)) left bottom / 1px 26px no-repeat,
    linear-gradient(var(--hud-cyan), var(--hud-cyan)) right bottom / 26px 1px no-repeat,
    linear-gradient(var(--hud-cyan), var(--hud-cyan)) right bottom / 1px 26px no-repeat;
  opacity: 0.35;
}
.hud-rail {
  position: fixed; top: 50%; transform: translateY(-50%);
  writing-mode: vertical-rl; font-family: var(--hud-mono);
  font-size: 8.5px; letter-spacing: 0.36em; text-transform: uppercase;
  color: rgba(127, 166, 180, 0.55); pointer-events: none; z-index: 0;
}
.hud-rail.left { left: 10px; }
.hud-rail.right { right: 10px; }

/* ---------- top bar ---------- */
.hud-top {
  display: flex; align-items: flex-start; justify-content: space-between;
  gap: 18px; flex-wrap: wrap; padding: 4px 2px 10px;
}
.hud-brand { display: flex; align-items: center; gap: 12px; }
.hud-mark {
  width: 34px; height: 34px; display: grid; place-items: center;
  border: 1px solid rgba(79, 243, 255, 0.45); border-radius: 50%;
  color: var(--hud-cyan); font-size: 15px;
  box-shadow: 0 0 18px rgba(79, 243, 255, 0.35), inset 0 0 12px rgba(79, 243, 255, 0.2);
}
.hud-title {
  font-family: var(--hud-mono); font-size: 27px; line-height: 1;
  letter-spacing: 0.16em; color: #effcff; font-weight: 600;
  text-shadow: 0 0 22px rgba(79, 243, 255, 0.45);
}
.hud-title em { font-style: normal; color: var(--hud-cyan); }
.hud-sub {
  margin-top: 6px; font-family: var(--hud-mono); font-size: 8.5px;
  letter-spacing: 0.42em; text-transform: uppercase; color: var(--hud-dim);
}
.hud-stats { display: flex; align-items: center; gap: 18px; flex-wrap: wrap; }
.hud-stat {
  font-family: var(--hud-mono); font-size: 9px; letter-spacing: 0.2em;
  color: var(--hud-dim); text-transform: uppercase; text-align: right;
}
.hud-stat b {
  display: block; margin-top: 3px; font-size: 15px; letter-spacing: 0.06em;
  color: #eafcff; font-weight: 600;
}
.hud-stat b.red { color: var(--hud-red); text-shadow: 0 0 12px rgba(255, 59, 59, 0.5); }
.hud-stat b.orange { color: var(--hud-orange); text-shadow: 0 0 12px rgba(255, 159, 28, 0.45); }
.hud-stat b.green { color: var(--hud-green); text-shadow: 0 0 12px rgba(61, 220, 132, 0.4); }
.hud-rec {
  display: flex; align-items: center; gap: 7px;
  border: 1px solid var(--hud-line); padding: 7px 10px;
  font-family: var(--hud-mono); font-size: 9.5px; letter-spacing: 0.14em;
  color: #a8cbd6; text-transform: uppercase;
}
.hud-rec i {
  width: 7px; height: 7px; border-radius: 50%; background: var(--hud-red);
  box-shadow: 0 0 10px var(--hud-red); display: inline-block;
  animation: hudpulse 1.6s infinite;
}
.hud-rec b { color: #eafcff; font-weight: 500; letter-spacing: 0.08em; }
@keyframes hudpulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.25; } }

/* ---------- panels ---------- */
.st-key-hud-left, .st-key-hud-right {
  position: relative;
  border: 1px solid var(--hud-line);
  background: linear-gradient(180deg, rgba(8, 19, 27, 0.94), rgba(4, 10, 15, 0.9));
  box-shadow: inset 0 0 46px rgba(79, 243, 255, 0.05);
  padding: 14px 14px 16px;
  backdrop-filter: blur(7px);
  overflow: visible;
}
.st-key-hud-left::after, .st-key-hud-right::after {
  content: ""; position: absolute; inset: -1px; pointer-events: none;
  background:
    linear-gradient(var(--hud-cyan), var(--hud-cyan)) left top / 12px 1px no-repeat,
    linear-gradient(var(--hud-cyan), var(--hud-cyan)) left top / 1px 12px no-repeat,
    linear-gradient(var(--hud-cyan), var(--hud-cyan)) right bottom / 12px 1px no-repeat,
    linear-gradient(var(--hud-cyan), var(--hud-cyan)) right bottom / 1px 12px no-repeat;
  opacity: 0.75;
}
.hud-head {
  display: flex; align-items: center; gap: 8px; margin: 12px 0 9px;
  font-family: var(--hud-mono); font-size: 9px; letter-spacing: 0.26em;
  text-transform: uppercase; color: var(--hud-cyan);
}
.hud-head:first-child { margin-top: 0; }
.hud-head .tick {
  width: 5px; height: 5px; background: var(--hud-cyan);
  box-shadow: 0 0 8px var(--hud-cyan);
}
.hud-head::after {
  content: ""; flex: 1; height: 1px;
  background: linear-gradient(90deg, rgba(79, 243, 255, 0.4), transparent);
}
.hud-note {
  font-family: var(--hud-mono); font-size: 9px; line-height: 1.6;
  letter-spacing: 0.06em; color: #6f93a1; margin: 8px 0 0;
}
.hud-note a { color: var(--hud-cyan); }

/* ---------- key/value + bars ---------- */
.kv {
  display: flex; justify-content: space-between; gap: 10px;
  padding: 5px 0; border-bottom: 1px dashed rgba(79, 243, 255, 0.12);
  font-family: var(--hud-mono); font-size: 10px; letter-spacing: 0.09em;
}
.kv:last-child { border-bottom: none; }
.kv span { color: #6f93a1; text-transform: uppercase; }
.kv b { color: #e6f9ff; font-weight: 500; text-align: right; }
.hud-bars { display: flex; flex-direction: column; gap: 9px; margin-top: 4px; }
.hud-bars .row {
  display: flex; justify-content: space-between; gap: 8px;
  font-family: var(--hud-mono); font-size: 9.5px; letter-spacing: 0.1em;
  color: #a9c6d1; text-transform: uppercase;
}
.hud-bars .row b { color: #eafcff; }
.hud-bars .bar { height: 3px; background: rgba(79, 243, 255, 0.12); margin-top: 4px; }
.hud-bars .bar i { display: block; height: 100%; }

/* ---------- badges ---------- */
.badge {
  display: inline-block; padding: 1px 7px; font-family: var(--hud-mono);
  font-size: 8.5px; letter-spacing: 0.2em; text-transform: uppercase;
  border: 1px solid currentColor;
}
.badge.red { color: var(--hud-red); background: rgba(255, 59, 59, 0.1); }
.badge.orange { color: var(--hud-orange); background: rgba(255, 159, 28, 0.1); }
.badge.green { color: var(--hud-green); background: rgba(61, 220, 132, 0.1); }
.badge.unknown { color: #8fa3ad; background: rgba(143, 163, 173, 0.1); }
.badge.dim { color: #7fa6b4; border-color: rgba(79, 243, 255, 0.25); }

/* ---------- target panel ---------- */
.target-name {
  font-family: var(--hud-mono); font-size: 14px; line-height: 1.35;
  letter-spacing: 0.04em; color: #f2feff; margin: 4px 0 8px;
}
.target-meta {
  font-family: var(--hud-mono); font-size: 9px; letter-spacing: 0.14em;
  text-transform: uppercase; color: #7fa6b4; margin-bottom: 10px;
}
.target-desc {
  font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
  font-size: 11.5px; line-height: 1.62; color: #b9d3dc;
  max-height: 190px; overflow-y: auto; padding-right: 4px;
  border-left: 2px solid rgba(79, 243, 255, 0.25); padding-left: 9px;
  margin: 6px 0 10px;
}
.hud-links {
  display: flex; flex-wrap: wrap; gap: 10px; margin: 4px 0 10px;
  font-family: var(--hud-mono); font-size: 9px; letter-spacing: 0.14em;
  text-transform: uppercase;
}
.hud-links a { text-decoration: none; border-bottom: 1px solid rgba(79, 243, 255, 0.4); }
.hud-list { list-style: none; margin: 6px 0 0; padding: 0; }
.hud-list li {
  padding: 6px 0; border-bottom: 1px dashed rgba(79, 243, 255, 0.1);
  font-family: var(--hud-mono); font-size: 10px; line-height: 1.45;
}
.hud-list li:last-child { border-bottom: none; }
.hud-list .meta { display: block; color: #6f93a1; font-size: 8.5px; letter-spacing: 0.1em; margin-top: 2px; }
.hud-empty {
  font-family: var(--hud-mono); font-size: 9.5px; letter-spacing: 0.16em;
  text-transform: uppercase; color: #6f93a1; padding: 14px 0;
}

/* ---------- globe slot ---------- */
.st-key-globe-wrap iframe {
  border: 1px solid var(--hud-line) !important;
  background: #04070b !important;
  box-shadow: 0 0 70px rgba(79, 243, 255, 0.09), inset 0 0 120px rgba(4, 7, 11, 0.9);
  height: min(72vh, 760px) !important;
  min-height: 480px;
  display: block;
}
.hud-globe-cap {
  display: flex; justify-content: space-between; gap: 12px;
  font-family: var(--hud-mono); font-size: 8.5px; letter-spacing: 0.22em;
  text-transform: uppercase; color: #6f93a1; padding: 6px 2px 2px;
}
.hud-globe-cap b { color: var(--hud-cyan); font-weight: 500; }

/* ---------- footer ---------- */
.hud-bottom {
  display: flex; justify-content: space-between; gap: 16px; flex-wrap: wrap;
  border-top: 1px solid var(--hud-line); margin-top: 10px; padding: 8px 2px 6px;
  font-family: var(--hud-mono); font-size: 8.5px; letter-spacing: 0.2em;
  text-transform: uppercase; color: #5f7f8c;
}
.hud-bottom b { color: #9fc4d0; font-weight: 500; }
.hud-bottom .warn { color: #d8a24a; }

/* ---------- streamlit widgets ---------- */
[data-testid="stCheckbox"] label p,
[data-testid="stToggle"] label p {
  font-family: var(--hud-mono) !important; font-size: 10px !important;
  letter-spacing: 0.14em; text-transform: uppercase; color: #a9c6d1 !important;
}
[data-baseweb="checkbox"] div[role="checkbox"] {
  background: #060e14 !important;
  border-color: rgba(79, 243, 255, 0.35) !important;
  border-radius: 1px !important;
}
[data-baseweb="checkbox"] div[aria-checked="true"] {
  background: var(--hud-cyan) !important;
  border-color: var(--hud-cyan) !important;
}
[data-baseweb="checkbox"] div[aria-checked="true"] svg { fill: #04070b !important; }
[data-testid="stSlider"] label p, [data-testid="stSelectbox"] label p,
[data-testid="stWidgetLabel"] p {
  font-family: var(--hud-mono) !important; font-size: 8.5px !important;
  letter-spacing: 0.18em; text-transform: uppercase; color: #6f93a1 !important;
}
[data-baseweb="select"] > div {
  background: #060e14 !important; border-color: rgba(79, 243, 255, 0.28) !important;
  border-radius: 1px !important; min-height: 32px;
  font-family: var(--hud-mono) !important; font-size: 10px !important;
  letter-spacing: 0.1em; color: #cfe3ea !important;
}
[data-baseweb="popover"] li { font-family: var(--hud-mono) !important; font-size: 10px !important; }
[data-testid="stSlider"] [role="slider"] { background: var(--hud-cyan) !important; }
[data-testid="stCaptionContainer"] p, [data-testid="stCaptionContainer"] {
  font-family: var(--hud-mono) !important; font-size: 8.5px !important;
  letter-spacing: 0.09em; color: #6f93a1 !important; line-height: 1.6 !important;
}
.stButton button, [data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-primary"] {
  width: 100%; border-radius: 1px !important;
  font-family: var(--hud-mono) !important; font-size: 9.5px !important;
  letter-spacing: 0.2em; text-transform: uppercase;
  background: rgba(79, 243, 255, 0.07) !important;
  border: 1px solid rgba(79, 243, 255, 0.3) !important;
  color: #bfeef7 !important; padding: 0.4rem 0.5rem !important;
  min-height: 30px;
}
[data-testid="stBaseButton-primary"] {
  background: rgba(79, 243, 255, 0.16) !important;
  border-color: var(--hud-cyan) !important; color: #f0ffff !important;
}
.stButton button:hover, [data-testid="stBaseButton-secondary"]:hover,
[data-testid="stBaseButton-primary"]:hover {
  background: rgba(79, 243, 255, 0.22) !important;
  border-color: var(--hud-cyan) !important; color: #ffffff !important;
}
[data-testid="stAlert"] {
  border-radius: 1px; border: 1px solid rgba(255, 159, 28, 0.4);
  background: rgba(255, 159, 28, 0.08);
  font-family: var(--hud-mono); font-size: 10px; letter-spacing: 0.06em;
}
[data-testid="stAlert"] p { color: #ffd9a8 !important; font-size: 10px !important; }
hr { border-color: var(--hud-line); margin: 12px 0; }
[data-testid="stExpander"] details {
  border: 1px solid var(--hud-line) !important; border-radius: 1px !important;
  background: rgba(6, 14, 20, 0.75) !important;
}
[data-testid="stExpander"] summary p {
  font-family: var(--hud-mono); font-size: 9.5px; letter-spacing: 0.12em;
  text-transform: uppercase; color: #a9c6d1;
}
[data-testid="stDataFrame"] { border: 1px solid var(--hud-line); }

/* ---------- typography guards against Streamlit defaults ---------- */
[data-testid="stMarkdownContainer"] .hud-head {
  display: flex; align-items: center; gap: 8px;
  margin: 14px 0 8px !important; font-family: var(--hud-mono) !important;
  font-size: 9px !important; line-height: 1.4 !important;
  letter-spacing: 0.26em !important; text-transform: uppercase;
  color: var(--hud-cyan) !important;
}
[data-testid="stMarkdownContainer"] .hud-note {
  font-family: var(--hud-mono) !important; font-size: 9px !important;
  line-height: 1.65 !important; letter-spacing: 0.07em !important;
  color: #6f93a1 !important; margin: 7px 0 0 !important;
}
[data-testid="stMarkdownContainer"] .kv {
  font-family: var(--hud-mono) !important; font-size: 10px !important;
  letter-spacing: 0.09em !important; margin: 0 !important;
}
[data-testid="stMarkdownContainer"] .kv span { color: #6f93a1 !important; }
[data-testid="stMarkdownContainer"] .kv b { color: #e6f9ff !important; font-weight: 500 !important; }
[data-testid="stMarkdownContainer"] .hud-title {
  font-size: 27px !important; letter-spacing: 0.16em !important;
  margin: 0 !important; line-height: 1 !important;
}
[data-testid="stMarkdownContainer"] .hud-sub {
  font-size: 8.5px !important; letter-spacing: 0.42em !important;
  margin: 6px 0 0 !important; color: var(--hud-dim) !important;
}
[data-testid="stMarkdownContainer"] .hud-stat {
  font-size: 9px !important; letter-spacing: 0.2em !important; margin: 0 !important;
}
[data-testid="stMarkdownContainer"] .hud-rec {
  font-size: 9.5px !important; letter-spacing: 0.14em !important; margin: 0 !important;
}
[data-testid="stMarkdownContainer"] .badge {
  font-size: 8.5px !important; letter-spacing: 0.2em !important;
  margin: 0 !important; padding: 1px 7px !important; line-height: 1.5 !important;
}
[data-testid="stMarkdownContainer"] .target-name {
  font-size: 14px !important; line-height: 1.35 !important; margin: 4px 0 8px !important;
}
[data-testid="stMarkdownContainer"] .target-meta {
  font-size: 9px !important; letter-spacing: 0.14em !important;
  margin: 0 0 10px !important; color: #7fa6b4 !important;
}
[data-testid="stMarkdownContainer"] .target-desc {
  font-size: 11.5px !important; line-height: 1.62 !important;
  font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif !important;
}
[data-testid="stMarkdownContainer"] .hud-links { font-size: 9px !important; }
[data-testid="stMarkdownContainer"] .hud-empty {
  font-size: 9.5px !important; letter-spacing: 0.16em !important;
  margin: 0 !important;
}
[data-testid="stMarkdownContainer"] .hud-list li {
  font-size: 10px !important; line-height: 1.45 !important; margin: 0 !important;
}
[data-testid="stMarkdownContainer"] .hud-list .meta {
  font-size: 8.5px !important; letter-spacing: 0.1em !important;
}
[data-testid="stMarkdownContainer"] .hud-globe-cap {
  font-size: 8.5px !important; letter-spacing: 0.22em !important;
  margin: 0 !important; padding: 6px 2px 2px !important;
}
[data-testid="stMarkdownContainer"] .hud-bottom {
  font-size: 8.5px !important; letter-spacing: 0.2em !important;
  margin: 10px 0 0 !important;
}
[data-testid="stMarkdownContainer"] .hud-bars .row {
  font-size: 9.5px !important; letter-spacing: 0.1em !important; margin: 0 !important;
}
</style>
"""


def inject_theme() -> None:
    st.markdown('<div class="hud-vignette"></div>', unsafe_allow_html=True)
    st.markdown('<div class="hud-corners"></div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="hud-rail left">GOD\'S EYE // WILDFIRE SURVEILLANCE '
        "&nbsp;·&nbsp; GDACS · NASA FIRMS</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="hud-rail right">LIVE FEED // SITUATIONAL AWARENESS</div>',
        unsafe_allow_html=True,
    )
    st.markdown(HUD_CSS, unsafe_allow_html=True)


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
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _fmt_day(value) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{pd.Timestamp(value):%Y-%m-%d}"


def _safe_url(value) -> str | None:
    text = str(value or "")
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return text
    return None


def _safe_text(value, fallback: str = "—") -> str:
    if value is None:
        return fallback
    if not isinstance(value, str):
        try:
            if pd.isna(value):
                return fallback
        except (TypeError, ValueError):
            pass
    text = str(value).strip()
    return html.escape(text) if text else fallback


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


def get_report_news(event_id: str, name: str, country: str | None, timespan: str):
    """Fetch related news for one report, cached per report for 15 minutes."""
    cache = st.session_state.setdefault("_news_cache", {})
    entry = cache.get(event_id)
    now = time.monotonic()
    if entry is not None:
        ttl = NEWS_ERROR_TTL_SECONDS if entry["error"] else NEWS_TTL_SECONDS
        if (now - entry["fetched_at"]) < ttl:
            return entry["data"], entry["error"], entry["fetched_wall"], entry["source"]

    try:
        data = search_news(
            build_report_query(name, country), max_records=25, timespan=timespan
        )
    except (RuntimeError, ValueError) as gdelt_exc:
        try:
            data = search_google_news(name, country, max_records=25, timespan=timespan)
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
    if from_date is None or pd.isna(from_date):
        return "2w"
    start = pd.Timestamp(from_date)
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
    days = (pd.Timestamp.now(tz="UTC") - start).days + 2
    return f"{min(max(days, 7), 90)}d"


def filter_reports(
    reports: pd.DataFrame, include_ended: bool, alert_levels: list[str]
) -> pd.DataFrame:
    filtered = reports[reports["alert_level"].isin(alert_levels)]
    if not include_ended and "is_current" in filtered.columns:
        filtered = filtered[filtered["is_current"]]
    return filtered


def _report_tip(row: pd.Series) -> str:
    parts = [f"<b>{_safe_text(row.get('name'), 'WILDFIRE')}</b>"]
    meta = [_safe_text(row.get("country"), "—")]
    meta.append(f"{str(row.get('alert_level', 'Unknown')).upper()} ALERT")
    parts.append(f"<span>{' · '.join(meta)}</span>")
    if pd.notna(row.get("from_date")):
        parts.append(f"<span>FROM {_fmt_day(row.get('from_date'))}</span>")
    return "<br/>".join(parts)


def _report_tag(row: pd.Series) -> str:
    iso = str(row.get("iso3") or "").strip().upper()
    if iso:
        return iso[:3]
    country = str(row.get("country") or "").strip()
    if country:
        return country[:3].upper()
    score = row.get("alert_score")
    if score is not None and pd.notna(score):
        return f"{float(score):.1f}"
    return str(row.get("alert_level", "WF")).upper()[:3]


def globe_report_payload(reports: pd.DataFrame) -> list[dict]:
    payload: list[dict] = []
    # Spread reports that share one coordinate so each is individually visible.
    offsets: dict[tuple[float, float], int] = {}
    for _, row in reports.iterrows():
        lat, lng = row.get("latitude"), row.get("longitude")
        if pd.isna(lat) or pd.isna(lng):
            continue
        lat, lng = float(lat), float(lng)
        key = (round(lat, 2), round(lng, 2))
        seen = offsets.get(key, 0)
        offsets[key] = seen + 1
        if seen:
            ring = 0.32 * (1 + seen // 6)
            angle = seen * 2.39996
            lat = max(-84.0, min(84.0, lat + ring * math.sin(angle)))
            lng = ((lng + ring * math.cos(angle) + 180.0) % 360.0) - 180.0
        payload.append(
            {
                "id": str(row["event_id"]),
                "lat": lat,
                "lng": lng,
                "level": str(row.get("alert_level", "Unknown")),
                "tag": _report_tag(row),
                "tip": _report_tip(row),
            }
        )
    return payload


def _hotspot_tip(row: pd.Series) -> str:
    parts = ["<b>SATELLITE HOTSPOT</b>"]
    observed = " ".join(
        str(row.get(key, "")) for key in ("acq_date", "acq_time") if str(row.get(key, ""))
    )
    meta = [html.escape(observed or "—"), html.escape(str(row.get("satellite", "")))]
    parts.append(f"<span>{' · '.join(part for part in meta if part)}</span>")
    parts.append("<span>NOT A VERIFIED PERIMETER</span>")
    return "<br/>".join(parts)


def globe_hotspot_payload(hotspots: pd.DataFrame | None) -> tuple[list[dict], int]:
    if hotspots is None or hotspots.empty:
        return [], 0
    frame = hotspots.dropna(subset=["latitude", "longitude"])
    total = len(frame)
    if total > HOTSPOT_RENDER_LIMIT:
        if "frp" in frame.columns:
            frame = frame.sort_values("frp", ascending=False, kind="stable")
        frame = frame.head(HOTSPOT_RENDER_LIMIT)
    payload = [
        {
            "lat": float(row["latitude"]),
            "lng": float(row["longitude"]),
            "date": str(row.get("acq_date", "")),
            "time": str(row.get("acq_time", "")),
            "satellite": str(row.get("satellite", "")),
            "confidence": str(row.get("confidence", "")),
            "frp": "" if pd.isna(row.get("frp")) else f"{float(row.get('frp')):.1f}",
            "tip": _hotspot_tip(row),
        }
        for _, row in frame.iterrows()
    ]
    return payload, total


def _topbar(reports: pd.DataFrame, status: str, updated: str) -> None:
    counts = reports["alert_level"].value_counts() if not reports.empty else {}
    st.markdown(
        f"""
<div class="hud-top">
  <div class="hud-brand">
    <div class="hud-mark">◎</div>
    <div>
      <div class="hud-title">GOD'S EYE <em>VIEW</em></div>
      <div class="hud-sub">NO PLACE LEFT BEHIND · GLOBAL WILDFIRE SURVEILLANCE</div>
    </div>
  </div>
  <div class="hud-stats">
    <div class="hud-stat">Red Alert<b class="red">{int(counts.get('Red', 0)):,}</b></div>
    <div class="hud-stat">Orange<b class="orange">{int(counts.get('Orange', 0)):,}</b></div>
    <div class="hud-stat">Green<b class="green">{int(counts.get('Green', 0)):,}</b></div>
    <div class="hud-stat">Tracked<b>{len(reports):,}</b></div>
    <div class="hud-rec"><i></i>{html.escape(status)} <b>{html.escape(updated)}</b></div>
  </div>
</div>""",
        unsafe_allow_html=True,
    )


def _render_target(row: pd.Series) -> None:
    level = str(row.get("alert_level", "Unknown"))
    level_class = level.lower() if level in ALERT_LEVELS else "unknown"
    links = []
    for label, url in (
        ("Official report", row.get("report_url")),
        ("Event details", row.get("details_url")),
    ):
        safe = _safe_url(url)
        if safe:
            links.append(
                f'<a href="{html.escape(safe, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer">{html.escape(label)} →</a>'
            )

    score = row.get("alert_score")
    score_text = f"{float(score):.2f}" if score is not None and pd.notna(score) else "—"
    lat, lng = row.get("latitude"), row.get("longitude")
    coords = (
        f"{float(lat):.2f}, {float(lng):.2f}"
        if pd.notna(lat) and pd.notna(lng)
        else "—"
    )
    status = "ACTIVE" if bool(row.get("is_current")) else "ENDED"
    description = _safe_text(row.get("description"), "No description supplied by GDACS.")
    if len(description) > 900:
        description = description[:900] + "…"

    st.markdown(
        f"""
<div class="hud-head">Target Lock</div>
<div class="target-name">{_safe_text(row.get('name'), 'WILDFIRE REPORT')}</div>
<div class="target-meta">
  <span class="badge {level_class}">{html.escape(level)}</span>
  &nbsp;{_safe_text(row.get('country'))} &nbsp;·&nbsp; {status}
</div>
<div class="kv"><span>Event id</span><b>{_safe_text(row.get('event_id'))}</b></div>
<div class="kv"><span>First report</span><b>{_fmt_day(row.get('from_date'))}</b></div>
<div class="kv"><span>Last report</span><b>{_fmt_day(row.get('to_date'))}</b></div>
<div class="kv"><span>Alert score</span><b>{score_text}</b></div>
<div class="kv"><span>Coordinates</span><b>{coords}</b></div>
<div class="hud-head">Situation</div>
<div class="target-desc">{description}</div>
<div class="hud-links">{' '.join(links) or '—'}</div>
""",
        unsafe_allow_html=True,
    )


def _render_related_news(row: pd.Series) -> None:
    event_id = str(row.get("event_id"))
    requested = st.session_state.setdefault("_news_requested", set())
    if st.button(
        "Query live headlines",
        key=f"news-{event_id}",
        help="Search GDELT, then Google News, for this report.",
    ):
        requested.add(event_id)

    if event_id not in requested:
        st.markdown(
            '<p class="hud-note">Headlines are fetched on demand from GDELT '
            "(Google News fallback). Matches are not verification.</p>",
            unsafe_allow_html=True,
        )
        return

    country = (
        str(row.get("country"))
        if pd.notna(row.get("country")) and str(row.get("country")).strip()
        else None
    )
    data, error, fetched_wall, source = get_report_news(
        event_id,
        str(row.get("name") or "wildfire"),
        country,
        _news_timespan(row.get("from_date")),
    )
    if error:
        st.warning(f"Headline feed unavailable: {error}")
        return
    if data is None or data.empty:
        st.markdown(
            '<p class="hud-empty">No matching English headlines.</p>',
            unsafe_allow_html=True,
        )
        return

    items = []
    for article in data.head(8).itertuples(index=False):
        title = html.escape(article.title or "(untitled)")
        safe = _safe_url(article.url)
        link = (
            f'<a href="{html.escape(safe, quote=True)}" target="_blank" '
            f'rel="noopener noreferrer">{title}</a>'
            if safe
            else title
        )
        seen = f"{article.seen_at:%Y-%m-%d %H:%MZ}" if pd.notna(article.seen_at) else ""
        meta = " · ".join(part for part in (article.domain or "", seen) if part)
        items.append(f'<li>{link}<span class="meta">{html.escape(meta)}</span></li>')
    st.markdown(
        f'<p class="hud-note">{len(data):,} articles · {html.escape(str(source))} · '
        f"retrieved {_fmt_timestamp(fetched_wall)}</p>"
        f'<ul class="hud-list">{"".join(items)}</ul>',
        unsafe_allow_html=True,
    )


def _render_hotspot(hotspot: dict) -> None:
    observed = " ".join(
        part for part in (hotspot.get("date", ""), hotspot.get("time", "")) if part
    )
    st.markdown(
        f"""
<div class="hud-head">Target Lock</div>
<div class="target-name">Satellite hotspot detection</div>
<div class="target-meta">
  <span class="badge dim">Unverified</span>
  &nbsp;{html.escape(str(hotspot.get('satellite') or 'FIRMS'))}
</div>
<div class="kv"><span>Observed</span><b>{html.escape(observed or '—')} UTC</b></div>
<div class="kv"><span>Confidence</span><b>{html.escape(str(hotspot.get('confidence') or '—'))}</b></div>
<div class="kv"><span>FRP</span><b>{html.escape(str(hotspot.get('frp') or '—'))} MW</b></div>
<div class="kv"><span>Coordinates</span><b>{float(hotspot.get('lat', 0)):.2f}, {float(hotspot.get('lng', 0)):.2f}</b></div>
<p class="hud-note">A thermal detection is not a verified fire perimeter,
report, or forecast.</p>
""",
        unsafe_allow_html=True,
    )


def _render_summary(
    reports: pd.DataFrame,
    counts,
    snapshot: FeedSnapshot,
    window_days: int,
    countries: int,
) -> None:
    total = len(reports)
    bars = []
    for level in ALERT_LEVELS:
        count = int(counts.get(level, 0)) if total else 0
        share = (count / total * 100) if total else 0
        bars.append(
            f'<div><div class="row"><span>{level}</span><b>{count:,}</b></div>'
            f'<div class="bar"><i style="width:{share:.1f}%;'
            f'background:{ALERT_COLORS[level]}"></i></div></div>'
        )

    top_countries = pd.Series(dtype=int)
    if "country" in reports.columns and not reports.empty:
        top_countries = (
            reports["country"]
            .dropna()
            .astype(str)
            .str.strip()
            .replace("", pd.NA)
            .dropna()
            .value_counts()
            .head(5)
        )
    if top_countries.empty:
        country_rows = '<li>No country data</li>'
    else:
        peak = int(top_countries.max()) or 1
        country_rows = "".join(
            f'<li><div class="row"><span>{html.escape(str(name))}</span>'
            f"<b>{int(value):,}</b></div>"
            f'<div class="bar"><i style="width:{value / peak * 100:.0f}%;'
            f'background:rgba(79,243,255,.75)"></i></div></li>'
            for name, value in top_countries.items()
        )

    st.markdown(
        f"""
<div class="hud-head">Situation</div>
<div class="kv"><span>Tracked reports</span><b>{total:,}</b></div>
<div class="kv"><span>Countries</span><b>{countries:,}</b></div>
<div class="kv"><span>Window</span><b>{window_days} days</b></div>
<div class="kv"><span>Last sync</span><b>{_fmt_timestamp(snapshot.fetched_wall)}</b></div>
<div class="hud-head">Alert mix</div>
<div class="hud-bars">{''.join(bars)}</div>
<div class="hud-head">Top regions</div>
<ul class="hud-bars hud-list">{country_rows}</ul>
<p class="hud-note">Select a marker on the globe, or a signal below, to open
its report and situation panel.</p>
""",
        unsafe_allow_html=True,
    )


def _render_signal_list(ordered: pd.DataFrame) -> None:
    st.markdown('<div class="hud-head">Signals</div>', unsafe_allow_html=True)
    if ordered.empty:
        st.markdown(
            '<p class="hud-empty">No reports match the current filters.</p>',
            unsafe_allow_html=True,
        )
        return
    for _, row in ordered.head(6).iterrows():
        level = str(row.get("alert_level", "Unknown"))
        level_class = level.lower() if level in ALERT_LEVELS else "unknown"
        st.markdown(
            f'<p class="hud-list" style="margin:0"><span class="badge {level_class}">'
            f"{html.escape(level)}</span> {_safe_text(row.get('name'), 'WILDFIRE')}"
            f'<span class="meta">{_safe_text(row.get("country"))} · '
            f"{_fmt_day(row.get('from_date'))}</span></p>",
            unsafe_allow_html=True,
        )
        if st.button("Open report", key=f"sig-{row['event_id']}"):
            st.session_state["_selected_event"] = str(row["event_id"])
            st.session_state["_selected_hotspot"] = None
            st.rerun()


def _render_footer(reports: pd.DataFrame, hotspots_total: int, window_days: int) -> None:
    st.markdown(
        f"""
<div class="hud-bottom">
  <div>SRC <b>GDACS · NASA FIRMS · GDELT</b></div>
  <div>WINDOW <b>{window_days}D</b> &nbsp;·&nbsp; REPORTS <b>{len(reports):,}</b>
    &nbsp;·&nbsp; HOTSPOTS <b>{hotspots_total:,}</b></div>
  <div class="warn">Situational awareness only · not an emergency decision tool</div>
</div>""",
        unsafe_allow_html=True,
    )


def _render_controls(
    map_key: str,
    has_firms: bool,
    reports: pd.DataFrame,
    hotspots: pd.DataFrame | None,
    window_days: int,
) -> None:
    st.markdown('<div class="hud-head">Data Layers</div>', unsafe_allow_html=True)
    if st.button("Resync feeds", type="primary", key="resync"):
        st.session_state["_feed_states"] = {}
        st.session_state["_news_cache"] = {}
        st.session_state["_news_requested"] = set()
        st.rerun()
    st.markdown(
        '<p class="hud-note">AUTO-SYNC · 5 MIN · GDACS + FIRMS</p>',
        unsafe_allow_html=True,
    )

    st.markdown('<div class="hud-head">Reports</div>', unsafe_allow_html=True)
    st.selectbox(
        "Look-back window",
        REPORT_WINDOWS,
        index=REPORT_WINDOWS.index(window_days) if window_days in REPORT_WINDOWS else 1,
        key="window_days",
        format_func=lambda value: f"{value} days",
    )
    counts = reports["alert_level"].value_counts() if not reports.empty else {}
    for level in ALERT_LEVELS:
        st.checkbox(
            f"{level}  ·  {int(counts.get(level, 0)):,}",
            value=True,
            key=f"level_{level.lower()}",
        )
    st.checkbox("Include ended reports", value=False, key="include_ended")

    st.markdown('<div class="hud-head">Satellite</div>', unsafe_allow_html=True)
    st.toggle(
        "FIRMS detections",
        value=has_firms,
        disabled=not has_firms,
        key="show_hotspots",
        help="NASA FIRMS near-real-time thermal detections.",
    )
    if has_firms:
        st.selectbox("Sensor", SUPPORTED_SOURCES, index=2, key="firms_source")
        st.slider("Look-back", min_value=1, max_value=5, value=1, key="firms_days")
        if hotspots is not None and not hotspots.empty:
            st.caption(f"{len(hotspots):,} detections loaded worldwide.")
    else:
        st.markdown(
            '<p class="hud-note">Layer locked. Add a free <a href="'
            'https://firms.modaps.eosdis.nasa.gov/api/map_key/" target="_blank" '
            'rel="noopener noreferrer">NASA FIRMS map key</a> as FIRMS_MAP_KEY '
            "to overlay thermal detections.</p>",
            unsafe_allow_html=True,
        )

    st.markdown('<div class="hud-head">Events</div>', unsafe_allow_html=True)
    hotspot_label = (
        f"{len(hotspots):,}"
        if hotspots is not None and not hotspots.empty
        else ("KEY READY" if map_key else "LOCKED")
    )
    st.markdown(
        f"""
<div class="kv"><span>Wildfire reports</span><b>{len(reports):,}</b></div>
<div class="kv"><span>Satellite detections</span><b>{hotspot_label}</b></div>
""",
        unsafe_allow_html=True,
    )


def _read_settings(has_firms: bool) -> tuple[int, list[str], bool, bool, str, int]:
    window_days = int(st.session_state.get("window_days", 14))
    alert_levels = [
        level
        for level in ALERT_LEVELS
        if st.session_state.get(f"level_{level.lower()}", True)
    ]
    include_ended = bool(st.session_state.get("include_ended", False))
    show_hotspots = has_firms and bool(st.session_state.get("show_hotspots", True))
    source = st.session_state.get("firms_source", SUPPORTED_SOURCES[2])
    day_range = int(st.session_state.get("firms_days", 1))
    return window_days, alert_levels, include_ended, show_hotspots, source, day_range


def main() -> None:
    st.set_page_config(
        page_title="GOD'S EYE VIEW | Global Wildfire Surveillance",
        page_icon="◎",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    inject_theme()

    map_key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    has_firms = bool(map_key)

    @st.fragment(run_every=REFRESH_SECONDS)
    def console() -> None:
        (
            window_days,
            alert_levels,
            include_ended,
            show_hotspots,
            source,
            day_range,
        ) = _read_settings(has_firms)

        gdacs_snapshot = load_feed(
            "gdacs",
            GDACS_TTL_SECONDS,
            lambda: fetch_wildfire_events(days=window_days),
        )
        all_reports = (
            gdacs_snapshot.data
            if gdacs_snapshot.data is not None
            else pd.DataFrame(columns=["event_id", "alert_level"])
        )

        firms_snapshot = None
        if show_hotspots:
            firms_snapshot = load_feed(
                "firms",
                FIRMS_TTL_SECONDS,
                lambda: fetch_hotspots(
                    map_key=map_key,
                    source=source,
                    area=AREA_BOUNDS["Global"],
                    day_range=day_range,
                ),
            )

        counts = (
            all_reports["alert_level"].value_counts()
            if not all_reports.empty
            else pd.Series(dtype=int)
        )
        _topbar(
            all_reports,
            "FEED LIVE" if gdacs_snapshot.error is None else "FEED DEGRADED",
            _fmt_timestamp(gdacs_snapshot.fetched_wall),
        )

        left, center, right = st.columns([0.95, 2.5, 1.08], gap="small")

        reports = (
            filter_reports(all_reports, include_ended, alert_levels)
            if not all_reports.empty and alert_levels
            else all_reports.head(0)
        )
        ordered = reports.assign(
            _rank=reports["alert_level"].map(ALERT_RANK)
        ).sort_values(["_rank", "from_date"], ascending=[True, False], kind="stable")
        report_payload = globe_report_payload(ordered)

        hotspots = (
            firms_snapshot.data
            if firms_snapshot is not None and show_hotspots
            else None
        )
        hotspot_payload, hotspot_total = globe_hotspot_payload(hotspots)

        countries = (
            int(
                reports["country"]
                .dropna()
                .astype(str)
                .str.strip()
                .replace("", pd.NA)
                .dropna()
                .nunique()
            )
            if "country" in reports.columns and not reports.empty
            else 0
        )

        with left:
            with st.container(key="hud-left"):
                _render_controls(
                    map_key,
                    has_firms,
                    all_reports,
                    firms_snapshot.data if firms_snapshot is not None else None,
                    window_days,
                )

        with center:
            with st.container(key="globe-wrap"):
                selected = _wildfire_globe(
                    reports=report_payload,
                    hotspots=hotspot_payload,
                    show_hotspots=bool(show_hotspots),
                    selected=st.session_state.get("_selected_event"),
                    key="globe",
                    default=None,
                )
                st.markdown(
                    f'<div class="hud-globe-cap"><span>ORBIT <b>GLOBAL</b> · '
                    f"PLOTTED <b>{len(report_payload):,}</b></span>"
                    f"<span>THERMAL <b>{hotspot_total:,}</b> · FIRMS</span>"
                    f"<span>CLICK A MARKER TO LOCK TARGET</span></div>",
                    unsafe_allow_html=True,
                )

        if isinstance(selected, dict) and selected.get("ts"):
            if selected["ts"] != st.session_state.get("_globe_ts"):
                st.session_state["_globe_ts"] = selected["ts"]
                kind = selected.get("kind")
                if kind == "report":
                    st.session_state["_selected_event"] = str(selected.get("id"))
                    st.session_state["_selected_hotspot"] = None
                elif kind == "hotspot":
                    st.session_state["_selected_hotspot"] = selected
                    st.session_state["_selected_event"] = None
                else:
                    st.session_state["_selected_event"] = None
                    st.session_state["_selected_hotspot"] = None

        with right:
            with st.container(key="hud-right"):
                selected_id = st.session_state.get("_selected_event")
                hotspot = st.session_state.get("_selected_hotspot")

                if hotspot:
                    _render_hotspot(hotspot)
                    if st.button("Release target", key="release-hotspot"):
                        st.session_state["_selected_hotspot"] = None
                        st.rerun()
                elif selected_id is not None:
                    match = all_reports[
                        all_reports["event_id"].astype(str) == str(selected_id)
                    ]
                    if not match.empty:
                        _render_target(match.iloc[0])
                        _render_related_news(match.iloc[0])
                        if st.button("Release target", key="release-target"):
                            st.session_state["_selected_event"] = None
                            st.rerun()
                    else:
                        st.session_state["_selected_event"] = None
                        _render_summary(
                            reports, counts, gdacs_snapshot, window_days, countries
                        )
                else:
                    _render_summary(
                        reports, counts, gdacs_snapshot, window_days, countries
                    )

                if gdacs_snapshot.error:
                    st.warning(f"GDACS: {gdacs_snapshot.error}")
                if firms_snapshot is not None and firms_snapshot.error:
                    st.warning(f"FIRMS: {firms_snapshot.error}")

                _render_signal_list(ordered)

        _render_footer(reports, hotspot_total, window_days)

    console()


main()
