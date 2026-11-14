"""Temporary viewer labels, tested without contacting the active Herdr server."""

import fcntl
import importlib.util
import json
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT.parent / 'htk'
PATH = ROOT / 'libexec/viewer_pane_name/wrapper.py'
spec = importlib.util.spec_from_file_location('viewer_pane_name', PATH)
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)


class PaneNameTests(unittest.TestCase):
    def test_only_interactive_viewers_inside_herdr_get_short_names(self):
        with patch.dict(os.environ, {'HERDR_ENV': '1', 'HERDR_PANE_ID': 'test:p1'}), \
                patch.object(sys.stdin, 'isatty', return_value=True), \
                patch.object(sys.stdout, 'isatty', return_value=True):
            for command, name in wrapper.NAMES.items():
                self.assertEqual(wrapper.viewer_name(command, []), name)
                for flag in ('--once', '--json', '--help', '-h', '--workspaces', '--projects'):
                    self.assertIsNone(wrapper.viewer_name(command, [flag]))
            self.assertIsNone(wrapper.viewer_name('herdr-pane', ['create', 'right']))
            self.assertEqual(wrapper.viewer_name('list-view', ['--', 'provider', '--json']), 'LIST')
            with patch.dict(os.environ, {'HERDR_ENV': '0'}):
                self.assertIsNone(wrapper.viewer_name('pr-status', []))
            with patch.object(sys.stdout, 'isatty', return_value=False):
                self.assertIsNone(wrapper.viewer_name('pr-status', []))

    def test_restores_exact_explicit_label_on_exit_and_error(self):
        for fail in (False, True):
            with self.subTest(fail=fail), patch.object(wrapper, 'herdr') as herdr:
                herdr.return_value = {'result': {'pane': {'pane_id': 'resolved:p1', 'label': 'MY OLD NAME'}}}
                try:
                    with wrapper.pane_name('PRS'):
                        self.assertEqual(herdr.call_args.args, ('pane', 'rename', 'resolved:p1', 'PRS'))
                        if fail:
                            raise RuntimeError('viewer failed')
                except RuntimeError:
                    pass
                self.assertEqual(herdr.call_args.args, ('pane', 'rename', 'resolved:p1', 'MY OLD NAME'))
                self.assertEqual(herdr.call_args_list[0].args, ('pane', 'current', '--current'))

    def test_unlabeled_pane_restores_inherited_terminal_title(self):
        with patch.object(wrapper, 'herdr') as herdr:
            herdr.return_value = {'result': {'pane': {'pane_id': 'test:p1', 'terminal_title': 'zsh'}}}
            with wrapper.pane_name('SLACK'):
                pass
            self.assertEqual(herdr.call_args.args, ('pane', 'rename', 'test:p1', '--clear'))

    def test_herdr_errors_are_quiet_and_never_block_the_viewer(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired('herdr', 2), ValueError('bad JSON')):
            with patch.object(wrapper, 'herdr', side_effect=error) as herdr:
                with wrapper.pane_name('PRS'):
                    pass
                self.assertEqual(herdr.call_count, 1)
        with patch.object(wrapper, 'herdr', side_effect=[
            {'result': {'pane': {'pane_id': 'test:p1', 'label': 'OLD'}}},
            subprocess.TimeoutExpired('herdr', 2), {}]) as herdr:
            with wrapper.pane_name('PRS'):
                pass
            self.assertEqual(herdr.call_args.args, ('pane', 'rename', 'test:p1', 'OLD'))

    def test_real_viewer_keeps_name_on_escape_and_restores_on_q_ctrl_c_or_termination(self):
        # A local fake CLI isolates all mutations from the user's Herdr server.
        for key, original in ((b'q', 'PREVIOUS'), (b'Q', 'UPPERCASE'),
                              (b'\x03', None), (None, 'KEEP ME')):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                state = folder / 'pane.json'
                state.write_text(json.dumps({'pane_id': 'test:p1', 'label': original}))
                cli = folder / 'herdr'
                cli.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
from pathlib import Path
path = Path(os.environ['PANE_STATE'])
pane = json.loads(path.read_text())
if sys.argv[1:3] == ['pane', 'rename']:
    pane['label'] = None if sys.argv[4] == '--clear' else sys.argv[4]
    path.write_text(json.dumps(pane))
print(json.dumps({'result': {'pane': pane}}))
''')
                cli.chmod(0o700)
                master, slave = pty.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
                env = {**os.environ, 'TERM': 'xterm-256color', 'HERDR_ENV': '1',
                       'HERDR_PANE_ID': 'test:p1', 'PANE_STATE': str(state),
                       'PATH': str(folder) + os.pathsep + os.environ.get('PATH', '')}
                terminal_session = ('import fcntl, os, sys, termios; os.setsid(); '
                    'fcntl.ioctl(0, termios.TIOCSCTTY, 0); '
                    'os.execv(sys.executable, [sys.executable, *sys.argv[1:]])')
                process = subprocess.Popen([sys.executable, '-c', terminal_session,
                    str(PATH), 'list-view', sys.executable,
                    str(CORE / 'libexec/list_view/viewer.py'), '--data', '["Visible item"]'],
                    stdin=slave, stdout=slave, stderr=slave, env=env)
                try:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        if json.loads(state.read_text())['label'] == 'LIST':
                            break
                        time.sleep(0.01)
                    else:
                        self.fail('pane did not acquire viewer name')
                    # Wait for curses input so Ctrl-C is handled by the viewer.
                    output = b''
                    while b'Visible item' not in output and time.monotonic() < deadline:
                        if select.select([master], [], [], 0.1)[0]:
                            output += os.read(master, 65536)
                    self.assertIn(b'Visible item', output)
                    os.write(master, b'\x1b')
                    time.sleep(0.15)
                    self.assertIsNone(process.poll())
                    self.assertEqual(json.loads(state.read_text())['label'], 'LIST')
                    if key is None:
                        process.send_signal(signal.SIGTERM)
                    else:
                        os.write(master, key)
                    deadline = time.monotonic() + 5
                    while process.poll() is None and time.monotonic() < deadline:
                        if select.select([master], [], [], 0.05)[0]:
                            output += os.read(master, 65536)
                    self.assertEqual(process.poll(), 0, output)
                    self.assertEqual(json.loads(state.read_text())['label'], original)
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    os.close(master)
                    os.close(slave)


if __name__ == '__main__':
    unittest.main()
