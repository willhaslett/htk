#!/usr/bin/env python3
"""Open a persistent, live Markdown rendering in a document window or browser."""

import argparse
import os
import sys
import errno
import hashlib
import json
import mimetypes
import re
import secrets
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cli_support import ArgumentParser

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))
from window_binding import DocumentRegistry, WindowBinding

ASSETS = {
    "": ("index.html", "text/html; charset=utf-8"),
    "viewer.js": ("viewer.js", "text/javascript; charset=utf-8"),
    "lifetime.js": ("lifetime.js", "text/javascript; charset=utf-8"),
    "style.css": ("style.css", "text/css; charset=utf-8"),
    "marked.js": ("vendor/marked.js", "text/javascript; charset=utf-8"),
    "purify.js": ("vendor/purify.js", "text/javascript; charset=utf-8"),
    "mermaid.js": ("vendor/mermaid.js", "text/javascript; charset=utf-8"),
}
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif", ".bmp", ".ico"}


class PreviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, source, port=0, *, binding=None):
        self.source = source
        self.token = secrets.token_urlsafe(24)
        self.binding = binding or WindowBinding()
        super().__init__(("127.0.0.1", port), PreviewHandler)
        self.url = f"http://127.0.0.1:{self.server_port}/{self.token}/"


class PreviewHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def respond(self, status, body=b"", content_type="text/plain; charset=utf-8", etag=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; "
                         # Mermaid SVGs contain generated styles and style attributes.
                         "style-src 'self' 'unsafe-inline'; img-src 'self' data: https: http:; "
                         "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def route(self):
        port = self.server.server_port
        if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            self.respond(403, b"Invalid host")
            return None
        path = unquote(urlsplit(self.path).path)
        prefix = f"/{self.server.token}/"
        if not path.startswith(prefix):
            self.respond(404, b"Not found")
            return None
        return path[len(prefix):]

    def do_GET(self):
        route = self.route()
        if route is None:
            return
        if route == "content":
            self.content()
        elif route == "events":
            self.events()
        elif route == "binding":
            self.binding_response(phase=self.server.binding.phase())
        elif route in ASSETS:
            name, content_type = ASSETS[route]
            self.respond(200, (APP_DIR / name).read_bytes(), content_type)
        elif route.startswith("local/"):
            self.local_image(route[len("local/"):])
        else:
            self.respond(404, b"Not found")

    def binding_response(self, **fields):
        body = json.dumps({"source": str(self.server.source), "url": self.server.url, **fields}).encode()
        self.respond(200, body, "application/json; charset=utf-8")

    def page_id(self):
        page = parse_qs(urlsplit(self.path).query).get("page", [""])[0]
        return page if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", page) else None

    def do_POST(self):
        route = self.route()
        if route is None:
            return
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host')}":
            self.respond(403, b"Invalid origin")
            return
        if route == "leave" and (page := self.page_id()):
            self.server.binding.disconnect(page)
            self.respond(204)
        elif route == "open-failed":
            self.server.binding.open_failed()
            self.binding_response()
        elif route == "claim":
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1024:
                    raise ValueError()
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError()
            except (ValueError, UnicodeError):
                self.respond(400, b"Invalid request")
                return
            action = self.server.binding.claim(open_window=bool(data.get("open")),
                                               focus=bool(data.get("focus")))
            self.binding_response(action=action)
        else:
            self.respond(404, b"Not found")

    def events(self):
        page = self.page_id()
        binding = self.server.binding
        stream = binding.connect(page) if page else None
        if stream is None:
            self.respond(410, b"Preview closed")
            return
        focus_version = 0
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            while True:
                with binding.changed:
                    if binding.stopped or binding.pages.get(page) is not stream:
                        break
                    if focus_version != binding.focus_version:
                        message = b"event: focus\ndata: {}\n\n"
                        focus_version = binding.focus_version
                    else:
                        # Reconnect sooner than the close/reload grace period.
                        message = b": connected\nretry: 500\n\n"
                self.wfile.write(message)
                self.wfile.flush()
                with binding.changed:
                    binding.changed.wait(timeout=1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            binding.disconnect(page, stream)

    def content(self):
        try:
            raw = self.server.source.read_bytes()
            markdown = raw.decode("utf-8-sig")
        except (OSError, UnicodeError):
            self.respond(503, b"Cannot read the file. Waiting for it to become available again.")
            return
        etag = '"' + hashlib.sha256(raw).hexdigest() + '"'
        if self.headers.get("If-None-Match") == etag:
            self.respond(304, etag=etag)
            return
        body = json.dumps({"markdown": markdown, "name": self.server.source.name,
                           "path": str(self.server.source)}).encode("utf-8")
        self.respond(200, body, "application/json; charset=utf-8", etag)

    def local_image(self, relative):
        try:
            root = self.server.source.resolve().parent
            image = (root / relative).resolve()
            if not image.is_relative_to(root) or image.suffix.lower() not in IMAGE_TYPES:
                self.respond(404, b"Not found")
                return
            body = image.read_bytes()
            etag = '"' + hashlib.sha256(body).hexdigest() + '"'
            if self.headers.get("If-None-Match") == etag:
                self.respond(304, etag=etag)
            else:
                self.respond(200, body, mimetypes.guess_type(image.name)[0] or "application/octet-stream", etag)
        except (OSError, ValueError):
            self.respond(404, b"Image not found")


def open_preview(url, focus=False, reuse=False, *, presentation="browser", title="Markdown"):
    if presentation == "window":
        if not reuse:
            from native_window import open_window
            return open_window(url, title, focus=focus)
        return None  # The existing renderer receives --focus over its live stream.
    if sys.platform == "darwin":
        subprocess.run(["/usr/bin/osascript", "-l", "JavaScript",
                        str(APP_DIR / "open-background.js"), url,
                        *(["--focus"] if focus else []), *(["--reuse"] if reuse else [])],
                       check=True, capture_output=True, text=True)
    elif not reuse:
        webbrowser.open(url, autoraise=focus)


def acquire_preview(source, port=0, *, no_open=False, focus=False, registry=None):
    """Return (URL, owner server or None, should open) under a document lock."""
    registry = registry or DocumentRegistry(source)
    source = registry.source
    replacement_deadline = None
    while True:
        with registry.locked():
            url = registry.read()
            result = registry.request(url, "claim", {"open": not no_open, "focus": focus}) if url else None
            if result and result.get("action") in {"open", "reuse"}:
                return url, None, result["action"] == "open"
            if not result or result.get("action") != "closing":
                try:
                    server = PreviewServer(source, port)
                except OSError as error:
                    # A just-closed preview may still be releasing an explicitly
                    # requested port. Give its owner time to close the socket.
                    if error.errno != errno.EADDRINUSE or not port or not (
                            replacement_deadline or (result and result.get("action") == "expired")):
                        raise
                    replacement_deadline = replacement_deadline or time.monotonic() + 2
                    if time.monotonic() >= replacement_deadline:
                        raise
                else:
                    server.binding.claim(open_window=not no_open, focus=focus)
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    try:
                        registry.write(server.url)
                    except BaseException:
                        server.shutdown()
                        server.server_close()
                        raise
                    return server.url, server, not no_open
        # A closing page may be reloading. Wait out its short grace period
        # before replacing the server, without holding the per-document lock.
        time.sleep(0.1)


def serve_preview(server, registry, window=None):
    try:
        while server.binding.phase() != "expired":
            if window is not None and window.poll() is not None:
                if window.returncode:
                    raise OSError("The macOS preview window exited before it was closed normally")
                break
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nPreview stopped.")
    finally:
        if window is not None and window.poll() is None:
            try:
                window.terminate()
                window.wait(timeout=3)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                try:
                    window.kill()
                except ProcessLookupError:
                    pass
                window.wait()
        server.binding.stop()
        server.shutdown()
        server.server_close()
        registry.remove(server.url)


def presentation_preference():
    # Use the same inherited profile as the command dispatcher, not a second
    # independent preferences file. This preference affects standalone previews.
    sys.path.insert(0, str(APP_DIR.parent))
    from toolkit import load_profile
    value = load_profile().get("views", {}).get("markdown", {}).get("presentation", "browser")
    if value not in ("browser", "window"):
        raise ValueError("views.markdown.presentation must be browser or window")
    return value


def main(argv=None):
    parser = ArgumentParser(prog=os.environ.get("HTK_CLI_PROG", "mdview"), description=__doc__)
    parser.add_argument("file", type=Path, help="Markdown file to preview")
    opening = parser.add_mutually_exclusive_group()
    opening.add_argument("--no-open", action="store_true", help="Print the URL without opening a preview")
    opening.add_argument("--focus", action="store_true", help="Bring the preview forward (default: open in background)")
    surface = parser.add_mutually_exclusive_group()
    surface.add_argument("--window", dest="presentation", action="store_const", const="window", help="Use a standalone macOS window")
    surface.add_argument("--browser", dest="presentation", action="store_const", const="browser", help="Use the normal browser")
    parser.add_argument("--port", type=int, default=0, help="Local port (default: choose a free port)")
    args = parser.parse_args(argv)
    source = args.file.expanduser().resolve()
    if not source.is_file():
        parser.error(f"File not found: {source}")
    try:
        presentation = args.presentation or presentation_preference()
        if presentation == "window" and sys.platform != "darwin" and not args.no_open:
            parser.error("--window requires macOS; use --browser on this host")
        registry = DocumentRegistry(source, presentation=presentation)
        url, server, should_open = acquire_preview(source, args.port, no_open=args.no_open,
                                                   focus=args.focus, registry=registry)
    except (OSError, ValueError) as error:
        parser.exit(1, f"mdview: {error}\n")
    print(f"{'Previewing' if server or should_open else 'Reusing preview for'} {source}\n{url}", flush=True)
    window = None
    if should_open or (args.focus and not args.no_open):
        try:
            window = open_preview(url, focus=args.focus, reuse=not should_open,
                                  presentation=presentation, title=source.name)
        except (OSError, subprocess.CalledProcessError) as error:
            if should_open:
                if server:
                    server.binding.open_failed()
                else:
                    registry.request(url, "open-failed", {})
            detail = getattr(error, "stderr", None) or str(error)
            print(f"Could not open the preview: {detail.strip()}. Use --browser or open the URL above manually.", file=sys.stderr)
    if server:
        print("Leave this running. Closing the preview window or Ctrl-C stops the server.", flush=True)
        try:
            serve_preview(server, registry, window)
        except OSError as error:
            parser.exit(1, f"mdview: {error}\n")


if __name__ == "__main__":
    main()
