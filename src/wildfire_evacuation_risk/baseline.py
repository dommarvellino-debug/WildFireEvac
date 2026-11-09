"""Kaplan-Meier reference probabilities for a no-feature baseline."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sksurv.nonparametric import kaplan_meier_estimator


@dataclass(frozen=True)
class KaplanMeierBaseline:
    probabilities: dict[float, float | None]
    max_followup_hours: float


def fit_kaplan_meier_baseline(
    outcomes: pd.DataFrame,
    horizons: Iterable[float] = (12, 24, 48, 72),
) -> KaplanMeierBaseline:
    """Estimate constant cumulative event probabilities at supported horizons."""
    required = {"time_to_hit_hours", "event"}
    missing_columns = required - set(outcomes.columns)
    if missing_columns:
        raise ValueError(f"Outcomes are missing required columns: {sorted(missing_columns)}")
    if outcomes.empty:
        raise ValueError("At least one outcome row is required.")

    times = pd.to_numeric(outcomes["time_to_hit_hours"], errors="raise")
    events = pd.to_numeric(outcomes["event"], errors="raise")
    if times.isna().any() or events.isna().any():
        raise ValueError("Survival targets contain missing values.")
    time_values = times.to_numpy(dtype=float)
    event_values = events.to_numpy()
    if not np.isfinite(time_values).all() or (time_values < 0).any():
        raise ValueError("Survival times must be finite and non-negative.")
    if not np.isin(event_values, [0, 1]).all():
        raise ValueError("Event indicator must contain only 0 (censored) or 1 (hit).")

    horizon_values = list(horizons)
    if not horizon_values:
        raise ValueError("At least one horizon must be provided.")
    if any(not np.isfinite(horizon) or horizon <= 0 for horizon in horizon_values):
        raise ValueError("Horizons must be finite, positive numbers.")
    if len(set(horizon_values)) != len(horizon_values):
        raise ValueError("Horizons must be unique.")

    event = event_values.astype(bool)
    km_times, survival = kaplan_meier_estimator(event, time_values)
    max_followup = float(time_values.max())
    probabilities: dict[float, float | None] = {}
    for horizon in horizon_values:
        if horizon > max_followup:
            probabilities[horizon] = None
            continue
        index = np.searchsorted(km_times, horizon, side="right") - 1
        survival_at_horizon = 1.0 if index < 0 else float(survival[index])
        probabilities[horizon] = 1.0 - survival_at_horizon

    return KaplanMeierBaseline(
        probabilities=probabilities,
        max_followup_hours=max_followup,
    )
