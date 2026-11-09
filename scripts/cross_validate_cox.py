from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from wildfire_evacuation_risk.cross_validation import repeated_stratified_cox_cv


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Repeated stratified 5-fold CV for a penalized Cox model."
    )
    parser.add_argument("csv", type=Path, help="Training CSV with survival targets")
    parser.add_argument(
        "--seeds",
        type=int,
        nargs="+",
        default=[42, 123, 2026, 2027, 2028],
        help="Random seeds; each runs a complete stratified CV repetition",
    )
    parser.add_argument("--folds", type=int, default=5, help="Stratified folds per seed")
    parser.add_argument(
        "--alpha",
        type=float,
        default=0.1,
        help="Fixed L2 penalty for CoxPH (not tuned on validation folds)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts"),
        help="Directory for OOF predictions and CV reports",
    )
    args = parser.parse_args()

    data = pd.read_csv(args.csv)
    excluded = {"event_id", "time_to_hit_hours", "event"}
    feature_columns = [
        column
        for column in data.select_dtypes(include=np.number).columns
        if column not in excluded
    ]
    if not feature_columns:
        raise ValueError("No numeric feature columns found in the training CSV.")

    result = repeated_stratified_cox_cv(
        data=data,
        feature_columns=feature_columns,
        seeds=args.seeds,
        n_splits=args.folds,
        alpha=args.alpha,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = args.output_dir / "cox_cv_metrics.csv"
    predictions_path = args.output_dir / "cox_cv_oof_predictions.csv"
    folds_path = args.output_dir / "cox_cv_fold_counts.csv"
    result.per_seed_metrics.to_csv(metrics_path, index=False)
    result.mean_oof_predictions.to_csv(predictions_path, index_label="row_index")
    result.fold_event_counts.to_csv(folds_path, index=False)

    print(f"Features: {len(feature_columns)}")
    print(f"Folds per seed: {args.folds}")
    print(f"Seeds: {args.seeds}")
    print(f"Fixed CoxPH L2 penalty: {args.alpha}")
    print("\nPer-seed out-of-fold metrics:")
    print(result.per_seed_metrics.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print("\nAcross-seed summary (mean ± sample standard deviation):")
    for column in ("score", "c_index", "weighted_brier"):
        values = result.per_seed_metrics[column]
        print(f"  {column}: {values.mean():.4f} ± {values.std(ddof=1):.4f}")
    print(f"\nMetrics: {metrics_path.resolve()}")
    print(f"Mean OOF probabilities: {predictions_path.resolve()}")
    print(f"Fold event counts: {folds_path.resolve()}")


if __name__ == "__main__":
    main()
