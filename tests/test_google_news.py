import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from wildfire_evacuation_risk import google_news
from wildfire_evacuation_risk.google_news import (
    build_google_query,
    search_google_news,
)

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
  <title>Fire spreads across Portugal</title>
  <link>https://example.test/portugal-fire</link>
  <pubDate>Mon, 10 Nov 2026 03:15:00 GMT</pubDate>
  <source url="https://example.test">Example News</source>
</item>
<item>
  <title>Duplicate link</title>
  <link>https://example.test/portugal-fire</link>
  <pubDate>Mon, 10 Nov 2026 04:15:00 GMT</pubDate>
  <source url="https://example.test">Example News</source>
</item>
<item>
  <title>Evacuations continue</title>
  <link>https://example.test/evacuations</link>
  <pubDate>Mon, 10 Nov 2026 02:15:00 GMT</pubDate>
  <source url="https://other.test">Other News</source>
</item>
</channel></rss>"""


class GoogleNewsClientTests(unittest.TestCase):
    @patch("wildfire_evacuation_risk.google_news.urlopen")
    def test_builds_query_and_parses_rss_items(self, urlopen) -> None:
        urlopen.return_value.__enter__.return_value = BytesIO(RSS.encode("utf-8"))

        articles = search_google_news(
            "Forest fires in Portugal", "Portugal", max_records=10, timespan="30d"
        )

        request = urlopen.call_args.args[0]
        self.assertIn("news.google.com/rss/search", request.full_url)
        self.assertIn("hl=en-US", request.full_url)
        self.assertIn("when%3A30d", request.full_url)
        self.assertEqual(len(articles), 2)
        self.assertEqual(articles.iloc[0]["title"], "Fire spreads across Portugal")
        self.assertEqual(articles.iloc[0]["domain"], "Example News")
        self.assertEqual(articles.iloc[0]["language"], "English")
        self.assertEqual(articles.iloc[1]["url"], "https://example.test/evacuations")
        self.assertEqual(articles.iloc[0]["seen_at"].year, 2026)

    @patch("wildfire_evacuation_risk.google_news.urlopen")
    def test_respects_max_records(self, urlopen) -> None:
        urlopen.return_value.__enter__.return_value = BytesIO(RSS.encode("utf-8"))

        articles = search_google_news("Portugal", max_records=1, timespan="7d")

        self.assertEqual(len(articles), 1)

    @patch("wildfire_evacuation_risk.google_news.urlopen")
    def test_unreadable_feed_raises(self, urlopen) -> None:
        urlopen.return_value.__enter__.return_value = BytesIO(b"not xml")

        with self.assertRaisesRegex(RuntimeError, "unreadable"):
            search_google_news("Portugal")

    @patch("wildfire_evacuation_risk.google_news.urlopen")
    def test_rate_limit_has_actionable_message(self, urlopen) -> None:
        urlopen.side_effect = HTTPError(
            "https://news.google.com/rss/search", 429, "too many", hdrs=None, fp=None
        )

        with self.assertRaisesRegex(RuntimeError, "rate limit"):
            search_google_news("Portugal")

    @patch("wildfire_evacuation_risk.google_news.urlopen")
    def test_connection_errors_are_wrapped(self, urlopen) -> None:
        urlopen.side_effect = URLError("offline")

        with self.assertRaisesRegex(RuntimeError, "connect"):
            search_google_news("Portugal")

    def test_builds_query_from_report_name_and_country(self) -> None:
        self.assertEqual(
            build_google_query("Forest fires in Portugal", "Portugal"),
            '"Portugal" wildfire',
        )
        self.assertEqual(
            build_google_query("Wildfire - Para State, Brazil", "Brazil"),
            '"Para State" "Brazil" wildfire',
        )

    def test_rejects_invalid_parameters(self) -> None:
        with self.assertRaisesRegex(ValueError, "report name"):
            build_google_query("   ")
        with self.assertRaisesRegex(ValueError, "max records"):
            search_google_news("Portugal", max_records=0)
        with self.assertRaisesRegex(ValueError, "Timespan"):
            search_google_news("Portugal", timespan="yearly")

    def test_article_columns_match_news_client(self) -> None:
        from wildfire_evacuation_risk.news import ARTICLE_COLUMNS

        self.assertEqual(google_news.ARTICLE_COLUMNS, ARTICLE_COLUMNS)


if __name__ == "__main__":
    unittest.main()
