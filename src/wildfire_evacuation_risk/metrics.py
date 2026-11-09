"""Competition metric for fixed-horizon wildfire threat probabilities."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sksurv.metrics import concordance_index_censored

from wildfire_evacuation_risk.labels import add_horizon_labels

HORIZON_WEIGHTS = {24: 0.3, 48: 0.4, 72: 0.3}


@dataclass(frozen=True)
class CompetitionMetricResult:
    score: float
    c_index: float
    weighted_brier: float
    brier_by_horizon: dict[int, float]
    known_rows_by_horizon: dict[int, int]


def competition_metric(
    outcomes: pd.DataFrame,
    predictions: pd.DataFrame,
) -> CompetitionMetricResult:
    """Calculate 0.3*C-index + 0.7*(1-weighted Brier).

    The C-index risk score is `prob_72h`: higher threat probability indicates
    higher risk (earlier event). Horizon Brier scores exclude rows censored
    before the respective horizon.
    """
    required_outcomes = {"time_to_hit_hours", "event"}
    missing_outcomes = required_outcomes - set(outcomes.columns)
    if missing_outcomes:
        raise ValueError(
            f"Outcomes are missing required columns: {sorted(missing_outcomes)}"
        )

    prediction_columns = {f"prob_{horizon}h" for horizon in HORIZON_WEIGHTS}
    missing_predictions = prediction_columns - set(predictions.columns)
    if missing_predictions:
        raise ValueError(
            f"Predictions are missing required columns: {sorted(missing_predictions)}"
        )
    if not outcomes.index.equals(predictions.index):
        raise ValueError("Outcomes and predictions must have identical indexes.")
    if len(outcomes) == 0:
        raise ValueError("At least one outcome row is required.")

    labeled = add_horizon_labels(outcomes, horizons=HORIZON_WEIGHTS)
    probabilities: dict[int, np.ndarray] = {}
    for horizon in HORIZON_WEIGHTS:
        values = pd.to_numeric(
            predictions[f"prob_{horizon}h"], errors="raise"
        ).to_numpy(dtype=float)
        if not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
            raise ValueError(
                f"prob_{horizon}h must contain finite probabilities in [0, 1]."
            )
        probabilities[horizon] = values

    brier_by_horizon: dict[int, float] = {}
    known_rows_by_horizon: dict[int, int] = {}
    for horizon in HORIZON_WEIGHTS:
        labels = labeled[f"threat_by_{horizon}h"]
        known = labels.notna().to_numpy()
        count = int(known.sum())
        if count == 0:
            raise ValueError(
                f"No outcomes are known at {horizon} hours; Brier score is undefined."
            )
        observed = labels.loc[known].to_numpy(dtype=float)
        brier_by_horizon[horizon] = float(
            np.mean((probabilities[horizon][known] - observed) ** 2)
        )
        known_rows_by_horizon[horizon] = count

    weighted_brier = float(
        sum(HORIZON_WEIGHTS[h] * brier_by_horizon[h] for h in HORIZON_WEIGHTS)
    )
    event = pd.to_numeric(outcomes["event"], errors="raise").to_numpy(dtype=int)
    times = pd.to_numeric(
        outcomes["time_to_hit_hours"], errors="raise"
    ).to_numpy(dtype=float)
    c_index = float(
        concordance_index_censored(
            event.astype(bool),
            times,
            probabilities[72],
        )[0]
    )
    score = 0.3 * c_index + 0.7 * (1.0 - weighted_brier)

    return CompetitionMetricResult(
        score=score,
        c_index=c_index,
        weighted_brier=weighted_brier,
        brier_by_horizon=brier_by_horizon,
        known_rows_by_horizon=known_rows_by_horizon,
    )
