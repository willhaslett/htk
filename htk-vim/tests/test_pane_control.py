"""Safety checks for directional pane controls and confirmation."""

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('pane_control', Path(__file__).resolve().parents[1] / 'libexec/herdr_controls/pane.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def pane(pane_id, x=0, y=0, width=80, height=24):
    return {'pane_id': pane_id, 'rect': {'x': x, 'y': y, 'width': width, 'height': height}}


class PaneControlTests(unittest.TestCase):
    def setUp(self):
        self.source = {'pane_id': 'w1:p2', 'terminal_id': 'stable-terminal', 'workspace_id': 'w1',
                       'tab_id': 'w1:t1', 'label': 'NAMED SESSION', 'agent': 'codex',
                       'agent_session': {'value': 'original-session'}, 'agent_status': 'working'}

    def test_stacked_pair_can_be_reoriented_but_row_boundary_is_a_noop(self):
        stack = {'panes': [pane('a', y=0), pane('b', y=24)]}
        self.assertEqual(helper.perpendicular_target(stack, 'b', 'right'), 'a')
        self.assertEqual(helper.perpendicular_target(stack, 'a', 'left'), 'b')
        row = {'panes': [pane('a', x=0), pane('b', x=80)]}
        self.assertIsNone(helper.perpendicular_target(row, 'b', 'right'))
        self.assertEqual(helper.perpendicular_target(row, 'b', 'up'), 'a')

    def test_single_pane_move_makes_no_mutations(self):
        call = Mock(return_value={'neighbor': {'layout': {'panes': [pane(self.source['pane_id'])]}}})
        self.assertFalse(helper.move(call, self.source, 'left', True)['changed'])
        self.assertEqual(call.call_count, 1)

    def test_target_selected_by_requested_direction_takes_priority(self):
        call = Mock(return_value={'neighbor': {'neighbor_pane_id': 'directional-target', 'layout': {}}})
        with patch.object(helper, 'place', return_value=self.source) as place:
            helper.move(call, self.source, 'up', True)
        self.assertEqual(place.call_args.args[2:4], ('directional-target', 'up'))

    def test_failed_move_never_closes_or_retries_terminal(self):
        call = Mock(side_effect=[{'move_result': {'changed': True, 'pane': self.source}},
                                TimeoutError('response lost')])
        with self.assertRaisesRegex(RuntimeError, 'remains alive'):
            helper.place(call, self.source, 'w1:p1', 'left', True)
        self.assertEqual([item.args[0] for item in call.call_args_list], ['pane.move', 'pane.move'])

    def test_creation_inherits_foreground_cwd_and_names_shell(self):
        source = dict(self.source, cwd='/old', foreground_cwd='/actual')
        call = Mock(return_value={'pane': {'pane_id': 'w1:p3'}})
        helper.create(call, source, 'right', False)
        self.assertEqual(call.call_args_list[0].args[1]['cwd'], '/actual')
        self.assertEqual(call.call_args_list[1].args, ('pane.rename', {'pane_id': 'w1:p3', 'label': 'SHELL'}))

    def test_empty_and_partial_confirmation_cannot_close(self):
        shell = dict(self.source, agent=None, agent_session=None)
        for answer in ['', 'y', 'yes', 'close', '\x1b']:
            with self.subTest(answer=answer), patch('builtins.input', return_value=answer), patch('builtins.print'):
                call = Mock()
                self.assertFalse(helper.close(call, shell)['changed'])
                call.assert_not_called()

    def test_agent_session_is_blocked_for_every_status_without_override(self):
        for status in ('working', 'idle', 'blocked', 'done', 'unknown', None):
            with self.subTest(status=status), patch('builtins.input', return_value='CLOSE'), patch('builtins.print'):
                call = Mock()
                result = helper.close(call, dict(self.source, agent_status=status))
                self.assertEqual(result, {'changed': False, 'reason': 'open_agent_session'})
                call.assert_not_called()

    def test_agent_without_session_id_still_blocks_close(self):
        source = dict(self.source)
        del source['agent_session']
        with patch('builtins.input', return_value='CLOSE'), patch('builtins.print'):
            call = Mock()
            self.assertEqual(helper.close(call, source)['reason'], 'open_agent_session')
            call.assert_not_called()

    def test_session_without_agent_label_still_blocks_close(self):
        with patch('builtins.input', return_value='CLOSE'), patch('builtins.print'):
            call = Mock()
            self.assertEqual(helper.close(call, dict(self.source, agent=None))['reason'], 'open_agent_session')
            call.assert_not_called()

    def test_agent_starting_during_confirmation_blocks_close(self):
        shell = dict(self.source, agent=None, agent_session=None)
        call = Mock(return_value={'pane': self.source})
        with patch('builtins.input', return_value='CLOSE'), patch('builtins.print'):
            self.assertEqual(helper.close(call, shell)['reason'], 'open_agent_session')
        self.assertEqual(call.call_args_list[0].args, ('pane.get', {'pane_id': shell['pane_id']}))
        self.assertEqual(call.call_count, 1)

    def test_confirmation_refuses_replaced_terminal(self):
        shell = dict(self.source, agent=None, agent_session=None)
        changed = dict(shell, terminal_id='replacement-terminal')
        call = Mock(return_value={'pane': changed})
        with patch('builtins.input', return_value='CLOSE'), patch('builtins.print'):
            with self.assertRaisesRegex(RuntimeError, 'occupant changed'):
                helper.close(call, shell)
        self.assertEqual(call.call_count, 1)

    def test_confirmed_shell_close_targets_exact_original_pane(self):
        shell = dict(self.source, agent=None, agent_session=None)
        call = Mock(return_value={'pane': shell})
        with patch('builtins.input', return_value='CLOSE'), patch('builtins.print'):
            self.assertTrue(helper.close(call, shell)['changed'])
        self.assertEqual(call.call_args.args, ('pane.close', {'pane_id': shell['pane_id']}))


if __name__ == '__main__':
    unittest.main()
