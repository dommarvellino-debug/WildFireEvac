"""Repeated stratified out-of-fold evaluation for survival models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sksurv.ensemble import (
    GradientBoostingSurvivalAnalysis,
    RandomSurvivalForest,
)
from sksurv.linear_model import CoxPHSurvivalAnalysis
from sksurv.util import Surv

from wildfire_evacuation_risk.metrics import (
    CompetitionMetricResult,
    competition_metric,
)
from wildfire_evacuation_risk.labels import add_horizon_labels

HORIZONS = (12, 24, 48, 72)
MODEL_NAMES = ("cox", "random_survival_forest", "gradient_boosting_survival")


@dataclass(frozen=True)
class CrossValidationResult:
    per_seed_metrics: pd.DataFrame
    mean_oof_predictions: pd.DataFrame
    fold_event_counts: pd.DataFrame


def build_survival_estimator(
    model_name: str,
    *,
    alpha: float = 0.1,
    random_state: int | None = None,
) -> Pipeline:
    """Create a model pipeline whose imputer is fitted on each training fold."""
    if model_name == "cox":
        estimator = CoxPHSurvivalAnalysis(alpha=alpha)
        scale_features = True
    elif model_name == "random_survival_forest":
        estimator = RandomSurvivalForest(
            n_estimators=100,
            min_samples_split=6,
            min_samples_leaf=3,
            max_features="sqrt",
            n_jobs=-1,
            random_state=random_state,
        )
        scale_features = False
    elif model_name == "gradient_boosting_survival":
        estimator = GradientBoostingSurvivalAnalysis(
            loss="coxph",
            n_estimators=100,
            learning_rate=0.05,
            max_depth=1,
            min_samples_leaf=5,
            random_state=random_state,
        )
        scale_features = False
    else:
        raise ValueError(f"Unknown survival model: {model_name!r}")

    steps: list[tuple[str, object]] = [
        ("imputer", SimpleImputer(strategy="median", keep_empty_features=True))
    ]
    if scale_features:
        steps.append(("scaler", StandardScaler()))
    steps.append(("survival_model", estimator))
    return Pipeline(steps=steps)


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


def _horizon_diagnostics(
    outcomes: pd.DataFrame,
    predictions: pd.DataFrame,
) -> dict[str, float | int]:
    labels = add_horizon_labels(outcomes, horizons=HORIZONS)
    diagnostics: dict[str, float | int] = {}
    for horizon in HORIZONS:
        target = labels[f"threat_by_{horizon}h"]
        known = target.notna()
        diagnostics[f"brier_{horizon}h"] = float(
            np.mean(
                (
                    predictions.loc[known, f"prob_{horizon}h"].to_numpy(dtype=float)
                    - target.loc[known].to_numpy(dtype=float)
                )
                ** 2
            )
        )
        diagnostics[f"known_{horizon}h"] = int(known.sum())
    return diagnostics


def repeated_stratified_survival_cv(
    data: pd.DataFrame,
    feature_columns: Sequence[str],
    model_name: str,
    seeds: Sequence[int] = (42, 123, 2026, 2027, 2028),
    n_splits: int = 5,
    alpha: float = 0.1,
) -> CrossValidationResult:
    """Generate repeated OOF probabilities from stratified survival-model folds."""
    required = {"time_to_hit_hours", "event"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Data is missing required columns: {sorted(missing)}")
    if model_name not in MODEL_NAMES:
        raise ValueError(f"Unknown survival model: {model_name!r}")
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

    survival_target = Surv.from_dataframe("event", "time_to_hit_hours", data)
    outcome_data = data[["time_to_hit_hours", "event"]]
    horizon_predictions: list[pd.DataFrame] = []
    metric_rows: list[dict[str, float | int]] = []
    fold_rows: list[dict[str, int | str]] = []

    for seed in seeds:
        splitter = StratifiedKFold(
            n_splits=n_splits,
            shuffle=True,
            random_state=seed,
        )
        oof = pd.DataFrame(
            index=data.index,
            columns=[f"prob_{horizon}h" for horizon in HORIZONS],
            dtype=float,
        )

        for fold, (train_indices, valid_indices) in enumerate(
            splitter.split(features, event), start=1
        ):
            model = build_survival_estimator(
                model_name,
                alpha=alpha,
                random_state=seed * 100 + fold,
            )
            model.fit(features.iloc[train_indices], survival_target[train_indices])
            survival_functions = model.predict_survival_function(
                features.iloc[valid_indices]
            )

            for position, row_index in enumerate(data.index[valid_indices]):
                survival_function = survival_functions[position]
                for horizon in HORIZONS:
                    probability = 1.0 - _survival_probability_at(
                        survival_function, horizon
                    )
                    oof.loc[row_index, f"prob_{horizon}h"] = np.clip(
                        probability, 0.0, 1.0
                    )

            fold_rows.append(
                {
                    "model": model_name,
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
        metric_row = _metric_row(int(seed), metric_result)
        metric_row.update(_horizon_diagnostics(outcome_data, oof))
        metric_rows.append(metric_row)
        horizon_predictions.append(oof)

    stacked = pd.concat(horizon_predictions, keys=seeds, names=["seed", "row"])
    mean_predictions = stacked.groupby(level="row")[
        [f"prob_{horizon}h" for horizon in HORIZONS]
    ].mean().reindex(data.index)

    return CrossValidationResult(
        per_seed_metrics=pd.DataFrame(metric_rows),
        mean_oof_predictions=mean_predictions,
        fold_event_counts=pd.DataFrame(fold_rows),
    )


def repeated_stratified_cox_cv(
    data: pd.DataFrame,
    feature_columns: Sequence[str],
    seeds: Sequence[int] = (42, 123, 2026, 2027, 2028),
    n_splits: int = 5,
    alpha: float = 0.1,
) -> CrossValidationResult:
    """Backward-compatible Cox-only entry point."""
    return repeated_stratified_survival_cv(
        data,
        feature_columns,
        model_name="cox",
        seeds=seeds,
        n_splits=n_splits,
        alpha=alpha,
    )
