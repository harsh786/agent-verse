"""Structural checks for the standalone data analyst roadmap."""

from pathlib import Path
import unittest


ROADMAP = Path(__file__).with_name("data-analyst-job-roadmap-india.html")


class DataAnalystRoadmapHtmlTests(unittest.TestCase):
    def test_roadmap_has_required_standalone_sections(self) -> None:
        self.assertTrue(ROADMAP.exists(), "Create the standalone HTML roadmap.")

        content = ROADMAP.read_text(encoding="utf-8")

        for section_id in (
            "quick-start",
            "mindset",
            "excel",
            "sql",
            "bi",
            "python",
            "statistics",
            "portfolio",
            "hiring",
            "readiness",
        ):
            self.assertIn(f'id="{section_id}"', content)

        self.assertIn("@media print", content)
        self.assertIn("prefers-reduced-motion", content)
        self.assertNotRegex(content, r'<script[^>]+src=|<link[^>]+stylesheet|https?://')


if __name__ == "__main__":
    unittest.main()
