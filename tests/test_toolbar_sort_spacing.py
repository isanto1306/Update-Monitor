"""Regression tests for the narrowly scoped toolbar sort selector spacing."""
import re
import unittest
from pathlib import Path

JS = (Path(__file__).resolve().parents[1] / "static" / "update-channel.js").read_text(encoding="utf-8")


class SortSpacingTests(unittest.TestCase):
    def test_selector_is_scoped_to_toolbar_sort_control(self):
        self.assertRegex(JS, r"um-sort-label-spacing-v0[0-9]+")
        self.assertIn(".toolbar-current-sort .backup-retention-control", JS)
        self.assertIn(".toolbar-current-sort [data-current-sort-select-label]", JS)
        self.assertIn("padding-right:9px", JS)

    def test_control_is_wider_than_old_174_px(self):
        m = re.search(
            r"\.toolbar-current-sort \.backup-retention-control\{[^}]*width:(\d+)px",
            JS,
        )
        self.assertIsNotNone(m)
        self.assertGreater(int(m.group(1)), 174)
        self.assertLessEqual(int(m.group(1)), 210)

    def test_narrow_viewports_can_shrink_without_overflow(self):
        self.assertIn("@media(max-width:560px)", JS)
        self.assertIn("min-width:0 !important", JS)
        self.assertIn("text-overflow:ellipsis", JS)

if __name__ == "__main__":
    unittest.main()
