import unittest

import numpy as np
import pandas as pd

from wildfire_evacuation_risk.cross_validation import repeated_stratified_cox_cv


class RepeatedStratifiedCoxCVTests(unittest.TestCase):
    def test_repeats_stratified_folds_and_returns_complete_oof_predictions(self) -> None:
        row_count = 30
        event = np.array([1] * 15 + [0] * 15)
        times = np.r_[np.arange(3, 48, 3), np.full(15, 72.0)]
        feature = np.r_[np.linspace(2, 0, 15), np.linspace(-1, 1, 15)]
        feature[::7] = np.nan
        data = pd.DataFrame(
            {
                "event": event,
                "time_to_hit_hours": times,
                "early_growth": feature,
                "distance": np.linspace(10, 200, row_count),
            }
        )

        result = repeated_stratified_cox_cv(
            data,
            feature_columns=("early_growth", "distance"),
            seeds=(17, 29),
            n_splits=5,
            alpha=0.1,
        )

        self.assertEqual(len(result.per_seed_metrics), 2)
        self.assertEqual(len(result.fold_event_counts), 10)
        self.assertEqual(
            result.fold_event_counts.groupby("seed")["valid_events"].sum().to_dict(),
            {17: 15, 29: 15},
        )
        self.assertEqual(len(result.mean_oof_predictions), row_count)
        self.assertEqual(
            list(result.mean_oof_predictions.columns),
            ["prob_12h", "prob_24h", "prob_48h", "prob_72h"],
        )
        self.assertFalse(result.mean_oof_predictions.isna().any().any())
        self.assertTrue(
            result.mean_oof_predictions.apply(
                lambda column: column.between(0, 1).all()
            ).all()
        )


if __name__ == "__main__":
    unittest.main()
