from __future__ import annotations

import html
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import pandas as pd
import pydeck as pdk
import streamlit as st

from wildfire_evacuation_risk.firms import (
    AREA_BOUNDS,
    SUPPORTED_SOURCES,
    fetch_hotspots,
)
from wildfire_evacuation_risk.gdacs import fetch_wildfire_events
from wildfire_evacuation_risk.google_news import search_google_news
from wildfire_evacuation_risk.news import build_report_query, search_news

st.set_page_config(
    page_title="WildFireEvac | Global Wildfire Awareness",
    page_icon="🔥",
    layout="wide",
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


def render_related_news(row) -> None:
    st.divider()
    st.markdown("**Related news**")
    st.caption(
        "Headlines matched by keyword from GDELT (Google News fallback); "
        "matches are not verification."
    )

    requested = st.session_state.setdefault("_news_requested", set())
    if st.button("Find related news", key=f"news-button-{row.event_id}"):
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


def render_report_detail(row) -> None:
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

    render_related_news(row)


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

    reports = snapshot.data
    reports = filter_reports(reports, include_ended, alert_levels)

    counts = reports["alert_level"].value_counts() if not reports.empty else {}
    metrics = st.columns(4)
    metrics[0].metric("Reports", f"{len(reports):,}")
    for column, level in zip(metrics[1:], ("Red", "Orange", "Green")):
        column.metric(level, f"{int(counts.get(level, 0)):,}")
    st.caption(f"GDACS wildfire events · last {window_days} days")

    if reports.empty:
        st.info(
            "No wildfire reports matched. Try including ended reports, a "
            "longer window, or more alert levels."
        )
        return

    ordered = reports.assign(
        _rank=reports["alert_level"].map(ALERT_RANK)
    ).sort_values(["_rank", "from_date"], ascending=[True, False], kind="stable")
    for row in ordered.itertuples(index=False):
        label = " · ".join(part for part in (row.name, row.country) if part)
        with st.expander(f"{label} ({row.alert_level})"):
            render_report_detail(row)


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


def _map_view_state(area_name: str) -> pdk.ViewState:
    if area_name == "Global":
        longitude, latitude, zoom = 110, 5, 1
    elif area_name == "Indonesia":
        longitude, latitude, zoom = 118, -2, 4
    else:
        longitude, latitude, zoom = 114, -1, 5
    return pdk.ViewState(
        longitude=longitude,
        latitude=latitude,
        zoom=zoom,
        min_zoom=0,
        max_zoom=16,
        pitch=0,
    )


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
) -> None:
    hotspots = snapshot.data if has_key and snapshot is not None else None
    if has_key and snapshot is not None:
        render_feed_status("FIRMS hotspots", snapshot)

    mapped = events
    if {"latitude", "longitude"}.issubset(events.columns):
        mapped = events.dropna(subset=["latitude", "longitude"]).copy()
    else:
        mapped = events.head(0).copy()
    if hotspots is not None:
        metrics = st.columns(3)
        metrics[0].metric("Detections", f"{len(hotspots):,}")
        metrics[1].metric(
            "Latest observation date",
            str(hotspots["acq_date"].max())
            if len(hotspots) and "acq_date" in hotspots
            else "—",
        )
        metrics[2].metric("Feed", "NASA FIRMS")
        if hotspots.empty:
            st.warning("No satellite detections were returned for this selection.")
    else:
        st.metric("Reports on map", f"{len(mapped):,}")

    if has_key and hotspots is None and snapshot is not None and snapshot.error:
        st.caption("Showing GDACS reports only while the satellite feed is down.")

    layers = []
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
        event_data = ordered.assign(
            label=(
                ordered["name"].astype(str)
                + " ("
                + ordered["alert_level"].astype(str)
                + ")"
            ).map(html.escape),
            detail=detail,
            fill_color=ordered["alert_level"].map(
                lambda level: _rgb(ALERT_COLORS.get(level, ALERT_COLORS["Unknown"]))
            ),
        )
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=event_data,
                get_position="[longitude, latitude]",
                get_fill_color="fill_color",
                get_line_color="[255, 255, 255, 220]",
                get_radius=35000,
                radius_min_pixels=5,
                radius_max_pixels=18,
                line_width_min_pixels=1,
                stroked=True,
                pickable=True,
            )
        )

    if hotspots is not None and not hotspots.empty:
        hotspot_data = hotspots.assign(
            label="Satellite hotspot",
            detail=_hotspot_tooltips(hotspots),
        )
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=hotspot_data,
                get_position="[longitude, latitude]",
                get_fill_color="[220, 70, 32, 190]",
                get_line_color="[90, 35, 20, 220]",
                get_radius=2500,
                radius_min_pixels=2,
                radius_max_pixels=12,
                line_width_min_pixels=1,
                stroked=True,
                pickable=True,
            )
        )

    if layers:
        deck = pdk.Deck(
            layers=layers,
            initial_view_state=_map_view_state(area_name),
            map_style=(
                "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"
            ),
            tooltip={
                "html": "<b>{label}</b><br/>{detail}",
                "style": {"backgroundColor": "#202833", "color": "white"},
            },
        )
        st.pydeck_chart(deck, use_container_width=True)
        st.caption(
            "Large points: GDACS wildfire reports, coloured by alert level. "
            "Small points: NASA FIRMS hotspot detections. Detections are not "
            "fire perimeters."
        )
    else:
        st.info("No reports or detections to map for this selection.")

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
    st.title("WildFireEvac · Global wildfire awareness")
    st.caption(
        "Report-first situational awareness: GDACS wildfire events, related "
        "English news, and NASA FIRMS satellite detections. Not an emergency "
        "decision tool."
    )

    map_key = os.environ.get("FIRMS_MAP_KEY", "").strip()

    with st.sidebar:
        st.header("Settings")
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

        st.divider()
        st.header("Satellite feed")
        area_name = st.selectbox("Coverage", list(AREA_BOUNDS))
        source = st.selectbox("Satellite feed", SUPPORTED_SOURCES, index=2)
        day_range = st.slider("Look-back (days)", min_value=1, max_value=5, value=1)
        st.caption(
            "The Live Map plots GDACS reports without a key; NASA FIRMS "
            "hotspot detections need a free FIRMS map key."
        )

    refresh_interval = REFRESH_OPTIONS[interval_label] if auto_refresh else None

    reports_tab, map_tab, forecast_tab = st.tabs(
        ["Reports", "Live Map", "Forecast"]
    )

    with reports_tab:

        @st.fragment(run_every=refresh_interval)
        def reports_view() -> None:
            include_ended = st.checkbox(
                "Include ended reports", value=False, key="include_ended"
            )
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
                    st.session_state.get("include_ended", False),
                    alert_levels,
                )

            if not map_key:
                st.info(
                    "Satellite detections need a free [NASA FIRMS map "
                    "key](https://firms.modaps.eosdis.nasa.gov/api/map_key/). "
                    "Set `FIRMS_MAP_KEY` and restart the app to add hotspots; "
                    "the GDACS report layer works without it."
                )
            render_live_map(snapshot, area_name, events, bool(map_key))

        map_view()

    with forecast_tab:
        st.info(
            "Forecast panels arrive in later phases: detection analytics from "
            "the current feeds, 72-hour fire-weather context for active "
            "clusters, and optional survival-model probabilities."
        )
        st.markdown(
            "- **Detection analytics** — counts and trends from the FIRMS feed\n"
            "- **Fire weather** — temperature, humidity, wind, and "
            "precipitation for active clusters\n"
            "- **Model estimate** — optional, caveated survival-model "
            "probabilities"
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
