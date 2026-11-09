from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from wildfire_evacuation_risk.labels import add_horizon_labels


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build fixed-horizon threat labels from survival outcomes."
    )
    parser.add_argument("csv", type=Path, help="Training CSV with survival targets")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/train_horizon_labels.csv"),
        help="Output CSV path (default: artifacts/train_horizon_labels.csv)",
    )
    args = parser.parse_args()

    data = pd.read_csv(args.csv)
    labeled = add_horizon_labels(data)
    label_columns = [column for column in labeled if column.startswith("threat_by_")]

    print(f"Records: {len(labeled)}")
    for column in label_columns:
        counts = labeled[column].value_counts(dropna=False)
        print(
            f"{column}: positive={int(counts.get(1, 0))}, "
            f"negative={int(counts.get(0, 0))}, "
            f"unknown={int(counts.get(pd.NA, 0))}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    labeled.to_csv(args.output, index=False)
    print(f"Labeled dataset saved to: {args.output.resolve()}")


if __name__ == "__main__":
    main()
