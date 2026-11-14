# Workspace status layout

Keep exactly three columns: **Task**, **State**, **Next / waiting**, separated
by two spaces. Start with Task 20 characters including the two-space item
indent, and State 15 characters; give the remaining width to Next / waiting.
Adapt widths to the pane while keeping the grid aligned. Keep rows on one line;
shorten text and put supporting detail in linked notes. The renderer clips overflow.

```text
     Task                  State            Next / waiting
────────────────────────────────────────────────────────────────────────────────
     A — TRAVEL
  ⌃1   1. Book train        Ready review     User: choose a departure.
       2. Plan route     ● Comparing        Manager: compare two routes.
  ⌃2   3. Book hotel        Await dates      User: book after dates settle.

     B — PACKING
       1. Checklist      ○ Ready resume     B manager: finish the checklist.

Status saved: 1 Jan 2026 12:00 EST · ● agent working · ○ idle
```

This is illustrative data, not an existing workspace.

- The shared renderer reserves a five-character shortcut/selection gutter when
  links are present: two selection positions, then the dim three-character
  shortcut hint. Headings start at the content edge; numbered items are indented
  two spaces inside Task. Leave the gutter blank on unlinked rows; do not author
  shortcuts into task text. Without links the renderer can omit the empty gutter.
- Use bold cyan column and stream headings, a quiet separator below the column
  headings and a blank line between streams. Headings use `A — TRAVEL`; items
  use `1. Book train`. A flat list uses the same layout without stream headings.
- Keep task text normally colored; linked names are cyan and underlined with
  URLs hidden. Selection brackets belong to the renderer.
- Reserve the first two characters of State for `● `, `○ ` or two spaces. Keep
  markers neutral and aligned, with free text following at the same position.
  They describe agent activity when saved, not every background process.
- In Next / waiting, use red (`error`) for immediate user action and yellow
  (`warning`) for user-only action waiting on a dependency. In the example,
  Book train is red and Book hotel yellow. Name the dependency; other owners
  and waits retain normal color.
- Show saved time with timezone and the activity legend in the footer. Polling
  time is separate: refreshing alone must not change saved activity observations.

Use the [list renderer](../../../../htk/libexec/list_view/README.md) for
columns, sections, tones and links. Project the existing work record; this layout
does not introduce another maintained source or a new storage schema.
