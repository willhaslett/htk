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
sys.path.insert(0, str(ROOT / "libexec"))
from snapshot_list.task_data import parse_tasks
spec = importlib.util.spec_from_file_location("status_records", ROOT / "libexec/snapshot_list/work_records.py")
viewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(viewer)


def load_renderer():
    directory = ROOT / "libexec/list_view"
    spec = importlib.util.spec_from_file_location("status_test_renderer", directory / "viewer.py")
    renderer = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(renderer)
    finally:
        sys.path.pop(0)
    return renderer


class StatusRecordTests(unittest.TestCase):
    def setUp(self):
        configuration = tempfile.TemporaryDirectory()
        self.addCleanup(configuration.cleanup)
        environment = patch.dict(os.environ, {"XDG_CONFIG_HOME": configuration.name})
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("DAILY_TOOLKIT_CONFIG", None)





    def test_json_keeps_full_targets_markers_hierarchy_and_all_links(self):
        url = "https://example.com/" + "a" * 80
        text = ("# Project\n## Active\n"
                "- [ ] A1 [BLOCKED: B2] — Review [first](https://example.com/a) and [second](https://example.com/b).\n"
                "  - [x] A2 [DONE] — Child task\n"
                "- B1 [A1] — An ID is not a status\n"
                "- Today\n  - Ask someone " + url + "\n"
                "\nHigh level\n- Launch\n   https://example.com/details\n")
        document = viewer.source_document(text, "Tasks", Path("work"))
        self.assertEqual([section["title"] for section in document["sections"]],
                         ["Project / Active", "- Today", "High level"])
        self.assertTrue(all(section["show_count"] is False for section in document["sections"]))
        self.assertTrue(document["columns"][0]["wrap"])
        cells = [row["cells"]["task"] for section in document["sections"] for row in section["rows"]]
        self.assertEqual(cells[0]["text"], "- [ ] A1 [BLOCKED: B2] — Review first and second.")
        self.assertEqual(cells[0]["tone"], "warning")
        self.assertEqual(cells[1]["href"], "https://example.com/a")
        self.assertEqual(cells[2]["href"], "https://example.com/b")
        self.assertEqual(cells[3]["text"], "  - [x] A2 [DONE] — Child task")
        self.assertEqual(cells[3]["tone"], "muted")
        self.assertEqual(cells[4]["tone"], "normal")
        self.assertEqual(viewer.source_tone("[READY2] — A work ID"), "normal")
        self.assertEqual(viewer.source_tone("[READYMADE] — A label"), "normal")
        self.assertEqual(cells[5]["href"], url)
        self.assertEqual(cells[5]["text"], "  - Ask someone")
        self.assertEqual(cells[7]["text"], "   https://example.com/details")
        self.assertNotIn("\x1b", json.dumps(document))

    def test_json_provider_and_plain_nonterminal_modes_leave_source_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.md"
            text = "# Today\n- [ ] A1 — [Read guide](https://example.com/guide)\n"
            path.write_text(text)
            result = subprocess.run([str(ROOT / "bin/status-list"), str(path), "--title", "MY TASKS", "--json"],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            document = json.loads(result.stdout)
            self.assertEqual(document["title"], "MY TASKS")
            cell = document["sections"][0]["rows"][0]["cells"]["task"]
            self.assertEqual(cell["text"], "- [ ] A1 — Read guide")
            self.assertEqual(cell["href"], "https://example.com/guide")
            plain = subprocess.run([str(ROOT / "bin/status-list"), str(path)],
                                   capture_output=True, text=True, timeout=5)
            self.assertEqual(plain.returncode, 0, plain.stderr)
            self.assertIn("A1", plain.stdout)
            self.assertNotIn("\x1b", plain.stdout)
            self.assertEqual(path.read_text(), text)
            path.unlink()
            error = subprocess.run([str(ROOT / "bin/status-list"), str(path), "--json"],
                                   capture_output=True, text=True, timeout=5)
            self.assertNotEqual(error.returncode, 0)
            self.assertEqual(error.stdout, "")
            self.assertIn("Status unavailable", error.stderr)


    def test_task_data_projects_stable_ids_children_states_and_links(self):
        source = {"version": 1, "title": "Work", "tasks": [
            {"id": "T01", "title": "Launch", "state": "Planned", "notes": "Detailed planning",
             "children": [{"id": "T04", "title": "Review", "state": "Blocked",
                           "next": "[Read guide](https://example.com/guide)"}]},
            {"id": "T02", "title": "Earlier work", "state": "Done"},
            {"id": "T03", "title": "Old plan", "state": "Canceled"}]}
        text = json.dumps(source)
        data = parse_tasks(text)
        projected = viewer.task_document(data, None, Path("tasks.json"), hide_completed=True)
        rows = projected["sections"][0]["rows"]
        self.assertEqual(projected["title"], "Work")
        self.assertEqual(rows[0]["cells"]["task"], "T01  Launch")
        self.assertEqual(rows[1]["cells"]["task"], "  ↳ T04  Review")
        self.assertEqual(rows[1]["cells"]["next"]["href"], "https://example.com/guide")
        self.assertEqual(rows[1]["cells"]["state"]["tone"], "warning")
        self.assertEqual(len(rows), 2)
        self.assertEqual(json.dumps(data), text)  # Filtering only affects the view.
        all_tasks = viewer.task_document(data, "Override", Path("tasks.json"))
        self.assertEqual(all_tasks["title"], "Override")
        self.assertEqual(len(all_tasks["sections"][0]["rows"]), 4)
        renderer = load_renderer()
        document = renderer.parse_document(json.dumps(projected))
        self.assertIn("T04", renderer.plain(document))
        self.assertNotIn("Detailed planning", renderer.plain(document))
        source["tasks"][0]["state"] = "Completed"
        projected = viewer.task_document(source, None, Path("tasks.json"), hide_completed=True)
        self.assertEqual(len(projected["sections"][0]["rows"]), 2)  # Keep the parent as context.

    def test_invalid_task_data_reports_actionable_errors(self):
        task = {"id": "T01", "title": "Review", "state": "Planned"}
        cases = [
            ("{", "invalid task JSON"),
            (json.dumps({"version": True, "title": "Work", "tasks": []}), "version must be 1"),
            (json.dumps({"version": 1, "title": "Work", "tasks": [task, task]}), "duplicate task ID"),
            (json.dumps({"version": 1, "title": "Work", "tasks": [{**task, "children": [task]}]}), "duplicate task ID"),
            (json.dumps({"version": 1, "title": "Work", "tasks": [{**task, "next": []}]}), "next must be a string"),
            (json.dumps({"version": 1, "title": "Work", "tasks": [{**task, "id": " T01"}]}), "whitespace"),
            (json.dumps({"version": 1, "title": "Work", "tasks": [{**task, "state": " "}]}), "nonempty string"),
            (json.dumps({"version": 1, "title": "Work", "tasks": [{**task, "children": {}}]}), "children must be an array"),
        ]
        for text, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                parse_tasks(text)

    def test_task_json_cli_prints_human_output_or_projection_without_writing_source(self):
        source = {"version": 1, "title": "MY TASK DATA", "tasks": [
            {"id": "T04", "title": "CURRENT_TASK", "state": "Investigating", "next": "Prove the behavior"},
            {"id": "T02", "title": "DONE_TASK", "state": "Done", "notes": "Historical detail"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            text = json.dumps(source)
            path.write_text(text)
            env = {key: value for key, value in os.environ.items()
                   if key != "HERDR_PANE_ID"}
            command = [str(ROOT / "bin/status-list"), str(path), "--hide-completed"]
            plain = subprocess.run(command + ["--once"], capture_output=True, text=True, env=env, timeout=5)
            self.assertEqual(plain.returncode, 0, plain.stderr)
            self.assertIn("MY TASK DATA", plain.stdout)
            self.assertIn("CURRENT_TASK", plain.stdout)
            self.assertNotIn("DONE_TASK", plain.stdout)
            self.assertNotIn('"tasks"', plain.stdout)
            self.assertNotIn("\x1b", plain.stdout)
            projection = subprocess.run(command + ["--json"], capture_output=True, text=True, env=env, timeout=5)
            self.assertEqual(projection.returncode, 0, projection.stderr)
            self.assertIn("sections", json.loads(projection.stdout))
            self.assertNotIn("tasks", json.loads(projection.stdout))
            self.assertEqual(path.read_text(), text)
            path.write_text("{")
            error = subprocess.run(command + ["--json"], capture_output=True, text=True, env=env, timeout=5)
            self.assertNotEqual(error.returncode, 0)
            self.assertEqual(error.stdout, "")
            self.assertIn("invalid task JSON", error.stderr)
            self.assertEqual(path.read_text(), "{")

    def test_task_json_provider_watches_edits_and_recovers_from_partial_saves(self):
        renderer = load_renderer()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            source = {"version": 1, "title": "Work", "tasks": [
                {"id": "T04", "title": "Review", "state": "Planned", "next": "Prove it"}]}
            path.write_text(json.dumps(source))
            watcher = renderer.Watcher(command=[sys.executable, str(ROOT / "libexec/snapshot_list/viewer.py"),
                                               str(path), "--json"], refresh=0.05, timeout=3)
            watcher.start()

            def wait_for(predicate):
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    snapshot = watcher.snapshot()
                    if predicate(snapshot):
                        return snapshot
                    time.sleep(0.02)
                self.fail(f"timed out waiting for task projection: {watcher.snapshot()}")

            try:
                original = wait_for(lambda snapshot: snapshot.document is not None)
                self.assertEqual(original.document.sections[0].rows[0].cells[1].text, "Planned")
                path.write_text("{")
                stale = wait_for(lambda snapshot: snapshot.error is not None)
                self.assertEqual(stale.document, original.document)
                self.assertIn("invalid task JSON", stale.error)
                source["tasks"][0].update(state="Done", next="Validated")
                replacement = path.with_suffix(".tmp")
                replacement.write_text(json.dumps(source))
                replacement.replace(path)
                recovered = wait_for(lambda snapshot: snapshot.error is None and snapshot.document
                                     and snapshot.document.sections[0].rows[0].cells[1].text == "Done")
                row = recovered.document.sections[0].rows[0]
                self.assertEqual(row.cells[0].text, "T04  Review")
                self.assertEqual(row.cells[2].text, "Validated")
                self.assertEqual(json.loads(path.read_text()), source)
            finally:
                watcher.close()

    def test_shared_terminal_pipeline_scrolls_links_polls_and_recovers_atomically(self):
        wrapper = """
import fcntl, os, subprocess, sys, termios
os.setsid()
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
before = termios.tcgetattr(0)
child = subprocess.Popen(sys.argv[1:])
code = child.wait()
after = termios.tcgetattr(0)
before[3] &= ~getattr(termios, 'PENDIN', 0)
after[3] &= ~getattr(termios, 'PENDIN', 0)
sys.exit(code if before == after else 125)
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "work.md"
            source = ("# Shared tasks\n- [ ] A1 — [FIRST_LINKED_TASK](https://example.com/first)\n"
                      + "   https://example.com/indented\n"
                      + "\n".join(f"- A{index} — Task {index}" for index in range(2, 26))
                      + "\n- A26 — LAST_TASK\n")
            path.write_text(source)
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 14, 90, 0, 0))
            env = {**os.environ, "TERM": "xterm-256color", "PYTHONDONTWRITEBYTECODE": "1"}
            env.pop("HERDR_PANE_ID", None)
            process = subprocess.Popen([sys.executable, "-c", wrapper, str(ROOT / "bin/status-list"),
                str(path), "--title", "ADAPTER TEST"], stdin=slave, stdout=slave, stderr=slave, env=env)
            output = bytearray()

            def read_until(needle, start=0, timeout=4):
                deadline = time.monotonic() + timeout
                while needle not in output[start:] and time.monotonic() < deadline:
                    if select.select([master], [], [], 0.05)[0]:
                        output.extend(os.read(master, 65536))
                    if process.poll() is not None:
                        break
                self.assertIn(needle, output[start:])

            try:
                read_until(b"FIRST_LINKED_TASK")
                read_until(b"\x1b]8;;https://example.com/first\x1b\\")
                indented_link = (b"\x1b]8;;https://example.com/indented\x1b\\"
                                 b"https://example.com/indented\x1b]8;;\x1b\\")
                read_until(indented_link)
                self.assertIn(b"ADAPTER TEST", output)
                self.assertNotIn(b"LAST_TASK", output)
                start = len(output)
                os.write(master, b"G")
                read_until(b"LAST_TASK", start)
                start = len(output)
                os.write(master, b"g")
                read_until(b"FIRST_LINKED_TASK", start)
                self.assertEqual(path.read_text(), source)
                start = len(output)
                path.unlink()
                read_until(b"STALE", start)
                # A failed refresh changes status only. The previous table stays
                # on screen instead of being cleared and retransmitted.
                self.assertNotIn(b"\x1b[2J", output[start:])
                previous_frame = output[output.rfind(b"\x1b[2J", 0, start):start]
                self.assertIn(b"FIRST_LINKED_TASK", previous_frame)
                replacement = path.with_suffix(".tmp")
                replacement.write_text("# Recovered\n- B1 [READY] — AFTER_ATOMIC_SAVE\n")
                start = len(output)
                replacement.replace(path)
                read_until(b"AFTER_ATOMIC_SAVE", start)
                recovered_frame = output[output.rfind(b"\x1b[2J", start):]
                self.assertNotIn(b"STALE", recovered_frame)
                self.assertNotIn(b"https://example.com/first", recovered_frame)
                os.write(master, b"q")
                deadline = time.monotonic() + 2
                while process.poll() is None and time.monotonic() < deadline:
                    if select.select([master], [], [], 0.05)[0]:
                        output.extend(os.read(master, 65536))
                self.assertEqual(process.poll(), 0, output)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)
                os.close(master)
                os.close(slave)


if __name__ == "__main__":
    unittest.main()
