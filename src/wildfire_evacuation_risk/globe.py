"""Interactive 3D globe renderer embedded in the Streamlit dashboard."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence


GLOBE_SCRIPT_URL = "https://unpkg.com/globe.gl@2.45.0/dist/globe.gl.min.js"
EARTH_TEXTURE_URL = "https://unpkg.com/three-globe/example/img/earth-blue-marble.jpg"


def build_globe_html(
    reports: Sequence[Mapping[str, object]],
    hotspots: Sequence[Mapping[str, object]],
    longitude: float,
    latitude: float,
    camera_altitude: float,
) -> str:
    """Build the self-contained HTML payload for the interactive globe."""
    data = json.dumps(
        {"reports": list(reports), "hotspots": list(hotspots)},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    html, body {{
      width: 100%; height: 100%; margin: 0; overflow: hidden;
      background: #080b10; font-family: Inter, Arial, sans-serif;
    }}
    #globeWrapper {{
      width: 100%; height: 740px; position: relative; overflow: hidden;
      background: #080b10;
    }}
    #globeContainer {{ position: absolute; inset: 0; }}
    #globeStatus {{
      position: absolute; z-index: 2; top: 16px; left: 16px;
      max-width: min(460px, calc(100% - 64px)); padding: 10px 14px;
      border: 1px solid #3a5660; border-radius: 12px;
      background: rgba(8, 24, 31, .92); color: #bcefff; font-size: 13px;
    }}
    #globeStatus[data-state="error"] {{
      border-color: #783b35; background: rgba(25, 16, 18, .95); color: #ffd5cc;
    }}
    #selectedPoint {{
      display: none; position: absolute; z-index: 2; right: 16px; top: 16px;
      max-width: 340px; padding: 14px 16px; border: 1px solid rgba(255,255,255,.15);
      border-radius: 14px; background: rgba(10,12,17,.92); color: #e8eaed;
      font-size: 13px; line-height: 1.5;
    }}
    #globeCredit {{
      position: absolute; z-index: 2; left: 16px; bottom: 12px;
      color: rgba(255,255,255,.68); font-size: 10px; pointer-events: none;
    }}
  </style>
  <script src="{GLOBE_SCRIPT_URL}"></script>
</head>
<body>
  <div id="globeWrapper">
    <div id="globeContainer" aria-label="Interactive 3D wildfire awareness globe"></div>
    <div id="globeStatus" role="status">Loading global Earth texture…</div>
    <div id="selectedPoint"></div>
    <div id="globeCredit">Static base imagery: NASA Blue Marble Next Generation</div>
  </div>
  <script>
    const data = {data};
    const status = document.getElementById("globeStatus");
    const showError = (error) => {{
      status.textContent = `Globe imagery failed to load. ${{error.message || ""}}`;
      status.dataset.state = "error";
    }};
    if (typeof Globe !== "function") {{
      showError(new Error("The globe renderer did not load from its CDN."));
    }} else {{
      const points = [
        ...data.reports.map((report) => ({{ ...report, kind: "report" }})),
        ...data.hotspots.map((hotspot) => ({{ ...hotspot, kind: "hotspot" }}))
      ];
      const container = document.getElementById("globeContainer");
      const selectedPoint = document.getElementById("selectedPoint");
      const globe = Globe()(container)
        .backgroundColor("#080b10")
        .globeImageUrl({json.dumps(EARTH_TEXTURE_URL)})
        .showAtmosphere(true)
        .atmosphereColor("#42b6d2")
        .atmosphereAltitude(0.16)
        .pointLat("latitude")
        .pointLng("longitude")
        .pointColor((point) => point.kind === "hotspot"
          ? "#ff572e"
          : `rgb(${{point.color[0]}},${{point.color[1]}},${{point.color[2]}})`)
        .pointAltitude((point) => point.kind === "report" ? 0.018 : 0.012)
        .pointRadius((point) => point.kind === "report" ? 0.14 : 0.06)
        .pointResolution(12)
        .pointLabel((point) =>
          `<strong>${{point.label}}</strong><br>${{point.detail}}`
        )
        .pointsData(points)
        .onPointClick((point) => {{
          selectedPoint.innerHTML = `<strong>${{point.label}}</strong><br>${{point.detail}}`;
          selectedPoint.style.display = "block";
        }});

      globe.pointOfView(
        {{ lat: {latitude}, lng: {longitude}, altitude: {camera_altitude} }},
        0
      );
      globe.controls().autoRotate = false;
      globe.controls().enableDamping = true;

      const earthTexture = new Image();
      earthTexture.crossOrigin = "anonymous";
      earthTexture.onload = () => {{ status.style.display = "none"; }};
      earthTexture.onerror = () => showError(new Error("NASA Blue Marble is unavailable."));
      earthTexture.src = {json.dumps(EARTH_TEXTURE_URL)};

      new ResizeObserver(() => {{
        globe.width(container.clientWidth);
        globe.height(container.clientHeight);
      }}).observe(container);
    }}
  </script>
</body>
</html>"""
