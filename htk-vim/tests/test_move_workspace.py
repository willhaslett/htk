"""Workspace reorder invariants; no live Herdr mutations."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "move_workspace", Path(__file__).resolve().parents[1] / "libexec/herdr_controls/workspace.py"
)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def workspace(name, repo=None, linked=False):
    value = {"workspace_id": name}
    if repo:
        value["worktree"] = {"repo_key": repo, "is_linked_worktree": linked}
    return value


class WorkspaceMoveTests(unittest.TestCase):
    def test_each_position_and_boundary_without_wrapping(self):
        original = [workspace(name) for name in "ABCD"]
        for index, source in enumerate("ABCD"):
            for direction, offset in [("up", -1), ("down", 1)]:
                with self.subTest(source=source, direction=direction):
                    plan = helper.move_plan(original, source, direction)
                    destination = index + offset
                    if not 0 <= destination < 4:
                        self.assertIsNone(plan)
                        continue
                    order = list("ABCD")
                    order.remove(source)
                    before = plan["before_workspace_id"]
                    order.insert(order.index(before) if before else len(order), source)
                    expected = list("ABCD")
                    expected[index], expected[destination] = expected[destination], expected[index]
                    self.assertEqual(order, expected)

    def test_linked_workspace_moves_the_whole_group(self):
        items = [workspace("A"), workspace("P", "repo"),
                 workspace("L1", "repo", True), workspace("L2", "repo", True),
                 workspace("B"), workspace("C")]
        for source in ["P", "L1", "L2"]:
            self.assertEqual(helper.move_plan(items, source, "up"), {
                "workspace_ids": ["P", "L1", "L2"], "before_workspace_id": "A"})
            self.assertEqual(helper.move_plan(items, source, "down"), {
                "workspace_ids": ["P", "L1", "L2"], "before_workspace_id": "C"})
        self.assertEqual(helper.move_plan(items, "A", "down")["before_workspace_id"], "B")
        self.assertEqual(helper.move_plan(items, "B", "up")["before_workspace_id"], "P")

    def test_missing_target_or_parent_fails(self):
        with self.assertRaisesRegex(ValueError, "no longer present"):
            helper.move_plan([workspace("A")], "missing", "down")
        with self.assertRaisesRegex(ValueError, "primary workspace is missing"):
            helper.move_plan([workspace("L", "repo", True)], "L", "up")

    def test_keyboard_context_overrides_the_servers_launching_pane(self):
        with tempfile.TemporaryDirectory() as directory:
            sock = str(Path(directory) / "server.sock")
            environment = {"HERDR_SOCKET_PATH": sock,
                           "HERDR_ACTIVE_WORKSPACE_ID": "B", "HERDR_WORKSPACE_ID": "A"}
            with patch.dict(os.environ, environment, clear=True), \
                    patch.object(helper, "request") as request:
                request.side_effect = [{"workspaces": [workspace(name) for name in "ABC"]}, {}]
                self.assertEqual(helper.main(["down"]), 0)
                self.assertEqual(request.call_args.args, (sock, "workspace.move_block", {
                    "workspace_ids": ["B"], "before_workspace_id": None}))

    def test_dry_run_never_sends_a_mutation(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {}, clear=True), \
                patch.object(helper, "request") as request, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            request.return_value = {"workspaces": [workspace(name) for name in "AB"]}
            self.assertEqual(helper.main(["down", "--workspace", "A", "--socket",
                                          str(Path(directory) / "server.sock"), "--dry-run"]), 0)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(json.loads(output.getvalue())["params"]["workspace_ids"], ["A"])


if __name__ == "__main__":
    unittest.main()
