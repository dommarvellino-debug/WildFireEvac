"""Utilities for deriving fixed-horizon threat labels from survival outcomes."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd


def add_horizon_labels(
    data: pd.DataFrame,
    horizons: Iterable[float] = (12, 24, 48, 72),
) -> pd.DataFrame:
    """Add nullable binary labels, excluding cases censored before each horizon."""
    required = {"time_to_hit_hours", "event"}
    missing_columns = required - set(data.columns)
    if missing_columns:
        raise ValueError(f"Data is missing required columns: {sorted(missing_columns)}")

    times = pd.to_numeric(data["time_to_hit_hours"], errors="raise")
    events = pd.to_numeric(data["event"], errors="raise")
    if times.isna().any() or events.isna().any():
        raise ValueError("Survival targets contain missing values.")
    if not np.isfinite(times.to_numpy(dtype=float)).all() or (times < 0).any():
        raise ValueError("Survival times must be finite and non-negative.")
    if not events.isin([0, 1]).all():
        raise ValueError("Event indicator must contain only 0 (censored) or 1 (hit).")

    horizon_values = list(horizons)
    if not horizon_values:
        raise ValueError("At least one horizon must be provided.")
    if any(not np.isfinite(horizon) or horizon <= 0 for horizon in horizon_values):
        raise ValueError("Horizons must be finite, positive numbers.")
    if len(set(horizon_values)) != len(horizon_values):
        raise ValueError("Horizons must be unique.")

    result = data.copy()
    hit = events.eq(1)
    for horizon in horizon_values:
        label = pd.Series(pd.NA, index=data.index, dtype="Int8")
        hit_by_horizon = hit & times.le(horizon)
        known_safe_at_horizon = times.ge(horizon) & ~hit_by_horizon
        label.loc[hit_by_horizon] = 1
        label.loc[known_safe_at_horizon] = 0
        result[f"threat_by_{horizon:g}h"] = label

    return result
