import unittest

from wildfire_evacuation_risk.globe import (
    EARTH_TEXTURE_URL,
    GLOBE_SCRIPT_URL,
    build_globe_html,
)


class GlobeHtmlTests(unittest.TestCase):
    def test_builds_an_interactive_globe_with_attributed_earth_texture(self) -> None:
        html = build_globe_html(
            reports=[],
            hotspots=[],
            longitude=12,
            latitude=14,
            camera_altitude=2.4,
        )

        self.assertIn(GLOBE_SCRIPT_URL, html)
        self.assertIn(EARTH_TEXTURE_URL, html)
        self.assertIn("Globe()(container)", html)
        self.assertIn(
            '<div id="globeContainer" '
            'aria-label="Interactive 3D wildfire awareness globe"></div>',
            html,
        )
        self.assertIn(
            'const selectedPoint = document.getElementById("selectedPoint");',
            html,
        )
        self.assertIn("NASA Blue Marble Next Generation", html)
        self.assertNotIn("accessToken", html)
        self.assertIn("Globe imagery failed to load", html)

    def test_includes_report_and_hotspot_details_safely(self) -> None:
        html = build_globe_html(
            reports=[
                {
                    "longitude": 114.0,
                    "latitude": -1.0,
                    "label": "Fire <script>alert(1)</script>",
                    "detail": "Indonesia & current",
                    "color": [255, 70, 45],
                }
            ],
            hotspots=[
                {
                    "longitude": 115.0,
                    "latitude": -2.0,
                    "label": "Satellite hotspot",
                    "detail": "Observed today",
                }
            ],
            longitude=118,
            latitude=-2,
            camera_altitude=1.45,
        )

        self.assertIn("Fire \\u003cscript\\u003ealert(1)\\u003c/script\\u003e", html)
        self.assertIn("Indonesia \\u0026 current", html)
        self.assertIn("Satellite hotspot", html)
        self.assertIn('kind: "report"', html)
        self.assertIn('kind: "hotspot"', html)
        self.assertIn(".onPointClick(", html)


if __name__ == "__main__":
    unittest.main()
