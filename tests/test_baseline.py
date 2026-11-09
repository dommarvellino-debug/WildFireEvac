import unittest

import numpy as np
import pandas as pd
from sksurv.metrics import concordance_index_censored

from wildfire_evacuation_risk.baseline import fit_kaplan_meier_baseline


class KaplanMeierBaselineTests(unittest.TestCase):
    def test_estimates_constant_risk_at_supported_horizons(self) -> None:
        outcomes = pd.DataFrame(
            {
                "time_to_hit_hours": [1, 2, 3, 4],
                "event": [1, 0, 1, 0],
            }
        )

        baseline = fit_kaplan_meier_baseline(outcomes, horizons=(1, 2, 5))

        self.assertAlmostEqual(baseline.probabilities[1], 0.25)
        self.assertAlmostEqual(baseline.probabilities[2], 0.25)
        self.assertIsNone(baseline.probabilities[5])
        self.assertEqual(baseline.max_followup_hours, 4)

    def test_constant_risk_score_has_half_concordance(self) -> None:
        outcomes = pd.DataFrame(
            {
                "time_to_hit_hours": [1, 2, 3, 4],
                "event": [1, 0, 1, 0],
            }
        )
        score = concordance_index_censored(
            outcomes["event"].to_numpy(dtype=bool),
            outcomes["time_to_hit_hours"].to_numpy(),
            np.zeros(len(outcomes)),
        )[0]

        self.assertEqual(score, 0.5)


if __name__ == "__main__":
    unittest.main()
