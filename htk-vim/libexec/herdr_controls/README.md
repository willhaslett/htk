# Herdr pane and workspace actions

Optional Herdr 0.9.5+ helpers, using htk's managed Python. The commands
are independent of key mappings; [the preset](../../README.md) supplies
one Vim-style map. They use native Herdr public APIs and keep terminal/session
identity when moving panes.

```sh
herdr-pane create left --pane PANE_ID --socket SOCKET_PATH
herdr-pane move down --pane PANE_ID --socket SOCKET_PATH
herdr-pane close --pane PANE_ID --socket SOCKET_PATH
herdr-pane-to-tab --pane PANE_ID --new-tab --label SESSION
herdr-pane-to-tab --pane PANE_ID             # numbered destination picker
herdr-pane-to-tab --pane PANE_ID --tab TAB_ID --no-focus
herdr-pane-to-tab --pane PANE_ID --list      # inspect choices only
herdr-move-workspace up --workspace WORKSPACE_ID --socket SOCKET_PATH --dry-run
```

Herdr shortcut commands supply `HERDR_ACTIVE_PANE_ID`,
`HERDR_ACTIVE_WORKSPACE_ID`, `HERDR_SOCKET_PATH` and `HERDR_BIN_PATH`. They identify
the invoking pane/server, unlike inherited server-launch IDs. Outside a shortcut,
pass explicit IDs. `herdr-pane` and `herdr-move-workspace` accept `--socket`;
`herdr-pane-to-tab` uses `HERDR_SOCKET_PATH` via the Herdr CLI.
`herdr-move-workspace` can discover server focus when
no workspace ID is supplied. `herdr-pane-to-tab` uses the Herdr CLI on that socket;
it lists tabs in current display order, with numbers disambiguating equal names.
`--new-tab` and `--tab` are alternatives to its interactive picker.

## Movement

Directions are left/down/up/right. Creation inherits source cwd and names new
shells SHELL; `--no-focus` keeps focus. Pane movement selects a directional
neighbor, or a perpendicular neighbor when reorienting a stacked/side-by-side
layout. Already-aligned edges and single-pane tabs are no-ops. On Herdr 0.9.5,
within-tab movement stages through PANE MOVE TEMP before returning; interrupted
moves leave terminals alive for inspection. There is no blind mutation retry.
Unzoom source/destination tabs before tab moves. The picker rechecks the original
terminal, workspace and tab before submitting a move, so a changed source or
closed destination stops it. Empty source tabs disappear naturally.

Workspace reordering moves the primary workspace and its linked worktrees as
one group; boundaries are no-ops. It uses workspace.move_block and preserves
focus. Helpers serialize repeated operations per socket, excluding human time
spent in close confirmation.

## Guarded pane close

Any detected agent or agent-session identity blocks closure, regardless of
working/idle/blocked/done status. Exit the agent normally first. An agent-free
pane requires typing CLOSE; Enter cancels. The helper rechecks occupant identity
and agent presence after confirmation. It has no force-close override. This
protects terminal work; other Herdr close commands retain their own behavior.

The original local helpers' tests cover ordering, stable identities, geometry,
zoom/changed-source failures, no retries and close safeguards. See `tests/`.
