import json
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from wildfire_evacuation_risk.gdacs import fetch_wildfire_events


def _feature(
    event_id,
    *,
    episode_id=1,
    alert="Orange",
    is_current="true",
    modified="2026-11-10T01:00:00",
    to_date="2026-11-09T00:00:00",
    event_type="WF",
):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [114.3, -1.2]},
        "properties": {
            "eventtype": event_type,
            "eventid": event_id,
            "episodeid": episode_id,
            "eventname": "Wildfire - Para State, Brazil",
            "description": "<p>Active <b>fire</b> front.</p>",
            "alertlevel": alert,
            "alertscore": "1.5",
            "country": "Brazil",
            "iso3": "bra",
            "fromdate": "2026-11-07T00:00:00",
            "todate": to_date,
            "datemodified": modified,
            "iscurrent": is_current,
            "url": {
                "details": "https://www.gdacs.org/details",
                "report": "https://www.gdacs.org/report",
            },
        },
    }


class GdacsClientTests(unittest.TestCase):
    @patch("wildfire_evacuation_risk.gdacs.urlopen")
    def test_builds_wildfire_query_and_parses_features(self, urlopen) -> None:
        payload = {"type": "FeatureCollection", "features": [_feature(123)]}
        urlopen.return_value.__enter__.return_value = BytesIO(
            json.dumps(payload).encode("utf-8")
        )

        reports = fetch_wildfire_events(days=7)

        request = urlopen.call_args_list[0].args[0]
        self.assertIn("eventlist=WF", request.full_url)
        self.assertIn("alertlevel=green;orange;red", request.full_url)
        self.assertNotIn("fromdate=", request.full_url)
        self.assertEqual(len(reports), 1)
        row = reports.iloc[0]
        self.assertEqual(row["event_id"], "123")
        self.assertEqual(row["alert_level"], "Orange")
        self.assertAlmostEqual(float(row["latitude"]), -1.2)
        self.assertAlmostEqual(float(row["longitude"]), 114.3)
        self.assertTrue(bool(row["is_current"]))
        self.assertEqual(row["description"], "Active fire front.")
        self.assertEqual(row["report_url"], "https://www.gdacs.org/report")

    @patch("wildfire_evacuation_risk.gdacs.urlopen")
    def test_keeps_only_latest_episode_per_event(self, urlopen) -> None:
        payload = {
            "type": "FeatureCollection",
            "features": [
                _feature(7, episode_id=1, alert="Orange", modified="2026-11-09T00:00:00"),
                _feature(7, episode_id=2, alert="Red", modified="2026-11-10T00:00:00"),
            ],
        }
        urlopen.return_value.__enter__.return_value = BytesIO(
            json.dumps(payload).encode("utf-8")
        )

        reports = fetch_wildfire_events()

        self.assertEqual(len(reports), 1)
        self.assertEqual(reports.iloc[0]["alert_level"], "Red")

    @patch("wildfire_evacuation_risk.gdacs.urlopen")
    def test_skips_non_wildfire_features(self, urlopen) -> None:
        payload = {
            "type": "FeatureCollection",
            "features": [_feature(9, event_type="EQ")],
        }
        urlopen.return_value.__enter__.return_value = BytesIO(
            json.dumps(payload).encode("utf-8")
        )

        reports = fetch_wildfire_events()

        self.assertTrue(reports.empty)

    @patch("wildfire_evacuation_risk.gdacs.urlopen")
    def test_empty_feature_collection_returns_typed_frame(self, urlopen) -> None:
        payload = {"type": "FeatureCollection", "features": []}
        urlopen.return_value.__enter__.return_value = BytesIO(
            json.dumps(payload).encode("utf-8")
        )

        reports = fetch_wildfire_events()

        self.assertTrue(reports.empty)
        self.assertIn("event_id", reports.columns)
        self.assertIn("alert_level", reports.columns)

    @patch("wildfire_evacuation_risk.gdacs.urlopen")
    def test_pages_until_short_page_and_dedupes_overlap(self, urlopen) -> None:
        def response(features):
            context = MagicMock()
            context.__enter__.return_value = BytesIO(
                json.dumps(
                    {"type": "FeatureCollection", "features": features}
                ).encode("utf-8")
            )
            return context

        urlopen.side_effect = [
            response(
                [
                    _feature(1, episode_id=1),
                    _feature(2, episode_id=1),
                    _feature(3, episode_id=1),
                ]
            ),
            response([_feature(3, episode_id=2), _feature(4, episode_id=1)]),
        ]

        reports = fetch_wildfire_events(days=7, page_size=3)

        urls = [call.args[0].full_url for call in urlopen.call_args_list]
        self.assertEqual(len(urls), 2)
        self.assertIn("pageNumber=1", urls[0])
        self.assertIn("alertlevel=green;orange;red", urls[0])
        self.assertIn("pageNumber=2", urls[1])
        self.assertEqual(sorted(reports["event_id"]), ["1", "2", "3", "4"])

    @patch("wildfire_evacuation_risk.gdacs.urlopen")
    def test_stops_at_max_pages(self, urlopen) -> None:
        def response(*args, **kwargs):
            context = MagicMock()
            context.__enter__.return_value = BytesIO(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "features": [_feature(1), _feature(2)],
                    }
                ).encode("utf-8")
            )
            return context

        urlopen.side_effect = response

        fetch_wildfire_events(page_size=2, max_pages=3)

        self.assertEqual(urlopen.call_count, 3)

    @patch("wildfire_evacuation_risk.gdacs.urlopen")
    def test_windows_out_old_ended_events(self, urlopen) -> None:
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
        old = "2020-01-01T00:00:00"
        payload = {
            "type": "FeatureCollection",
            "features": [
                _feature(1, is_current="false", to_date=old),
                _feature(2, is_current="false", to_date=recent),
                _feature(3, is_current="true", to_date=old),
            ],
        }
        urlopen.return_value.__enter__.return_value = BytesIO(
            json.dumps(payload).encode("utf-8")
        )

        reports = fetch_wildfire_events(days=30)

        self.assertEqual(sorted(reports["event_id"]), ["2", "3"])

    @patch("wildfire_evacuation_risk.gdacs.urlopen")
    def test_quiet_window_404_is_empty(self, urlopen) -> None:
        urlopen.side_effect = HTTPError(
            "https://www.gdacs.org/gdacsapi/test",
            404,
            "not found",
            hdrs=None,
            fp=None,
        )

        reports = fetch_wildfire_events()

        self.assertTrue(reports.empty)

    @patch("wildfire_evacuation_risk.gdacs.urlopen")
    def test_provider_errors_are_sanitized(self, urlopen) -> None:
        urlopen.side_effect = HTTPError(
            "https://www.gdacs.org/gdacsapi/test",
            503,
            "unavailable",
            hdrs=None,
            fp=None,
        )

        with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
            fetch_wildfire_events()

    @patch("wildfire_evacuation_risk.gdacs.urlopen")
    def test_connection_errors_are_wrapped(self, urlopen) -> None:
        urlopen.side_effect = URLError("offline")

        with self.assertRaisesRegex(RuntimeError, "connect"):
            fetch_wildfire_events()

    def test_rejects_invalid_parameters(self) -> None:
        with self.assertRaisesRegex(ValueError, "look-back"):
            fetch_wildfire_events(days=0)
        with self.assertRaisesRegex(ValueError, "page size"):
            fetch_wildfire_events(page_size=101)
        with self.assertRaisesRegex(ValueError, "max pages"):
            fetch_wildfire_events(max_pages=21)


if __name__ == "__main__":
    unittest.main()
