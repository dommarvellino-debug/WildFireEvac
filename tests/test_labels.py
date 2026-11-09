import unittest

import pandas as pd

from wildfire_evacuation_risk.labels import add_horizon_labels


class AddHorizonLabelsTests(unittest.TestCase):
    def test_labels_hits_safe_cases_and_early_censoring(self) -> None:
        data = pd.DataFrame(
            {
                "time_to_hit_hours": [12, 13, 11, 12, 20],
                "event": [1, 1, 0, 0, 0],
            }
        )

        labeled = add_horizon_labels(data, horizons=(12,))

        self.assertEqual(
            labeled["threat_by_12h"].tolist(),
            [1, 0, pd.NA, 0, 0],
        )

    def test_censoring_before_later_horizon_is_unknown(self) -> None:
        data = pd.DataFrame(
            {
                "time_to_hit_hours": [30, 30, 30],
                "event": [0, 0, 1],
            }
        )

        labeled = add_horizon_labels(data, horizons=(12, 48))

        self.assertEqual(labeled["threat_by_12h"].tolist(), [0, 0, 0])
        self.assertEqual(
            labeled["threat_by_48h"].tolist(),
            [pd.NA, pd.NA, 1],
        )

    def test_rejects_invalid_event_values(self) -> None:
        data = pd.DataFrame({"time_to_hit_hours": [1], "event": [2]})

        with self.assertRaisesRegex(ValueError, "Event indicator"):
            add_horizon_labels(data)


if __name__ == "__main__":
    unittest.main()
