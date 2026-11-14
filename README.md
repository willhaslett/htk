# Portable Herdr setup

A standalone file tree with keyboard navigation and shortcut popup, personal
appearance, pane/workspace helpers, local list/status and Markdown viewers,
ordinary GitHub PR viewing, and optional Codex session naming. There is no Git
history or saved workspace/session data. Keep these directories together.

## Install on another Mac

Install Herdr and Ghostty separately; install Codex only if you want agent
launching/naming, and `gh` only for the ordinary GitHub viewer. Helpers target
Herdr 0.9.5+; the Ghostty pane chord uses `activate_key_table_once`.
The optional native Markdown window needs Apple's Swift command line tools;
use `htk mdview FILE --browser` if those tools are unavailable.

1. Unzip this folder into its permanent location. Paths with spaces work.
2. Run `./setup.sh`. It provisions Python 3.12 from public sources and installs
   commands into `~/.local/bin` and the `htk` skill/startup note into `~/.codex`.
   It preserves existing unrelated files and prints a rollback command. Add
   `~/.local/bin` to PATH. Use `./setup.sh --help` for installation behavior.
3. Back up existing Herdr/Ghostty configuration. For a fresh installation, copy
   `personal/herdr/config.toml` to `~/.config/herdr/config.toml` and
   `personal/ghostty/config` to `~/.config/ghostty/config`. Copy
   `personal/ghostty/themes/Monokai Pro` into `~/.config/ghostty/themes/`.
   If configuration already exists, merge the relevant tables/keys instead of
   replacing it. Use one `[keys]` table and avoid duplicate bindings.
4. Link the popup: `herdr plugin link /absolute/path/to/this-folder/htk-vim/plugins/keymap`.
   Quote that path if it contains spaces. Run `herdr config check`, then
   `herdr server reload-config`; reload Ghostty's configuration too.
5. Run `htk doctor`, `htk list --once --data '["Ready"]'`, and Command+/.

Setup leaves both application configs unchanged until you apply them yourself.
It does not install apps, model credentials or fonts. JetBrains Mono is optional;
choose an installed font instead if needed. Re-run setup after moving the tree;
relink the popup too. Installed launchers use the tree at its installed location.
Optionally source `personal/shell/aliases.zsh` for the short `status` command.

## What is preserved

The full personal appearance and selected current keyboard bindings are in
`personal/`. The original reusable snippets remain in `htk-vim/presets/`.
Command+H/L changes tabs; Command+J/K changes workspaces. Shift moves them.
Option+Command+H/J/K/L navigates panes; Shift+Option+Command moves panes;
Control+Option+Command creates them. Command+N creates a numbered workspace;
Shift+Command+N adds a Codex manager. Command+/ opens the shortcut reference.
Ghostty forwards these keys, including prefix aliases; upstream shortcuts can
still intercept input. It is best used in a dedicated Herdr terminal profile.

Bindings requiring external fleet/tab-close helpers are omitted. Command+W is
left disabled in Herdr rather than replacing its confirmation behavior with an
unsafe close. Option+Command+X retains the bundled protected pane-close helper.
The web/server application and remote execution adapters are outside this tree.
There are no service daemons or automatic login/startup registrations here.

## Check the tree

`./verify.sh` checks file integrity and runs the bundled tests in a relocated
temporary copy with an isolated home. It does not touch live Herdr services or
open browser windows. It uses the portable runtime after setup; alternatively,
set `HTK_PORTABLE_PYTHON` to a Python 3.12+ executable. Node.js enables an
additional nonvisual Markdown refresh regression; it is optional at runtime.

`MANIFEST.json` lists every packaged file and SHA-256 checksum except itself.
`VALIDATION.json` records the checks run when this copy was prepared. Third-party
Markdown renderer license notices remain alongside their bundled code.
# htk
