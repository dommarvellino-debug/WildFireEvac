"""Backend-only client for NASA FIRMS near-real-time hotspot detections."""

from __future__ import annotations

import csv
import io
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

FIRMS_API_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
SUPPORTED_SOURCES = (
    "VIIRS_NOAA20_NRT",
    "VIIRS_NOAA21_NRT",
    "VIIRS_SNPP_NRT",
    "MODIS_NRT",
)
AREA_BOUNDS = {
    "Global": "world",
    "Indonesia": "95,-11,141,6",
    "Kalimantan": "108,-5,119,4",
}
REQUIRED_COLUMNS = {"latitude", "longitude", "acq_date", "acq_time"}


def fetch_hotspots(
    map_key: str,
    source: str = "VIIRS_SNPP_NRT",
    area: str = "world",
    day_range: int = 1,
    *,
    timeout: float = 30,
) -> pd.DataFrame:
    """Fetch FIRMS CSV detections without exposing credentials in error text."""
    if not map_key or not re.fullmatch(r"[A-Za-z0-9_-]+", map_key):
        raise ValueError("A valid FIRMS map key is required.")
    if source not in SUPPORTED_SOURCES:
        raise ValueError(f"Unsupported FIRMS source: {source!r}")
    if area != "world" and not re.fullmatch(r"-?\d+(?:\.\d+)?(?:,-?\d+(?:\.\d+)?){3}", area):
        raise ValueError("Area must be 'world' or a comma-separated bounding box.")
    if not isinstance(day_range, int) or not 1 <= day_range <= 5:
        raise ValueError("FIRMS day range must be an integer from 1 to 5.")

    url = f"{FIRMS_API_URL}/{map_key}/{source}/{area}/{day_range}"
    request = Request(
        url,
        headers={"User-Agent": "WildFireEvac/0.1 (global hotspot map)"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            content = response.read().decode("utf-8-sig")
    except HTTPError as exc:
        raise RuntimeError(f"FIRMS request failed with HTTP {exc.code}.") from None
    except URLError:
        raise RuntimeError("Could not connect to the FIRMS service.") from None
    except TimeoutError:
        raise RuntimeError("FIRMS request timed out.") from None

    if not content.strip():
        return pd.DataFrame(columns=sorted(REQUIRED_COLUMNS))

    reader = csv.DictReader(io.StringIO(content))
    if reader.fieldnames is None:
        raise RuntimeError("FIRMS response did not contain a CSV header.")
    columns = {column.strip().lower(): column for column in reader.fieldnames}
    missing = REQUIRED_COLUMNS - set(columns)
    if missing:
        raise RuntimeError(
            "FIRMS response is not a hotspot CSV; required fields missing: "
            f"{sorted(missing)}."
        )

    rows = list(reader)
    normalized = pd.DataFrame(
        [
            {name: row.get(original, "") for name, original in columns.items()}
            for row in rows
        ]
    )
    if normalized.empty:
        return normalized
    for column in ("latitude", "longitude"):
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    normalized = normalized.dropna(subset=["latitude", "longitude"])
    normalized = normalized.loc[
        normalized["latitude"].between(-90, 90)
        & normalized["longitude"].between(-180, 180)
    ].reset_index(drop=True)
    return normalized
