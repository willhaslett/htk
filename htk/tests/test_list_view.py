import curses
from datetime import datetime
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = ROOT / "libexec/list_view"
sys.path.insert(0, str(MODULE_DIR))
try:
    import list_model as model
    import list_watcher as watcher
    spec = importlib.util.spec_from_file_location("list_view_ui", MODULE_DIR / "viewer.py")
    viewer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(viewer)
finally:
    sys.path.pop(0)


def sample():
    return {"version": 1, "title": "Example list", "columns": [
        {"key": "id", "label": "Agent ID", "width": 8},
        {"key": "title", "label": "Title", "min_width": 8, "flex": 1},
        {"key": "status", "label": "Status", "width": 14}], "sections": [
        {"title": "Active", "rows": [{"cells": {
            "id": "A1", "title": "A long title 界 with more detail",
            "status": {"text": "Needs action", "tone": "warning"}}}]},
        {"title": "Finished", "rows": []}], "footer": "Source note"}


def eventually(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("expected state did not arrive")


class Screen:
    def __init__(self, height, width):
        self.height, self.width, self.writes = height, width, []
        self.clears = 0

    def getmaxyx(self):
        return self.height, self.width

    def erase(self):
        self.writes = []

    def clear(self):
        self.clears += 1
        self.erase()

    def addstr(self, y, x, text, color):
        assert 0 <= y < self.height and 0 <= x < self.width
        assert x + model.text_width(text) <= self.width
        self.writes.append((y, x, text, color))

    def refresh(self):
        pass

    def text(self):
        return "\n".join(text for _, _, text, _ in self.writes)


def linked_list():
    value = sample()
    value["sections"] = [
        {"title": "Empty", "rows": []},
        {"title": "Active", "rows": [{"cells": {
            "id": {"text": f"A{number}", "href": f"https://example.test/pr/{number}"},
            "title": "A title with enough detail to wrap across several lines",
            "status": "Ready"}} for number in (1, 2)]},
        {"title": "Assigned", "rows": [{"cells": {
            "id": {"text": "A1", "href": "https://example.test/pr/1"},
            "title": "Also assigned", "status": "Ready"}}]}]
    return value


class SelectionTests(unittest.TestCase):
    def test_focus_keeps_content_positions_colors_and_click_targets_in_stable_gutters(self):
        document = model.parse_document(json.dumps([
            {"Item": {"text": "  界 link", "href": "https://example.test/item"},
             "State": {"text": "Failed", "tone": "error"}}]))
        colors = {"focus": 33, "link": 36, "error": 31}
        for framed in (False, True):
            for width in (24, 80):
                with self.subTest(framed=framed, width=width):
                    selection = viewer.LinkSelection()
                    before, after = Screen(14, width), Screen(14, width)
                    old_links, new_links = io.StringIO(), io.StringIO()
                    viewer.draw(before, watcher.Snapshot(document), colors=colors, selection=selection,
                                framed=framed, link_output=old_links)
                    selection.move(1)
                    viewer.draw(after, watcher.Snapshot(document), colors=colors, selection=selection,
                                framed=framed, link_output=new_links)
                    body_y = 4 if framed else 2
                    content = lambda screen: [(x, text, color) for y, x, text, color in screen.writes
                                               if y == body_y and text not in ("[", "]")]
                    self.assertEqual(content(before), content(after))
                    self.assertEqual(old_links.getvalue(), new_links.getvalue())
                    self.assertTrue(any(color == 31 for _, _, color in content(after)))
                    left = 2 if framed else 0
                    right = width - 3 if framed else width - 1
                    self.assertIn((body_y, left, "[", 33), after.writes)
                    self.assertIn((body_y, right, "]", 33), after.writes)
                    selected = next(target for target in selection.targets if target.key == selection.key)
                    self.assertEqual([(y, x, text) for y, x, text, color in after.writes if color == 33],
                                     [(y, x, text) for y in range(body_y, body_y + selected.stop - selected.start)
                                      for x, text in ((left, "["), (right, "]"))])
                    labels = re.findall(r"\x1b]8;;[^\x1b]+\x1b\\(.*?)\x1b]8;;", new_links.getvalue())
                    self.assertTrue(labels)
                    self.assertTrue(all("[" not in label and "]" not in label for label in labels))

    def test_brackets_keep_compact_rows_fixed_when_selection_moves(self):
        document = model.parse_document(json.dumps([
            {"Item": {"text": f"Item {number}", "href": f"https://example.test/{number}"}}
            for number in range(3)]))
        selection = viewer.LinkSelection()
        screen = Screen(20, 40)
        snapshot = watcher.Snapshot(document)
        viewer.draw(screen, snapshot, selection=selection, framed=False)
        self.assertEqual([(target.start, target.stop) for target in selection.targets],
                         [(0, 1), (1, 2), (2, 3)])
        positions = [(y, x, text) for y, x, text, _ in screen.writes if text.strip().startswith("Item ")]
        self.assertEqual([y for y, _, _ in positions], [2, 3, 4])
        for _ in range(4):
            selection.move(1)
            viewer.draw(screen, snapshot, selection=selection, framed=False)
            self.assertEqual([(y, x, text) for y, x, text, _ in screen.writes
                              if text.strip().startswith("Item ")], positions)
        # The data and ordinary text output keep their original density.
        _, compact = viewer.table_lines(document, 40)
        self.assertEqual(len(compact), 3)
        self.assertIn("Item 0\nItem 1\nItem 2", viewer.plain(document))

    def test_small_pane_never_offers_an_invisible_link(self):
        document = model.parse_document(json.dumps([
            {"Item": {"text": "Visible link", "href": "https://example.test/item"}}]))
        for framed, minimum in ((False, 4), (True, 7)):
            selection = viewer.LinkSelection()
            viewer.draw(Screen(20, 40), watcher.Snapshot(document), selection=selection, framed=framed)
            selection.move(1)
            small = Screen(minimum - 1, 40)
            viewer.draw(small, watcher.Snapshot(document), selection=selection, framed=framed)
            self.assertIn("enlarge pane", small.text())
            self.assertEqual(selection.targets, [])
            self.assertIsNone(selection.enter())
            usable = Screen(minimum, 40)
            viewer.draw(usable, watcher.Snapshot(document), selection=selection, framed=framed)
            self.assertIn("Visible link", usable.text())
            self.assertIn("[", usable.text())
            self.assertIn("]", usable.text())
            self.assertEqual(selection.enter(), "https://example.test/item")

    def test_navigation_skips_headings_and_empty_sections_and_visits_duplicate_prs(self):
        document = model.parse_document(json.dumps(linked_list()))
        for direction, numbers in ((1, [1, 2, 1]), (-1, [1, 2, 1])):
            with self.subTest(direction=direction):
                selection = viewer.LinkSelection()
                viewer.draw(Screen(20, 80), watcher.Snapshot(document), selection=selection)
                self.assertIsNone(selection.href)
                visited = []
                for number in numbers:
                    selection.move(direction)
                    self.assertEqual(selection.href, f"https://example.test/pr/{number}")
                    visited.append(selection.key)
                self.assertNotEqual(visited[0], visited[-1])
                selection.move(direction)
                self.assertEqual(selection.key, visited[0])  # Wrap to the other end.
                selection.move(-direction)
                self.assertEqual(selection.key, visited[-1])

    def test_refresh_retains_pr_when_reordered_or_moved_and_clears_missing_pr(self):
        value = linked_list()
        selection = viewer.LinkSelection()

        def refresh():
            document = model.parse_document(json.dumps(value))
            viewer.draw(Screen(20, 80), watcher.Snapshot(document), selection=selection)

        refresh()
        selection.move(1)
        selection.move(1)
        self.assertEqual(selection.href, "https://example.test/pr/2")
        value["sections"][1]["rows"].reverse()
        refresh()
        self.assertEqual(selection.href, "https://example.test/pr/2")
        row = value["sections"][1]["rows"].pop(0)
        value["sections"].append({"title": "Merged", "rows": [row]})
        refresh()
        self.assertEqual(selection.href, "https://example.test/pr/2")
        self.assertEqual(selection.key[0], "Merged")
        value["sections"].pop()
        refresh()
        self.assertIsNone(selection.href)
        selection.move(-1)
        self.assertEqual(selection.key[0], "Assigned")
        value["sections"] = []
        refresh()
        selection.move(1)
        self.assertIsNone(selection.href)

    def test_selection_brackets_cover_wrapped_row_preserve_links_and_scroll_into_view(self):
        value = linked_list()
        value["columns"][1]["wrap"] = True
        document = model.parse_document(json.dumps(value))
        selection = viewer.LinkSelection()
        snapshot = watcher.Snapshot(document)
        viewer.draw(Screen(9, 46), snapshot, selection=selection, framed=False)
        selection.move(-1)
        edge_style = 768
        for height, width in ((9, 46), (7, 38), (20, 80)):
            with self.subTest(size=(height, width)):
                screen, output = Screen(height, width), io.StringIO()
                selection.ensure_visible = True
                offset = viewer.draw(screen, snapshot, selection=selection, framed=False,
                                     colors={"focus": edge_style, "link": 256}, link_output=output)
                selected = next(target for target in selection.targets if target.key == selection.key)
                self.assertGreaterEqual(selected.start, offset)
                self.assertLessEqual(selected.stop, offset + height - 3)
                rows = {2 + line - offset for line in range(selected.start, selected.stop)}
                writes = [entry for entry in screen.writes if entry[0] in rows]
                self.assertTrue(writes)
                markers = [(y, x, text) for y, x, text, color in writes if color == edge_style]
                self.assertEqual(set(markers), {(y, x, text) for y in rows
                                               for x, text in ((0, "["), (width - 1, "]"))})
                self.assertFalse(any(color & curses.A_REVERSE for _, _, _, color in writes))
                self.assertIn("\x1b[0;4;36m\x1b]8;;https://example.test/pr/1", output.getvalue())
                self.assertNotIn("0;7", output.getvalue())
        key = selection.key
        viewer.draw(Screen(1, 1), snapshot, selection=selection)
        self.assertEqual(selection.targets, [])
        self.assertEqual(selection.key, key)
        viewer.draw(Screen(20, 80), snapshot, selection=selection)
        self.assertEqual(selection.key, key)


class HierarchyTests(unittest.TestCase):
    def value(self):
        def row(name, children=None):
            result = {"cells": {"item": {"text": name, "href": "https://example.test/" + name},
                                "progress": {"text": "◑", "tone": "error"},
                                "duration": {"text": "", "elapsed_since": 100}}}
            if children is not None:
                result.update(children=children, link_label="Open " + name)
            return result
        return {"version": 1, "title": "Hierarchy", "columns": [
            {"key": "item", "label": "Build", "flex": 1},
            {"key": "progress", "label": "Progress", "width": 8},
            {"key": "duration", "label": "Duration", "width": 10}],
            "sections": [{"title": "", "show_count": False,
                          "rows": [row("parent", [row("child", [row("leaf")]), row("other")]), row("second", [])]}]}

    def test_schema_requires_visible_parent_link_same_columns_and_bounded_children(self):
        value = self.value()
        document = model.parse_document(json.dumps(value))
        self.assertEqual(len(document.sections[0].rows[0].children), 2)
        self.assertEqual(document.sections[0].rows[1].children, ())
        for change in ({"children": None}, {"children": [{}]}, {"link_label": "\x1b[0m"}):
            bad = self.value()
            bad["sections"][0]["rows"][0].update(change)
            with self.assertRaises(ValueError):
                model.parse_document(json.dumps(bad))
        value["sections"][0]["rows"][0]["cells"]["item"] = "No link"
        with self.assertRaisesRegex(ValueError, "visible parent link"):
            model.parse_document(json.dumps(value))
        value = self.value()
        row = value["sections"][0]["rows"][0]
        for _ in range(14):
            child = {"cells": row["cells"], "children": []}
            row["children"] = [child]
            row = child
        with self.assertRaisesRegex(ValueError, "12 levels"):
            model.parse_document(json.dumps(value))

    def test_enter_expands_parent_link_first_nested_children_back_and_empty_children(self):
        document = model.parse_document(json.dumps(self.value()))
        selection = viewer.LinkSelection()
        def draw():
            viewer.draw(Screen(12, 70), watcher.Snapshot(document), selection=selection, now=854)
        draw()
        self.assertIsNone(selection.enter())
        selection.move(1)
        parent = selection.key
        self.assertIsNone(selection.enter())
        draw()
        visible = selection.view(document)
        rows = visible.sections[0].rows
        self.assertEqual([r.cells[0].text for r in rows], ["Open parent", "  child", "  other"])
        self.assertEqual(rows[0].cells[1].text, "")
        self.assertEqual(selection.enter(), "https://example.test/parent")
        selection.move(1)
        self.assertIsNone(selection.enter())
        draw()
        self.assertEqual(selection.enter(), "https://example.test/child")
        selection.move(1)
        self.assertEqual(selection.enter(), "https://example.test/leaf")
        self.assertTrue(selection.back())
        draw()
        self.assertEqual(selection.href, "https://example.test/child")
        self.assertTrue(selection.back())
        draw()
        self.assertEqual(selection.key, parent)
        self.assertFalse(selection.back())
        selection.move(1)
        selection.enter()
        draw()
        self.assertEqual(len(selection.targets), 1)
        self.assertEqual(selection.enter(), "https://example.test/second")

    def test_refresh_preserves_expanded_path_selection_and_handles_disappearing_parent(self):
        value, selection = self.value(), viewer.LinkSelection()
        def draw(stale=False):
            document = model.parse_document(json.dumps(value))
            viewer.draw(Screen(10, 45), watcher.Snapshot(document, error="Offline" if stale else None),
                        selection=selection, now=854)
            return document
        draw()
        selection.move(1)
        selection.enter()
        draw()
        selection.move(-1)
        self.assertEqual(selection.href, "https://example.test/other")
        value["sections"][0]["rows"].reverse()
        value["sections"][0]["rows"][1]["children"].reverse()
        draw(stale=True)
        self.assertEqual(selection.href, "https://example.test/other")
        self.assertEqual(len(selection.path), 1)
        value["sections"][0]["rows"].pop()
        draw()
        self.assertEqual(selection.path, [])
        self.assertIsNone(selection.href)

    def test_child_timers_indentation_and_red_failure_survive_selection(self):
        document = model.parse_document(json.dumps(self.value()))
        selection = viewer.LinkSelection()
        viewer.draw(Screen(12, 70), watcher.Snapshot(document), selection=selection)
        selection.move(1)
        selection.enter()
        screen, output = Screen(12, 70), io.StringIO()
        viewer.draw(screen, watcher.Snapshot(document), selection=selection)
        selection.move(1)
        viewer.draw(screen, watcher.Snapshot(document), selection=selection, framed=False, now=854,
                    colors={"focus": 17, "error": 31}, link_output=output)
        self.assertIn("12m 34s", screen.text())
        self.assertTrue(any(text.strip() == "◑" and color == 31 for _, _, text, color in screen.writes))
        self.assertIn("\x1b]8;;https://example.test/child\x1b\\child", output.getvalue())
        self.assertNotIn("\x1b\\  child", output.getvalue())

    def test_screen_escape_backs_out_but_only_q_quits_at_root_or_in_children(self):
        snapshot = watcher.Snapshot(model.parse_document(json.dumps(self.value())))
        class LiveScreen(Screen):
            def __init__(self):
                super().__init__(12, 70)
                self.keys = iter([ord('j'), 13, ord('j'), 13, 27, 27, 27, ord('q')])
                self.frames = []
            def keypad(self, value): pass
            def timeout(self, value): pass
            def getch(self): return next(self.keys)
            def refresh(self): self.frames.append(self.text())
        screen = LiveScreen()
        with patch.object(curses, 'raw'), patch.object(curses, 'curs_set'), \
                patch.object(curses, 'has_colors', return_value=False), \
                patch.object(sys, 'stdout', io.StringIO()), patch.object(viewer, 'open_url') as opener:
            viewer.run_screen(screen, lambda: snapshot, 10, select_links=True)
        opener.assert_not_called()
        self.assertTrue(any("Open parent" in frame for frame in screen.frames))
        self.assertTrue(any("Open child" in frame for frame in screen.frames))
        self.assertNotIn("Open parent", screen.frames[-1])
        self.assertEqual(list(screen.keys), [])  # Root Escape did not exit before q.
        screen = LiveScreen()
        screen.keys = iter([ord('j'), 13, *b'\x1b[50;5u', 27, *b'\x1b[49;5u', ord('q')])
        with patch.object(curses, 'raw'), patch.object(curses, 'curs_set'), \
                patch.object(curses, 'has_colors', return_value=False), \
                patch.object(sys, 'stdout', io.StringIO()), patch.object(viewer, 'open_url') as opener:
            viewer.run_screen(screen, lambda: snapshot, 10, select_links=True)
        self.assertEqual([call.args[0] for call in opener.call_args_list],
                         ['https://example.test/child', 'https://example.test/parent'])
        self.assertTrue(any('⌃1 ' in frame and 'Open parent' in frame
                            for frame in screen.frames))
        for quit_key in (ord('q'), ord('Q'), 3):
            screen = LiveScreen()
            screen.keys = iter([ord('j'), 13, quit_key])
            with patch.object(curses, 'raw'), patch.object(curses, 'curs_set'), \
                    patch.object(curses, 'has_colors', return_value=False), \
                    patch.object(sys, 'stdout', io.StringIO()):
                viewer.run_screen(screen, lambda: snapshot, 10, select_links=True)
            self.assertIn("Open parent", screen.frames[-1])
            self.assertEqual(list(screen.keys), [])


class TimerTests(unittest.TestCase):
    def test_elapsed_schema_duration_rollover_and_static_completed_values(self):
        for start in (0, 100, 100.25):
            document = model.parse_document(json.dumps([{"Duration": {"text": "", "elapsed_since": start}}]))
            cell = document.sections[0].rows[0].cells[0]
            for elapsed, expected in ((-1, "0m 00s"), (59.9, "0m 59s"), (60, "1m 00s"),
                                      (754, "12m 34s"), (3600, "60m 00s")):
                with self.subTest(start=start, elapsed=elapsed):
                    self.assertEqual(cell.display_text(start + elapsed), expected)
        for invalid in (None, True, "100", -1, float("nan"), float("inf")):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "elapsed_since"):
                model.parse_document(json.dumps([{"Duration": {"text": "", "elapsed_since": invalid}}]))
        completed = model.Cell("12m 34s", "muted")
        self.assertEqual(completed.display_text(854), completed.display_text(1_000_000))

    def test_timer_link_uses_rendered_text_even_with_empty_fallback(self):
        document = model.parse_document(json.dumps([{"Duration": {
            "text": "", "elapsed_since": 100, "href": "https://example.test/build"}}]))
        targets = []
        _, lines = viewer.table_lines(document, 40, targets, now=854)
        self.assertEqual(lines[0][0][0].strip(), "12m 34s")
        self.assertEqual(targets[0].key[1], "https://example.test/build")

    def test_local_ticks_preserve_links_footer_and_selection_without_clearing_until_layout_changes(self):
        value = {"version": 1, "title": "Clock", "columns": [
            {"key": "branch", "label": "Branch", "flex": 1},
            {"key": "duration", "label": "Time", "min_width": 3}],
            "sections": [{"title": "", "show_count": False, "rows": [{"cells": {
                "branch": {"text": "Branch", "href": "https://example.test/build"},
                "duration": {"text": "", "elapsed_since": 100}}}]}]}
        snapshot = watcher.Snapshot(model.parse_document(json.dumps(value)), datetime(2026, 1, 1, 12, 34, 56))

        class LiveScreen(Screen):
            def __init__(self):
                super().__init__(12, 80)
                self.frames, self.clear_counts = [], []
                self.keys = iter([ord('j'), -1, -1, -1, ord('q')])

            def keypad(self, value):
                pass

            def timeout(self, value):
                pass

            def getch(self):
                return next(self.keys)

            def refresh(self):
                self.frames.append(self.writes[:])
                self.clear_counts.append(self.clears)
                self.writes = []

        screen, output = LiveScreen(), io.StringIO()
        # First tick selects, next advances seconds, then 9m59s -> 10m00s changes grid width.
        with patch.dict(os.environ, {'HERDR_PANE_ID': 'test-pane'}), \
                patch.object(curses, 'raw'), patch.object(curses, 'curs_set'), \
                patch.object(curses, 'has_colors', return_value=False), \
                patch.object(viewer.time, 'time', side_effect=[698, 698, 699, 700, 701]), \
                patch.object(sys, 'stdout', output):
            viewer.run_screen(screen, lambda: snapshot, 10, select_links=True)
        self.assertEqual(screen.clear_counts, [1, 2, 2, 3, 3])
        self.assertEqual([next(text.strip() for _, _, text, _ in frame if 'm ' in text)
                          for frame in screen.frames], ['9m 58s', '9m 58s', '9m 59s', '10m 00s', '10m 01s'])
        footers = [next((x, text) for y, x, text, _ in frame if y == 11) for frame in screen.frames]
        self.assertEqual(len(set(footers)), 1)
        self.assertEqual(output.getvalue().count('https://example.test/build'), 5)
        self.assertTrue(all(any(y == 2 and x == 0 and text == "[" for y, x, text, _ in frame)
                            for frame in screen.frames[1:]))
        self.assertFalse(any(color & curses.A_REVERSE for frame in screen.frames
                             for _, _, _, color in frame))


class NumberedItemTests(unittest.TestCase):
    def document(self):
        return model.parse_document(json.dumps({"version": 1, "title": "Numbered",
            "columns": [{"key": "item", "label": "Item", "wrap": True}],
            "sections": [{"title": "First", "rows": [
                {"cells": {"item": {"text": f"Item {i} wrapped with more text", "href": f"https://example.test/{i}"}}}
                for i in range(1, 7)]}, {"title": "Second", "rows": [
                {"cells": {"item": "Unlinked note"}},
                *[{"cells": {"item": {"text": f"Item {i}", "href": f"https://example.test/{i}"}}}
                  for i in range(7, 13)]]}]}))

    def test_numbering_skips_headings_unlinked_rows_and_continuations_and_stops_at_ten(self):
        targets = []
        header, lines = viewer.table_lines(self.document(), 22, targets, numbered=True)
        self.assertEqual(header[0], ("   ", "normal", None))
        self.assertEqual([lines[target.start][0][0] for target in targets],
                         ["⌃1 ", "⌃2 ", "⌃3 ", "⌃4 ", "⌃5 ", "⌃6 ", "⌃7 ", "⌃8 ", "⌃9 ", "⌃0 ", "   ", "   "])
        for target in targets:
            for line in lines[target.start + 1:target.stop]:
                self.assertEqual(line[0], ("   ", "muted", None))
        self.assertNotIn("⌃", viewer.plain(self.document()))
        header, lines = viewer.table_lines(self.document(), 22, numbered=True, top_level=False)
        self.assertEqual(lines[1][0][0], "⌃1 ")
        selection, screen = viewer.LinkSelection(), Screen(10, 35)
        viewer.draw(screen, watcher.Snapshot(self.document()), offset=100, selection=selection,
                    framed=False, numbered=True)
        self.assertFalse(any("⌃1" in text for _, _, text, _ in screen.writes))
        self.assertTrue(any("Item 12" in text for _, _, text, _ in screen.writes))

    def test_shortcuts_open_absolute_links_without_selecting_and_enable_selection_automatically(self):
        snapshot = watcher.Snapshot(self.document())
        class LiveScreen(Screen):
            def __init__(self):
                super().__init__(10, 70)
                self.keys = iter([*b"\x1b[48;5u", 13, ord('G'), *b"\x1b[50;5u",
                                  ord('j'), 13, ord('q')])
            def keypad(self, value): pass
            def timeout(self, value): pass
            def getch(self): return next(self.keys)
        output = io.StringIO()
        with patch.object(curses, 'raw'), patch.object(curses, 'curs_set'), \
                patch.object(curses, 'has_colors', return_value=False), \
                patch.object(sys, 'stdout', output), patch.object(viewer, 'open_url') as opener:
            viewer.run_screen(LiveScreen(), lambda: snapshot, 10)
        self.assertEqual([call.args[0] for call in opener.call_args_list],
                         ["https://example.test/10", "https://example.test/2", "https://example.test/1"])
        self.assertIn("\x1b[>1u", output.getvalue())
        self.assertTrue(output.getvalue().endswith("\x1b[<u"))

    def test_numbered_gutter_never_becomes_a_link_or_moves_on_selection(self):
        document, selection = self.document(), viewer.LinkSelection()
        before, after, output = Screen(18, 50), Screen(18, 50), io.StringIO()
        viewer.draw(before, watcher.Snapshot(document), selection=selection, framed=False, numbered=True)
        selection.move(1)
        viewer.draw(after, watcher.Snapshot(document), selection=selection, framed=False,
                    numbered=True, link_output=output)
        content = lambda screen: [(y, x, text) for y, x, text, _ in screen.writes if text not in ("[", "]")]
        self.assertEqual(content(before), content(after))
        self.assertTrue(any(text == "⌃1 " and x == 2 for _, x, text, _ in after.writes))
        heading_x = next(x for _, x, text, _ in after.writes if text.startswith("First"))
        item_x = next(x for _, x, text, _ in after.writes if text.startswith("Item 1 "))
        self.assertEqual(heading_x, item_x)
        self.assertGreater(heading_x, 2)  # Section titles leave the shortcut gutter clear.
        self.assertNotIn("⌃1", output.getvalue())

    def test_real_terminal_control_zero_opens_tenth_and_preserves_enter_selection_and_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder, document = Path(directory), [
                {"Item": {"text": f"ITEM_{i:02}", "href": f"https://example.test/{i}"}}
                for i in range(1, 13)]
            log, opener = folder / "opened", folder / "open"
            opener.write_text(f"#!{sys.executable}\nimport os,sys\n"
                              "with open(os.environ['SHORTCUT_LOG'], 'a') as log: log.write(sys.argv[1] + '\\n')\n")
            opener.chmod(0o700)
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 10, 70, 0, 0))
            wrapper = """
import fcntl, os, subprocess, sys, termios
os.setsid()
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
before = termios.tcgetattr(0)
code = subprocess.Popen(sys.argv[1:]).wait()
after = termios.tcgetattr(0)
before[3] &= ~getattr(termios, 'PENDIN', 0)
after[3] &= ~getattr(termios, 'PENDIN', 0)
sys.exit(code if before == after else 125)
"""
            process = subprocess.Popen([sys.executable, "-c", wrapper,
                str(ROOT / "bin/list-view"), "--data", json.dumps(document)],
                stdin=slave, stdout=slave, stderr=slave,
                env={**os.environ, "TERM": "xterm-256color", "HERDR_PANE_ID": "test:pane",
                     "PATH": str(folder) + os.pathsep + os.environ.get("PATH", ""), "SHORTCUT_LOG": str(log)})
            output = bytearray()
            try:
                deadline = time.monotonic() + 5
                while b"ITEM_01" not in output and time.monotonic() < deadline:
                    if select.select([master], [], [], 0.05)[0]:
                        output.extend(os.read(master, 65536))
                self.assertIn(b"ITEM_01", output)
                self.assertIn("⌃1".encode(), output)
                os.write(master, b"G\x1b[48;5u\x1b[49;5:2u\x1b[49;5:3u\rj\r")
                deadline = time.monotonic() + 4
                while (not log.exists() or len(log.read_text().splitlines()) < 2) and time.monotonic() < deadline:
                    if select.select([master], [], [], 0.05)[0]:
                        output.extend(os.read(master, 65536))
                self.assertEqual(sorted(log.read_text().splitlines()),
                                 sorted(["https://example.test/10", "https://example.test/1"]))
                os.write(master, b"q")
                deadline = time.monotonic() + 3
                while process.poll() is None and time.monotonic() < deadline:
                    if select.select([master], [], [], 0.05)[0]:
                        output.extend(os.read(master, 65536))
                self.assertEqual(process.poll(), 0, output)
                self.assertIn(b"\x1b[>1u", output)
                self.assertIn(b"\x1b[<u", output)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)
                os.close(master)
                os.close(slave)


class ModelTests(unittest.TestCase):
    def test_simple_lists_need_only_content(self):
        document = model.parse_document('["Book the train", "Pack a bag"]')
        self.assertEqual([cell.text for row in document.sections[0].rows for cell in row.cells],
                         ["Book the train", "Pack a bag"])
        self.assertEqual(viewer.plain(document, 80), "List\n\nItem\nBook the train\nPack a bag")
        empty = model.parse_document('[]')
        self.assertIn("(none)", viewer.plain(empty))
        self.assertEqual(len(empty.columns), 1)

    def test_simple_tables_infer_columns_and_keep_missing_cells_blank(self):
        value = {"title": "Travel", "rows": [
            {"Item": {"text": "Train", "href": "https://example.test/train"}, "State": "Booked"},
            {"Item": "Hotel", "Note": {"text": "Confirm dates", "tone": "warning"}}]}
        document = model.parse_document(json.dumps(value))
        self.assertEqual([column.label for column in document.columns], ["Item", "State", "Note"])
        self.assertTrue(all(column.wrap for column in document.columns))
        rows = document.sections[0].rows
        self.assertEqual(rows[0].cells[0].href, "https://example.test/train")
        self.assertEqual(rows[0].cells[2].text, "")
        self.assertEqual(rows[1].cells[1].text, "")
        self.assertEqual(rows[1].cells[2].tone, "warning")
        self.assertEqual(model.parse_document(json.dumps(value["rows"])).sections, document.sections)
        output = viewer.plain(document, 60)
        self.assertIn("Booked", output)
        self.assertNotIn("(2)", output)
        self.assertNotIn("https://", output)

    def test_simple_data_still_rejects_invalid_types_and_links(self):
        for value in ([42], ["item", {"Item": "other"}], [{}], {"rows": "text"},
                      {"rows": [], "extra": True}, {"title": "", "rows": []},
                      [{"Item": 42}], [{"Item": {"text": "Bad", "href": "file:///tmp/a"}}],
                      [{"": "Empty key"}], {"rows": [], "version": 2},
                      [{f"Column {i}": "value"} for i in range(33)]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                model.parse_document(json.dumps(value))

    def test_empty_state_is_optional_typed_and_incompatible_with_rows(self):
        value = sample()
        value["sections"] = []
        value["empty_state"] = {"text": "ACTIVITY ● CLEAR"}
        document = model.parse_document(json.dumps(value))
        self.assertEqual(document.empty_state, model.Cell("ACTIVITY ● CLEAR"))
        for state in (None, "clear", {}, {"text": 42}, {"text": " "},
                      {"text": "\x1b[0m"}, {"text": "Clear", "tone": "green"},
                      {"text": "Clear", "extra": True}):
            value["empty_state"] = state
            with self.subTest(state=state), self.assertRaises(ValueError):
                model.parse_document(json.dumps(value))
        value = sample()
        value["empty_state"] = {"text": "SOURCE ● OFFLINE", "tone": "error"}
        with self.assertRaisesRegex(ValueError, "no rows"):
            model.parse_document(json.dumps(value))

    def test_hrefs_accept_only_absolute_web_urls_without_controls(self):
        value = sample()
        cell = value["sections"][0]["rows"][0]["cells"]
        for target in ("https://example.test/item/1?view=full#details", "http://localhost:8080/one"):
            cell["id"] = {"text": "A1", "href": target}
            document = model.parse_document(json.dumps(value))
            self.assertEqual(document.sections[0].rows[0].cells[0].href, target)
        for target in (None, 42, "file:///tmp/file", "javascript:alert(1)", "//example.test/a",
                       "https://", "https://bad port/", "https://example.test:bad/a", "https://example.test/\n",
                       "https://example.test/\x1b]8;;other", "https://example.test/\u202e"):
            cell["id"] = {"text": "A1", "href": target}
            with self.subTest(target=target), self.assertRaises(ValueError):
                model.parse_document(json.dumps(value))

    def test_cell_tones_and_controls_are_data(self):
        value = sample()
        value["sections"][0]["rows"][0]["tone"] = "muted"
        value["sections"][0]["rows"][0]["cells"]["title"] = "\x1b[31mRed\x1b[0m\nnew\u202e\x00"
        document = model.parse_document(json.dumps(value))
        row = document.sections[0].rows[0]
        self.assertEqual(row.cells[0].tone, "muted")
        self.assertEqual(row.cells[1].text, "Red new")
        self.assertEqual(row.cells[2].tone, "warning")
        self.assertEqual(model.clip("界abc", 3), "界a")
        self.assertEqual(model.fit("界abc", 3), "界…")

    def test_invalid_documents_fail_at_boundary(self):
        mutations = [
            lambda value: value.update(version=True),
            lambda value: value.update(extra="unexpected"),
            lambda value: value.update(sections={}),
            lambda value: value["sections"][0].update(show_count=0),
            lambda value: value["sections"][0].update(show_count="false"),
            lambda value: value["columns"].append(value["columns"][0]),
            lambda value: value["columns"][0].update(width=-1),
            lambda value: value["columns"][0].update(flex=True),
            lambda value: value["columns"][0].update(wrap="true"),
            lambda value: value["columns"][0].update(align="middle"),
            lambda value: value["columns"][0].update(align=None),
            lambda value: value["sections"][0]["rows"][0]["cells"].pop("id"),
            lambda value: value["sections"][0]["rows"][0]["cells"].update(extra="x"),
            lambda value: value["sections"][0]["rows"][0]["cells"].update(id=42),
            lambda value: value["sections"][0]["rows"][0].update(tone="rainbow"),
        ]
        for mutate in mutations:
            value = sample()
            mutate(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                model.parse_document(json.dumps(value))
        for raw in ("null", "not json", "{}"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                model.parse_document(raw)


class RenderTests(unittest.TestCase):
    def test_centered_empty_state_has_no_chrome_and_clears_links_and_selection(self):
        value = sample()
        value["sections"] = []
        value["empty_state"] = {"text": "No active builds", "center": True, "tone": "muted"}
        document = model.parse_document(json.dumps(value))
        self.assertEqual(viewer.plain(document), "No active builds")
        for framed in (True, False):
            for height, width in ((12, 80), (25, 41), (1, 8), (2, 1)):
                with self.subTest(framed=framed, height=height, width=width):
                    screen, output, selection = Screen(height, width), io.StringIO(), viewer.LinkSelection()
                    viewer.draw(screen, watcher.Snapshot(model.parse_document(json.dumps(linked_list()))),
                                framed=framed, selection=selection, link_output=output)
                    selection.move(1)
                    output = io.StringIO()
                    viewer.draw(screen, watcher.Snapshot(document), framed=framed, selection=selection,
                                link_output=output, colors={"muted": 123})
                    label = model.clip("No active builds", width)
                    self.assertEqual(screen.writes, [((height - 1) // 2,
                        (width - model.text_width(label)) // 2, label, 123)])
                    self.assertIsNone(selection.href)
                    self.assertEqual(selection.targets, [])
                    self.assertEqual(output.getvalue(), "")
                    self.assertEqual(screen.clears, 2)

    def test_centered_empty_state_reports_stale_data_instead_of_false_empty(self):
        value = sample()
        value["sections"] = []
        value["empty_state"] = {"text": "No active builds", "center": True}
        document = model.parse_document(json.dumps(value))
        screen = Screen(12, 80)
        viewer.draw(screen, watcher.Snapshot(document, datetime(2026, 1, 1), "Cannot reach CI"))
        self.assertNotIn("No active builds", screen.text())
        self.assertIn("Data unavailable", screen.text())
        self.assertIn("STALE", screen.text())
        self.assertIn("Cannot reach CI", screen.text())
        viewer.draw(screen, watcher.Snapshot(document))
        self.assertEqual(screen.text(), "No active builds")
        value["empty_state"]["center"] = "yes"
        with self.assertRaisesRegex(ValueError, "center must be a boolean"):
            model.parse_document(json.dumps(value))

    def test_column_alignment_preserves_cell_width_and_link_offsets(self):
        for align, label, width, expected, x in (
                ("center", "•", 3, " • ", 1), ("center", "界", 4, " 界 ", 1),
                ("left", "•", 3, "•  ", 0), ("right", "•", 3, "  •", 2),
                ("center", "long label", 3, "lo…", 0)):
            with self.subTest(align=align, label=label):
                document = model.parse_document(json.dumps({"version": 1, "title": "Actions",
                    "columns": [{"key": "act", "label": "Act", "width": width, "align": align}],
                    "sections": [{"title": "Tasks", "rows": [{"cells": {
                        "act": {"text": label, "href": "https://example.test/action"}}}]}]}))
                _, lines = viewer.table_lines(document, 30)
                self.assertEqual(lines[1][0][0], expected)
                self.assertEqual(model.text_width(lines[1][0][0]), width)
                output = io.StringIO()
                viewer.draw(Screen(8, 30), watcher.Snapshot(document), framed=False, link_output=output)
                self.assertIn(f"\x1b[4;{x + 1}H", output.getvalue())
                self.assertIn(f"\x1b]8;;https://example.test/action\x1b\\{expected.strip()}\x1b]8;;",
                              output.getvalue())

    def test_empty_state_replaces_table_and_keeps_its_tone_even_in_small_panes(self):
        value = sample()
        for section in value["sections"]:
            section["rows"] = []
        for tone, label in (("error", "SOURCE ● OFFLINE"), ("warning", "ACTIVITY ● UNAVAILABLE"),
                            ("success", "ACTIVITY ● CLEAR"), ("muted", "ACTIVITY ● CLEAR")):
            value["empty_state"] = {"text": label, "tone": tone}
            document = model.parse_document(json.dumps(value))
            for framed in (True, False):
                with self.subTest(tone=tone, framed=framed):
                    screen = Screen(12, 80)
                    offset = viewer.draw(screen, watcher.Snapshot(document), 100000,
                                         framed=framed, colors={tone: 123})
                    self.assertEqual(offset, 0)
                    self.assertIn((2 if framed else 0, 2 if framed else 0, label, 123), screen.writes)
                    for unwanted in ("Agent ID", "Active", "Finished", "(none)", "No items"):
                        self.assertNotIn(unwanted, screen.text())
            screen = Screen(1, 12)
            viewer.draw(screen, watcher.Snapshot(document), framed=False, colors={tone: 123})
            self.assertEqual(screen.writes, [(0, 0, model.clip(label, 12), 123)])
            self.assertEqual(viewer.plain(document), f"Example list\n\n{label}\n\nSource note")

    def test_wrapped_columns_preserve_text_links_and_scroll_widths(self):
        value = sample()
        value["columns"][1]["wrap"] = True
        text = "Every word survives wrapping including 界 and averylongwordwithoutspaces"
        value["sections"][0]["rows"][0]["cells"]["title"] = {
            "text": text, "href": "https://example.test/details"}
        document = model.parse_document(json.dumps(value))
        for width in (38, 62):
            with self.subTest(width=width):
                _, lines = viewer.table_lines(document, width)
                fragments = [part.strip() for line in lines for part, _, href in line if href]
                self.assertEqual("".join(fragments).replace(" ", ""), text.replace(" ", ""))
                self.assertTrue(all(sum(model.text_width(part) for part, _, _ in line) <= width for line in lines))
                screen, output = Screen(8, width), io.StringIO()
                offset = viewer.draw(screen, watcher.Snapshot(document), 100000,
                                     framed=False, link_output=output)
                self.assertGreater(offset, 0)
                self.assertIn("Finished", screen.text())
        self.assertEqual(viewer.wrap_text("界界x", 2), ["界", "界", "x"])
        self.assertEqual(viewer.wrap_text("x", 0), [""])
        self.assertIsNone(viewer.move(ord("x"), 1, 5, (ord("x"),)))

    def test_child_labels_hang_wrapped_text_beneath_visible_label(self):
        text = "  ↳ A1 — Review evidence including 界 and averylongwordwithoutspaces"
        for width in (6, 12, 24):
            with self.subTest(width=width):
                lines = viewer.wrap_text(text, width)
                self.assertTrue(lines[0].startswith("  ↳ "))
                self.assertTrue(all(line.startswith("    ") for line in lines[1:]))
                self.assertTrue(all(model.text_width(line) <= width for line in lines))
                content = "".join(line[4:] for line in lines).replace(" ", "")
                self.assertEqual(content, text[4:].replace(" ", ""))
        for width in range(6):
            with self.subTest(narrow_width=width):
                self.assertTrue(all(model.text_width(line) <= width
                                    for line in viewer.wrap_text(text, width)))

    def test_herdr_layout_uses_host_frame_and_retains_status_and_links(self):
        value = sample()
        value["sections"][0]["rows"][0]["cells"]["id"] = {
            "text": "A1", "href": "https://example.test/item"}
        document = model.parse_document(json.dumps(value))
        snapshot = watcher.Snapshot(document, datetime(2026, 1, 1, 12, 34, 56))
        screen, output = Screen(12, 80), io.StringIO()
        viewer.draw(screen, snapshot, framed=False, link_output=output, colors={"link": 256})
        self.assertNotIn(document.title, screen.text())
        self.assertTrue(all(char not in screen.text() for char in "╭╮╰╯│"))
        self.assertIn((0, 0, "Agent ID", 0), screen.writes)
        self.assertIn((3, 0, "A1", 256 | curses.A_UNDERLINE), screen.writes)
        self.assertIn("\x1b[4;1H", output.getvalue())
        self.assertIn("https://example.test/item", output.getvalue())
        footer = "".join(text for y, _, text, _ in screen.writes if y == 11)
        self.assertIn("Updated 12:34:56", footer)
        self.assertIn("q/Ctrl-C close", footer)
        stale = watcher.Snapshot(document, snapshot.updated_at, "unavailable")
        viewer.draw(screen, stale, framed=False)
        self.assertIn("STALE · last good 12:34:56 · unavailable", screen.text())
        for height, width in ((1, 1), (4, 12), (10, 40)):
            viewer.draw(Screen(height, width), snapshot, framed=False)

    def test_link_overlay_clips_labels_and_never_links_padding_or_curses_escapes(self):
        value = sample()
        cells = value["sections"][0]["rows"][0]["cells"]
        cells["id"] = {"text": "A1", "href": "https://example.test/id"}
        cells["title"] = {"text": "Long title 界 beyond available space", "tone": "warning",
                          "href": "https://example.test/title"}
        document = model.parse_document(json.dumps(value))
        screen, output = Screen(14, 40), io.StringIO()
        viewer.draw(screen, watcher.Snapshot(document), link_output=output, colors={"link": 256, "warning": 512})
        overlay = output.getvalue()
        self.assertIn("\x1b[0;4;36m\x1b]8;;https://example.test/id\x1b\\A1" + viewer.LINK_END, overlay)
        self.assertIn("\x1b[0;4;33m\x1b]8;;https://example.test/title\x1b\\Long titl…" + viewer.LINK_END, overlay)
        self.assertIn((5, 2, "A1", 256 | curses.A_UNDERLINE), screen.writes)
        self.assertIn((5, 4, " " * 6, 0), screen.writes)
        self.assertIn((5, 12, "Long titl…", 512 | curses.A_UNDERLINE), screen.writes)
        self.assertTrue(overlay.startswith("\x1b7" + viewer.LINK_END))
        self.assertTrue(overlay.endswith(viewer.LINK_END + "\x1b8"))
        self.assertNotIn("\x1b", screen.text())
        self.assertNotIn("https://", screen.text())
        self.assertNotIn("https://", viewer.plain(document))
        self.assertNotIn("\x1b", viewer.plain(document))
        output.seek(0)
        output.truncate()
        cells["id"], cells["title"] = "A1", "Plain title"
        viewer.draw(screen, watcher.Snapshot(model.parse_document(json.dumps(value))), link_output=output)
        self.assertEqual(screen.clears, 2)
        self.assertEqual(output.getvalue(), "")

    def test_link_indentation_and_padding_stay_outside_styled_clickable_span(self):
        target = "https://example.test/task"
        for framed in (False, True):
            for tone in ("normal", "warning"):
                for label in ("https://example.test/task", "界 linked text"):
                    with self.subTest(framed=framed, tone=tone, label=label):
                        document = model.parse_document(json.dumps({"version": 1, "title": "Tasks",
                            "columns": [{"key": "task", "label": "Task", "flex": 1}],
                            "sections": [{"title": "Today", "rows": [{"cells": {"task": {
                                "text": "   " + label + "  ", "href": target, "tone": tone}}}]}]}))
                        screen, output = Screen(12, 60), io.StringIO()
                        colors = {"link": 256, "warning": 512}
                        viewer.draw(screen, watcher.Snapshot(document), framed=framed,
                                    colors=colors, link_output=output)
                        row, left, available = (5, 2, 56) if framed else (3, 0, 60)
                        base_color = colors.get(tone, 0)
                        link_color = colors["link" if tone == "normal" else tone] | curses.A_UNDERLINE
                        label_end = left + 3 + model.text_width(label)
                        self.assertIn((row, left, "   ", base_color), screen.writes)
                        self.assertIn((row, left + 3, label, link_color), screen.writes)
                        self.assertIn((row, label_end, " " * (left + available - label_end), base_color), screen.writes)
                        overlay = output.getvalue()
                        self.assertIn(f"\x1b[{row + 1};{left + 4}H", overlay)
                        self.assertIn(f"\x1b]8;;{target}\x1b\\{label}{viewer.LINK_END}", overlay)
                        self.assertNotIn(f"\x1b]8;;{target}\x1b\\ ", overlay)

    def test_whitespace_only_link_cell_has_no_underlining_or_click_target(self):
        value = sample()
        value["sections"][0]["rows"][0]["cells"]["id"] = {
            "text": "   ", "href": "https://example.test/blank"}
        screen, output = Screen(12, 60), io.StringIO()
        viewer.draw(screen, watcher.Snapshot(model.parse_document(json.dumps(value))),
                    framed=False, link_output=output)
        self.assertEqual(output.getvalue(), "")
        self.assertTrue(all(not color & curses.A_UNDERLINE for _, _, _, color in screen.writes))

    def test_link_underlines_preserve_semantic_tones(self):
        colors = {"link": 256, "muted": curses.A_DIM, "info": 256, "success": 512, "warning": 768, "error": 1024}
        styles = {"normal": "0;4;36", "muted": "0;4;2", "info": "0;4;36", "success": "0;4;32",
                  "warning": "0;4;33", "error": "0;4;31"}
        for tone, style in styles.items():
            with self.subTest(tone=tone):
                value = sample()
                value["sections"][0]["rows"][0]["cells"]["id"] = {
                    "text": "A1", "tone": tone, "href": "https://example.test/item"}
                document = model.parse_document(json.dumps(value))
                screen, output = Screen(14, 80), io.StringIO()
                viewer.draw(screen, watcher.Snapshot(document), colors=colors, link_output=output)
                color = colors["link" if tone == "normal" else tone]
                self.assertIn((5, 2, "A1", color | curses.A_UNDERLINE), screen.writes)
                self.assertIn(f"\x1b[{style}m\x1b]8;;https://example.test/item", output.getvalue())

    def test_section_counts_can_be_hidden_for_populated_and_empty_sections(self):
        value = sample()
        for section in value["sections"]:
            section["show_count"] = False
        document = model.parse_document(json.dumps(value))
        self.assertTrue(all(not section.show_count for section in document.sections))
        screen = Screen(20, 100)
        viewer.draw(screen, watcher.Snapshot(document))
        self.assertIn("Active", screen.text())
        self.assertIn("Finished", screen.text())
        self.assertNotIn("(1)", screen.text())
        self.assertNotIn("(0)", screen.text())
        self.assertIn("(none)", screen.text())
        self.assertNotIn("(1)", viewer.plain(document))
        self.assertNotIn("(0)", viewer.plain(document))

    def test_narrow_and_wide_frames_keep_bounds_and_shared_grid(self):
        document = model.parse_document(json.dumps(sample()))
        snapshot = watcher.Snapshot(document, datetime.now())
        for height, width in ((1, 1), (6, 10), (7, 12), (14, 30), (20, 80), (24, 180)):
            with self.subTest(size=(height, width)):
                screen = Screen(height, width)
                viewer.draw(screen, snapshot, 10000)
        header, body = viewer.table_lines(document, 100)
        self.assertEqual(sum(model.text_width(text) for text, _, _ in header), 100)
        self.assertEqual(sum(model.text_width(text) for text, _, _ in body[1]), 100)
        screen = Screen(20, 100)
        viewer.draw(screen, snapshot)
        self.assertIn("Active (1)", screen.text())
        self.assertIn("Finished (0)", screen.text())
        self.assertIn("(none)", screen.text())

    def test_polling_keeps_interval_label_and_footer_positions_unchanged(self):
        document = model.parse_document(json.dumps(sample()))
        shared_positions = set()
        for interval in (1, 9, 10, 99, 0.25, 100000, 1e-10):
            for framed in (False, True):
                with self.subTest(interval=interval, framed=framed):
                    phases = []
                    for second in (56, 57, 58):
                        snapshot = watcher.Snapshot(document, datetime(2026, 1, 1, 12, 34, second))
                        screen = Screen(12, 180)
                        viewer.draw(screen, snapshot, refresh=interval, framed=framed)
                        status = next(text for _, _, text, _ in screen.writes if text.startswith("Updated"))
                        self.assertIn(f"every {interval:g}s", status)
                        self.assertNotIn("refreshing", screen.text())
                        phases.append(status.replace(f"12:34:{second}", "12:34:56"))
                    self.assertEqual(len(set(phases)), 1)
                    if not framed:
                        self.assertEqual(len({text.index("q/Ctrl-C close") for text in phases}), 1)
                        self.assertEqual(len({text.index("Source note") for text in phases}), 1)
                        if interval in (1, 9, 10, 99):
                            shared_positions.update(text.index("q/Ctrl-C close") for text in phases)
        self.assertEqual(len(shared_positions), 1)  # Same slot across one- and two-digit intervals.

    def test_status_updates_do_not_clear_table_or_rewrite_its_hyperlinks(self):
        value = sample()
        value['sections'][0]['rows'][0]['cells']['id'] = {'text': 'A1', 'href': 'https://example.test/old'}
        document = model.parse_document(json.dumps(value))
        value['sections'][0]['rows'][0]['cells']['id']['href'] = 'https://example.test/new'
        changed = model.parse_document(json.dumps(value))
        now = datetime(2026, 1, 1, 12, 34, 56)
        snapshots = [watcher.Snapshot(document, now),
                     watcher.Snapshot(document, datetime(2026, 1, 1, 12, 34, 57)),
                     watcher.Snapshot(document, now, error='offline'),
                     watcher.Snapshot(document, now), watcher.Snapshot(changed, now)]

        class LiveScreen(Screen):
            def __init__(self):
                super().__init__(12, 180)
                self.frames = []
                self.keys = iter([-1, -1, -1, -1, ord('q')])

            def keypad(self, value):
                pass

            def timeout(self, value):
                pass

            def getch(self):
                return next(self.keys)

            def refresh(self):
                self.frames.append(self.writes[:])
                self.writes = []

        for select_links in (False, True):
            screen, output = LiveScreen(), io.StringIO()
            source = iter(snapshots)
            with self.subTest(select_links=select_links), \
                    patch.dict(os.environ, {'HERDR_PANE_ID': 'test-pane'}), \
                    patch.object(curses, 'raw'), patch.object(curses, 'curs_set'), \
                    patch.object(curses, 'has_colors', return_value=False), patch.object(sys, 'stdout', output):
                viewer.run_screen(screen, lambda: next(source), 10, select_links=select_links)
            self.assertEqual(screen.clears, 2)  # Initial table and actual link change only.
            for frame in screen.frames[1:4]:
                self.assertTrue(frame)
                self.assertEqual({y for y, _, _, _ in frame}, {11})
                self.assertEqual(model.text_width(frame[0][2]), 180)  # Clears a longer previous status.
            self.assertEqual(output.getvalue().count('https://example.test/old'), 1)
            self.assertEqual(output.getvalue().count('https://example.test/new'), 1)

    def test_stale_status_is_visible_with_last_good_time(self):
        snapshot = watcher.Snapshot(model.parse_document(json.dumps(sample())),
                                    datetime(2026, 1, 1, 12, 34, 56), "unavailable")
        screen = Screen(20, 100)
        viewer.draw(screen, snapshot)
        self.assertIn("STALE · last good 12:34:56 · unavailable", screen.text())
        self.assertIn("A1", screen.text())

    def test_controls_page_scroll_and_close(self):
        for key in (ord("q"), ord("Q"), 3):
            self.assertIsNone(viewer.move(key, 3, 10))
        self.assertEqual(viewer.move(ord("j"), 3, 10), 4)
        self.assertEqual(viewer.move(curses.KEY_UP, 0, 10), 0)
        self.assertEqual(viewer.move(curses.KEY_NPAGE, 3, 10), 13)
        self.assertEqual(viewer.move(curses.KEY_PPAGE, 3, 10), 0)
        self.assertEqual(viewer.move(curses.KEY_HOME, 3, 10), 0)
        self.assertEqual(viewer.move(27, 3, 10), 3)


class ProviderTests(unittest.TestCase):
    def test_file_watch_handles_edits_replacement_missing_and_invalid_data_without_processes(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(watcher.subprocess, "Popen") as popen:
            path = Path(directory) / "list with spaces.json"
            source = watcher.Watcher(file=path, refresh=0.01)
            source.start()
            try:
                eventually(lambda: source.snapshot().error is not None)
                self.assertIsNone(source.snapshot().document)
                path.write_text('["Original"]')
                eventually(lambda: source.snapshot().document is not None)
                original = source.snapshot().document
                path.write_text('{"rows":')
                eventually(lambda: source.snapshot().error is not None)
                self.assertEqual(source.snapshot().document, original)
                replacement = path.with_suffix(".new")
                replacement.write_text('[{"Item":"Replacement", "State":"Ready"}]')
                replacement.replace(path)
                eventually(lambda: source.snapshot().error is None)
                updated = source.snapshot().document
                self.assertEqual(updated.sections[0].rows[0].cells[0].text, "Replacement")
                path.unlink()
                eventually(lambda: source.snapshot().error is not None)
                self.assertEqual(source.snapshot().document, updated)
                path.write_text('[]')
                eventually(lambda: source.snapshot().error is None)
                self.assertEqual(source.snapshot().document.sections[0].rows, ())
                popen.assert_not_called()
            finally:
                source.close()

    def test_file_size_limit_and_nonregular_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "list.json"
            path.write_bytes(b"x" * 101)
            with patch.object(watcher, "MAX_OUTPUT", 100), self.assertRaisesRegex(ValueError, "size limit"):
                watcher.read_file(path)
            with self.assertRaisesRegex(ValueError, "regular list file"):
                watcher.read_file(directory)
            pipe = Path(directory) / "pipe"
            os.mkfifo(pipe)
            with self.assertRaisesRegex(ValueError, "regular list file"):
                watcher.read_file(pipe)

    def test_successful_empty_state_replaces_previous_rows_then_accepts_rows_again(self):
        populated = model.parse_document(json.dumps(sample()))
        value = sample()
        value["sections"] = []
        value["empty_state"] = {"text": "SOURCE ● OFFLINE", "tone": "error"}
        empty = model.parse_document(json.dumps(value))
        current = [populated]
        with patch.object(watcher, "fetch", lambda *_: current[0]):
            source = watcher.Watcher(["unused"], refresh=0.01)
            source.start()
            try:
                eventually(lambda: source.snapshot().document is populated)
                previous_time = source.snapshot().updated_at
                current[0] = empty
                eventually(lambda: source.snapshot().document is empty)
                self.assertGreater(source.snapshot().updated_at, previous_time)
                self.assertIsNone(source.snapshot().error)
                self.assertEqual(source.snapshot().document.sections, ())
                current[0] = populated
                eventually(lambda: source.snapshot().document is populated)
                self.assertIsNone(source.snapshot().error)
            finally:
                source.close()

    def test_provider_arguments_are_not_shell_commands(self):
        argument = "literal spaces ; $(exit 2) `whoami`"
        script = "import json,sys; v=json.loads(sys.argv[1]); v['title']=sys.argv[2]; print(json.dumps(v))"
        document = watcher.fetch([sys.executable, "-c", script, json.dumps(sample()), argument],
                                 2, threading.Event())
        self.assertEqual(document.title, argument)

    def test_provider_errors_and_output_limits(self):
        for script, message in [
            ("import sys; print('bad token',file=sys.stderr); sys.exit(7)", "exited 7: bad token"),
            ("print('not JSON')", "invalid JSON"),
            ("print('x'*1000)", "size limit")]:
            with self.subTest(script=script), patch.object(watcher, "MAX_OUTPUT", 100):
                with self.assertRaisesRegex((ValueError, RuntimeError), message):
                    watcher.fetch([sys.executable, "-c", script], 2, threading.Event())

    def test_timeout_kills_provider_and_child(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_path = Path(directory) / "child.pid"
            child_script = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"
            script = ("import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,'-c',sys.argv[2]]); "
                      "pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)")
            before = time.monotonic()
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                watcher.fetch([sys.executable, "-c", script, str(pid_path), child_script], 0.4, threading.Event())
            self.assertLess(time.monotonic() - before, 1.5)
            child_pid = int(pid_path.read_text())
            eventually(lambda: subprocess.run(["ps", "-p", str(child_pid), "-o", "stat="],
                        capture_output=True, text=True).stdout.strip() in ("", "Z", "Z+"))

    def test_stale_data_recovers_and_polling_never_overlaps(self):
        document = model.parse_document(json.dumps(sample()))
        recover = threading.Event()
        active = calls = peak = 0

        def provider(*_args):
            nonlocal active, calls, peak
            active += 1
            calls += 1
            peak = max(peak, active)
            time.sleep(0.025)
            active -= 1
            if calls > 1 and not recover.is_set():
                raise RuntimeError("offline")
            return document

        with patch.object(watcher, "fetch", provider):
            source = watcher.Watcher(["unused"], refresh=0.01)
            source.start()
            try:
                eventually(lambda: source.snapshot().error == "offline")
                self.assertIs(source.snapshot().document, document)
                original_time = source.snapshot().updated_at
                recover.set()
                eventually(lambda: source.snapshot().error is None)
                self.assertGreater(source.snapshot().updated_at, original_time)
                self.assertEqual(peak, 1)
            finally:
                source.close()

    def test_close_cancels_running_provider_promptly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provider.pid"
            script = "import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)"
            source = watcher.Watcher([sys.executable, "-c", script, str(path)], timeout=30)
            source.start()
            try:
                eventually(path.exists)
                pid = int(path.read_text())
                before = time.monotonic()
                source.close()
                self.assertLess(time.monotonic() - before, 1)
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
            finally:
                source.close()


class CommandTests(unittest.TestCase):
    def test_real_terminal_selects_first_or_last_pr_and_enter_opens_only_selection(self):
        for initial, number in ((b"j", 1), (b"k", 20)):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as directory:
                directory = Path(directory)
                value = linked_list()
                value["sections"] = [value["sections"][0], {"title": "PRs", "rows": [
                    {"cells": {"id": {"text": f"A{index}", "href": f"https://example.test/pr/{index}"},
                               "title": f"PR {index}", "status": "Ready"}}
                    for index in range(1, 21)]}]
                path = directory / "prs.json"
                path.write_text(json.dumps(value))
                opened = directory / "opened"
                opener = directory / "open"
                opener.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "$HTK_TEST_OPEN_LOG"\n')
                opener.chmod(0o755)
                master, slave = pty.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 12, 100, 0, 0))
                process = subprocess.Popen([str(ROOT / "bin/list-view"), "--select-links",
                    "--refresh", "0.1", "--file", str(path)],
                    stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                    env={**os.environ, "PATH": f"{directory}:{os.environ['PATH']}",
                         "HTK_TEST_OPEN_LOG": str(opened), "HERDR_PANE_ID": "test-pane",
                         "TERM": "xterm-256color", "PYTHONDONTWRITEBYTECODE": "1"})
                output = bytearray()

                def read_until(needle, start=0):
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        if needle in output[start:]:
                            return bytes(output[start:])
                        if select.select([master], [], [], 0.05)[0]:
                            output.extend(os.read(master, 65536))
                    self.fail(f"Missing {needle!r}: {output[start:]!r}")

                def selected(index):
                    return f"\x1b]8;;https://example.test/pr/{index}\x1b\\A{index}".encode()

                try:
                    frame = read_until(b"\x1b]8;;https://example.test/pr/1\x1b\\A1")
                    self.assertNotIn(b"\x1b[0;7;33m", frame)
                    start = len(output)
                    # Enter before selection is inert. J/K then selects an endpoint.
                    os.write(master, b"\r" + initial)
                    frame = read_until(selected(number), start)
                    self.assertIn(b"\x1b[33m[", frame)
                    self.assertNotIn(b"\x1b[0;7", frame)
                    self.assertFalse(opened.exists())
                    os.write(master, b"\r")
                    expected = f"https://example.test/pr/{number}\n"
                    eventually(lambda: opened.exists() and opened.read_text() == expected)
                    # Crossing either boundary selects and reveals the opposite endpoint.
                    start = len(output)
                    os.write(master, b"k" if initial == b"j" else b"j")
                    opposite = 20 if number == 1 else 1
                    read_until(selected(opposite), start)
                    os.write(master, b"\r")
                    expected += f"https://example.test/pr/{opposite}\n"
                    eventually(lambda: opened.read_text() == expected)
                    start = len(output)
                    os.write(master, initial)
                    read_until(selected(number), start)
                    # A data refresh reorders rows without changing the selected PR.
                    start = len(output)
                    value["sections"][1]["rows"].reverse()
                    path.write_text(json.dumps(value))
                    read_until(selected(number), start)
                    start = len(output)
                    os.write(master, b"k" if initial == b"j" else b"j")
                    neighbor = 2 if number == 1 else 19
                    read_until(selected(neighbor), start)
                    os.write(master, b"\r")
                    expected += f"https://example.test/pr/{neighbor}\n"
                    eventually(lambda: opened.read_text() == expected)
                    start = len(output)
                    value["sections"] = []
                    value["empty_state"] = {"text": "NO PRS"}
                    path.write_text(json.dumps(value))
                    read_until(b"NO PRS", start)
                    os.write(master, b"j\rk\r\x03")
                    deadline = time.monotonic() + 3
                    while process.poll() is None and time.monotonic() < deadline:
                        if select.select([master], [], [], 0.05)[0]:
                            output.extend(os.read(master, 65536))
                    self.assertEqual(process.returncode, 0, output)
                    self.assertEqual(opened.read_text(), expected)
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=2)
                    os.close(master)
                    os.close(slave)

    def test_real_terminal_link_targets_refresh_scroll_resize_and_disappear(self):
        wrapper = """
import fcntl, os, subprocess, sys, termios
os.setsid()
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
before = termios.tcgetattr(0)
child = subprocess.Popen(sys.argv[1:])
code = child.wait()
after = termios.tcgetattr(0)
before[3] &= ~getattr(termios, 'PENDIN', 0)
after[3] &= ~getattr(termios, 'PENDIN', 0)
sys.exit(code if before == after else 125)
"""
        with tempfile.TemporaryDirectory() as directory:
            value = sample()
            value["sections"][0]["rows"] = [{"cells": {
                "id": {"text": f"   A{index}", "href": f"https://example.test/item/{index}"},
                "title": f"Item {index}", "status": "Ready"}} for index in range(1, 21)]
            path = Path(directory) / "list.json"
            path.write_text(json.dumps(value))
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 14, 80, 0, 0))
            process = subprocess.Popen([sys.executable, "-c", wrapper, str(ROOT / "bin/list-view"),
                "--refresh", "0.2", "--file", str(path)],
                stdin=slave, stdout=slave, stderr=slave,
                env={**os.environ, "HERDR_PANE_ID": "", "TERM": "xterm-256color", "PYTHONDONTWRITEBYTECODE": "1"})
            output = bytearray()

            def read_until(needle, start=0, complete_overlay=True):
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    location = output.find(needle, start)
                    if location >= 0 and (not complete_overlay or b"\x1b8" in output[location:]):
                        clear = output.rfind(b"\x1b[2J", start, location)
                        self.assertGreaterEqual(clear, start, output[start:])
                        return bytes(output[clear:])
                    if select.select([master], [], [], 0.05)[0]:
                        output.extend(os.read(master, 65536))
                self.fail(f"Missing {needle!r}: {output[start:]!r}")

            try:
                first = b"\x1b]8;;https://example.test/item/1\x1b\\A1\x1b]8;;\x1b\\"
                frame = read_until(first)
                self.assertIn(b"\x1b[0;4;36m" + first, frame)
                self.assertIn(b"\x1b[6;11H\x1b[0;4;36m" + first, frame)
                self.assertNotIn("https://", model.clean(frame.decode("utf-8")))
                # Successful polls with identical data update only the clock/status,
                # leaving the table and its terminal links physically undisturbed.
                start = len(output)
                deadline = time.monotonic() + 2
                while len(output) == start and time.monotonic() < deadline:
                    if select.select([master], [], [], 0.05)[0]:
                        output.extend(os.read(master, 65536))
                self.assertGreater(len(output), start)
                self.assertNotIn(b"\x1b[2J", output[start:])
                self.assertNotIn(b"\x1b]8;;https", output[start:])
                start = len(output)
                value["sections"][0]["rows"][0]["cells"]["id"]["href"] = "https://example.test/revised"
                path.write_text(json.dumps(value))
                revised = b"\x1b]8;;https://example.test/revised\x1b\\A1\x1b]8;;\x1b\\"
                frame = read_until(revised, start)
                self.assertNotIn(first, frame)
                start = len(output)
                os.write(master, b"G")
                last = b"\x1b]8;;https://example.test/item/20\x1b\\A20\x1b]8;;\x1b\\"
                frame = read_until(last, start)
                self.assertNotIn(revised, frame)
                start = len(output)
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 14, 40, 0, 0))
                os.killpg(process.pid, signal.SIGWINCH)
                frame = read_until(last, start)
                for row, column in re.findall(rb"\x1b\[(\d+);(\d+)H\x1b\[[0-9;]*m\x1b\]8;;https", frame):
                    self.assertLessEqual(int(row), 14)
                    self.assertLessEqual(int(column), 40)
                start = len(output)
                value["title"] = "UNLINKED_NOW"
                for row in value["sections"][0]["rows"]:
                    row["cells"]["id"] = row["cells"]["id"]["text"]
                path.write_text(json.dumps(value))
                frame = read_until(b"UNLINKED_NOW", start, complete_overlay=False)
                self.assertNotIn(b"\x1b]8;;https", frame)
                start = len(output)
                value["sections"] = []
                value["empty_state"] = {"text": "SOURCE ● OFFLINE", "tone": "error"}
                path.write_text(json.dumps(value))
                frame = read_until("SOURCE ● OFFLINE".encode(), start, complete_overlay=False)
                for unwanted in (b"Agent ID", b"Active", b"Finished", b"(none)", b"\x1b]8;;https"):
                    self.assertNotIn(unwanted, frame)
                start = len(output)
                value.pop("empty_state")
                value["sections"] = [{"title": "REPOPULATED", "rows": sample()["sections"][0]["rows"]}]
                path.write_text(json.dumps(value))
                frame = read_until(b"REPOPULATED", start, complete_overlay=False)
                self.assertNotIn(b"OFFLINE", frame)
                start = len(output)
                path.write_text(json.dumps({"title": "Travel", "rows": [
                    {"Item": {"text": "Book train", "href": "https://example.test/train"},
                     "State": "Ready"}]}))
                frame = read_until(b"\x1b]8;;https://example.test/train\x1b\\Book train", start)
                self.assertIn(b"Travel", frame)
                self.assertIn(b"Ready", frame)
                self.assertNotIn(b"REPOPULATED", frame)
                os.write(master, b"\x03")
                deadline = time.monotonic() + 2
                while process.poll() is None and time.monotonic() < deadline:
                    if select.select([master], [], [], 0.05)[0]:
                        output.extend(os.read(master, 65536))
                self.assertEqual(process.poll(), 0, output)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)
                os.close(master)
                os.close(slave)

    def test_direct_data_and_provider_once_through_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / "list-view"
            link.symlink_to(ROOT / "bin/list-view")
            result = subprocess.run([str(link), "--once", "--data", json.dumps(sample())],
                                    cwd=directory, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("A1", result.stdout)
        self.assertIn("Needs action", result.stdout)
        self.assertNotIn("\x1b", result.stdout)
        result = subprocess.run([str(ROOT / "bin/list-view"), "--once", "--", sys.executable,
                                "-c", "import sys; print(sys.argv[1])", json.dumps(sample())],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Finished (0)", result.stdout)
        value = sample()
        value["sections"] = []
        value["empty_state"] = {"text": "ACTIVITY ● CLEAR", "tone": "success"}
        result = subprocess.run([str(ROOT / "bin/list-view"), "--once", "--data", json.dumps(value)],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Example list\n\nACTIVITY ● CLEAR\n\nSource note\n")

    def test_missing_conflicting_and_invalid_sources(self):
        for args in ([], ["--data", "{}"], ["--refresh", "nan", "--data", "{}"],
                     ["--file", "/nonexistent-list-view-test.json"],
                     ["--file", "file.json", "--data", "[]"],
                     ["--file", "file.json", "--", "echo", "[]"],
                     ["--data", json.dumps(sample()), "--", "echo", "ignored"]):
            with self.subTest(args=args):
                result = subprocess.run([str(ROOT / "bin/list-view"), "--once", *args],
                                        capture_output=True, text=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)

    def test_simple_data_and_file_once_match_without_a_provider_script(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "travel plans.json"
            data = '{"title":"Travel", "rows":[{"Item":"Train", "State":"Booked"}, {"Item":"Hotel"}]}'
            path.write_text(data)
            outputs = []
            for args in (["--file", str(path)], ["--data", data],
                         ["--", sys.executable, "-c", "import sys; print(sys.argv[1])", data]):
                result = subprocess.run([str(ROOT / "bin/list-view"), "--once", *args],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                outputs.append(result.stdout)
            self.assertEqual(len(set(outputs)), 1)
            self.assertIn("Travel", outputs[0])
            self.assertIn("Booked", outputs[0])
            self.assertIn("Hotel", outputs[0])
            self.assertNotIn("(2)", outputs[0])

    def test_real_terminal_escape_stays_open_and_q_or_ctrl_c_restore_terminal(self):
        wrapper = """
import fcntl, os, signal, subprocess, sys, termios
os.setsid()
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
before = termios.tcgetattr(0)
signal.signal(signal.SIGINT, lambda *_: None)
child = subprocess.Popen(sys.argv[1:])
code = child.wait()
after = termios.tcgetattr(0)
before[3] &= ~getattr(termios, 'PENDIN', 0)
after[3] &= ~getattr(termios, 'PENDIN', 0)
sys.exit(code if before == after else 125)
"""
        for key, hosted in ((b"q", False), (b"Q", False), (b"\x03", False), (b"q", True)):
            with self.subTest(key=key, hosted=hosted), tempfile.TemporaryDirectory() as directory:
                master, slave = pty.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 14, 80, 0, 0))
                path = Path(directory) / "provider.pid"
                script = ("import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
                          "time.sleep(60)")
                large_list = sample()
                large_list["sections"][0]["rows"].extend({"cells": {
                    "id": f"A{index}", "title": "LAST_SCROLL_ROW" if index == 20 else f"ITEM_{index:02}",
                    "status": "Ready"}} for index in range(2, 21))
                source = (["--data", json.dumps(large_list)] if key != b"\x03" else
                          ["--", sys.executable, "-c", script, str(path)])
                process = subprocess.Popen([sys.executable, "-c", wrapper, str(ROOT / "bin/list-view"),
                    *source], stdin=slave, stdout=slave, stderr=slave,
                    env={**os.environ, "HERDR_PANE_ID": "test:pane" if hosted else "",
                         "TERM": "xterm-256color", "PYTHONDONTWRITEBYTECODE": "1"})
                output = bytearray()
                try:
                    deadline = time.monotonic() + 5
                    needles = ([b"Active (20)", b"A1", b"Needs action"] + ([] if hosted else [b"Example list"])
                               if key != b"\x03" else [b"Loading"])
                    while time.monotonic() < deadline and (any(needle not in output for needle in needles)
                            or (key == b"\x03" and not path.exists())):
                        if select.select([master], [], [], 0.05)[0]:
                            output.extend(os.read(master, 65536))
                    for needle in needles:
                        self.assertIn(needle, output)
                    if hosted:
                        self.assertNotIn(b"Example list", output)
                        self.assertNotIn("╭".encode(), output)
                    os.write(master, b"\x1b")
                    time.sleep(0.15)
                    self.assertIsNone(process.poll(), output)
                    if key != b"\x03":
                        self.assertNotIn(b"LAST_SCROLL_ROW", output)
                        for scroll_key, expected in ((b"G", b"LAST_SCROLL_ROW"), (b"g", b"A long title")):
                            start = len(output)
                            os.write(master, scroll_key)
                            deadline = time.monotonic() + 2
                            while expected not in output[start:] and time.monotonic() < deadline:
                                if select.select([master], [], [], 0.05)[0]:
                                    output.extend(os.read(master, 65536))
                            self.assertIn(expected, output[start:])
                    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 10, 40, 0, 0))
                    os.killpg(process.pid, signal.SIGWINCH)
                    os.write(master, b"jk\x1b[B\x1b[A")
                    os.write(master, key)
                    deadline = time.monotonic() + 2
                    while process.poll() is None and time.monotonic() < deadline:
                        if select.select([master], [], [], 0.05)[0]:
                            output.extend(os.read(master, 65536))
                    self.assertEqual(process.poll(), 0, output)
                    if path.exists():
                        with self.assertRaises(ProcessLookupError):
                            os.kill(int(path.read_text()), 0)
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=2)
                    os.close(master)
                    os.close(slave)


if __name__ == "__main__":
    unittest.main()
