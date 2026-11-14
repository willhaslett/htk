---
name: htk
description: Use the portable Herdr keyboard helpers, local list/status viewers and Markdown preview with personal workspace conventions.
---

This is a local Herdr setup. Use native `herdr --skill` and command help for
current pane, tab and agent operations. Inspect the active target before changing
layout; preserve existing sessions, drafts and deliberate focus.

`htk --help` lists the portable commands. `htk list --file FILE.json` watches
ordinary lists; `htk status FILE.json` watches persisted work records. Use links
and the list viewer's dim Control+1…9,0 shortcuts where helpful.

`htk mdview FILE.md` opens a live Markdown preview. `--window` selects the
standalone macOS window; `--browser` selects the browser. Reuse the existing
preview and keep its serving process alive. Explicit output choices take priority.

`herdr-pane create|move DIRECTION` uses left/down/up/right.
`herdr-pane close` confirms closing an agent-free pane and blocks detected agents.
`herdr-pane-to-tab` offers destinations; `--new-tab` moves into a new tab.
`herdr-move-workspace up|down` reorders workspace groups.
`new-herdr-workspace` creates a numbered workspace with one MANAGER pane;
`--codex` starts the separately installed Codex CLI there.
Open the shortcut popup with Command+/ or
`herdr plugin action invoke htk-vim.keymap.show`.

## Workspace organization

These are personal defaults; adapt them to explicit requests and useful exceptions.
The MANAGER tab's MANAGER pane holds the continuing workspace conversation plus
views explicitly requested alongside it. Subordinate agents belong outside that tab.
A sustained distinct effort can have an unused letter and a tab such as
`A TRAVEL`, with its continuing conversation in `A MANAGER`. Workers normally
appear to its right as `AW1 BOOKINGS`, `AW2 ROUTE`. Reuse existing letters and
indices. Quick work can stay with the workspace manager; work streams are optional.
Names use ALL CAPS. Workspace emoji and names are mutable display labels;
use native stable IDs for operations. Delegate when the task and context justify it.

## Workspace status

Keep one persisted work record for current items, deferred ideas and completed
history; derive views from it rather than maintaining separate to-do/status copies.
Use the precise [status layout](references/status-template.md) when presenting it:
Task | State | Next / waiting. Number every current item and indent items two
spaces under their headings. Keep labels stable as completed items leave the view;
deferred ideas and history remain in the record. Under stream B, item 4 is B4.
Without streams, use simple numbers.

State is free text. Align `● ` for an agent working and `○ ` for idle in the
first two positions; leave them blank for items waiting on the user. Persist
that observation and the saved time. Red marks user action now; yellow marks a
user-only action blocked on a dependency. Other waits remain unhighlighted.
The shared renderer supplies the separate dim link-shortcut gutter. Reuse the
live viewer; closing it preserves its source.

The [setup guide](../../../README.md) and component command help describe access.
