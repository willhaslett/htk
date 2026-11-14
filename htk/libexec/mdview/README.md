# Markdown Rendering

`mdview` binds a Markdown document to a persistent preview window, alongside
editing in Herdr or any editor. Rendering the same file again reuses its window
and URL; edits update that window automatically. Closing the preview releases
the binding, so the next render opens a fresh window.

The implementation uses the toolkit's uv-managed Python 3.12 runtime.
The Markdown renderer, HTML sanitizer and Mermaid renderer are bundled, with no
runtime downloads.

```sh
mdview path/to/file.md
mdview path/to/file.md --window   # standalone macOS window, outside Chrome
mdview path/to/file.md --browser  # explicit normal-browser presentation
```

`--window` uses a small native macOS window with the system WebKit engine. It
reuses the bundled renderer, file watching, tables, Mermaid, images and scrolling;
there are no Chrome tabs or address bar. The window opens without activating its
application. Click it to interact, or use `--focus` to bring it forward explicitly.
Command–W closes the window, Command–R reloads, and ordinary copy/select-all work.
External document links open in the user's default application; remote pages
cannot replace the local preview. The window is independently movable and
resizable, with ordinary macOS window stacking rather than always-on-top behavior.

This optional adapter needs macOS AppKit/WebKit and the installed Swift command
line tools. Its small native helper is compiled on first use and cached under
`${XDG_CACHE_HOME:-~/.cache}/htk/mdview`; it adds no Python packages or runtime
downloads. A build/open failure is reported without silently opening Chrome.
Use `--browser` when the native adapter is unavailable.

Choose the standalone default in the existing toolkit profile:

```toml
[views.markdown]
presentation = "window" # or "browser"; browser is the unconfigured default
```

Command-line `--window`/`--browser` overrides this preference. The web app's
embedded Markdown panes are unchanged. Native and browser presentations keep
separate discovery records so an explicit window request does not silently reuse
an old Chrome preview, and existing browser previews remain intact.

The first command serves the document while the preview exists. Subsequent
commands print its existing URL and return. Changes appear automatically,
normally within a second, including changes to linked local images and SVG
diagrams. Closing a browser preview stops its server after a three-second grace
period; reloading within that interval preserves the binding. Closing the native
window stops its server when the window process exits. Ctrl-C also stops the
serving command. Different paths to the same file, including symlinks, share
the binding. Simultaneous requests open just one preview.

With browser presentation and Chrome as the default macOS browser, each document gets a separate
Chrome window. Other browsers use their standard opening behavior. New windows
open in the background, preserving the current application's focus. If Chrome
activates anyway, a short AppKit helper restores focus; a brief flicker is still
possible. `--focus` brings the browser forward and asks an existing preview to
focus itself, without opening another copy. The browser controls whether that
window can take focus.

`--no-open` prints the URL without opening a preview; the serving command waits
for the page to be opened manually. A later normal invocation can open that same
URL. `--port 8765` chooses the port when a new server is needed; it does not move
an existing preview.

GitHub-style tables, task lists, code blocks, links, images and fenced `mermaid`
diagrams are supported. Diagrams update with the file; an invalid diagram shows
its error and source without interrupting the rest of the preview.
Table cells stay on one line; scroll the table horizontally to see more. The viewer
preserves the visible document block and unchanged tables' horizontal positions
when the file changes. Relative images resolve beside the Markdown file (beside
the target for symlinks). Normal prose wraps to the window width without changing
the source file. Math rendering is not included.

The server listens only on `127.0.0.1`, uses a unique URL for each preview, and
serves only the selected Markdown file, viewer assets, and images inside the file's
directory. Rendered HTML is sanitized.

Per-document discovery records live in `${XDG_STATE_HOME:-~/.local/state}/htk/mdview`.
A browser connection holds the binding, rather than a timer in the page or just
a running server. Background windows don't expire when their timers are
throttled. Closing the page releases the connection; a broken connection also
handles browser crashes. Stale discovery records recover on the next invocation.
No browser-automation permissions are needed. The optional native window uses
the system build tools described above.

The toolkit's `bin/mdview` launcher runs `mdview.py` in this folder using its
managed runtime.
Third-party package versions and licenses are in `vendor/`. The standalone Mermaid
bundle comes from the pinned `mermaid@11.14.0` npm package (`dist/mermaid.min.js`);
it needs no Node installation to run in the viewer.

For a development-only rendering regression check, install jsdom into a temporary
directory and run the test from the repository root:

```sh
mdview_test_dir=$(mktemp -d)
npm install --prefix "$mdview_test_dir" --no-save --ignore-scripts jsdom@26.1.0
NODE_PATH="$mdview_test_dir/node_modules" node tests/mdview_rendering.cjs
rm -r "$mdview_test_dir"
```

This exercises the bundled renderers, refreshes, invalid-diagram recovery,
sanitization and scroll preservation. jsdom approximates SVG text measurements;
visual layout still needs a browser check.
