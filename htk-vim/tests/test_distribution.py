"""Install shared keyboard helpers without personal defaults or agent instructions."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT.parent / 'htk'
spec = importlib.util.spec_from_file_location('vim_distribution', CORE / 'libexec/toolkit.py')
htk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(htk)


class DistributionTests(unittest.TestCase):
    def test_clean_helper_install_runs_help_and_rolls_back_without_creating_agent_config(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = htk.load_profile(ROOT / 'toolkit.toml')
            self.assertEqual(set(config['commands']), {'herdr-pane', 'herdr-pane-to-tab', 'herdr-move-workspace'})
            self.assertNotIn('skill', config['profile'])
            self.assertNotIn('command_wrapper', config['profile'])
            manifest = htk.install(config, home / 'bin', home / 'codex', home / 'state')
            for command in config['commands']:
                self.assertTrue(Path(config['commands'][command]['script']).is_relative_to(ROOT))
                result = subprocess.run([str(home / 'bin' / command), '--help'],
                    env={'HOME': str(home), 'PATH': '/usr/bin:/bin'},
                    capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage:', result.stdout)
            self.assertFalse((home / 'codex').exists())
            htk.rollback(manifest)
            self.assertEqual(list((home / 'bin').iterdir()), [])
            self.assertFalse((home / 'codex').exists())
