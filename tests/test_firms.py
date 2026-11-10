import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from wildfire_evacuation_risk.firms import fetch_hotspots


class FirmsClientTests(unittest.TestCase):
    @patch("wildfire_evacuation_risk.firms.urlopen")
    def test_builds_global_query_and_parses_hotspot_rows(self, urlopen) -> None:
        response = BytesIO(
            b"latitude,longitude,acq_date,acq_time,satellite,confidence,frp\n"
            b"-1.2,114.3,2026-11-10,0320,VIIRS,high,18.5\n"
            b"bad,999,2026-11-10,0340,VIIRS,low,3.0\n"
        )
        urlopen.return_value.__enter__.return_value = response

        hotspots = fetch_hotspots(
            "sample_key_123",
            source="VIIRS_SNPP_NRT",
            area="world",
            day_range=2,
        )

        request = urlopen.call_args.args[0]
        self.assertIn("/sample_key_123/VIIRS_SNPP_NRT/world/2", request.full_url)
        self.assertEqual(len(hotspots), 1)
        self.assertEqual(hotspots.loc[0, "latitude"], -1.2)
        self.assertEqual(hotspots.loc[0, "longitude"], 114.3)

    @patch("wildfire_evacuation_risk.firms.urlopen")
    def test_sanitizes_provider_errors_so_token_is_not_in_message(self, urlopen) -> None:
        secret = "sample_key_123"
        urlopen.side_effect = HTTPError(
            f"https://example.test/{secret}",
            403,
            "forbidden",
            hdrs=None,
            fp=None,
        )

        with self.assertRaises(RuntimeError) as raised:
            fetch_hotspots(secret)

        self.assertIn("HTTP 403", str(raised.exception))
        self.assertNotIn(secret, str(raised.exception))

    def test_rejects_invalid_query_parameters(self) -> None:
        with self.assertRaisesRegex(ValueError, "map key"):
            fetch_hotspots("../invalid")
        with self.assertRaisesRegex(ValueError, "day range"):
            fetch_hotspots("sample_key", day_range=6)
        with self.assertRaisesRegex(ValueError, "Unsupported FIRMS source"):
            fetch_hotspots("sample_key", source="unknown")


if __name__ == "__main__":
    unittest.main()
