# Workspace status list

Workspace status is the single persisted work record: current work, deferred
ideas and completed history. `htk status FILE.json` watches its presentation;
there is no separate to-do command or separately maintained to-do list.
Other displays read the same records without copying their changing facts.
Status uses the ordinary list renderer and its standard navigation and link
shortcuts. Linked items get the same dim Control+1…9,0 gutter as other lists;
stable work labels stay in the item text. See the [list controls](../list_view/README.md#watching-and-interaction).
`--hide-completed` filters finished history from the live table. See the
[work record format](#work-records).

```sh
htk status ./STATUS.json --hide-completed
```

## Work records

The persisted JSON describes the work independently of display columns, widths
or colors. The [JSON Schema](tasks.schema.json) defines version 1:

```json
{
  "version": 1,
  "title": "Workspace status",
  "tasks": [
    {
      "id": "1",
      "title": "Launch",
      "state": "In progress",
      "next": "Manager: validate the demo",
      "notes": "Supporting context and evidence stay with the record."
    }
  ]
}
```

Every item has a unique stable `id`, a `title` and a free-text `state`.
`next`, `notes` and nested `children` are optional. Source order determines
display order; moving an item does not change its identity. Notes can contain
Markdown links. The existing file name is independent of its role: a workspace
can use its existing `TODO.json` as its single status record.

`--hide-completed` omits Done, Completed, Canceled and Cancelled items (case
insensitive), keeping a finished parent if it has unfinished children. Filtering
never deletes records or history. Work with another verification or action
remaining is still active.

`--once` and nonterminal output print a readable table. `--json` emits the
list-view projection, not the persisted work records. Links remain actionable
in the live view. File changes and atomic replacements refresh the display;
unreadable or malformed sources keep the last good view marked stale until the
source recovers. The viewer never writes the source.

Plain text work lists and saved Markdown status tables are also readable file
inputs. They are not a second work-tracking feature. `--hide-completed` applies
only to JSON records.

## Saved reports and configured sources

`status-list FILE` watches the saved work stream table in the workspace's authoritative
current Markdown status (conventionally `STATUS.md`) and renders it with the
shared terminal `list-view`. Legacy newest-first archives are also supported. It reads the file
every second, including atomic replacements, and never changes it or performs a
new work-status lookup. Keep the process running for live updates.

A saved Markdown status can add a timestamp, narrative and evidence or preserve
a point-in-time record. For workspaces backed by task data, its task facts come
from that work list. Existing standalone Markdown status files still work.

```sh
status-list ./STATUS.md
status-list ./STATUS.md --title "Workspace status"
status-list ./STATUS.md --once
status-list ./STATUS.md --json
```

`status-list WORKSPACE` resolves a short name from `[workspaces.NAME].status` in the
active profile; `--workspaces` lists aliases and paths. These aliases are configured
source names, not live host workspace IDs. Will's shell alias is `status`.
Legacy `--projects`, `[projects.NAME]`, `Project status` / `Project snapshot`
headings and `snapshot-list` remain supported; no file rename is needed.

`htk status --all` (or `status --all` with Will's existing shell alias) opens the
configured overall view. It reuses that view's renderer and watches it every
15 seconds. `--once` prints a snapshot; `--json` runs the configured renderer
directly and preserves its JSON. Bare `status` still shows help and configured
sources. File/workspace invocations retain their existing behavior.

Configure the existing renderer as an argument array in the active profile:

```toml
[views.status]
all_command = ["python3", "/absolute/path/to/existing-status-renderer.py"]
```

The renderer writes the usual list-view JSON. Its own sources, selection rules
and workspace names remain authoritative. `--title` and `--hide-completed`
apply to file sources; they cannot be combined with `--all`.

The table displays Act first, then Work stream, Current state, and Next action or
dependency. Act-first and work-stream-first source layouts both work, including
reports using the legacy `Thread` or `Work` header.
Bold parent rows group work streams; child labels use `&emsp;↳ ` (or plain `↳ `) in
the source. The terminal restores their indentation and hangs wrapped labels
under the item text. Parent Act cells are blank; child Act markers are yellow
and centered. Older flat tables still render unchanged. Long prose wraps within
its column.
The newest entry's timestamp appears above the rows so its recorded evidence is distinct
from the viewer's last file-read time. Archives are newest-first. Once an entry
heading is found, missing or malformed new content is an error rather than a
reason to silently display an older snapshot. The shared viewer keeps its last
successful list visible and marks it stale until the next successful read.

Supported source structure is deliberately small: the current snapshot template,
its older `Work` heading and two/three-column variants, Markdown table alignment
rows, escaped/inline-code pipes, inline links, emphasis/code markers, and the
template's HTML Act marker. Fenced code examples are ignored. This is a table
source, not a general Markdown preview; browser preview remains available through
`mdview` when explicitly wanted.

Cells with one HTTP(S) link become terminal hyperlinks. For cells with multiple
distinct links, the main cell stays plain and indented `↳` rows below the work stream
provide each label with its own target in the same column. Other columns on
those supplemental rows stay blank. All link labels remain in the main text,
and local evidence paths remain visible in parentheses. Full source
content and summary/evidence paragraphs remain in the current Markdown document;
older entries live in the workspace's separate archive. `q`/Ctrl-C closes the
viewer; j/k or arrows scroll. `--once` and
non-terminal output print the table; `--json` exposes its structured list data.
