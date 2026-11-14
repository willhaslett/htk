"""Exercise packaged installation, relocation, command wiring and configuration."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('portable_toolkit', ROOT / 'htk/libexec/toolkit.py')
toolkit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(toolkit)


class PortableTests(unittest.TestCase):
    def test_profile_references_only_files_in_the_tree(self):
        config = toolkit.load_profile(ROOT / 'personal/toolkit.toml')
        self.assertEqual(set(config['commands']), {
            'herdr-pane', 'herdr-pane-to-tab', 'herdr-move-workspace', 'new-herdr-workspace'})
        for command in config['commands'].values():
            path = Path(command['script'])
            self.assertTrue(path.is_relative_to(ROOT))
            self.assertTrue(path.is_file())
        for field in ('skill', 'command_wrapper'):
            path = Path(config['profile'][field])
            self.assertTrue(path.is_relative_to(ROOT))
            self.assertTrue(path.exists())
        self.assertNotIn('workspaces', config)

    def test_unconfigured_commands_are_local_only(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, 'HOME': directory, 'XDG_CONFIG_HOME': str(Path(directory)/'config')}
            env.pop('HTK_CONFIG', None)
            env.pop('DAILY_TOOLKIT_CONFIG', None)
            result = subprocess.run([str(ROOT / 'htk/bin/htk'), '--help'], env=env,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('github-prs', result.stdout)
            result = subprocess.run([str(ROOT / 'htk/bin/htk'), 'list', '--once', '--data', '["Fresh setup"]'],
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Fresh setup', result.stdout)
        self.assertEqual(set(toolkit.load_profile(ROOT / 'htk/profiles/core.toml')), {'profile', 'schema', '_skill_sources', '_path', '_root'})

    def test_install_commands_then_restore_existing_files(self):
        with tempfile.TemporaryDirectory(prefix="install ' $ ") as directory:
            directory = Path(directory)
            bin_dir = directory / 'bin'
            codex_dir = directory / 'agent config'
            state = directory / 'state'
            bin_dir.mkdir()
            codex_dir.mkdir()
            original = 'Existing personal guidance.\n'
            (codex_dir / 'AGENTS.md').write_text(original)
            (bin_dir / 'unrelated').write_text('Keep this.\n')
            args = [str(ROOT / 'htk/bin/htk'), 'install', '--config', str(ROOT / 'personal/toolkit.toml'),
                    '--bin-dir', str(bin_dir), '--codex-dir', str(codex_dir), '--state-dir', str(state)]
            installed = subprocess.run(args, capture_output=True, text=True, timeout=15)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            manifest = Path(json.loads((state / 'last-install.json').read_text())['manifest'])
            self.assertIn(original, (codex_dir / 'AGENTS.md').read_text())
            self.assertTrue((codex_dir / 'skills/htk/SKILL.md').is_file())
            for command in ('htk', 'mdview', 'list-view', 'snapshot-list', 'status-list',
                            'github-pr-status', 'name-session', 'new-herdr-workspace',
                            'herdr-pane', 'herdr-pane-to-tab', 'herdr-move-workspace'):
                result = subprocess.run([str(bin_dir / command), '--help'],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, (command, result.stderr))
            result = subprocess.run([str(bin_dir / 'htk'), 'list', '--once', '--data', '["Relocated setup"]'],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Relocated setup', result.stdout)
            source = directory / 'status.json'
            source.write_text(json.dumps({'version': 1, 'title': 'Travel', 'tasks': [
                {'id': '1', 'title': 'Pack bag', 'state': 'Ready', 'next': 'User: pack'}]}))
            result = subprocess.run([str(bin_dir / 'htk'), 'status', str(source), '--json'],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Pack bag', result.stdout)
            result = subprocess.run([str(bin_dir / 'htk'), 'doctor'], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(bin_dir / 'htk'), '--clients'], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual({c['name'] for c in json.loads(result.stdout)['clients']}, {'list', 'status', 'github-prs'})
            result = subprocess.run([str(bin_dir / 'htk'), 'rollback', str(manifest)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((codex_dir / 'AGENTS.md').read_text(), original)
            self.assertEqual([p.name for p in bin_dir.iterdir()], ['unrelated'])
            self.assertFalse((codex_dir / 'skills/htk').exists())

    def test_install_does_not_replace_an_unrelated_command(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            (directory / 'bin').mkdir()
            existing = directory / 'bin/htk'
            existing.write_text('Unrelated executable')
            config = toolkit.load_profile(ROOT / 'personal/toolkit.toml')
            with self.assertRaisesRegex(ValueError, 'Refusing to replace'):
                toolkit.install(config, directory / 'bin', directory / 'codex', directory / 'state')
            self.assertEqual(existing.read_text(), 'Unrelated executable')
            self.assertFalse((directory / 'codex').exists())

    def test_popup_reads_exported_bindings_without_running_them(self):
        result = subprocess.run(['bash', str(ROOT / 'htk-vim/plugins/keymap/run.sh'), '--print',
                                 '--config', str(ROOT / 'personal/herdr/config.toml')],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('New workspace with agent', result.stdout)
        self.assertIn('Close pane', result.stdout)
        self.assertIn('H', result.stdout)

    def test_popup_and_source_launcher_use_the_portable_runtime_without_environment_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            runtime = directory / 'data/htk-portable/runtime'
            runtime.mkdir(parents=True)
            (runtime / 'python').symlink_to(Path(sys.executable).resolve())
            env = {'HOME': str(directory / 'home'), 'XDG_DATA_HOME': str(directory / 'data'),
                   'XDG_CONFIG_HOME': str(directory / 'config'), 'PATH': '/usr/bin:/bin',
                   'PYTHONDONTWRITEBYTECODE': '1'}
            for command in ([str(ROOT / 'htk/bin/htk'), 'list', '--once', '--data', '["Local runtime"]'],
                            ['bash', str(ROOT / 'htk-vim/plugins/keymap/run.sh'), '--print',
                             '--config', str(ROOT / 'personal/herdr/config.toml')]):
                result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_exported_custom_commands_have_bundled_targets(self):
        config = tomllib.loads((ROOT / 'personal/herdr/config.toml').read_text())
        for row in config['keys']['command']:
            if row['type'] == 'plugin_action':
                self.assertEqual(row['command'], 'htk-vim.keymap.show')
            else:
                self.assertTrue(row['command'].startswith(tuple('"$HOME/.local/bin/' + name + '"'
                    for name in ('new-herdr-workspace', 'herdr-pane', 'herdr-pane-to-tab', 'herdr-move-workspace'))))
        self.assertEqual(config['keys']['close_tab'], '')
        self.assertEqual(config['keys']['close_pane'], '')

    def test_shell_entrypoints_have_valid_syntax(self):
        scripts = [ROOT / 'setup.sh', ROOT / 'verify.sh', ROOT / 'htk/bootstrap.sh',
                   ROOT / 'htk/libexec/python.sh', ROOT / 'htk-vim/plugins/keymap/run.sh']
        scripts += list((ROOT / 'htk/bin').iterdir())
        for script in scripts:
            result = subprocess.run(['bash', '-n', str(script)], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, (script, result.stderr))

    def test_agent_guidance_links_stay_inside_the_tree(self):
        for document in (ROOT / 'personal/skills/htk').rglob('*.md'):
            for link in re.findall(r'\]\(([^)]+)\)', document.read_text()):
                target = (document.parent / link.split('#')[0]).resolve()
                self.assertTrue(target.is_relative_to(ROOT), (document, link))
                self.assertTrue(target.is_file(), (document, link))


if __name__ == '__main__':
    unittest.main()
