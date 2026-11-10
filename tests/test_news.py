import json
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from wildfire_evacuation_risk import news
from wildfire_evacuation_risk.news import build_report_query, search_news


class NewsClientTests(unittest.TestCase):
    def setUp(self) -> None:
        news._last_request_at = None

    @patch("wildfire_evacuation_risk.news.urlopen")
    def test_builds_artlist_query_and_parses_articles(self, urlopen) -> None:
        payload = {
            "articles": [
                {
                    "title": "Fire spreads across Para State",
                    "url": "https://example.test/a",
                    "domain": "example.test",
                    "language": "English",
                    "sourcecountry": "Brazil",
                    "seendate": "20261110T031500Z",
                },
                {
                    "title": "Duplicate link",
                    "url": "https://example.test/a",
                    "domain": "example.test",
                    "language": "English",
                    "sourcecountry": "Brazil",
                    "seendate": "20261110T041500Z",
                },
            ]
        }
        urlopen.return_value.__enter__.return_value = BytesIO(
            json.dumps(payload).encode("utf-8")
        )

        articles = search_news(
            '"Para State" sourcelang:english',
            max_records=10,
            timespan="7d",
            min_interval=0,
        )

        request = urlopen.call_args.args[0]
        self.assertIn("mode=ArtList", request.full_url)
        self.assertIn("format=json", request.full_url)
        self.assertIn("maxrecords=10", request.full_url)
        self.assertIn("timespan=7d", request.full_url)
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles.iloc[0]["title"], "Fire spreads across Para State")
        self.assertEqual(articles.iloc[0]["domain"], "example.test")
        self.assertEqual(articles.iloc[0]["seen_at"].year, 2026)

    @patch("wildfire_evacuation_risk.news.urlopen")
    def test_plain_text_200_raises_runtime_error(self, urlopen) -> None:
        # GDELT reports rate limits as plain text with HTTP 200.
        urlopen.return_value.__enter__.return_value = BytesIO(
            b"Please limit requests to one every 5 seconds"
        )

        with self.assertRaisesRegex(RuntimeError, "non-JSON"):
            search_news("wildfire", min_interval=0)

    @patch("wildfire_evacuation_risk.news.urlopen")
    def test_empty_body_returns_typed_frame(self, urlopen) -> None:
        urlopen.return_value.__enter__.return_value = BytesIO(b"")

        articles = search_news("wildfire", min_interval=0)

        self.assertTrue(articles.empty)
        self.assertEqual(tuple(articles.columns), news.ARTICLE_COLUMNS)

    @patch("wildfire_evacuation_risk.news.urlopen")
    def test_no_articles_returns_typed_frame(self, urlopen) -> None:
        urlopen.return_value.__enter__.return_value = BytesIO(b'{"articles": []}')

        articles = search_news("wildfire", min_interval=0)

        self.assertTrue(articles.empty)
        self.assertEqual(tuple(articles.columns), news.ARTICLE_COLUMNS)

    @patch("wildfire_evacuation_risk.news.urlopen")
    def test_provider_errors_are_sanitized(self, urlopen) -> None:
        urlopen.side_effect = HTTPError(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            500,
            "server error",
            hdrs=None,
            fp=None,
        )

        with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
            search_news("wildfire", min_interval=0)

    @patch("wildfire_evacuation_risk.news.urlopen")
    def test_rate_limit_has_actionable_message(self, urlopen) -> None:
        urlopen.side_effect = HTTPError(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            429,
            "too many requests",
            hdrs=None,
            fp=None,
        )

        with self.assertRaisesRegex(RuntimeError, "rate limit"):
            search_news("wildfire", min_interval=0)

    def test_builds_query_from_report_name_and_country(self) -> None:
        query = build_report_query("Wildfire - Para State, Brazil", "Brazil")

        self.assertEqual(query, '"Para State" "Brazil" sourcelang:english')

    def test_query_reuses_country_already_in_place_name(self) -> None:
        query = build_report_query("Forest Fire in Chile, Chile", "Chile")

        self.assertEqual(query, '"Chile" sourcelang:english')

    def test_query_strips_plural_report_prefix(self) -> None:
        query = build_report_query("Forest fires in Portugal", "Portugal")

        self.assertEqual(query, '"Portugal" sourcelang:english')

    def test_query_uses_first_country_when_report_spans_several(self) -> None:
        query = build_report_query(
            "Forest fires in South Sudan, Sudan", "South Sudan, Sudan"
        )

        self.assertEqual(query, '"South Sudan" sourcelang:english')

    def test_rejects_invalid_parameters(self) -> None:
        with self.assertRaisesRegex(ValueError, "query"):
            search_news("   ", min_interval=0)
        with self.assertRaisesRegex(ValueError, "max records"):
            search_news("wildfire", max_records=0, min_interval=0)
        with self.assertRaisesRegex(ValueError, "timespan"):
            search_news("wildfire", timespan="yearly", min_interval=0)
        with self.assertRaisesRegex(ValueError, "sort"):
            search_news("wildfire", sort="Newest", min_interval=0)


if __name__ == "__main__":
    unittest.main()
