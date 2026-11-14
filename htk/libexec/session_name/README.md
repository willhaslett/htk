# Session names

`name-session` gives the current Codex session the name
`<workspace>-YYYYMMDDHHMMSS`, using the local time when the command runs. For
example: `workflow-dev-20261021150409`.

```sh
name-session
name-session --dry-run
name-session --workspace my-workspace
```

Inside Herdr, the command reads the calling pane's workspace label using
`HERDR_WORKSPACE_ID`. Elsewhere, `--workspace` supplies that label. Renaming uses
`CODEX_THREAD_ID` from the current Codex session. The command prints the saved
name after verifying it through Codex's API. `--dry-run` only prints the proposed
name and works without a Codex session.

The helper opens a short-lived `codex app-server --stdio` connection, initializes
it, calls `thread/name/set`, and verifies with `thread/read`. It uses the current
Codex installation and configuration; it does not start an agent turn or send
terminal input. It changes the saved Codex name shown when resuming, while the
Herdr workspace label stays as it is. No additional runtime state is created.

Naming is an optional convenience. Agents can choose when it is useful and can
use the native `/rename <name>` command or another suitable method instead. A
name already chosen for a particular purpose can simply be kept.

If the rename request is acknowledged but verification fails or times out, the
name may already have changed; the helper reports the uncertainty without
retrying. Older Codex installations may lack this API.

References: [Codex naming API](https://developers.openai.com/codex/app-server)
and [native rename command](https://developers.openai.com/codex/cli/slash-commands).
