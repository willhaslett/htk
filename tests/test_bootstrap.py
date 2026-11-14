"""Exercise runtime selection without downloading or changing the real home."""
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    def test_incompatible_path_uv_uses_private_runtime_and_keeps_existing_uv(self):
        with tempfile.TemporaryDirectory(prefix="runtime with spaces ") as temporary:
            root = Path(temporary)
            runtime = root / 'runtime'
            (runtime / 'bin').mkdir(parents=True)
            user_bin = root / 'bin'
            user_bin.mkdir()
            existing = user_bin / 'uv'
            existing.write_text('#!/bin/sh\n[ "$1" = --version ] || exit 77\necho "uv 0.7.13"\n')
            existing.chmod(0o755)
            original = existing.read_bytes()
            private = runtime / 'bin/uv-0.11.2'
            private.write_text(
                '#!/bin/sh\ncase "$*" in\n'
                '  "python install 3.12.13") exit 0 ;;\n'
                '  "python find --managed-python 3.12.13") printf "%s\\n" '
                + shlex.quote(sys.executable) + ' ;;\n'
                '  *) exit 78 ;;\nesac\n')
            private.chmod(0o755)
            env = {**os.environ, 'HTK_RUNTIME': str(runtime),
                   'PATH': str(user_bin) + ':/usr/bin:/bin'}
            env.pop('DAILY_TOOLKIT_UV', None)
            result = subprocess.run(['bash', str(ROOT / 'htk/bootstrap.sh')], env=env,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Managed Python ready', result.stdout)
            self.assertEqual((runtime / 'python').resolve(), Path(sys.executable).resolve())
            self.assertEqual(existing.read_bytes(), original)

    def test_explicit_incompatible_or_missing_uv_fails_before_provisioning(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            incompatible = root / 'uv'
            incompatible.write_text('#!/bin/sh\necho "uv 0.7.13"\n')
            incompatible.chmod(0o755)
            for override in (incompatible, root / 'missing'):
                with self.subTest(override=override):
                    runtime = root / ('runtime-' + override.name)
                    result = subprocess.run(['bash', str(ROOT / 'htk/bootstrap.sh')],
                        env={**os.environ, 'HTK_RUNTIME': str(runtime), 'DAILY_TOOLKIT_UV': str(override)},
                        capture_output=True, text=True, timeout=5)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('DAILY_TOOLKIT_UV', result.stderr)
                    self.assertFalse((runtime / 'python').exists())


if __name__ == '__main__':
    unittest.main()
