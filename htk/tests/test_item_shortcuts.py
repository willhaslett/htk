"""The shared Control-digit convention, independent of any data provider."""

import curses
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec/list_view"))
import item_shortcuts as shortcuts
sys.path.pop(0)


class ShortcutTests(unittest.TestCase):
    def test_first_ten_labels_and_stable_gutters(self):
        self.assertEqual([shortcuts.label(i) for i in range(12)],
                         ["⌃1", "⌃2", "⌃3", "⌃4", "⌃5", "⌃6", "⌃7", "⌃8", "⌃9", "⌃0", "", ""])
        self.assertEqual(shortcuts.prefix(0), "⌃1 ")
        self.assertEqual(shortcuts.prefix(0, top_level=False), "⌃1 ")
        self.assertEqual(shortcuts.prefix(9), "⌃0 ")
        self.assertEqual(shortcuts.prefix(10), "   ")
        self.assertEqual(shortcuts.prefix(None), "   ")

    def test_exact_control_presses_only_and_zero_means_tenth(self):
        for index, digit in enumerate("1234567890"):
            for event in (1, "1"):
                self.assertEqual(shortcuts.index_for_key(ord(digit), 4, event), index)
            for modifiers in (0, 1, 2, 5, 8, 12):
                self.assertIsNone(shortcuts.index_for_key(ord(digit), modifiers))
            for event in (2, 3, "2", "3"):
                self.assertIsNone(shortcuts.index_for_key(ord(digit), 4, event))
        for code in (0, 3, 27, ord("q"), 999999):
            self.assertIsNone(shortcuts.index_for_key(code, 4))

    def test_curses_adapter_handles_fragmentation_and_preserves_normal_keys(self):
        keys = shortcuts.CursesKeys()
        result = []
        for key in b"\x1b[49;5u\x1b[48;5:1u\x1b[49;5:2u\x1b[49;5:3u\x1b[49;9u":
            result.extend(keys.feed(key))
        self.assertEqual(result, [("open_index", 0), ("open_index", 9)])
        self.assertEqual(keys.feed(ord("1")), [ord("1")])
        self.assertEqual(keys.feed(curses.KEY_DOWN), [curses.KEY_DOWN])
        self.assertEqual(keys.feed(0), [0])  # No legacy Ctrl-2 interpretation.
        self.assertEqual(keys.feed(27, now=1), [])
        self.assertEqual(keys.feed(-1, now=1.1), [27])
        self.assertEqual(keys.feed(27, now=2), [])
        self.assertEqual(keys.feed(ord("q"), now=2.01), [27, ord("q")])
        result = []
        for key in b"\x1b[27u\x1b[99;5u\x1b[103;2u\x1b[81;1u":
            result.extend(keys.feed(key))
        self.assertEqual(result, [27, 3, ord("G"), ord("Q")])

    def test_incomplete_and_unrelated_sequences_do_not_activate_items(self):
        keys = shortcuts.CursesKeys()
        for key in b"\x1b[49;":
            self.assertEqual(keys.feed(key, now=1), [])
        self.assertEqual(keys.feed(-1, now=1.3), [])
        self.assertEqual(keys.feed(ord("q"), now=1.4), [ord("q")])
        for key in b"\x1b[1;5A":
            self.assertEqual(keys.feed(key), [])


if __name__ == "__main__":
    unittest.main()
