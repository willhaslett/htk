import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("snapshot_source", ROOT / "libexec/snapshot_list/source.py")
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)
sys.path.insert(0, str(ROOT / "libexec/list_view"))
from list_model import parse_document
from list_watcher import Watcher
sys.path.pop(0)

TABLE = """| Thread | Act | Current state | Next action or dependency |
|---|:---:|---|---|
| **A1 — Research** [PR](https://example.test/pr/1) | <div align="center"><font color="#d4a72c">•</font></div> | In review with evidence | Read the review and decide. |
"""
ACT_FIRST_TABLE = """| Act | Thread | Current state | Next action or dependency |
|:---:|---|---|---|
| • | **A1 — Research** [PR](https://example.test/pr/1) | In review with evidence | Read the review and decide. |
"""

HIERARCHICAL_TABLE = """| Act | Thread | Current state | Next action or dependency |
|:---:|---|---|---|
|  | **A — Research** | In progress |  |
|  | &emsp;↳ [A1 — Gather evidence](https://example.test/evidence) | Complete |  |
| <div align="center"><font color="#d4a72c">•</font></div> | &emsp;↳ A2 — Choose approach | Ready for decision | Pick an approach |
|  | **B — Delivery** | Waiting |  |
|  | &emsp;↳ B1 — Implement | Blocked on A2 | Start after the decision |
"""


def wait_for(predicate):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError("snapshot source did not reach the expected state")


class SnapshotTests(unittest.TestCase):
    def test_all_status_reuses_configured_command_and_leaves_no_argument_discovery_intact(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fixture = directory / 'shared source ; literal.json'
            document = {'version': 1, 'title': 'Existing overall status',
                        'columns': [{'key': 'task', 'label': 'Task'}, {'key': 'next', 'label': 'Next / waiting'}],
                        'sections': [{'title': 'Renamable workspace', 'rows': [{'cells': {
                            'task': {'text': 'Review', 'href': 'https://example.test/review'},
                            'next': {'text': 'Will: review now', 'tone': 'error'}}}]}]}
            original = json.dumps(document, indent=2) + '\n'
            fixture.write_text(original)
            provider = directory / 'existing renderer.py'
            provider.write_text('from pathlib import Path\nimport sys\nsys.stdout.write(Path(sys.argv[1]).read_text())\n')
            config = directory / 'toolkit.toml'
            config.write_text('schema = 1\n[profile]\nname = "test"\n'
                              '[workspaces.example]\nstatus = "example.md"\n'
                              '[views.status]\nall_command = ' + json.dumps([sys.executable, str(provider), str(fixture)]) + '\n')
            env = {**os.environ, 'HTK_CONFIG': str(config)}
            for command in ([str(ROOT / 'bin/htk'), 'status'], [str(ROOT / 'bin/status-list')],
                            [str(ROOT / 'bin/snapshot-list')]):
                with self.subTest(command=command):
                    structured = subprocess.run([*command, '--all', '--json'], env=env,
                                                capture_output=True, text=True, timeout=5)
                    self.assertEqual(structured.returncode, 0, structured.stderr)
                    self.assertEqual(structured.stdout, original)
                    self.assertEqual(structured.stderr, '')
                    plain = subprocess.run([*command, '--all', '--once'], env=env,
                                           capture_output=True, text=True, timeout=5)
                    self.assertEqual(plain.returncode, 0, plain.stderr)
                    self.assertIn('Existing overall status', plain.stdout)
                    self.assertIn('Will: review now', plain.stdout)
                    discovery = subprocess.run(command, env=env, capture_output=True, text=True, timeout=5)
                    self.assertEqual(discovery.returncode, 0, discovery.stderr)
                    self.assertIn('--all', discovery.stdout)
                    self.assertIn('example.md', discovery.stdout)
                    self.assertNotIn('Will: review now', discovery.stdout)
            self.assertEqual(fixture.read_text(), original)

    def test_all_status_reports_missing_configuration_and_rejects_file_only_options(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / 'toolkit.toml'
            config.write_text('schema = 1\n[profile]\nname = "test"\n')
            env = {**os.environ, 'HTK_CONFIG': str(config)}
            base = [str(ROOT / 'bin/htk'), 'status', '--all']
            missing = subprocess.run([*base, '--json'], env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(missing.returncode, 1)
            self.assertEqual(missing.stdout, '')
            self.assertIn('not configured', missing.stderr)
            for arguments in (['example.md'], ['--title', 'Other'], ['--hide-completed'], ['--unknown-option']):
                result = subprocess.run([*base, *arguments], env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertIn('--help', result.stderr)
                self.assertIn('options:', result.stderr)
                self.assertNotIn('Traceback', result.stderr)

    def test_status_projects_persisted_work_records(self):
        tasks = {"version": 1, "title": "Workspace work", "tasks": [
            {"id": "T04", "title": "Review", "state": "Planned", "next": "Read the demo"},
            {"id": "T02", "title": "Earlier work", "state": "Done"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "STATUS.json"
            original = json.dumps(tasks)
            path.write_text(original)
            env = os.environ.copy()

            def run(command, *args):
                return subprocess.run([str(ROOT / "bin" / command), str(path), *args],
                                      env=env, capture_output=True, text=True, timeout=5)

            status = run("status-list", "--hide-completed", "--json")
            self.assertEqual(status.returncode, 0, status.stderr)
            projected = json.loads(status.stdout)
            self.assertEqual(projected["title"], "Workspace work")
            self.assertEqual(len(projected["sections"][0]["rows"]), 1)
            human = run("status-list", "--hide-completed", "--once")
            self.assertEqual(human.returncode, 0, human.stderr)
            self.assertIn("T04", human.stdout)
            self.assertNotIn('"tasks"', human.stdout)
            self.assertNotIn("Earlier work", human.stdout)
            self.assertEqual(path.read_text(), original)

    def test_work_stream_headers_preserve_legacy_reports(self):
        for legacy in (TABLE, ACT_FIRST_TABLE, HIERARCHICAL_TABLE):
            renamed = legacy.replace("Thread", "Work stream")
            with self.subTest(table=legacy):
                self.assertEqual(source.newest_table(renamed), source.newest_table(legacy))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "STATUS.md"
            path.write_text(HIERARCHICAL_TABLE.replace("Thread", "Work stream"))
            result = subprocess.run([str(ROOT / "bin/status-list"), str(path), "--once"],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Work stream", result.stdout)
            self.assertIn("A — Research", result.stdout)

    def test_work_stream_children_keep_indentation_links_actions_and_source_order(self):
        for marker in ("&emsp;↳ ", "↳ "):
            with self.subTest(marker=marker):
                _, rows = source.newest_table(HIERARCHICAL_TABLE.replace("&emsp;↳ ", marker))
                self.assertEqual([row["cells"]["thread"]["text"] for row in rows], [
                    "A — Research", "  ↳ A1 — Gather evidence", "  ↳ A2 — Choose approach",
                    "B — Delivery", "  ↳ B1 — Implement"])
                self.assertEqual(rows[1]["cells"]["thread"]["href"], "https://example.test/evidence")
                self.assertEqual([row["cells"]["act"]["text"] for row in rows], ["", "", "•", "", ""])
                self.assertEqual(rows[2]["cells"]["act"]["tone"], "warning")
                self.assertEqual(rows[2]["cells"]["next"]["text"], "Pick an approach")

    def test_hierarchical_status_command_preserves_visible_indent_and_centered_act(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "STATUS.md"
            path.write_text(HIERARCHICAL_TABLE)
            before = path.read_bytes()
            result = subprocess.run([str(ROOT / "bin/status-list"), str(path), "--once"],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = result.stdout.splitlines()
            parent = next(line for line in lines if "A — Research" in line)
            child = next(line for line in lines if "↳ A2" in line)
            parent_x = parent.index("A — Research")
            self.assertEqual(child.index("↳ A2"), parent_x + 2)
            self.assertEqual(parent[:3], "   ")
            self.assertEqual(child[:3], " • ")
            self.assertEqual(path.read_bytes(), before)

    def test_grouped_work_stream_links_keep_separate_targets_in_supplemental_rows(self):
        grouped = "**A3/A4 — Reports** [A3](https://example.test/pr/3), [A4](https://example.test/pr/4)"
        table = TABLE.replace("**A1 — Research** [PR](https://example.test/pr/1)", grouped)
        _, rows = source.newest_table(table)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["cells"]["thread"], {"text": "A3/A4 — Reports A3, A4"})
        self.assertEqual(rows[0]["cells"]["act"]["text"], "•")
        for row, label, target in zip(rows[1:], ("A3", "A4"), ("3", "4")):
            self.assertEqual(row["cells"]["thread"], {"text": "  ↳ " + label, "href": "https://example.test/pr/" + target})
            self.assertTrue(all(row["cells"][key] == {"text": ""} for key in ("act", "state", "next")))

    def test_multilinks_stay_in_their_column_and_repeated_targets_do_not_duplicate(self):
        text = TABLE.replace("Read the review and decide.",
                             "Read [review](https://example.test/review) and https://example.test/evidence.")
        _, rows = source.newest_table(text)
        self.assertNotIn("href", rows[0]["cells"]["next"])
        self.assertIn("href", rows[0]["cells"]["thread"])
        self.assertEqual([row["cells"]["next"]["href"] for row in rows[1:]],
                         ["https://example.test/review", "https://example.test/evidence"])
        self.assertTrue(all(row["cells"]["thread"] == {"text": ""} for row in rows[1:]))
        duplicate = "[A3](https://example.test/pr/3) and [again](https://example.test/pr/3)"
        result, links = source.cell_links(duplicate)
        self.assertEqual(result["href"], "https://example.test/pr/3")
        self.assertEqual(len(links), 1)

    def test_newest_entry_only_and_template_markup_links(self):
        text = "# Project snapshot archive\n\n**[Project snapshot — today](/archive.md)**\n" + TABLE
        text += "\n---\n**[Project snapshot — yesterday](/archive.md)**\n" + TABLE.replace("A1", "B2")
        heading, rows = source.newest_table(text)
        self.assertEqual(heading, "Project snapshot — today")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cells"]["thread"], {"text": "A1 — Research PR", "href": "https://example.test/pr/1"})
        self.assertEqual(rows[0]["cells"]["act"], {"text": "•", "tone": "warning"})
        self.assertNotIn("B2", json.dumps(rows))

    def test_incomplete_new_entry_never_falls_back_to_old(self):
        text = "**[Project snapshot — today](/archive.md)**\nSaving…\n\n"
        text += "**[Project snapshot — yesterday](/archive.md)**\n" + TABLE
        with self.assertRaisesRegex(ValueError, "newest entry"):
            source.newest_table(text)

    def test_status_headings_preserve_newest_entry_boundary_with_legacy_history(self):
        old = "**[Project snapshot — yesterday](/archive.md)**\n" + TABLE.replace("A1", "B2")
        for heading in ("**[Project status — today](/STATUS.md)**", "## Project status — today",
                        "**[Workspace status — today](/STATUS.md)**", "## Workspace status — today",
                        "**[Workspace snapshot — today](/STATUS.md)**"):
            with self.subTest(heading=heading):
                label, rows = source.newest_table(heading + "\n" + TABLE + "\n" + old)
                self.assertIn("today", label)
                self.assertIn("A1", json.dumps(rows))
                self.assertNotIn("B2", json.dumps(rows))
                with self.assertRaisesRegex(ValueError, "newest entry"):
                    source.newest_table(heading + "\nSaving…\n" + old)

    def test_legacy_headers_empty_table_and_fenced_examples(self):
        text = "```markdown\n" + TABLE + "```\n\n## 2026-10-21 — Checkpoint\n"
        text += "| Work | Current position |\n|---|---|\n| B2 | Waiting |\n"
        heading, rows = source.newest_table(text)
        self.assertEqual(rows[0]["cells"]["thread"]["text"], "B2")
        self.assertEqual(rows[0]["cells"]["act"]["text"], "")
        self.assertEqual(rows[0]["cells"]["next"]["text"], "")
        self.assertIn("2026-10-21", heading)
        _, rows = source.newest_table("\n".join(TABLE.splitlines()[:2]))
        self.assertEqual(rows, [])

    def test_escaped_pipes_code_and_local_evidence_remain_text(self):
        self.assertEqual(source.cell("[evidence](https://example.test/Thing_(topic))"),
                         {"text": "evidence", "href": "https://example.test/Thing_(topic)"})
        text = TABLE.replace("In review with evidence", r"Compare `a|b` and x\|y")
        text = text.replace("Read the review and decide.", "Read [assessment](/tmp/evidence.md).")
        _, rows = source.newest_table(text)
        self.assertEqual(rows[0]["cells"]["state"]["text"], "Compare a|b and x|y")
        self.assertEqual(rows[0]["cells"]["next"]["text"], "Read assessment (/tmp/evidence.md).")
        self.assertNotIn("href", rows[0]["cells"]["next"])
        with self.assertRaisesRegex(ValueError, "cells; expected"):
            source.newest_table(TABLE + "| A2 | missing columns |\n")

    def test_source_document_and_command_leave_file_unchanged(self):
        for table in (ACT_FIRST_TABLE, TABLE):
            with self.subTest(table=table), tempfile.TemporaryDirectory() as directory:
                self.check_source_document_and_command(directory, table)

    def check_source_document_and_command(self, directory, table):
        path = Path(directory) / "snapshot with spaces.md"
        path.write_text("# My project\n" + table)
        before = path.read_bytes()
        document = source.document(path)
        parsed = parse_document(json.dumps(document))
        self.assertEqual(parsed.title, "My project")
        self.assertEqual(parsed.columns[0].key, "act")
        self.assertEqual(parsed.sections[0].rows[0].cells[0].text, "•")
        self.assertEqual(parsed.sections[0].rows[0].cells[1].href, "https://example.test/pr/1")
        self.assertTrue(all(c.wrap for c in parsed.columns if c.key != "act"))
        command = [str(ROOT / "bin/snapshot-list"), str(path), "--title", "Snapshot", "--once"]
        result = subprocess.run(command, capture_output=True, text=True, timeout=5,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Snapshot", result.stdout)
        self.assertIn("A1", result.stdout)
        self.assertIn("decide.", result.stdout)
        header = next(line for line in result.stdout.splitlines() if "Current state" in line)
        self.assertTrue(header.startswith("Act"), header)
        row = next(line for line in result.stdout.splitlines() if "A1" in line)
        self.assertEqual(row[:3], " • ", row)
        self.assertNotIn("\x1b", result.stdout)
        self.assertEqual(before, path.read_bytes())

    def test_shared_watcher_keeps_last_good_on_missing_or_partial_save_and_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.md"
            path.write_text(TABLE)
            command = [sys.executable, str(ROOT / "libexec/snapshot_list/viewer.py"), str(path), "--json"]
            watcher = Watcher(command, refresh=.08, timeout=2)
            watcher.start()
            try:
                wait_for(lambda: watcher.snapshot().document is not None)
                original = watcher.snapshot().document
                path.unlink()
                wait_for(lambda: watcher.snapshot().error is not None)
                self.assertIs(watcher.snapshot().document, original)
                path.write_text("**[Project snapshot — today](/archive.md)**\nPartial write")
                wait_for(lambda: "newest entry" in (watcher.snapshot().error or ""))
                self.assertIs(watcher.snapshot().document, original)
                temporary = path.with_suffix(".tmp")
                temporary.write_text(TABLE.replace("A1", "C3"))
                os.replace(temporary, path)
                wait_for(lambda: watcher.snapshot().error is None and
                         watcher.snapshot().document.sections[0].rows[0].cells[1].text.startswith("C3"))
            finally:
                watcher.close()


if __name__ == "__main__":
    unittest.main()
