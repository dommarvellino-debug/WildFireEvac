"""Fit final survival models and convert survival curves to hit probabilities."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline
from sksurv.util import Surv

from wildfire_evacuation_risk.cross_validation import (
    HORIZONS,
    build_survival_estimator,
)


def fit_survival_model(
    training_data: pd.DataFrame,
    feature_columns: Sequence[str],
    model_name: str,
    *,
    alpha: float = 0.1,
    random_state: int = 2026,
) -> Pipeline:
    """Fit one survival model on all labeled training data."""
    required = {"time_to_hit_hours", "event"}
    missing = required - set(training_data.columns)
    if missing:
        raise ValueError(f"Training data is missing columns: {sorted(missing)}")
    missing_features = set(feature_columns) - set(training_data.columns)
    if missing_features:
        raise ValueError(f"Training data is missing features: {sorted(missing_features)}")
    features = training_data.loc[:, feature_columns]
    if list(features.select_dtypes(exclude=np.number).columns):
        raise ValueError("All model features must be numeric.")

    survival_target = Surv.from_dataframe(
        "event",
        "time_to_hit_hours",
        training_data,
    )
    model = build_survival_estimator(
        model_name,
        alpha=alpha,
        random_state=random_state,
    )
    model.fit(features, survival_target)
    return model


def predict_hit_probabilities(
    model: Pipeline,
    data: pd.DataFrame,
    feature_columns: Sequence[str],
    horizons: Sequence[float] = HORIZONS,
) -> pd.DataFrame:
    """Read each row's survival curve and return hit probability 1 - S(t)."""
    missing_features = set(feature_columns) - set(data.columns)
    if missing_features:
        raise ValueError(f"Prediction data is missing features: {sorted(missing_features)}")
    if not horizons or any(not np.isfinite(h) or h <= 0 for h in horizons):
        raise ValueError("Horizons must be a non-empty sequence of positive numbers.")

    curves = model.predict_survival_function(data.loc[:, feature_columns])
    predictions = pd.DataFrame(
        index=data.index,
        columns=[f"prob_{horizon:g}h" for horizon in horizons],
        dtype=float,
    )
    for row_index, curve in zip(data.index, curves):
        for horizon in horizons:
            index = np.searchsorted(curve.x, horizon, side="right") - 1
            survival_probability = 1.0 if index < 0 else float(curve.y[index])
            predictions.loc[row_index, f"prob_{horizon:g}h"] = np.clip(
                1.0 - survival_probability,
                0.0,
                1.0,
            )
    return predictions
