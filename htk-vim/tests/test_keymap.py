"""Directional reference derives from configuration without running its actions."""

import copy
import fcntl
import importlib.util
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'libexec/herdr_controls/keymap.py'
spec = importlib.util.spec_from_file_location('keymap', SCRIPT)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

CONFIG = '''[keys]
prefix = "ctrl+n"
previous_tab = ["prefix+p", "cmd+h"]
next_tab = "cmd+l"
focus_pane_down = "alt+cmd+j"
close_pane = ""
[[keys.command]]
key = ["prefix+2", "ctrl+alt+cmd+j"]
description = "Create pane down"
command = "touch /must-not-execute"
'''


class BindingTests(unittest.TestCase):
    def test_reads_actions_and_custom_descriptions_without_leader_aliases_or_execution(self):
        config = helper.tomllib.loads(CONFIG)
        before = copy.deepcopy(config)
        rows = helper.bindings(config)
        self.assertEqual(rows, [
            (frozenset(['cmd']), {'h': 'Previous tab', 'l': 'Next tab'}),
            (frozenset(['alt', 'cmd']), {'j': 'Focus pane down'}),
            (frozenset(['ctrl', 'alt', 'cmd']), {'j': 'Create pane down'}),
        ])
        self.assertEqual(config, before)

    def test_empty_layers_are_omitted_and_new_modifier_layers_are_discovered(self):
        rows = helper.bindings({'keys': {'command': [
            {'key': 'cmd+option+shift+control+k', 'description': 'A future action'},
        ]}})
        self.assertEqual(len(rows), 1)
        self.assertEqual(helper.layer_label(rows[0][0]), '⇧⌃⌥⌘')
        self.assertEqual(rows[0][1], {'k': 'A future action'})

    def test_duplicate_aliases_deduplicate_and_conflicting_actions_remain_visible(self):
        rows = helper.bindings({'keys': {
            'previous_tab': ['cmd+h', 'super+h'],
            'command': [{'key': 'cmd+h', 'description': 'Another action'}],
        }})
        self.assertEqual(rows[0][1]['h'], 'Previous tab / Another action')

    def test_non_directional_or_invalid_keys_are_not_reported_as_layers(self):
        for value in ('prefix+h', 'cmd+n', 'cmd+cmd+h', 'hyper+j', '', None):
            with self.subTest(value=value):
                self.assertIsNone(helper.chord(value))

    def test_descriptions_cannot_inject_terminal_controls(self):
        rows = helper.bindings({'keys': {'command': [{
            'key': 'cmd+h', 'description': 'First\x1b[2J\nSecond',
        }]}})
        self.assertNotIn('\x1b', rows[0][1]['h'])
        self.assertNotIn('\n', rows[0][1]['h'])

    def test_reference_fits_small_terminals_without_losing_shortcuts(self):
        rows = helper.bindings(helper.tomllib.loads(CONFIG))
        for width in (30, 40, 70, 106):
            lines = helper.reference(rows, width)
            self.assertTrue(all(len(line) <= width for line, _ in lines), width)
            text = '\n'.join(line for line, _ in lines)
            self.assertIn('H', text)
            self.assertIn('L', text)
        text = '\n'.join(t for t, _ in helper.reference(rows, 106))
        for level in ('1 ⌘', '2 ⌥⌘', '3 ⌃⌥⌘'):
            self.assertIn(level, text)

    def test_inspection_rereads_edits_and_does_not_modify_the_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'config.toml'
            for label in ('Create pane down', 'New pane below'):
                text = CONFIG.replace('Create pane down', label)
                source.write_text(text)
                result = subprocess.run([sys.executable, str(SCRIPT), '--config', str(source), '--json'],
                                        text=True, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(label, result.stdout)
                self.assertEqual(source.read_text(), text)

    def test_standard_actions_show_direct_assignments_and_leave_missing_ones_blank(self):
        config = {'keys': {
            'prefix': 'ctrl+n',
            'new_workspace': 'cmd+n',
            'new_tab': ['prefix+c', 'cmd+t'],
            'close_pane': '',
            'command': [{'key': ['prefix+shift+p', 'shift+cmd+n'],
                         'description': 'New workspace with Codex',
                         'command': 'touch /must-not-execute'}],
        }}
        before = copy.deepcopy(config)
        text = '\n'.join(line for line, _ in helper.reference(helper.bindings(config), 106, config))
        for label in ('⌘N', 'New workspace', '⇧⌘N', 'New workspace with agent', '⌘T'):
            self.assertIn(label, text)
        close_row = next(line for line in text.splitlines() if 'Close pane' in line)
        self.assertEqual(close_row.strip('│ '), 'Close pane')
        self.assertNotIn('Prefix', text)
        self.assertNotIn('⌃N', text)
        agent_row = next(line for line in text.splitlines() if 'New workspace with agent' in line)
        self.assertIn('⇧⌘N', agent_row)
        self.assertEqual(config, before)

    def test_different_direction_assignments_keep_their_full_shortcuts(self):
        config = {'keys': {
            'previous_tab': 'cmd+h', 'next_tab': 'cmd+l',
            'focus_pane_left': 'alt+cmd+l', 'focus_pane_right': 'alt+cmd+h',
        }}
        text = '\n'.join(line for line, _ in helper.reference(helper.bindings(config), 106, config))
        for label in ('← Left H', '→ Right L', '⌥⌘L', 'Pane navigation left',
                      '⌥⌘H', 'Pane navigation right'):
            self.assertIn(label, text)
        self.assertLess(text.index('← Left H'), text.index('Pane navigation left'))


class TerminalTests(unittest.TestCase):
    def test_escape_and_q_exit_cleanly_and_restore_terminal_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'config.toml'
            source.write_text(CONFIG)
            for dismiss in (b'\x1b', b'q'):
                with self.subTest(dismiss=dismiss):
                    master, slave = pty.openpty()
                    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 21, 110, 0, 0))
                    original = termios.tcgetattr(slave)
                    process = subprocess.Popen([sys.executable, str(SCRIPT), '--config', str(source)],
                                               stdin=slave, stdout=slave, stderr=slave,
                                               env={**os.environ, 'TERM': 'xterm-256color'})
                    try:
                        output = b''
                        deadline = time.monotonic() + 5
                        while b'Esc / Enter / q close' not in output and time.monotonic() < deadline:
                            if select.select([master], [], [], 0.1)[0]:
                                output += os.read(master, 65536)
                        self.assertIn(b'Esc / Enter / q close', output)
                        os.write(master, dismiss)
                        deadline = time.monotonic() + 3
                        while process.poll() is None and time.monotonic() < deadline:
                            if select.select([master], [], [], 0.05)[0]:
                                output += os.read(master, 65536)
                        self.assertEqual(process.poll(), 0, output[-1000:])
                        restored = termios.tcgetattr(slave)
                        # Darwin may mark pending input after tcsetattr; this is
                        # kernel bookkeeping, not a changed input/echo mode.
                        restored[3] &= ~getattr(termios, 'PENDIN', 0)
                        original[3] &= ~getattr(termios, 'PENDIN', 0)
                        self.assertEqual(restored, original)
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait()
                        os.close(master)
                        os.close(slave)


if __name__ == '__main__':
    unittest.main()
