# List viewer

`list-view` owns the live terminal display: layout, scrolling, resizing, links,
refreshes and clean shutdown. Supply data; an ordinary list needs no custom
program, column declarations or section scaffolding. The renderer supplies
compact btop-inspired styling; the examples below describe its data format.
Inside Herdr, its existing pane supplies the frame and title: the viewer
automatically shows a frameless table with cyan headings and a single bottom
status line. Outside Herdr, it retains the titled cyan frame. This uses Herdr's
existing `HERDR_PANE_ID` environment variable and needs no extra command or option.

```sh
# Watch an editable JSON file. Changes appear within a second by default.
list-view --file /path/to/travel.json

# Show a fixed list in memory. No source file or polling.
list-view --data '["Book the train", "Pack a bag"]'

# Print the file without opening an interactive viewer.
list-view --once --file /path/to/travel.json

# Poll a command when obtaining the data actually requires one.
list-view --refresh 10 --timeout 25 -- COMMAND ARG...
```

## Simple data

Use a JSON array of strings for a one-column list, or an array of objects for a
table. Object keys become column headings in first-seen order; absent cells stay
blank. Columns wrap and share the available width. There is no section heading
or item count. All cell text is a string. Cells can also be objects with `text`,
`href` and/or `tone`, as in the example below. An empty array clears the rows.

An optional envelope supplies a title and footer:

```json
{
  "title": "Travel",
  "rows": [
    {"Item": "Book the train", "State": "Ready"},
    {"Item": {"text": "Hotel", "href": "https://example.com/hotel"}}
  ],
  "footer": "October trip"
}
```

The envelope requires `rows`; `title` defaults to `List` and `footer` to empty.
Rows contain either strings or nonempty objects; mixed types are rejected.
The same input works with `--file`, `--data`, or a source command. Use the full
layout format below only for explicit widths, sections or other layout control.

## Watching and interaction

`--file PATH` reads JSON directly without starting a provider process. It follows
file replacements as well as edits; missing, malformed or oversized files keep
the last good view marked stale until readable data returns. A temporary or
workspace-local file is enough; there is no list registry or automatic persistence.

A source command runs directly with its argument array, without a shell; quotes,
spaces and flags in arguments are preserved. Stdout contains only JSON. A
nonzero exit code, malformed result or timeout marks the view stale and keeps
the last successful document visible. The viewer displays the last successful
update time and an error, and tries again on the next interval. Provider stderr
supplies details for nonzero exits. Initial failures show an empty waiting view.
`--once` reports failures on stderr and exits nonzero.

The default interval is one second for files and ten seconds for commands;
`--refresh` overrides either. Slow runs never overlap;
if a run takes longer than the interval, the next starts after it finishes.
The interval label stays visible while a poll runs; there is no changing
`refreshing` label. Ordinary intervals share a fixed-width slot across views.
The last successful update time advances after a poll completes. Timestamp or
error updates redraw only the status line, leaving the table and its links in
place. Data, scroll and size changes still redraw the table and clear stale
hyperlink targets.
Each run has a 25-second timeout by default. Stdout is limited to 4 MiB and
stderr to 64 KiB. Providers should finish after producing one result, and should
not detach child processes into their own sessions. The viewer terminates its
provider process group on completion, timeout or close. File input has the same
4 MiB limit. The viewer never writes source files or saves lists or credentials.

`q` (or `Q`) or Ctrl-C closes the viewer, including during a provider request. Use
`j`/`k` or down/up to scroll, Page Down/Page Up (or space/`b`) for a page, and
`g`/`G` or Home/End for the beginning/end. Resizing preserves the shared column
grid and clips cells to the available space. Very narrow panes may show only
part of the grid. `--once` prints all rows as plain text with no terminal controls.
Cells may supply an `href`; terminals supporting OSC8 hyperlinks make the shown
label clickable without displaying the URL. Clicking follows the terminal's
usual link affordance: Control-click in Herdr; other terminals use their own
gesture. Links move with their rows on
refresh, scroll and resize. Terminals without hyperlink support still show the
same labels. Linked labels are underlined and cyan by default; explicit cell
tones keep their color or dimming, with an underline. Leading indentation and
trailing padding keep their spacing without underlining or a click target.
`--once` always prints labels alone.

Linked rows automatically enable keyboard selection; `--select-links` also
selects this mode explicitly. Nothing is selected
initially: `j`/down selects the first linked row and `k`/up selects the last.
Further presses move between rows, wrapping at both ends and skipping headings
and unlinked rows. Yellow square brackets mark the selected row without a filled
background or extra vertical spacing. Reserved left/right gutters keep content
in place as selection moves. Content keeps its colors, including red failures.
Wrapped rows have brackets on each visible continuation line. Enter opens its first link in the
default browser, or shows its children when present. Selection follows
the same URL across
refreshes and section changes and scrolls into view when navigating. Page and
Home/End scrolling remain available. PR views enable this mode automatically.

The first ten linked items have a leading shortcut gutter: `⌃1` through
`⌃9`, then `⌃0`. Control+1 through Control+0 opens the corresponding item's first
link without changing the current selection. Headings, unlinked rows and wrapped
continuations do not consume numbers. The numbers refer to list order across
sections, even when scrolled; later items use J/K and Enter. In a child view the
parent-page link is numbered first, using the same `⌃1` label. The gutter
stays outside hyperlinks and shares the same width on every row. Plain output
and JSON keep their source content. Enhanced terminal key events provide these
shortcuts while the viewer has focus; no global bindings are installed.

Workspace status displays are ordinary list views and use these same controls.
Their work labels, such as `B4`, belong in the item text. The dim shortcut gutter
is separate from that text, with section headings aligned to the content column.
Providers supply the links; the viewer supplies shortcut hints from list order.

[`item_shortcuts.py`](item_shortcuts.py) supplies the shared `prefix(index)` label
gutter and `index_for_key(code, modifiers, event=1)` decoder. Other viewers can
reuse them with their own item action.

[`progress_style.py`](progress_style.py) supplies `bar(current, total, width=6)`
for compact heavy/light dash tracks. Omitted or unavailable counts use a dashed
track. `TONE` selects the non-bold cyan `info` tone; callers can use `muted` for
completed work or `error` for failures. The helper emits plain text, so it also
works with other terminal renderers.

Expandable rows use the same selection controls in a focused child view. Its
first row links to the parent page, followed by the indented children. Enter
expands another row with children or opens a leaf link. Escape returns one level and keeps the viewer open at the root; `q` or Ctrl-C
closes immediately from any level. Open views and selections
follow their URLs when refreshed data reorders rows. If an opened parent
disappears, the viewer returns to its surviving ancestor.

Selection geometry and its yellow ANSI foreground live in
[`selection_style.py`](selection_style.py), shared with Calendar. A renderer can
reserve stable gutters using `content_span(x, width)` and draw unlinked brackets
using `draw_edges(put, y, x, width)`, where `put` accepts `(y, x, text, tone)`.
Register its `focus` tone with `SGR` (or `ANSI_COLOR` for curses); the terminal
palette supplies the user's yellow. Item text and hyperlink styling remain
independent. Compact date cells use `padding=0`; very narrow cells retain a left
bracket when both brackets cannot fit.

## Shared control bar

`control_bar.py` supplies a small terminal-independent component for interactive
views. Commands have a label and binding; latching
toggles add a gray/green dot. Availability, active state and focus are independent.
Off toggles can still be activated; unavailable controls have no click target.

```python
from control_bar import Control, layout

controls = [
    Control("quit", "Quit", "⌃D"),
    Control("run", "Run query", "⌘Enter", enabled=editor_ready),
    Control("vim", "Vim mode", "⌘I", kind="toggle", active=vim_enabled),
]
bar = layout(controls, width, focused=selected_action)
```

`bar.spans` contains `(x, y, text, tone)` dataclass records, `bar.targets` contains
`(x, y, width, action)` records, and `bar.height` tells the consumer where its next
section begins. `bar.cells` exposes each cell's `(x, y, width, control)`, including
unavailable controls. Paint the spans through the view's renderer and route clicks
within each target to its action. The component emits no terminal escapes and
does not register shortcuts, dispatch commands, own input or store preferences.

Map `normal` to the view's full text color, `muted`/`disabled` to gray, `success`
to green, and `focus` to `selection_style.SGR`'s yellow. Focus uses that shared
helper's brackets in permanently reserved one-column gutters, so text and hit
regions do not move during navigation. The bar has no pipes, cell borders or
underlining. Controls fill an equal-width table in row order, with as many columns
as fit (21 terminal columns minimum per cell), up to the number of controls.
Wide bars fit all controls on one row; narrower bars use fewer columns.
The final row retains the same column boundaries instead of stretching its cells.
A control with `starts_row=True` begins a new row. Use this to keep a short group
such as Quit on its own first row; subsequent controls still wrap onto additional
aligned rows when a pane shrinks, then return to their grid columns as it grows.
The example above flows Quit and the other controls through the same grid.

Each cell has an eight-column, right-aligned binding field, one space, then a
left-aligned label field. Bindings use compact key names and at most three
leading modifier symbols (`⌃`, `⌥`, `⇧`, `⌘`). A single space separates the
modifier group from the key name (`⌘ I`, `⇧⌘ Enter`); longer bindings truncate to eight
display columns. A switch's dot leads its label field, so indicators line up down
each column. Commands have no dot. Long labels end in an ellipsis within their
own cell. A pane too narrow for even a binding plus label retains the label and
indicator. Availability and toggle changes do not reflow cells.

Layout work is bounded by the number of controls and adds no timers, threads or
dependencies. Views keep their own action and query/session semantics. Other
views can adopt it without changing their renderer.

## Full layout format, version 1

Existing providers can keep this format. It is optional for new lists.

```json
{
  "version": 1,
  "title": "Workspaces",
  "columns": [
    {"key": "id", "label": "ID", "width": 6},
    {"key": "title", "label": "Title", "min_width": 12, "flex": 1},
    {"key": "status", "label": "Status", "width": 16}
  ],
  "sections": [
    {
      "title": "Active",
      "rows": [
        {
          "cells": {
            "id": {"text": "A1", "href": "https://example.com/projects/A1"},
            "title": "Prepare the proposal",
            "status": {"text": "Needs review", "tone": "warning"}
          }
        }
      ]
    },
    {"title": "Finished", "show_count": false, "rows": []}
  ],
  "footer": "Optional source note"
}
```

- The document requires `version: 1`, `title`, `columns` and `sections`. `footer`
  is optional. All displayed text is a string; terminal controls are removed.
- Optional `empty_state` is an object with required nonempty `text` and optional
  `tone` (default `normal`). It replaces the table with one compact status label;
  column headers, section headings and `(none)` placeholders are omitted. Keep
  `sections` empty, or supply only sections with no rows; combining rows with an
  empty state is rejected. This is a successful provider result that immediately
  replaces previous rows, rather than a fetch failure that retains stale data.
  `--once` prints the title, label and optional footer without table scaffolding.
  For example: `"empty_state": {"text": "ACTIVITY ● CLEAR", "tone": "success"}`.
  Optional boolean `center: true` places only the label at the horizontal and
  vertical center of the terminal, hiding the frame, headings and footer.
  `--once` then prints only the label. If fetching fails after a centered empty
  success, the viewer shows “Data unavailable” with stale/error information;
  it does not continue claiming the source is empty.
  The provider chooses the label, indicator and tone; the renderer does not infer
  service-specific states. Existing documents without this field keep their
  normal table behavior.
- Each of 1–32 columns requires a unique `key` and a `label`. `width` is its
  preferred terminal-cell width; without it the width follows the content,
  capped at 48. `min_width` defaults to 3. `flex` is an optional positive stretch
  weight, sharing spare width; flexible columns compress first when necessary.
  Supplied dimensions/weights are integers from 1 to 10000. If both width and
  minimum are specified, width must be at least the minimum. On narrow terminals
  even minimum widths may be reduced to fit.
  Optional `wrap: true` wraps that column's cell text onto continuation lines,
  keeping the other columns aligned. The default clips long cells. Wrapped link
  text retains the same target on every visible continuation line.
  Optional `align` is `left` (default), `center` or `right`; it aligns the header
  and each cell within the available terminal-cell width, after wrapping/clipping.
- Every section requires `title` and `rows`; empty sections display `(none)`.
  Optional boolean `show_count` defaults to `true`, appending `(N)` to its
  heading. Set it to `false` to show only the title, even when the section is empty.
- Every row requires `cells`, with exactly one entry for every column key. Each
  cell is a string or an object with required `text`, optional `tone`, and
  optional `href`. A row may also have a `tone`, inherited by cells without
  their own tone. `href` is an absolute HTTP(S) URL with a host and no whitespace
  or control characters. Only the visible, clipped label is linked; padding
  and adjacent cells remain outside the link.
- Optional cell `elapsed_since` is a finite nonnegative Unix timestamp. The
  viewer replaces that cell's text with elapsed time such as `12m 34s`, updating
  once per second without fetching new data or clearing the screen. `text`
  remains required. Omit `elapsed_since` to display a fixed completed duration.
- Optional row `children` is an array of rows using the same column keys, nested
  up to 12 levels. The parent needs a visible linked cell. With `--select-links`,
  Enter opens a child view whose first row links to that parent, using optional
  nonempty `link_label` (default `Open in browser`). An empty array still opens
  the parent-link view; omit `children` for a leaf that opens its link directly.
  The first column becomes `Item` in child views; child labels gain two spaces
  of indentation. Providers choose the child order.
- Tones are `normal`, `muted`, `info` (cyan), `success`, `warning` and `error`.
- Unknown fields, duplicate column keys, missing cells and invalid types are
  rejected, keeping accidental source-format changes visible.

Choose exactly one of `--file PATH`, `--data JSON` or `-- COMMAND [ARG...]`.
`--data` retains its initial value until closed. `--timeout` applies only to
commands. All three sources support `--once`.
