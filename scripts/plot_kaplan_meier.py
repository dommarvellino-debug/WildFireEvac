from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sksurv.nonparametric import kaplan_meier_estimator


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize event/censor counts and plot a Kaplan-Meier curve."
    )
    parser.add_argument("csv", type=Path, help="Training CSV with survival targets")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/kaplan_meier_train.png"),
        help="Output PNG path (default: artifacts/kaplan_meier_train.png)",
    )
    args = parser.parse_args()

    data = pd.read_csv(args.csv)
    required = {"time_to_hit_hours", "event"}
    missing_columns = required - set(data.columns)
    if missing_columns:
        raise ValueError(f"CSV is missing required columns: {sorted(missing_columns)}")

    outcomes = data[["time_to_hit_hours", "event"]]
    if outcomes.isna().any().any():
        raise ValueError("Survival targets contain missing values.")

    times = outcomes["time_to_hit_hours"].to_numpy(dtype=float)
    event = outcomes["event"].to_numpy()
    if not np.isfinite(times).all() or (times < 0).any():
        raise ValueError("Survival times must be finite and non-negative.")
    if not np.isin(event, [0, 1]).all():
        raise ValueError("Event indicator must contain only 0 (censored) or 1 (hit).")

    event = event.astype(bool)
    km_times, survival = kaplan_meier_estimator(event, times)
    max_followup = float(times.max())
    counts = pd.Series(event).value_counts().reindex([True, False], fill_value=0)

    print(f"Records: {len(data)}")
    print(f"Observed hits (event=1): {counts[True]}")
    print(f"Censored (event=0): {counts[False]}")
    print(f"Maximum observed follow-up: {max_followup:.2f} hours")
    print("Estimated cumulative hit probability (1 - Kaplan-Meier survival):")
    for horizon in (12, 24, 48, 72):
        if horizon > max_followup:
            print(f"  {horizon} hours: not estimable; follow-up ends earlier")
            continue
        index = np.searchsorted(km_times, horizon, side="right") - 1
        survival_at_horizon = 1.0 if index < 0 else float(survival[index])
        print(f"  {horizon} hours: {1.0 - survival_at_horizon:.1%}")

    censored_times = times[~event]
    censor_indices = np.searchsorted(km_times, censored_times, side="right") - 1
    censor_survival = np.ones_like(censored_times)
    observed_before = censor_indices >= 0
    censor_survival[observed_before] = survival[censor_indices[observed_before]]

    fig, ax = plt.subplots(figsize=(9, 6))
    ax.step(
        np.r_[0.0, km_times],
        np.r_[1.0, survival],
        where="post",
        color="#b33b2e",
        linewidth=2,
        label="Kaplan–Meier estimate",
    )
    ax.scatter(
        censored_times,
        censor_survival,
        marker="|",
        s=65,
        color="#222222",
        label="Censored observations",
        zorder=3,
    )
    ax.set(
        xlim=(0, max_followup),
        ylim=(0, 1.02),
        xlabel="Hours since t0 + 5h",
        ylabel="Probability fire has not yet come within 5 km",
        title="Time to evacuation-zone threat — training data",
    )
    ax.grid(alpha=0.25)
    ax.legend()
    fig.tight_layout()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=160)
    print(f"Kaplan–Meier plot saved to: {args.output.resolve()}")


if __name__ == "__main__":
    main()
