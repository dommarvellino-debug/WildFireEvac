from __future__ import annotations

import argparse
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from wildfire_evacuation_risk.cross_validation import (
    MODEL_NAMES,
    repeated_stratified_survival_cv,
)
from wildfire_evacuation_risk.modeling import (
    fit_survival_model,
    predict_hit_probabilities,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cross-validate, fit, and predict with scikit-survival models."
    )
    parser.add_argument("train_csv", type=Path, help="Labeled training CSV")
    parser.add_argument("test_csv", type=Path, help="Unlabeled test CSV")
    parser.add_argument(
        "--seeds",
        type=int,
        nargs="+",
        default=[42, 123, 2026, 2027, 2028],
        help="Random seeds for repeated stratified cross-validation",
    )
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--alpha", type=float, default=0.1)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    args = parser.parse_args()

    train = pd.read_csv(args.train_csv)
    test = pd.read_csv(args.test_csv)
    excluded = {"event_id", "time_to_hit_hours", "event"}
    feature_columns = [
        column
        for column in train.select_dtypes(include=np.number).columns
        if column not in excluded
    ]
    missing_test_features = set(feature_columns) - set(test.columns)
    if missing_test_features:
        raise ValueError(
            f"Test data is missing training features: {sorted(missing_test_features)}"
        )
    if not feature_columns:
        raise ValueError("No numeric feature columns found in the training CSV.")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    all_metrics: list[pd.DataFrame] = []

    for model_name in MODEL_NAMES:
        print(f"\n=== {model_name} ===")
        cv_result = repeated_stratified_survival_cv(
            train,
            feature_columns,
            model_name=model_name,
            seeds=args.seeds,
            n_splits=args.folds,
            alpha=args.alpha,
        )
        metrics = cv_result.per_seed_metrics.copy()
        metrics.insert(0, "model", model_name)
        all_metrics.append(metrics)
        cv_result.mean_oof_predictions.to_csv(
            args.output_dir / f"{model_name}_oof_predictions.csv",
            index_label="row_index",
        )
        cv_result.fold_event_counts.to_csv(
            args.output_dir / f"{model_name}_fold_counts.csv",
            index=False,
        )

        print(cv_result.per_seed_metrics.to_string(
            index=False, float_format=lambda value: f"{value:.4f}"
        ))
        for column in ("score", "c_index", "weighted_brier"):
            values = cv_result.per_seed_metrics[column]
            print(f"{column}: {values.mean():.4f} ± {values.std(ddof=1):.4f}")

        fitted_model = fit_survival_model(
            train,
            feature_columns,
            model_name=model_name,
            alpha=args.alpha,
            random_state=2026,
        )
        model_path = args.output_dir / f"{model_name}.joblib"
        joblib.dump(fitted_model, model_path)
        test_predictions = predict_hit_probabilities(
            fitted_model,
            test,
            feature_columns,
        )
        test_output = test[["event_id"]].copy() if "event_id" in test else pd.DataFrame(
            index=test.index
        )
        test_output = pd.concat([test_output, test_predictions], axis=1)
        test_output.to_csv(
            args.output_dir / f"{model_name}_test_predictions.csv",
            index=False,
        )
        print(f"Final fitted model: {model_path.resolve()}")
        print(
            f"Test probabilities: "
            f"{(args.output_dir / f'{model_name}_test_predictions.csv').resolve()}"
        )

    pd.concat(all_metrics, ignore_index=True).to_csv(
        args.output_dir / "survival_models_cv_metrics.csv",
        index=False,
    )
    print(f"\nCombined CV metrics: {(args.output_dir / 'survival_models_cv_metrics.csv').resolve()}")


if __name__ == "__main__":
    main()
