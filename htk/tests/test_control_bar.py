"""Shared control bars keep state, focus, layout and hit regions consistent."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec/list_view"))
from control_bar import BINDING_WIDTH, MIN_CELL_WIDTH, Control, layout
from list_model import text_width
sys.path.pop(0)


class ControlBarTests(unittest.TestCase):
    def test_only_toggles_have_indicators_and_states_are_independent(self):
        for enabled in (False, True):
            for active in (False, True):
                with self.subTest(enabled=enabled, active=active):
                    controls = [Control("run", "Run", "⌘Enter", active=active, enabled=enabled),
                                Control("vim", "Vim", "⌘I", kind="toggle", active=active, enabled=enabled)]
                    bar = layout(controls, 90, focused="vim")
                    dots = [span for span in bar.spans if span.text == "●"]
                    self.assertEqual(len(dots), 1)
                    self.assertEqual(dots[0].tone, "success" if active else "muted")
                    run = next(span for span in bar.spans if span.text == "Run")
                    vim = next(span for span in bar.spans if span.text == "Vim")
                    self.assertEqual(run.tone, "normal" if enabled else "disabled")
                    self.assertEqual(vim.tone, "disabled" if not enabled else "normal" if active else "muted")
                    self.assertEqual([target.action for target in bar.targets], ["run", "vim"] if enabled else [])
                    self.assertEqual([span.text for span in bar.spans if span.tone == "focus"], ["[", "]"])

    def test_grid_fills_available_columns_without_stretching_partial_rows(self):
        controls = [Control(str(i), f"Item {i}", str(i)) for i in range(9)]
        bar = layout(controls, 101)
        self.assertEqual(bar.height, 3)
        self.assertEqual([(cell.x, cell.y, cell.width) for cell in bar.cells],
                         [(0, 0, 25), (25, 0, 25), (50, 0, 25), (75, 0, 26),
                          (0, 1, 25), (25, 1, 25), (50, 1, 25), (75, 1, 26), (0, 2, 25)])
        self.assertEqual([cell.control for cell in bar.cells], controls)
        self.assertEqual([(target.x, target.y, target.width) for target in bar.targets],
                         [(cell.x, cell.y, cell.width) for cell in bar.cells])
        self.assertEqual(layout(controls[:5], 101).cells, bar.cells[:5])
        wide = layout(controls, 9 * MIN_CELL_WIDTH)
        self.assertEqual(wide.height, 1)
        self.assertEqual(len(wide.cells), 9)
        self.assertEqual(wide.cells[-1].x + wide.cells[-1].width, 9 * MIN_CELL_WIDTH)
        wider = layout(controls, 360)
        self.assertEqual(wider.height, 1)
        self.assertEqual(wider.cells[-1].x + wider.cells[-1].width, 360)

    def test_binding_and_label_columns_align_across_rows_and_toggle_states(self):
        controls = [Control("run", "Run", "R"), Control("vim", "Vim", "⌘I", kind="toggle"),
                    Control("other", "Other", "⌥⌘K"), Control("open", "Open", "⌘O"),
                    Control("switch", "Switch", "⇧⌥⌘Enter", kind="toggle", active=True),
                    Control("next", "Next", ""), Control("long", "Long", "LongBinding"),
                    Control("wide", "界😀", "界😀XYZAB")]
        bar = layout(controls, 120)
        for cell in bar.cells:
            spans = [span for span in bar.spans if span.y == cell.y and cell.x <= span.x < cell.x + cell.width]
            label = next(span for span in spans if span.text == cell.control.label)
            label_x = cell.x + 1 + BINDING_WIDTH + 1
            if cell.control.kind == "toggle":
                self.assertEqual(next(span.x for span in spans if span.text == "●"), label_x)
                label_x += 2
            self.assertEqual(label.x, label_x)
            if cell.control.binding:
                binding = next(span for span in spans if span is not label and span.text != "●")
                self.assertEqual(binding.x + text_width(binding.text), cell.x + 1 + BINDING_WIDTH)
                self.assertLessEqual(text_width(binding.text), BINDING_WIDTH)
        self.assertIn("LongBind", [span.text for span in bar.spans])
        self.assertIn("⇧⌥⌘ Ente", [span.text for span in bar.spans])
        self.assertEqual(len([span for span in bar.spans if span.text == "●"]), 2)

    def test_row_groups_reflow_and_snap_back_to_the_same_columns_on_resize(self):
        controls = [Control("quit", "Quit", "⌃D"),
                    *[Control(str(i), f"Item {i}", str(i), starts_row=i == 0) for i in range(8)]]
        for width in (84, 83, 42, 21, 105, 126, 147, 168, 336, 83, 84):
            with self.subTest(width=width):
                columns = min(8, max(1, width // MIN_CELL_WIDTH))
                bar = layout(controls, width, focused="0")
                self.assertEqual(bar.height, 1 + (8 + columns - 1) // columns)
                self.assertEqual([(cell.x, cell.y) for cell in bar.cells[:2]], [(0, 0), (0, 1)])
                self.assertEqual(sum(cell.y == 0 for cell in bar.cells), 1)
                for index, cell in enumerate(bar.cells[1:]):
                    row, column = divmod(index, columns)
                    self.assertEqual((cell.x, cell.y), (column * width // columns, row + 1))
                self.assertEqual(bar.cells, layout(controls, width).cells)
        first = layout([Control("first", "First", starts_row=True)], 84)
        self.assertEqual(first.height, 1)

    def test_narrow_bars_use_fewer_columns_and_labels_stay_inside_their_cell(self):
        controls = [Control(str(i), "Long control label", "⌘Enter") for i in range(8)]
        for columns in range(1, 9):
            width = columns * MIN_CELL_WIDTH
            bar = layout(controls, width)
            self.assertEqual(sum(cell.y == 0 for cell in bar.cells), columns)
            self.assertEqual(bar.height, (len(controls) + columns - 1) // columns)
            self.assertTrue(any(span.text.endswith("…") for span in bar.spans))
            for cell in bar.cells:
                for span in bar.spans:
                    if span.y == cell.y and cell.x <= span.x < cell.x + cell.width:
                        self.assertLessEqual(span.x + text_width(span.text), cell.x + cell.width - 1)

    def test_focus_gutters_do_not_move_content_or_click_targets(self):
        controls = [Control("run", "Run", "R"), Control("vim", "Vim", "I", kind="toggle")]
        initial = layout(controls, 30)
        for focused in ("run", "vim"):
            bar = layout(controls, 30, focused=focused)
            self.assertEqual(bar.height, initial.height)
            self.assertEqual(bar.targets, initial.targets)
            self.assertEqual(tuple(span for span in bar.spans if span.tone != "focus"), initial.spans)
            target = next(target for target in bar.targets if target.action == focused)
            brackets = [span for span in bar.spans if span.tone == "focus"]
            self.assertEqual([(span.x, span.y) for span in brackets],
                             [(target.x, target.y), (target.x + target.width - 1, target.y)])

    def test_narrow_widths_wide_unicode_and_targets_stay_in_bounds(self):
        controls = [Control("open", "Open 界😀", "⌘O"),
                    Control("vim", "Vim", "⌘I", kind="toggle", active=True)]
        for width in range(1, 60):
            for focused in (None, "open", "vim"):
                bar = layout(controls, width, focused=focused)
                occupied = set()
                painted = set()
                for span in bar.spans:
                    self.assertGreaterEqual(span.x, 0)
                    self.assertLessEqual(span.x + text_width(span.text), width)
                    self.assertLess(span.y, bar.height)
                    cells = {(span.y, x) for x in range(span.x, span.x + text_width(span.text))}
                    self.assertFalse(painted & cells)
                    painted |= cells
                for target in bar.targets:
                    self.assertGreater(target.width, 0)
                    self.assertLessEqual(target.x + target.width, width)
                    cells = {(target.y, x) for x in range(target.x, target.x + target.width)}
                    self.assertFalse(occupied & cells)
                    occupied |= cells
        for width in (0, -1):
            self.assertEqual(layout(controls, width).height, 0)
            self.assertEqual(layout(controls, width).targets, ())
        self.assertEqual(layout([], 80).height, 0)

    def test_content_is_plain_text_and_kind_is_explicit(self):
        bar = layout([Control("open", "\x1b[4mOpen\x1b[0m\nViewer", "⌘O")], 80)
        self.assertEqual([span.text for span in bar.spans], ["⌘ O", "Open Viewer"])
        self.assertNotIn("│", str(bar.spans))
        self.assertNotIn("\x1b", str(bar.spans))
        with self.assertRaises(ValueError):
            Control("open", "Open", kind="link")
        with self.assertRaisesRegex(ValueError, "three modifier"):
            Control("open", "Open", "⌃⌥⇧⌘O")
