from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd
import pydeck as pdk
import streamlit as st

from wildfire_evacuation_risk.firms import (
    AREA_BOUNDS,
    SUPPORTED_SOURCES,
    fetch_hotspots,
)

st.set_page_config(
    page_title="WildFireEvac | Global Hotspots",
    page_icon="🔥",
    layout="wide",
)

st.title("WildFireEvac · Live hotspot map")
st.caption(
    "Satellite fire detections for situational awareness. Hotspots are not "
    "confirmed fire perimeters, evacuation boundaries, or threat forecasts."
)

map_key = os.environ.get("FIRMS_MAP_KEY", "").strip()
if not map_key:
    st.error(
        "FIRMS_MAP_KEY is not set. Add your rotated map key as a local Windows "
        "environment variable, then restart this app."
    )
    st.stop()

with st.sidebar:
    st.header("Data feed")
    area_name = st.selectbox("Coverage", list(AREA_BOUNDS))
    source = st.selectbox("Satellite feed", SUPPORTED_SOURCES, index=2)
    day_range = st.slider("Look-back (days)", min_value=1, max_value=5, value=1)
    refresh = st.button("Refresh hotspots", type="primary")
    st.caption("NASA FIRMS Near Real-Time detections; 1–5 day query range.")

if refresh or "hotspots" not in st.session_state:
    try:
        with st.spinner("Fetching recent satellite detections from FIRMS..."):
            hotspots = fetch_hotspots(
                map_key=map_key,
                source=source,
                area=AREA_BOUNDS[area_name],
                day_range=day_range,
            )
        st.session_state["hotspots"] = hotspots
        st.session_state["feed_selection"] = (area_name, source, day_range)
        st.session_state["fetched_at"] = datetime.now(timezone.utc)
    except (ValueError, RuntimeError) as exc:
        st.error(f"Could not load FIRMS hotspots: {exc}")
        st.stop()

selection = (area_name, source, day_range)
loaded_area, loaded_source, loaded_day_range = st.session_state["feed_selection"]
if st.session_state.get("feed_selection") != selection:
    st.info("Settings changed. Select **Refresh hotspots** to load that feed.")

hotspots: pd.DataFrame = st.session_state["hotspots"]
fetched_at = st.session_state["fetched_at"]
st.caption(
    f"Loaded feed: {loaded_source} · Coverage: {loaded_area} · "
    f"Look-back: {loaded_day_range} day(s) · "
    f"Retrieved: {fetched_at:%Y-%m-%d %H:%M:%S UTC} · "
    "Observation dates are provided by the satellite feed."
)

metric_columns = st.columns(3)
metric_columns[0].metric("Detections", f"{len(hotspots):,}")
metric_columns[1].metric(
    "Latest observation date",
    str(hotspots["acq_date"].max()) if len(hotspots) and "acq_date" in hotspots else "—",
)
metric_columns[2].metric("Feed", "NASA FIRMS")

if hotspots.empty:
    st.warning("No detections were returned for this selection.")
else:
    if loaded_area == "Global":
        longitude, latitude, zoom = 110, 5, 1
    elif loaded_area == "Indonesia":
        longitude, latitude, zoom = 118, -2, 4
    else:
        longitude, latitude, zoom = 114, -1, 5

    layer = pdk.Layer(
        "ScatterplotLayer",
        data=hotspots,
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
    deck = pdk.Deck(
        layers=[layer],
        initial_view_state=pdk.ViewState(
            longitude=longitude,
            latitude=latitude,
            zoom=zoom,
            min_zoom=0,
            max_zoom=16,
            pitch=0,
        ),
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        tooltip={
            "html": (
                "<b>Satellite hotspot</b><br/>"
                "Observed: {acq_date} {acq_time} UTC<br/>"
                "Satellite: {satellite}<br/>"
                "Confidence: {confidence}<br/>"
                "FRP: {frp} MW"
            ),
            "style": {"backgroundColor": "#202833", "color": "white"},
        },
    )
    st.pydeck_chart(deck, use_container_width=True)

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

st.divider()
st.caption("Hotspot data source: NASA FIRMS. Use remains subject to NASA FIRMS terms.")
st.warning(
    "This prototype displays hotspot observations only. It does not identify "
    "verified incident perimeters or issue evacuation advice. Do not use it "
    "for emergency decisions."
)
