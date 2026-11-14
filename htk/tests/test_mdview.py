import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mdview_app", ROOT / "libexec/mdview/mdview.py")
mdview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mdview)
PreviewServer = mdview.PreviewServer


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source = self.root / "notes.md"
        self.source.write_text("# Notes\n\nOriginal text.\n")
        self.server = PreviewServer(self.source)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.prefix = f"/{self.server.token}/"

    def tearDown(self):
        self.server.binding.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()

    def get(self, route, headers=None, use_token=True):
        connection = HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request("GET", (self.prefix if use_token else "/") + route, headers=headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_content_and_conditional_refresh(self):
        status, headers, body = self.get("content")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["markdown"], self.source.read_text())
        self.assertEqual(self.get("content", {"If-None-Match": headers["ETag"]})[0], 304)

    def test_atomic_save_and_missing_file_recovery(self):
        _, headers, _ = self.get("content")
        self.source.unlink()
        self.assertEqual(self.get("content")[0], 503)
        replacement = self.root / "replacement.md"
        replacement.write_text("# Notes\n\nChanged text.\n")
        replacement.replace(self.source)
        status, new_headers, body = self.get("content", {"If-None-Match": headers["ETag"]})
        self.assertEqual(status, 200)
        self.assertNotEqual(headers["ETag"], new_headers["ETag"])
        self.assertIn("Changed text.", json.loads(body)["markdown"])

    def test_assets_are_bundled(self):
        for asset in ["", "style.css", "viewer.js", "lifetime.js", "marked.js", "purify.js", "mermaid.js"]:
            with self.subTest(asset=asset):
                status, _, body = self.get(asset)
                self.assertEqual(status, 200)
                self.assertTrue(body)

    def test_diagram_edits_are_delivered_by_existing_watch_endpoint(self):
        self.source.write_text("```mermaid\nflowchart LR\nA --> B\n```\n")
        _, headers, body = self.get("content")
        self.assertIn("A --> B", json.loads(body)["markdown"])
        self.source.write_text("```mermaid\nflowchart LR\nA --> C\n```\n")
        status, updated, body = self.get("content", {"If-None-Match": headers["ETag"]})
        self.assertEqual(status, 200)
        self.assertNotEqual(updated["ETag"], headers["ETag"])
        self.assertIn("A --> C", json.loads(body)["markdown"])

    def test_diagram_styles_are_allowed_but_scripts_stay_local(self):
        _, headers, _ = self.get("")
        policy = headers["Content-Security-Policy"]
        self.assertIn("style-src 'self' 'unsafe-inline';", policy)
        self.assertIn("script-src 'self';", policy)

    def test_only_local_images_are_served(self):
        (self.root / "image.png").write_bytes(b"test image")
        self.assertEqual(self.get("local/image.png")[2], b"test image")
        self.assertEqual(self.get("local/notes.md")[0], 404)
        self.assertEqual(self.get("local/../mdview.py")[0], 404)
        (self.root / "outside.png").symlink_to(Path(__file__).resolve())
        self.assertEqual(self.get("local/outside.png")[0], 404)

    def test_local_diagram_changes_have_independent_etags(self):
        self.source.write_text("![Diagram](diagram.svg)\n")
        image = self.root / "diagram.svg"
        image.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>First</text></svg>')
        _, content_headers, _ = self.get("content")
        status, image_headers, _ = self.get("local/diagram.svg")
        self.assertEqual(status, 200)
        self.assertEqual(self.get("local/diagram.svg", {"If-None-Match": image_headers["ETag"]})[0], 304)
        image.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>Updated</text></svg>')
        self.assertEqual(self.get("content", {"If-None-Match": content_headers["ETag"]})[0], 304)
        status, updated_headers, body = self.get("local/diagram.svg", {"If-None-Match": image_headers["ETag"]})
        self.assertEqual(status, 200)
        self.assertNotEqual(updated_headers["ETag"], image_headers["ETag"])
        self.assertIn(b"Updated", body)

    def test_token_and_host_required(self):
        self.assertEqual(self.get("content", use_token=False)[0], 404)
        self.assertEqual(self.get("content", {"Host": "unrelated.example"})[0], 403)

    def post(self, route, data=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request("POST", self.prefix + route, body=json.dumps(data or {}), headers=headers or {})
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def stream(self, page):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request("GET", self.prefix + "events?page=" + page)
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
        self.addCleanup(connection.close)
        self.addCleanup(response.close)
        return response

    def test_browser_connection_and_close_release_binding(self):
        response = self.stream("window-one")
        self.assertEqual(response.readline(), b": connected\n")
        self.assertEqual(self.server.binding.phase(), "active")
        status, body = self.post("claim", {"open": True})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["action"], "reuse")
        self.assertEqual(self.post("leave?page=window-one")[0], 204)
        self.assertEqual(self.server.binding.phase(), "closing")
        self.assertEqual(self.get("events?page=window-one")[0], 410)
        self.server.binding.grace = 0
        self.server.binding.disconnect("window-one")
        self.assertEqual(self.server.binding.phase(), "expired")

    def test_reload_reuses_same_server_and_focus_is_delivered_to_page(self):
        self.stream("before-reload").readline()
        self.post("leave?page=before-reload")
        response = self.stream("after-reload")
        response.readline()
        response.readline()
        self.assertEqual(self.server.binding.phase(), "active")
        self.post("claim", {"open": True, "focus": True})
        # Skip an already-buffered keepalive if the request overlapped a write.
        while response.readline() != b"event: focus\n":
            pass
        self.assertEqual(response.readline(), b"data: {}\n")

    def test_stream_disconnect_without_pagehide_releases_binding(self):
        response = self.stream("crashed-page")
        response.readline()
        response.close()
        deadline = time.monotonic() + 5
        while self.server.binding.phase() == "active" and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.server.binding.phase(), "closing")

    def test_post_requires_token_host_and_same_origin(self):
        self.assertEqual(self.post("claim", headers={"Host": "other.example"})[0], 403)
        self.assertEqual(self.post("claim", headers={"Origin": "https://other.example"})[0], 403)
        prefix, self.prefix = self.prefix, "/"
        try:
            self.assertEqual(self.post("claim")[0], 404)
        finally:
            self.prefix = prefix


class WindowBindingTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.binding = mdview.WindowBinding(clock=lambda: self.now)

    def test_hidden_window_does_not_expire_when_timers_are_throttled(self):
        self.binding.claim(open_window=True)
        self.binding.connect("page")
        self.now += 86400
        self.assertEqual(self.binding.phase(), "active")
        self.assertEqual(self.binding.claim(open_window=True), "reuse")

    def test_only_one_launch_is_reserved_before_the_page_connects(self):
        self.assertEqual(self.binding.claim(open_window=True), "open")
        self.assertEqual(self.binding.claim(open_window=True), "reuse")
        self.now += 31
        self.assertEqual(self.binding.phase(), "expired")

    def test_reload_grace_and_reconnected_stream_identity(self):
        original = self.binding.connect("reload")
        replacement = self.binding.connect("reload")
        self.binding.disconnect("reload", original)
        self.assertEqual(self.binding.phase(), "active")
        self.binding.disconnect("reload", replacement)
        self.assertEqual(self.binding.phase(), "closing")
        self.now += 1
        self.binding.connect("new-page")
        self.now += 100
        self.assertEqual(self.binding.phase(), "active")
        self.binding.disconnect("new-page")
        self.now += 4
        self.assertEqual(self.binding.phase(), "expired")
        self.assertIsNone(self.binding.connect("too-late"))

    def test_failed_browser_open_can_be_retried(self):
        self.binding.claim(open_window=True)
        self.binding.open_failed()
        self.assertEqual(self.binding.claim(open_window=True), "open")


class DocumentRegistryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "notes.md"
        self.source.write_text("# Notes\n")
        self.registry = mdview.DocumentRegistry(self.source, self.root / "state")
        self.servers = []
        self.addCleanup(self.stop_servers)

    def stop_servers(self):
        for server in self.servers:
            server.binding.stop()
            server.shutdown()
            server.server_close()

    def acquire(self, **kwargs):
        url, server, should_open = mdview.acquire_preview(self.source, registry=self.registry, **kwargs)
        if server:
            self.servers.append(server)
        return url, server, should_open

    def test_simultaneous_requests_and_symlinks_share_one_window(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.acquire(), range(6)))
        self.assertEqual(len({result[0] for result in results}), 1)
        self.assertEqual(sum(result[2] for result in results), 1)
        self.assertEqual(len(self.servers), 1)
        alias = self.root / "alias.md"
        alias.symlink_to(self.source)
        alias_registry = mdview.DocumentRegistry(alias, self.registry.directory)
        self.assertEqual(alias_registry.key, self.registry.key)
        self.assertEqual(self.registry.record.stat().st_mode & 0o777, 0o600)

    def test_closed_window_gets_a_new_url_and_old_cleanup_preserves_it(self):
        first, server, _ = self.acquire()
        server.binding.connect("closed")
        server.binding.grace = 0
        server.binding.disconnect("closed")
        second, _, opens = self.acquire()
        self.assertNotEqual(second, first)
        self.assertTrue(opens)
        self.registry.remove(first)
        self.assertEqual(self.registry.read(), second)

    def test_no_open_can_later_open_the_same_served_document(self):
        url, _, opens = self.acquire(no_open=True)
        self.assertFalse(opens)
        reused, server, opens = self.acquire()
        self.assertEqual(reused, url)
        self.assertIsNone(server)
        self.assertTrue(opens)
        self.assertFalse(self.acquire()[2])

    def test_render_during_reload_keeps_the_original_window(self):
        original, server, _ = self.acquire()
        server.binding.connect("old-page")
        server.binding.disconnect("old-page")
        with ThreadPoolExecutor(max_workers=1) as pool:
            request = pool.submit(self.acquire)
            server.binding.connect("reloaded-page")
            url, owner, opens = request.result(timeout=3)
        self.assertEqual(url, original)
        self.assertIsNone(owner)
        self.assertFalse(opens)

    def test_closed_preview_can_immediately_reuse_an_explicit_port(self):
        original, server, _ = self.acquire()
        server.binding.grace = 0.05
        server.binding.connect("closed-page")
        finisher = threading.Thread(target=mdview.serve_preview, args=(server, self.registry), daemon=True)
        finisher.start()
        server.binding.disconnect("closed-page")
        url, replacement, opens = self.acquire(port=server.server_port)
        finisher.join(timeout=3)
        self.assertFalse(finisher.is_alive())
        self.assertEqual(replacement.server_port, server.server_port)
        self.assertNotEqual(url, original)
        self.assertTrue(opens)
        self.assertEqual(self.registry.read(), url)

    def test_corrupt_and_dead_records_recover(self):
        with self.registry.locked():
            self.registry.record.write_text("invalid JSON")
        self.assertIsNone(self.registry.read())
        with self.registry.locked():
            self.registry.write("http://127.0.0.1:1/old-token/")
        url, server, opens = self.acquire()
        self.assertIsNotNone(server)
        self.assertTrue(opens)
        self.assertEqual(self.registry.read(), url)

    def test_invalid_registry_urls_never_contact_another_host(self):
        for url in ["http://example.com/secret/", "https://127.0.0.1:42/secret/", "file:///tmp/a"]:
            with patch("window_binding.HTTPConnection") as connection:
                self.assertIsNone(self.registry.request(url, "binding"))
                connection.assert_not_called()

    def test_native_and_existing_browser_previews_keep_separate_bindings(self):
        native = mdview.DocumentRegistry(self.source, self.registry.directory, presentation="window")
        self.assertNotEqual(native.record, self.registry.record)
        with self.registry.locked():
            self.registry.write("http://127.0.0.1:1/browser/")
        with native.locked():
            native.write("http://127.0.0.1:2/window/")
        self.assertEqual(self.registry.read(), "http://127.0.0.1:1/browser/")
        self.assertEqual(native.read(), "http://127.0.0.1:2/window/")
        native.remove(native.read())
        self.assertIsNotNone(self.registry.read())

    def test_native_exit_releases_its_server_and_discovery_record(self):
        _, server, _ = self.acquire()
        window = Mock(returncode=0)
        window.poll.return_value = 0
        mdview.serve_preview(server, self.registry, window)
        self.assertFalse(self.registry.record.exists())
        self.assertEqual(server.binding.phase(), "expired")
        window.terminate.assert_not_called()

    def test_native_failure_also_releases_its_server(self):
        _, server, _ = self.acquire()
        window = Mock(returncode=1)
        window.poll.return_value = 1
        with self.assertRaisesRegex(OSError, "preview window exited"):
            mdview.serve_preview(server, self.registry, window)
        self.assertFalse(self.registry.record.exists())
        self.assertEqual(server.binding.phase(), "expired")

    def test_stopping_server_ends_its_native_window(self):
        _, server, _ = self.acquire()
        server.binding.stop()
        window = Mock()
        window.poll.return_value = None
        mdview.serve_preview(server, self.registry, window)
        window.terminate.assert_called_once()
        window.wait.assert_called_once_with(timeout=3)

    def test_separate_cli_invocations_reuse_and_window_close_stops_owner(self):
        environment = {**os.environ, "XDG_STATE_HOME": str(self.root / "cli-state")}
        command = [sys.executable, str(ROOT / "libexec/mdview/mdview.py"), str(self.source), "--no-open", "--browser"]
        owner = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, env=environment)
        self.addCleanup(lambda: owner.poll() is None and owner.kill())
        try:
            self.assertIn("Previewing", owner.stdout.readline())
            url = owner.stdout.readline().strip()
            self.assertTrue(url.startswith("http://127.0.0.1:"))
            repeated = subprocess.run(command, capture_output=True, text=True, env=environment, timeout=10)
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertIn(url, repeated.stdout)
            registry = mdview.DocumentRegistry(self.source, self.root / "cli-state/htk/mdview")
            record = registry.request(url, "binding")
            self.assertEqual(record["phase"], "waiting")
            # A browser opening and then closing supplies this same lifecycle signal.
            registry.request(url, "leave?page=closed-window", {})
            output, errors = owner.communicate(timeout=10)
            self.assertEqual(owner.returncode, 0, errors)
            self.assertFalse(registry.record.exists())
            replacement = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True, env=environment)
            try:
                replacement.stdout.readline()
                next_url = replacement.stdout.readline().strip()
                self.assertNotEqual(next_url, url)
                replacement.send_signal(signal.SIGINT)
                replacement.communicate(timeout=10)
                self.assertEqual(replacement.returncode, 0)
            finally:
                if replacement.poll() is None:
                    replacement.kill()
                    replacement.communicate()
        finally:
            if owner.poll() is None:
                owner.kill()
                owner.communicate()


class PresentationTests(unittest.TestCase):
    def test_native_open_never_launches_or_activates_a_browser(self):
        with patch("native_window.open_window") as native, patch.object(mdview.subprocess, "run") as browser:
            process = mdview.open_preview("http://127.0.0.1:42/document/", presentation="window", title="Notes.md")
            self.assertIs(process, native.return_value)
            native.assert_called_once_with("http://127.0.0.1:42/document/", "Notes.md", focus=False)
            browser.assert_not_called()
            native.reset_mock()
            mdview.open_preview("http://127.0.0.1:42/document/", presentation="window", reuse=True, focus=True)
            native.assert_not_called()
            browser.assert_not_called()

    def test_profile_selects_presentation_and_rejects_unknown_values(self):
        # Loading the preference uses the normal inherited profile loader.
        mdview.presentation_preference()
        with patch("toolkit.load_profile", return_value={"views": {"markdown": {"presentation": "window"}}}):
            self.assertEqual(mdview.presentation_preference(), "window")
        with patch("toolkit.load_profile", return_value={}):
            self.assertEqual(mdview.presentation_preference(), "browser")
        with patch("toolkit.load_profile", return_value={"views": {"markdown": {"presentation": "other"}}}):
            with self.assertRaisesRegex(ValueError, "views.markdown.presentation"):
                mdview.presentation_preference()


class RefreshTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is optional; refresh logic checks use node:test")
    def test_browser_refresh_recovery(self):
        result = subprocess.run([shutil.which("node"), "--test", str(ROOT / "tests/mdview_refresh.cjs")],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
