import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libexec"))
from github_pr_status import presentation, provider
sys.path.pop(0)
sys.path.insert(0, str(ROOT / "libexec/list_view"))
from list_model import parse_document
sys.path.pop(0)
spec = importlib.util.spec_from_file_location("github_fixture", ROOT / "tests/fixtures/github_pr_status.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class GitHubPRTests(unittest.TestCase):
    def test_github_assignments_drafts_reviews_and_head_checks_are_distinct(self):
        source = fixture.pr(7, isDraft=True, reviewDecision="APPROVED",
            assignees={"nodes": [{"login": "alice"}], "pageInfo": {"hasNextPage": False}},
            reviewRequests={"totalCount": 2},
            commits={"nodes": [{"commit": {"oid": "head-7", "statusCheckRollup": {"state": "SUCCESS"}}}]})
        record = provider.github_pr(source, "github.com")
        cells = presentation.row(record)["cells"]
        self.assertEqual(cells["reviewers"], {"text": "alice", "tone": "warning"})
        self.assertEqual(cells["status"], {"text": "Draft", "tone": "warning"})
        self.assertEqual(cells["build"], {"text": "Passed", "tone": "success"})
        self.assertEqual(cells["pr"], {"text": "7", "href": source["url"]})
        self.assertEqual(provider.build_summary({**source, "headRefOid": "different"}), ("Unknown head", "warning"))

    def test_requested_reviewers_do_not_become_assignees(self):
        source = fixture.pr(1, reviewRequests={"totalCount": 1})
        record = provider.github_pr(source, "github.com")
        document = presentation.make_list({"open": [record], "reviews": []}, [], [], "alice", "example")
        self.assertEqual(document["sections"][0]["rows"][0]["cells"]["status"]["text"], "In review")
        self.assertEqual(document["sections"][0]["rows"][0]["cells"]["reviewers"]["text"], "")
        self.assertEqual(document["sections"][1]["rows"], [])
        self.assertEqual(presentation.status(provider.github_pr(fixture.pr(2), "github.com")), ("Open", "normal"))

    def test_history_has_blank_build_and_closed_lifecycle_overrides_draft(self):
        source = fixture.pr(4, state="CLOSED", isDraft=True, closedAt="2026-10-01T00:00:00Z")
        record = provider.history_pr(source, "github.com")
        self.assertEqual(presentation.status(record), ("Closed", "muted"))
        self.assertEqual(presentation.row(record)["cells"]["build"]["text"], "")
        with self.assertRaisesRegex(ValueError, "100 assignees"):
            provider.github_pr({**source, "assignees": {"pageInfo": {"hasNextPage": True}}}, "github.com")

    def test_current_paginates_and_deduplicates(self):
        calls = []
        def fetch(host, query, cursor):
            self.assertIn("assignee:alice", query)
            calls.append(cursor)
            return fixture.page([fixture.pr(1)], "next", 2) if cursor is None else fixture.page([fixture.pr(1)], total=2)
        self.assertEqual(len(provider.current_prs("github.com", "alice", "assignee", fetch)), 1)
        self.assertEqual(calls, [None, "next"])

    def test_search_caps_and_cyclic_cursors_fail_instead_of_truncating(self):
        for function in (lambda fetch: provider.current_prs("github.com", "alice", "author", fetch),
                         lambda fetch: provider.recent_history("github.com", "alice", True, fetch)):
            with self.subTest(function=function):
                with self.assertRaisesRegex(ValueError, "ended before"):
                    function(lambda *args: fixture.page([fixture.pr(1)], total=1500))
                calls = []
                def cyclic(host, query, cursor):
                    calls.append(cursor)
                    return fixture.page([fixture.pr(1)], "a" if len(calls) % 2 else "b", 100)
                with self.assertRaisesRegex(ValueError, "search limit"):
                    function(cyclic)
                self.assertEqual(calls, [None, "a", "b"])

    def test_history_event_order_survives_old_commented_prs(self):
        def fetch(host, query, cursor):
            return fixture.response({"variables": {"query": query, "cursor": cursor}})
        result = provider.recent_history("github.com", "alice", True, fetch)
        self.assertEqual([pr["number"] for pr in result], [50, *range(30, 39)])

    def test_api_errors_are_redacted_and_malformed_pages_rejected(self):
        bad = [{"errors": [{"message": "private-service-error-token"}]}, [],
               {"data": {"search": {"nodes": [], "pageInfo": {}}}}, {"data": None}]
        for body in bad:
            with self.subTest(body=body), patch.object(provider.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(body), "")):
                with self.assertRaises(ValueError) as error:
                    provider.gh_page("github.com", "is:pr", None)
                self.assertNotIn("private-service-error-token", str(error.exception))
        with patch.object(provider.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "private-service-error-token", "private-service-error-token")):
            with self.assertRaisesRegex(ValueError, "check gh auth status"):
                provider.gh_page("github.com", "is:pr", None)

    def test_timeout_is_a_failed_refresh_without_traceback(self):
        with patch.object(provider, "emit", side_effect=subprocess.TimeoutExpired(["gh"], 20)), contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(provider.main(["--user", "alice", "--json"]), 1)
        self.assertIn("PR status unavailable", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_cli_with_ordinary_github_fixture_and_no_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            gh = Path(directory) / "gh"
            gh.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " +
                          shlex.quote(str(ROOT / "tests/fixtures/github_pr_status.py")) + ' "$@"\n')
            gh.chmod(0o755)
            env = {**os.environ, "PATH": directory + os.pathsep + os.environ.get("PATH", ""),
                   "XDG_CONFIG_HOME": directory}
            env.pop("DAILY_TOOLKIT_CONFIG", None)
            command = [str(ROOT / "bin/github-pr-status"), "--user", "alice"]
            result = subprocess.run([*command, "--json"], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            document = json.loads(result.stdout)
            parse_document(result.stdout)
            self.assertEqual([len(section["rows"]) for section in document["sections"]], [3, 2, 10, 1])
            self.assertEqual(document["sections"][2]["rows"][0]["cells"]["pr"]["text"], "50")
            result = subprocess.run([*command, "--once"], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("MY OPEN PRS", result.stdout)
            self.assertIn("ASSIGNED TO ME", result.stdout)
            self.assertNotIn("\x1b", result.stdout)
            for mode in ("error", "malformed"):
                result = subprocess.run([*command, "--json"], env={**env, "FAKE_GH_MODE": mode}, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 1)
                self.assertIn("PR status unavailable", result.stderr)
                self.assertNotIn("private-service-error-token", result.stderr)
                self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
