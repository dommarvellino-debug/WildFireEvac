"""Repeated stratified out-of-fold evaluation for penalized Cox models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sksurv.linear_model import CoxPHSurvivalAnalysis
from sksurv.util import Surv

from wildfire_evacuation_risk.metrics import (
    CompetitionMetricResult,
    competition_metric,
)


@dataclass(frozen=True)
class CrossValidationResult:
    per_seed_metrics: pd.DataFrame
    mean_oof_predictions: pd.DataFrame
    fold_event_counts: pd.DataFrame


def _survival_probability_at(step_function: object, horizon: float) -> float:
    times = step_function.x
    survival = step_function.y
    index = np.searchsorted(times, horizon, side="right") - 1
    if index < 0:
        return 1.0
    return float(survival[index])


def _metric_row(seed: int, result: CompetitionMetricResult) -> dict[str, float | int]:
    row: dict[str, float | int] = {
        "seed": seed,
        "score": result.score,
        "c_index": result.c_index,
        "weighted_brier": result.weighted_brier,
    }
    for horizon, value in result.brier_by_horizon.items():
        row[f"brier_{horizon}h"] = value
        row[f"known_{horizon}h"] = result.known_rows_by_horizon[horizon]
    return row


def repeated_stratified_cox_cv(
    data: pd.DataFrame,
    feature_columns: Sequence[str],
    seeds: Sequence[int] = (42, 123, 2026, 2027, 2028),
    n_splits: int = 5,
    alpha: float = 0.1,
) -> CrossValidationResult:
    """Generate repeated OOF horizon probabilities with fold-local imputation."""
    required = {"time_to_hit_hours", "event"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Data is missing required columns: {sorted(missing)}")
    if not feature_columns:
        raise ValueError("At least one feature column is required.")
    if not data.index.is_unique:
        raise ValueError("Data index must be unique so OOF rows map to one fire each.")
    if len(set(feature_columns)) != len(feature_columns):
        raise ValueError("Feature column names must be unique.")
    missing_features = set(feature_columns) - set(data.columns)
    if missing_features:
        raise ValueError(
            f"Data is missing feature columns: {sorted(missing_features)}"
        )
    if not seeds or len(set(seeds)) != len(seeds):
        raise ValueError("Provide one or more unique random seeds.")
    if n_splits < 2:
        raise ValueError("n_splits must be at least 2.")
    if not np.isfinite(alpha) or alpha <= 0:
        raise ValueError("CoxPH alpha must be finite and positive.")

    features = data.loc[:, feature_columns]
    non_numeric = list(features.select_dtypes(exclude=np.number).columns)
    if non_numeric:
        raise ValueError(f"Features must be numeric: {non_numeric}")
    event = pd.to_numeric(data["event"], errors="raise")
    times = pd.to_numeric(data["time_to_hit_hours"], errors="raise")
    if event.isna().any() or times.isna().any():
        raise ValueError("Survival targets contain missing values.")
    if not event.isin([0, 1]).all():
        raise ValueError("Event indicator must contain only 0 (censored) or 1 (hit).")
    if not np.isfinite(times.to_numpy(dtype=float)).all() or (times < 0).any():
        raise ValueError("Survival times must be finite and non-negative.")
    if event.value_counts().min() < n_splits:
        raise ValueError("Each event class must contain at least n_splits rows.")

    survival_target = Surv.from_dataframe(
        "event",
        "time_to_hit_hours",
        data,
    )
    outcome_data = data[["time_to_hit_hours", "event"]]
    horizon_predictions: list[pd.DataFrame] = []
    metric_rows: list[dict[str, float | int]] = []
    fold_rows: list[dict[str, int]] = []

    for seed in seeds:
        splitter = StratifiedKFold(
            n_splits=n_splits,
            shuffle=True,
            random_state=seed,
        )
        oof = pd.DataFrame(
            index=data.index,
            columns=["prob_24h", "prob_48h", "prob_72h"],
            dtype=float,
        )

        for fold, (train_indices, valid_indices) in enumerate(
            splitter.split(features, event), start=1
        ):
            model = Pipeline(
                steps=[
                    ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                    ("scaler", StandardScaler()),
                    ("cox", CoxPHSurvivalAnalysis(alpha=alpha)),
                ]
            )
            model.fit(features.iloc[train_indices], survival_target[train_indices])
            survival_functions = model.predict_survival_function(
                features.iloc[valid_indices]
            )

            for position, row_index in enumerate(data.index[valid_indices]):
                survival_function = survival_functions[position]
                for horizon in (24, 48, 72):
                    probability = 1.0 - _survival_probability_at(
                        survival_function, horizon
                    )
                    oof.loc[row_index, f"prob_{horizon}h"] = np.clip(
                        probability, 0.0, 1.0
                    )

            fold_rows.append(
                {
                    "seed": int(seed),
                    "fold": fold,
                    "train_rows": len(train_indices),
                    "valid_rows": len(valid_indices),
                    "train_events": int(event.iloc[train_indices].sum()),
                    "valid_events": int(event.iloc[valid_indices].sum()),
                }
            )

        if oof.isna().any().any():
            raise RuntimeError(f"Seed {seed} did not produce complete OOF predictions.")
        metric_result = competition_metric(outcome_data, oof)
        metric_rows.append(_metric_row(int(seed), metric_result))
        repeat_predictions = oof.copy()
        repeat_predictions.insert(0, "seed", int(seed))
        horizon_predictions.append(repeat_predictions)

    stacked = pd.concat(horizon_predictions, keys=seeds, names=["seed", "row"])
    mean_predictions = stacked.groupby(level="row")[
        ["prob_24h", "prob_48h", "prob_72h"]
    ].mean().reindex(data.index)

    return CrossValidationResult(
        per_seed_metrics=pd.DataFrame(metric_rows),
        mean_oof_predictions=mean_predictions,
        fold_event_counts=pd.DataFrame(fold_rows),
    )
