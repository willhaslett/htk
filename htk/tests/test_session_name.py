from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("session_name", ROOT / "libexec/session_name/name.py")
namer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(namer)

FAKE_SERVER = '''
import json, sys, time
name = None
for line in sys.stdin:
    request = json.loads(line)
    with open(sys.argv[1], "a") as log:
        log.write(json.dumps(request) + "\\n")
    method = request["method"]
    if method == "initialized":
        continue
    result = {}
    error = None
    if method == "thread/name/set":
        name = request["params"]["name"]
        if name == "denied":
            error = {"message": "rename denied"}
        if name == "slow":
            time.sleep(60)
    elif method == "thread/read":
        result = {"thread": {"name": name}}
    print(json.dumps({"method": "unrelated/notification"}), flush=True)
    print(json.dumps({"id": request["id"], **({"error": error} if error else {"result": result})}), flush=True)
'''


class SessionNameTests(unittest.TestCase):
    def test_name_preserves_workspace_and_includes_hour_minute_second(self):
        self.assertEqual(namer.session_name("Daily Work", datetime(2026, 10, 21, 15, 4, 9)),
                         "Daily Work-20261021150409")
        for invalid in ("", "  ", "workspace\nother"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                namer.session_name(invalid)

    def test_workspace_uses_the_callers_identity(self):
        response = {"result": {"workspace": {"label": "calling-workspace"}}}
        result = subprocess.CompletedProcess([], 0, json.dumps(response), "")
        with patch.dict(os.environ, {"HERDR_ENV": "1", "HERDR_WORKSPACE_ID": "caller", "HERDR_BIN_PATH": "herdr"}), \
             patch.object(namer.subprocess, "run", return_value=result) as run:
            self.assertEqual(namer.current_workspace(), "calling-workspace")
        self.assertEqual(run.call_args.args[0], ["herdr", "workspace", "get", "caller"])

    def test_outside_herdr_needs_an_explicit_workspace(self):
        with patch.dict(os.environ, {"HERDR_ENV": "0"}), patch.object(namer.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "--workspace"):
                namer.current_workspace()
        run.assert_not_called()

    def test_protocol_renames_and_reads_metadata_without_an_agent_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server, log = root / "server.py", root / "requests.jsonl"
            server.write_text(FAKE_SERVER)
            namer.rename_thread("current-thread", "workflow-20261021150409",
                                command=(sys.executable, str(server), str(log)))
            requests = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual([r["method"] for r in requests],
                         ["initialize", "initialized", "thread/name/set", "thread/read"])
        self.assertEqual(requests[2]["params"], {"threadId": "current-thread", "name": "workflow-20261021150409"})
        self.assertEqual(requests[3]["params"], {"threadId": "current-thread", "includeTurns": False})

    def test_rpc_errors_and_timeout_do_not_retry_rename(self):
        for name, message in (("denied", "rename denied"), ("slow", "timed out")):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                server, log = root / "server.py", root / "requests.jsonl"
                server.write_text(FAKE_SERVER)
                with self.assertRaisesRegex(ValueError, message):
                    namer.rename_thread("current-thread", name, timeout=0.5,
                                        command=(sys.executable, str(server), str(log)))
                requests = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertEqual(sum(r["method"] == "thread/name/set" for r in requests), 1)

    def test_symlinked_launcher_previews_without_codex_or_herdr(self):
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / "name-session"
            link.symlink_to(ROOT / "bin/name-session")
            env = {k: v for k, v in os.environ.items() if k not in {"HERDR_ENV", "HERDR_WORKSPACE_ID", "CODEX_THREAD_ID"}}
            result = subprocess.run([str(link), "--workspace", "Daily Work", "--dry-run"],
                                    cwd=directory, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        stamp = result.stdout.strip().removeprefix("Daily Work-")
        self.assertEqual(len(stamp), 14)
        datetime.strptime(stamp, "%Y%m%d%H%M%S")


if __name__ == "__main__":
    unittest.main()
