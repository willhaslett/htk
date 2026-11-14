"""Shared progress tracks distinguish unknown, empty and completed work."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec/list_view"))
import progress_style
from list_model import text_width
sys.path.pop(0)


class ProgressStyleTests(unittest.TestCase):
    def test_known_progress_is_bounded_and_never_fills_early(self):
        cases = ((-1, 20, "──────"), (0, 20, "──────"), (5, 20, "━─────"),
                 (10, 20, "━━━───"), (19, 20, "━━━━━─"), (20, 20, "━━━━━━"),
                 (21, 20, "━━━━━━"))
        for current, total, expected in cases:
            with self.subTest(current=current, total=total):
                bar = progress_style.bar(current, total)
                self.assertEqual(bar, expected)
                self.assertEqual(text_width(bar), 6)

    def test_unknown_progress_has_a_distinct_stable_track(self):
        for current, total in ((None, None), (0, 0), (0, None), (None, 10), (5, -1)):
            self.assertEqual(progress_style.bar(current, total), "┄┄┄┄┄┄")
        self.assertEqual(progress_style.bar(1, 2, width=4), "━━──")
        self.assertEqual(progress_style.bar(width=0), "")
        self.assertEqual(progress_style.bar(1, 2, width=0), "")
