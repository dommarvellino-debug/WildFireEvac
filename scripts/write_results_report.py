from __future__ import annotations

import argparse
from pathlib import Path

import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sksurv.nonparametric import kaplan_meier_estimator

from wildfire_evacuation_risk.baseline import fit_kaplan_meier_baseline
from wildfire_evacuation_risk.labels import add_horizon_labels

MODEL_NAMES = {
    "cox": "Cox PH",
    "random_survival_forest": "Random survival forest",
    "gradient_boosting_survival": "Gradient-boosted survival trees",
}
HORIZONS = (12, 24, 48, 72)


def _survival_on_grid(curve: object, grid: np.ndarray) -> np.ndarray:
    indices = np.searchsorted(curve.x, grid, side="right") - 1
    result = np.ones_like(grid, dtype=float)
    observed = indices >= 0
    result[observed] = curve.y[indices[observed]]
    return result


def _format_mean_std(mean: float, std: float) -> str:
    return f"{mean:.4f} ± {std:.4f}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create the model results write-up and fire-curve plot."
    )
    parser.add_argument("train_csv", type=Path)
    parser.add_argument("test_csv", type=Path)
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts"))
    args = parser.parse_args()

    train = pd.read_csv(args.train_csv)
    test = pd.read_csv(args.test_csv)
    metrics_path = args.artifacts / "survival_models_cv_metrics.csv"
    if not metrics_path.exists():
        raise FileNotFoundError(f"Cross-validation metrics not found: {metrics_path}")
    cv_metrics = pd.read_csv(metrics_path)

    if "dist_min_ci_0_5h" not in test:
        raise ValueError("Test data is missing dist_min_ci_0_5h for close/far selection.")
    if test["dist_min_ci_0_5h"].isna().any():
        raise ValueError("Distance feature contains missing values.")

    close_index = test["dist_min_ci_0_5h"].idxmin()
    far_index = test["dist_min_ci_0_5h"].idxmax()
    selected = {
        "Closest test fire": close_index,
        "Farthest test fire": far_index,
    }

    feature_columns = [
        column
        for column in train.select_dtypes(include=np.number).columns
        if column not in {"event_id", "time_to_hit_hours", "event"}
    ]
    grid = np.linspace(0, 72, 361)
    km_times, km_survival_values = kaplan_meier_estimator(
        train["event"].to_numpy(dtype=bool),
        train["time_to_hit_hours"].to_numpy(dtype=float),
    )
    km_survival = np.ones_like(grid)
    km_indices = np.searchsorted(km_times, grid, side="right") - 1
    has_km = km_indices >= 0
    km_survival[has_km] = km_survival_values[km_indices[has_km]]

    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5), sharey=True)
    curves_by_fire: dict[str, dict[str, np.ndarray]] = {}
    for axis, (label, row_index) in zip(axes, selected.items()):
        fire = test.loc[[row_index]]
        fire_curves: dict[str, np.ndarray] = {}
        for model_key, model_name in MODEL_NAMES.items():
            model_path = args.artifacts / f"{model_key}.joblib"
            if not model_path.exists():
                raise FileNotFoundError(f"Fitted model not found: {model_path}")
            model = joblib.load(model_path)
            curve = model.predict_survival_function(fire[feature_columns])[0]
            threat_probability = 1.0 - _survival_on_grid(curve, grid)
            fire_curves[model_key] = threat_probability
            axis.plot(grid, threat_probability, linewidth=2, label=model_name)

        axis.step(
            grid,
            1.0 - km_survival,
            where="post",
            color="#555555",
            linestyle="--",
            linewidth=1.8,
            label="Kaplan–Meier population baseline",
        )
        distance_km = float(fire["dist_min_ci_0_5h"].iloc[0]) / 1000
        event_value = fire["event_id"].iloc[0] if "event_id" in fire else row_index
        event_id = str(int(event_value)) if float(event_value).is_integer() else str(event_value)
        axis.set_title(f"{label}\nID {event_id}; nearest-zone distance {distance_km:.2f} km")
        axis.set_xlabel("Hours after first 5-hour observation window")
        axis.set_xlim(0, 72)
        axis.set_ylim(0, 1)
        axis.grid(alpha=0.25)
        axis.legend(fontsize=8, loc="lower right")
        curves_by_fire[label] = fire_curves

    axes[0].set_ylabel("Estimated probability of threat by time (1 − S(t))")
    fig.suptitle("Predicted wildfire threat curves for two test fires")
    fig.tight_layout()
    plot_path = args.artifacts / "predicted_fire_curves.png"
    fig.savefig(plot_path, dpi=170)
    plt.close(fig)

    km_baseline = fit_kaplan_meier_baseline(train, horizons=HORIZONS)
    labels = add_horizon_labels(train, horizons=HORIZONS)
    km_briers: dict[int, float | None] = {}
    for horizon in HORIZONS:
        probability = km_baseline.probabilities[horizon]
        target = labels[f"threat_by_{horizon}h"]
        known = target.notna()
        if probability is None:
            km_briers[horizon] = None
        else:
            km_briers[horizon] = float(
                np.mean(
                    (probability - target.loc[known].to_numpy(dtype=float)) ** 2
                )
            )

    rows = []
    model_order = list(MODEL_NAMES)
    for model_key in model_order:
        group = cv_metrics.loc[cv_metrics["model"].eq(model_key)]
        if group.empty:
            raise ValueError(f"No CV metric rows found for model {model_key}.")
        rows.append(
            "| "
            + " | ".join(
                [
                    MODEL_NAMES[model_key],
                    _format_mean_std(group["score"].mean(), group["score"].std(ddof=1)),
                    _format_mean_std(
                        group["c_index"].mean(), group["c_index"].std(ddof=1)
                    ),
                    _format_mean_std(
                        group["weighted_brier"].mean(),
                        group["weighted_brier"].std(ddof=1),
                    ),
                ]
            )
            + " |"
        )

    horizon_rows = []
    horizon_brier_rows = []
    for horizon in HORIZONS:
        positives = int(labels[f"threat_by_{horizon}h"].eq(1).sum())
        negatives = int(labels[f"threat_by_{horizon}h"].eq(0).sum())
        unknown = int(labels[f"threat_by_{horizon}h"].isna().sum())
        horizon_rows.append(f"| {horizon}h | {positives} | {negatives} | {unknown} |")

    for model_key in model_order:
        group = cv_metrics.loc[cv_metrics["model"].eq(model_key)]
        brier_values = [
            _format_mean_std(
                group[f"brier_{horizon}h"].mean(),
                group[f"brier_{horizon}h"].std(ddof=1),
            )
            for horizon in HORIZONS
        ]
        horizon_brier_rows.append(
            "| "
            + MODEL_NAMES[model_key]
            + " | "
            + " | ".join(brier_values)
            + " |"
        )
    km_horizon_briers = [
        f"{km_briers[horizon]:.4f}" if km_briers[horizon] is not None else "N/A"
        for horizon in HORIZONS
    ]
    horizon_brier_rows.append("| Kaplan–Meier (in-sample) | " + " | ".join(km_horizon_briers) + " |")

    curve_table_rows = []
    for label, row_index in selected.items():
        fire = test.loc[row_index]
        distance_km = float(fire["dist_min_ci_0_5h"]) / 1000
        event_value = fire["event_id"] if "event_id" in test else row_index
        event_id = str(int(event_value)) if float(event_value).is_integer() else str(event_value)
        for model_key, model_name in MODEL_NAMES.items():
            predictions = curves_by_fire[label][model_key]
            values = [
                predictions[np.searchsorted(grid, horizon)]
                for horizon in HORIZONS
            ]
            curve_table_rows.append(
                f"| {label} ({event_id}, {distance_km:.2f} km) | {model_name} | "
                + " | ".join(f"{value:.3f}" for value in values)
                + " |"
            )

    report = f"""# Wildfire evacuation threat forecasting: model results

## Summary

This prototype estimates time until a wildfire comes within 5 km of an evacuation-zone centroid. Predictors use the first five hours only; the outcome clock starts at t0 + 5h. We evaluate a penalized Cox model, random survival forest, and gradient-boosted survival trees. Each model produces threat probabilities as `1 - S(t)`.

The models were evaluated with five repeats of event-stratified 5-fold cross-validation. Imputation and scaling (for Cox) were fit inside each training fold. Scores below are means ± sample standard deviations across the five repeats.

## Score comparison

| Model | Combined score | C-index | Weighted Brier |
|---|---:|---:|---:|
{chr(10).join(rows)}
| Kaplan–Meier reference | N/A | 0.5000 by design | N/A |

| Reference details | KM threat probability | KM Brier |
|---|---:|---:|
| 24h (in-sample) | {km_baseline.probabilities[24]:.4f} | {km_briers[24]:.4f} |
| 48h (in-sample) | {km_baseline.probabilities[48]:.4f} | {km_briers[48]:.4f} |
| 72h (in-sample) | Not estimable | Not estimable |

The Kaplan–Meier row is a no-feature reference: it assigns the same risk to every fire, so its C-index is 0.5. Its 72h curve value is not nonparametrically supported because the maximum training follow-up is {km_baseline.max_followup_hours:.2f} hours. Consequently its full weighted Brier and combined score are unavailable. The 24h/48h KM Brier values above are in-sample diagnostics, whereas model scores are out-of-fold; do not treat those KM calibration numbers as directly comparable CV estimates.

## Brier score by horizon

| Model | 12h | 24h | 48h | 72h |
|---|---:|---:|---:|---:|
{chr(10).join(horizon_brier_rows)}

Model values are mean ± SD across CV repeats. Kaplan–Meier values are in-sample; 72h is unsupported.

## Horizon label coverage

| Horizon | Known hits | Known safe | Unknown/censored early |
|---|---:|---:|---:|
{chr(10).join(horizon_rows)}

At 72h all 69 known labels are hits, with no known-safe cases. The low 72h Brier values are therefore mainly calibration against positive cases and do not establish the model's ability to distinguish fires that remain safe through 72 hours.

## Example predicted curves

The examples are the closest and farthest test fires by `dist_min_ci_0_5h` (minimum distance to an evacuation-zone centroid during the first five hours). Curves show model-estimated cumulative threat probability `1 - S(t)` from the end of that observation window. The dashed Kaplan–Meier line is the same population curve in both panels.

![Predicted threat curves for a close and far test fire](predicted_fire_curves.png)

| Test fire | Model | 12h | 24h | 48h | 72h |
|---|---|---:|---:|---:|---:|
{chr(10).join(curve_table_rows)}

These are illustrative individual predictions, not ground-truth outcomes. The survival step function is held flat beyond the last event time available to a fitted model; the tail should not be interpreted as evidence that threat risk stops.

## Submission status

The [WiDS Cambridge 2026 datathon page](https://www.widscambridge.org/datathon) says the global challenge ran from January through May 2026; the [Kaggle competition](https://www.kaggle.com/competitions/WiDSWorldWide_GlobalDathon26) is therefore past its stated regular window as of November 2026. The Kaggle page renders its late-submission setting dynamically, which could not be verified anonymously. No submission was made: this environment has no Kaggle CLI or local Kaggle credentials, and late acceptance could not be confirmed. A leaderboard score is not available.

## Reproducibility

Generate the report and plot after running the model pipeline:

```powershell
python scripts\\write_results_report.py C:\\path\\to\\train.csv C:\\path\\to\\test.csv
```

Inputs and outcomes are not included in this report artifact. CV details and predictions remain in the generated files under `artifacts\\`.
"""

    report_path = args.artifacts / "model_results_report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"Report: {report_path.resolve()}")
    print(f"Curve plot: {plot_path.resolve()}")
    print("Selected test examples:")
    for label, index in selected.items():
        fire = test.loc[index]
        print(
            f"  {label}: event_id={fire.get('event_id', index)}, "
            f"distance={fire['dist_min_ci_0_5h'] / 1000:.2f} km"
        )
    print("\nCV score summary:")
    for row in rows:
        print(row)


if __name__ == "__main__":
    main()
