"""Check the portable files and test a relocated copy without live configuration."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
IGNORED = {'.git', '__pycache__', '.DS_Store'}


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def packaged_files():
    actual = {}
    for path in ROOT.rglob('*'):
        if any(part in IGNORED for part in path.relative_to(ROOT).parts):
            continue
        if path.is_symlink():
            raise ValueError('Unexpected symlink: ' + str(path.relative_to(ROOT)))
        if path.is_file() and path.name != 'MANIFEST.json':
            relative = str(path.relative_to(ROOT))
            actual[relative] = {'sha256': file_hash(path), 'executable': bool(path.stat().st_mode & 0o111)}
    return actual


def check_manifest():
    manifest = json.loads((ROOT / 'MANIFEST.json').read_text())
    files = manifest['files']
    actual = packaged_files()
    if actual != files:
        changed = sorted(set(actual) ^ set(files) | {p for p in actual.keys() & files.keys() if actual[p] != files[p]})
        raise ValueError('Packaged files differ from manifest: ' + ', '.join(changed))
    return len(files) + 1


def main():
    if sys.version_info < (3, 12):
        raise SystemExit('Python 3.12+ is required.')
    print('Integrity: %d files verified' % check_manifest(), flush=True)
    python = Path(sys.executable).resolve()
    node = shutil.which('node')
    tools_path = os.pathsep.join(dict.fromkeys([
        str(python.parent), *([str(Path(node).parent)] if node else []),
        '/usr/bin', '/bin', '/usr/sbin', '/sbin']))
    results = []
    with tempfile.TemporaryDirectory(prefix="portable check ' ") as temporary:
        temporary = Path(temporary)
        tree = temporary / 'relocated setup with spaces'
        shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(*IGNORED))
        home = temporary / 'isolated home'
        home.mkdir()
        runtime = temporary / 'runtime'
        runtime.mkdir()
        (runtime / 'python').symlink_to(python)
        env = {'HOME': str(home), 'PATH': tools_path, 'HTK_RUNTIME': str(runtime),
               'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
               'XDG_CONFIG_HOME': str(home / '.config'),
               'XDG_DATA_HOME': str(home / '.local/share'),
               'XDG_STATE_HOME': str(home / '.local/state'),
               'XDG_CACHE_HOME': str(home / '.cache'),
               'TMPDIR': str(temporary), 'TERM': 'xterm-256color', 'LANG': 'en_US.UTF-8'}
        for component in ('htk', 'htk-vim', 'personal', '.'):
            directory = tree / component
            print('Testing ' + component + ' ...', flush=True)
            started = time.monotonic()
            result = subprocess.run([str(python), '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                                    cwd=directory, env=env, capture_output=True, text=True, timeout=240)
            output = result.stdout + result.stderr
            if result.returncode:
                print(output, file=sys.stderr)
                raise SystemExit(result.returncode)
            match = re.search(r'Ran (\d+) tests?', output)
            count = int(match.group(1)) if match else 0
            if count == 0:
                raise SystemExit('No tests discovered in ' + component)
            summary = {'component': component, 'tests': count,
                       'seconds': round(time.monotonic() - started, 2),
                       'result': output.strip().splitlines()[-1]}
            results.append(summary)
            print(json.dumps(summary), flush=True)
    print('PASS: %d tests in relocated isolated copy' % sum(r['tests'] for r in results), flush=True)
    return 0


if __name__ == '__main__':
    if sys.argv[1:] == ['--update-manifest']:
        manifest = {'version': 1, 'files': dict(sorted(packaged_files().items()))}
        (ROOT / 'MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n')
        print('Manifest updated; run ./verify.sh to validate this revision.')
        raise SystemExit(0)
    if sys.argv[1:]:
        raise SystemExit('Usage: verify.py [--update-manifest]')
    raise SystemExit(main())
