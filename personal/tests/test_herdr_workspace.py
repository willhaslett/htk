"""Workspace naming, shortcut context and duplicate launch prevention."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "herdr_workspace", Path(__file__).resolve().parents[1] / "libexec/herdr_workspace/create.py"
)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

PANE = {"pane_id": "w99:p1", "terminal_id": "terminal-original"}
MANAGER = PANE
BUSY = helper.HerdrError("agent_pane_busy", "shell is initializing")
STARTED = {"type": "agent_started"}
CREATED = {"workspace": {"workspace_id": "w99", "label": "01"},
           "tab": {"tab_id": "w99:t1"}, "root_pane": PANE}


def creation_replies(labels=(), focus=True):
    return [{"workspaces": [{"label": label} for label in labels]}, CREATED, {}, {}] + ([{}] if focus else [])


class CreationTests(unittest.TestCase):
    def test_names_use_only_current_exact_numeric_labels_and_cross_99(self):
        for labels, expected in (([], "01"), (["TRAVEL", "WORKFLOW 99"], "01"),
                                 (["2", "9"], "10"), (["007", "4"], "08"),
                                 ([" 12", "12 ", "-4", "+12", "1.5", "１２"], "01"),
                                 (["2", "RESEARCH"], "03"), (["98"], "99"), (["99"], "100")):
            with self.subTest(labels=labels):
                self.assertEqual(helper.next_workspace_name([
                    {"label": label, "number": 999} for label in labels]), expected)

    def test_bad_listing_is_not_treated_as_empty(self):
        for listing in (None, {}, ["12"], [{}], [{"label": None}]):
            with self.subTest(listing=listing), self.assertRaises(ValueError):
                helper.next_workspace_name(listing)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(helper, "invoke", side_effect=RuntimeError("offline")) as invoke:
            with self.assertRaisesRegex(RuntimeError, "offline"):
                helper.create_workspace("herdr", str(Path(directory) / "test.sock"))
            self.assertEqual(invoke.call_count, 1)

    def test_creation_names_the_single_manager_before_focusing(self):
        replies = creation_replies(["07"])
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(helper, "invoke", side_effect=replies) as invoke:
            helper.create_workspace("herdr", str(Path(directory) / "test.sock"), "/path with spaces")
        self.assertEqual([call.args for call in invoke.call_args_list], [
            ("herdr", "workspace", "list"),
            ("herdr", "workspace", "create", "--label", "08", "--no-focus", "--cwd", "/path with spaces"),
            ("herdr", "tab", "rename", "w99:t1", "MANAGER"),
            ("herdr", "pane", "rename", "w99:p1", "MANAGER"),
            ("herdr", "workspace", "focus", "w99")])

    def test_plain_shortcut_uses_active_cwd_and_preserves_no_focus(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(helper.os.environ, {
                "HERDR_SOCKET_PATH": str(Path(directory) / "test.sock"),
                "HERDR_ACTIVE_PANE_CWD": "/active project", "HERDR_PANE_ID": "stale:p9",
                "HERDR_BIN_PATH": "/chosen/herdr"}), \
                patch.object(helper, "invoke", side_effect=creation_replies(focus=False)) as invoke, \
                patch.object(helper, "start_codex") as start, patch.object(helper.sys, "stdout", io.StringIO()) as output:
            helper.main(["--no-focus"])
        result = json.loads(output.getvalue())
        self.assertEqual(result["pane_id"], MANAGER["pane_id"])
        self.assertEqual(result["shell_pane_ids"], [])
        calls = [call.args for call in invoke.call_args_list]
        self.assertIn(("/chosen/herdr", "workspace", "create", "--label", "01", "--no-focus",
                       "--cwd", "/active project"), calls)
        self.assertFalse(any("focus" in call for call in calls))
        self.assertNotIn("stale:p9", str(calls))
        start.assert_not_called()

    def test_focus_failure_keeps_workspace_without_starting_an_agent(self):
        replies = creation_replies(focus=False) + [RuntimeError("focus failed")]
        with tempfile.TemporaryDirectory() as directory, patch.dict(helper.os.environ, {
                "HERDR_SOCKET_PATH": str(Path(directory) / "test.sock")}), \
                patch.object(helper, "invoke", side_effect=replies) as invoke, \
                patch.object(helper, "start_codex") as start:
            with self.assertRaisesRegex(RuntimeError, "focus failed"):
                helper.main(["--codex"])
        self.assertEqual(sum(call.args[1:3] == ("workspace", "create") for call in invoke.call_args_list), 1)
        self.assertFalse(any("close" in call.args or "split" in call.args for call in invoke.call_args_list))
        start.assert_not_called()

    def test_missing_socket_stops_before_creating(self):
        with patch.dict(helper.os.environ, {}, clear=True), patch.object(helper, "invoke") as invoke, \
                patch.object(helper.sys, "stderr", io.StringIO()), self.assertRaises(SystemExit):
            helper.main([])
        invoke.assert_not_called()

    def test_failed_naming_does_not_recreate_or_start_codex(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(helper.os.environ, {
                "HERDR_SOCKET_PATH": str(Path(directory) / "test.sock")}), \
                patch.object(helper, "invoke", side_effect=[{"workspaces": []}, CREATED,
                    RuntimeError("rename failed")]) as invoke, patch.object(helper, "start_codex") as start:
            with self.assertRaisesRegex(RuntimeError, "rename failed"):
                helper.main(["--codex"])
        self.assertEqual(sum(call.args[1:3] == ("workspace", "create") for call in invoke.call_args_list), 1)
        start.assert_not_called()

    def test_simultaneous_shortcuts_allocate_distinct_names(self):
        labels = []

        def invoke(_herdr, *args):
            if args[:2] == ("workspace", "list"):
                return {"workspaces": [{"label": label} for label in labels]}
            if args[:2] == ("workspace", "create"):
                time.sleep(0.03)
                label = args[args.index("--label") + 1]
                labels.append(label)
                return dict(CREATED, workspace={"workspace_id": "w" + label, "label": label})
            return {}

        with tempfile.TemporaryDirectory() as directory, patch.object(helper, "invoke", side_effect=invoke), \
                ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(helper.create_workspace, "herdr", str(Path(directory) / "test.sock"),
                                       focus=False) for _ in range(2)]
            for future in futures:
                future.result(timeout=3)
        self.assertEqual(labels, ["01", "02"])


class StartupTests(unittest.TestCase):
    def test_busy_shell_retries_same_pane_and_name(self):
        with patch.object(helper, "invoke", side_effect=[
            BUSY, {"pane": PANE}, BUSY, {"pane": PANE}, STARTED
        ]) as invoke, patch.object(helper.time, "sleep"):
            self.assertEqual(helper.start_codex("herdr", PANE, "codex-test"), STARTED)
        starts = [c.args for c in invoke.call_args_list if c.args[1] == "agent"]
        self.assertEqual(len(starts), 3)
        self.assertTrue(all(c == starts[0] for c in starts))

    def test_changed_terminal_prevents_retry(self):
        changed = dict(PANE, terminal_id="replacement")
        with patch.object(helper, "invoke", side_effect=[BUSY, {"pane": changed}]) as invoke, \
                patch.object(helper.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "changed"):
                helper.start_codex("herdr", PANE, "codex-test")
        self.assertEqual(invoke.call_count, 2)

    def test_existing_agent_prevents_retry(self):
        occupied = dict(PANE, agent="codex")
        with patch.object(helper, "invoke", side_effect=[BUSY, {"pane": occupied}]) as invoke, \
                patch.object(helper.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "already has an agent"):
                helper.start_codex("herdr", PANE, "codex-test")
        self.assertEqual(invoke.call_count, 2)

    def test_shell_wait_is_bounded(self):
        with patch.object(helper, "invoke", side_effect=BUSY) as invoke, \
                patch.object(helper.time, "monotonic", side_effect=[0, 1, 16]), \
                patch.object(helper.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "within 15 seconds"):
                helper.start_codex("herdr", PANE, "codex-test")
        self.assertEqual(invoke.call_count, 1)

    def test_uncertain_or_submitted_launch_is_not_retried(self):
        errors = [
            subprocess.TimeoutExpired("herdr", 45),
            helper.HerdrError("agent_not_ready", "startup dialog"),
            helper.HerdrError("timeout", "startup timed out"),
            RuntimeError("connection lost"),
        ]
        for error in errors:
            with self.subTest(error=error), \
                    patch.object(helper, "invoke", side_effect=error) as invoke:
                with self.assertRaises(type(error)):
                    helper.start_codex("herdr", PANE, "codex-test")
                self.assertEqual(invoke.call_count, 1)

    def test_workspace_created_only_once(self):
        responses = creation_replies() + [BUSY, {"pane": MANAGER}, STARTED]
        with tempfile.TemporaryDirectory() as directory, patch.dict(helper.os.environ, {
                "HERDR_SOCKET_PATH": str(Path(directory) / "test.sock")}), \
                patch.object(helper, "invoke", side_effect=responses) as invoke, \
                patch.object(helper.time, "sleep"), patch.object(helper.sys, "stdout", io.StringIO()):
            helper.main(["--codex"])
        creates = [c for c in invoke.call_args_list if c.args[1:3] == ("workspace", "create")]
        self.assertEqual(len(creates), 1)
        starts = [c.args for c in invoke.call_args_list if c.args[1:3] == ("agent", "start")]
        self.assertTrue(all(c[-1] == MANAGER["pane_id"] for c in starts))

    def test_cli_error_code_is_parsed_without_matching_message_text(self):
        result = subprocess.CompletedProcess([], 1, "", '{"error":{"code":"agent_pane_busy","message":"busy"}}')
        with patch.object(helper.subprocess, "run", return_value=result):
            with self.assertRaises(helper.HerdrError) as raised:
                helper.invoke("herdr", "agent", "start")
        self.assertEqual(raised.exception.code, "agent_pane_busy")
        result = subprocess.CompletedProcess([], 1, "", "connection failed: agent_pane_busy")
        with patch.object(helper.subprocess, "run", return_value=result):
            with self.assertRaises(RuntimeError) as raised:
                helper.invoke("herdr", "agent", "start")
        self.assertNotIsInstance(raised.exception, helper.HerdrError)


if __name__ == "__main__":
    unittest.main()
