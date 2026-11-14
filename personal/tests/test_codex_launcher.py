"""The personal Codex launcher preserves arguments and skips its own links."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

LAUNCHER = Path(__file__).resolve().parents[1] / 'shell/codex'


class CodexLauncherTests(unittest.TestCase):
    def test_skips_own_symlinks_and_forwards_arguments_to_real_codex(self):
        with tempfile.TemporaryDirectory(prefix='codex launcher ') as temporary:
            root = Path(temporary)
            paths = [root / name for name in ('personal bin', 'duplicate', 'real bin')]
            for directory in paths:
                directory.mkdir()
            for directory in paths[:2]:
                (directory / 'codex').symlink_to(LAUNCHER)
            real = paths[2] / 'codex'
            real.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            real.chmod(0o755)
            for flags in ([], ['--no-daemon']):
                with self.subTest(flags=flags):
                    result = subprocess.run([str(paths[0] / 'codex'), *flags, 'resume', 'session-id',
                                             '--cd', 'project with spaces'],
                        env={**os.environ, 'PATH': os.pathsep.join(map(str, paths))},
                        capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.splitlines(),
                                     ['--no-daemon', 'resume', 'session-id', '--cd', 'project with spaces'])

    def test_missing_real_codex_fails_without_recursing(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'codex'
            target.symlink_to(LAUNCHER)
            result = subprocess.run([str(target), '--version'],
                env={**os.environ, 'PATH': temporary}, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 127)
            self.assertIn('install the Codex CLI', result.stderr)
