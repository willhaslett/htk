"""Destination ordering, selection, and session-preserving pane moves."""

import copy
import curses
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location('pane_to_tab', Path(__file__).resolve().parents[1] / 'libexec/herdr_controls/pane_to_tab.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

SOURCE = {'pane_id': 'w1:p3', 'terminal_id': 'original-terminal',
          'workspace_id': 'w1', 'tab_id': 'w1:t8', 'label': 'SOURCE'}
TABS = [
    {'tab_id': 'w1:t8', 'workspace_id': 'w1', 'label': 'SOURCE', 'number': 8},
    {'tab_id': 'w1:t9', 'workspace_id': 'w1', 'label': 'SAME NAME', 'number': 9},
    {'tab_id': 'w1:t2', 'workspace_id': 'w1', 'label': 'SAME NAME', 'number': 2},
]


def snapshot():
    return {'panes': [dict(SOURCE)], 'tabs': copy.deepcopy(TABS),
            'layouts': [{'tab_id': tab['tab_id'], 'zoomed': False} for tab in TABS]}


class Screen:
    def __init__(self, keys, height=20):
        self.keys = iter(keys)
        self.height = height
        self.frames = []

    def keypad(self, _value):
        pass

    def erase(self):
        self.frames.append([])

    def getmaxyx(self):
        return self.height, 76

    def addnstr(self, y, x, text, width, style):
        self.frames[-1].append((y, text[:width]))

    def refresh(self):
        pass

    def get_wch(self):
        return next(self.keys)


class PickerTests(unittest.TestCase):
    def choose(self, keys, tabs=None, height=20):
        screen = Screen(keys, height)
        with patch.object(curses, 'curs_set'), patch.object(curses, 'has_colors', return_value=False):
            result = helper.picker(screen, tabs or TABS, SOURCE)
        return result, screen

    def test_display_order_is_not_sorted_by_number_and_other_workspaces_are_excluded(self):
        data = snapshot()
        data['tabs'].insert(1, {'tab_id': 'w2:t1', 'workspace_id': 'w2', 'label': 'OTHER'})
        _, tabs = helper.context(data, SOURCE['pane_id'])
        self.assertEqual(tabs, TABS)

    def test_duplicate_names_are_numbered_and_select_distinct_tab_ids(self):
        result, screen = self.choose(['3', '\n'])
        self.assertEqual(result, 'w1:t2')
        text = '\n'.join(text for _, text in screen.frames[0])
        self.assertIn('1  SOURCE  (CURRENT)', text)
        self.assertIn('2  SAME NAME', text)
        self.assertIn('3  SAME NAME', text)

    def test_current_tab_and_invalid_numbers_do_not_submit(self):
        result, screen = self.choose(['1', '\n', '\b', '0', '\n', '\b', '2', '\n'])
        self.assertEqual(result, 'w1:t9')
        text = str(screen.frames)
        self.assertIn('Choose another tab.', text)
        self.assertIn('Choose a number from 1 to 3.', text)

    def test_escape_and_control_c_cancel(self):
        for key in ('\x1b', '\x03'):
            self.assertIsNone(self.choose([key])[0])

    def test_arrows_vim_keys_and_resize(self):
        self.assertEqual(self.choose([curses.KEY_RESIZE, 'j', '\n'])[0], 'w1:t2')
        self.assertEqual(self.choose([curses.KEY_DOWN, curses.KEY_UP, '\n'])[0], 'w1:t9')

    def test_multidigit_selection_scrolls_in_small_popup(self):
        tabs = TABS + [{'tab_id': f'w1:t{i}', 'workspace_id': 'w1', 'label': f'TAB {i}'}
                       for i in range(20, 40)]
        result, screen = self.choose(['1', '2', '\n'], tabs, height=10)
        self.assertEqual(result, tabs[11]['tab_id'])
        self.assertTrue(any('12  ' in text for _, text in screen.frames[-1]))


class MoveTests(unittest.TestCase):
    def test_new_tab_extraction_keeps_identity_and_workspace(self):
        call = Mock(side_effect=[{'snapshot': snapshot()}, {'move_result': {}}])
        helper.move_to_tab(call, SOURCE, focus=False, new_label='WORKER')
        self.assertEqual(call.call_args.args,
                         ('pane', 'move', 'w1:p3', '--new-tab', '--workspace', 'w1',
                          '--label', 'WORKER', '--no-focus'))
        self.assertEqual(call.call_count, 2)

    def test_move_targets_original_pane_and_exact_destination_without_restart(self):
        call = Mock(side_effect=[{'snapshot': snapshot()}, {'move_result': {}}])
        helper.move_to_tab(call, SOURCE, 'w1:t2')
        self.assertEqual(call.call_args.args, ('pane', 'move', 'w1:p3', '--tab', 'w1:t2',
                                              '--split', 'right', '--focus'))
        self.assertEqual(call.call_count, 2)

    def test_no_focus_is_preserved(self):
        call = Mock(side_effect=[{'snapshot': snapshot()}, {}])
        helper.move_to_tab(call, SOURCE, 'w1:t2', focus=False)
        self.assertEqual(call.call_args.args[-1], '--no-focus')

    def test_source_movement_or_replacement_stops_before_mutation(self):
        for key, replacement in (('terminal_id', 'new-terminal'), ('tab_id', 'w1:t2'),
                                 ('workspace_id', 'w2')):
            data = snapshot()
            data['panes'][0][key] = replacement
            call = Mock(return_value={'snapshot': data})
            with self.subTest(key=key), self.assertRaises(ValueError):
                helper.move_to_tab(call, SOURCE, 'w1:t9')
            self.assertEqual(call.call_count, 1)

    def test_closed_destination_or_source_stops_before_mutation(self):
        for missing in ('source', 'destination'):
            data = snapshot()
            if missing == 'source':
                data['panes'] = []
            else:
                data['tabs'].pop()
            call = Mock(return_value={'snapshot': data})
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                helper.move_to_tab(call, SOURCE, 'w1:t2')
            self.assertEqual(call.call_count, 1)

    def test_zoomed_source_or_destination_is_reported(self):
        for index in (0, 2):
            data = snapshot()
            data['layouts'][index]['zoomed'] = True
            call = Mock(return_value={'snapshot': data})
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, 'Unzoom'):
                helper.move_to_tab(call, SOURCE, 'w1:t2')
            self.assertEqual(call.call_count, 1)

    def test_uncertain_move_is_never_retried(self):
        call = Mock(side_effect=[{'snapshot': snapshot()}, RuntimeError('connection lost')])
        with self.assertRaisesRegex(RuntimeError, 'connection lost'):
            helper.move_to_tab(call, SOURCE, 'w1:t2')
        self.assertEqual(call.call_count, 2)

    def test_current_tab_is_noop(self):
        call = Mock(return_value={'snapshot': snapshot()})
        helper.move_to_tab(call, SOURCE, SOURCE['tab_id'])
        self.assertEqual(call.call_count, 1)


if __name__ == '__main__':
    unittest.main()
