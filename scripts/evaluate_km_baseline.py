from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sksurv.metrics import concordance_index_censored

from wildfire_evacuation_risk.baseline import fit_kaplan_meier_baseline
from wildfire_evacuation_risk.labels import add_horizon_labels


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate constant Kaplan-Meier reference probabilities."
    )
    parser.add_argument("csv", type=Path, help="Training CSV with survival targets")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/kaplan_meier_baseline.csv"),
        help="Output baseline predictions CSV",
    )
    args = parser.parse_args()

    data = pd.read_csv(args.csv)
    baseline = fit_kaplan_meier_baseline(data)
    labeled = add_horizon_labels(data)
    prediction = pd.DataFrame(index=data.index)

    print(f"Records: {len(data)}")
    print(f"Maximum observed follow-up: {baseline.max_followup_hours:.2f} hours")
    print("In-sample Kaplan-Meier calibration baseline:")

    for horizon, probability in baseline.probabilities.items():
        column = f"prob_{horizon:g}h"
        prediction[column] = probability
        labels = labeled[f"threat_by_{horizon:g}h"]
        known = labels.notna().to_numpy()
        positives = int(labels.eq(1).sum())
        negatives = int(labels.eq(0).sum())
        if probability is None:
            print(
                f"  {horizon:g}h: unsupported (follow-up ends earlier); "
                f"known labels={int(known.sum())} "
                f"(positive={positives}, negative={negatives})"
            )
            continue
        brier = float(
            np.mean(
                (
                    probability
                    - labels.loc[known].to_numpy(dtype=float)
                )
                ** 2
            )
        )
        print(
            f"  {horizon:g}h: probability={probability:.4f}, "
            f"Brier={brier:.4f}, known={int(known.sum())} "
            f"(positive={positives}, negative={negatives})"
        )

    event = data["event"].to_numpy(dtype=int).astype(bool)
    times = data["time_to_hit_hours"].to_numpy(dtype=float)
    constant_risk = np.zeros(len(data), dtype=float)
    c_index = float(
        concordance_index_censored(event, times, constant_risk)[0]
    )
    print(f"Constant-risk C-index: {c_index:.4f}")
    if baseline.probabilities.get(72) is None:
        print(
            "Full competition score unavailable: the KM baseline has no "
            "supported 72h probability."
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    prediction.to_csv(args.output, index=False)
    print(f"Baseline predictions saved to: {args.output.resolve()}")


if __name__ == "__main__":
    main()
