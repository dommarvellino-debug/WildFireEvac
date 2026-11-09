import unittest

import numpy as np
import pandas as pd

from wildfire_evacuation_risk.metrics import competition_metric


class CompetitionMetricTests(unittest.TestCase):
    def test_weighted_brier_and_combined_score(self) -> None:
        outcomes = pd.DataFrame(
            {
                "time_to_hit_hours": [10, 30, 50, 80],
                "event": [1, 1, 0, 0],
            }
        )
        predictions = pd.DataFrame(
            {
                "prob_24h": [0.8, 0.2, 0.1, 0.1],
                "prob_48h": [0.9, 0.7, 0.2, 0.1],
                "prob_72h": [0.9, 0.8, 0.3, 0.2],
            }
        )

        result = competition_metric(outcomes, predictions)

        expected_brier_24 = np.mean(
            [(0.8 - 1) ** 2, (0.2 - 0) ** 2, (0.1 - 0) ** 2, (0.1 - 0) ** 2]
        )
        expected_brier_48 = np.mean(
            [(0.9 - 1) ** 2, (0.7 - 1) ** 2, (0.2 - 0) ** 2, (0.1 - 0) ** 2]
        )
        expected_brier_72 = np.mean(
            [(0.9 - 1) ** 2, (0.8 - 1) ** 2, (0.2 - 0) ** 2]
        )
        expected_weighted_brier = (
            0.3 * expected_brier_24
            + 0.4 * expected_brier_48
            + 0.3 * expected_brier_72
        )

        self.assertAlmostEqual(result.c_index, 1.0)
        self.assertAlmostEqual(result.weighted_brier, expected_weighted_brier)
        self.assertAlmostEqual(
            result.score,
            0.3 * result.c_index + 0.7 * (1 - expected_weighted_brier),
        )
        self.assertEqual(result.known_rows_by_horizon, {24: 4, 48: 4, 72: 3})

    def test_excludes_censored_rows_before_each_horizon(self) -> None:
        outcomes = pd.DataFrame(
            {
                "time_to_hit_hours": [10, 20, 30, 40, 50, 80],
                "event": [1, 0, 1, 0, 1, 0],
            }
        )
        predictions = pd.DataFrame(
            {
                "prob_24h": [0.8, 0.4, 0.7, 0.3, 0.2, 0.1],
                "prob_48h": [0.9, 0.4, 0.8, 0.3, 0.2, 0.1],
                "prob_72h": [0.9, 0.4, 0.8, 0.3, 0.2, 0.1],
            }
        )

        result = competition_metric(outcomes, predictions)

        self.assertEqual(result.known_rows_by_horizon, {24: 5, 48: 4, 72: 4})

    def test_requires_probability_columns_and_valid_range(self) -> None:
        outcomes = pd.DataFrame(
            {
                "time_to_hit_hours": [10, 20],
                "event": [1, 1],
            }
        )
        predictions = pd.DataFrame(
            {
                "prob_24h": [0.5, 0.5],
                "prob_48h": [0.5, 0.5],
                "prob_72h": [1.1, 0.5],
            }
        )

        with self.assertRaisesRegex(ValueError, "prob_72h"):
            competition_metric(outcomes, predictions)


if __name__ == "__main__":
    unittest.main()
