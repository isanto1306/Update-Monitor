"""Regression: only the toolbar sort selector gains spacing for long labels."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "static/update-channel.js").read_text(encoding="utf-8")

class SortDropdownSpacingTests(unittest.TestCase):
    def test_only_sort_dropdown_gets_wider(self):
        self.assertIn(".toolbar-current-sort .backup-retention-control{flex:0 1 218px!important;width:218px!important;", SOURCE)
        self.assertNotIn("\'.backup-retention-control{flex:0 1 218px", SOURCE)

    def test_label_cannot_cover_chevron(self):
        self.assertIn("[data-current-sort-select-wrap] .backup-retention-select-button{padding-right:40px!important;}", SOURCE)
        self.assertIn("[data-current-sort-select-wrap] [data-current-sort-select-label]{display:block;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}", SOURCE)

    def test_mobile_selector_remains_responsive(self):
        self.assertIn("@media(max-width:560px){.toolbar-current-sort .backup-retention-control", SOURCE)
        self.assertIn("width:min(218px,58vw)!important;", SOURCE)


if __name__ == "__main__":
    unittest.main()
