# HTK

HTK brings shared tools, keyboard navigation, and workflow conventions to
Herdr and Codex. It includes a shortcut popup, pane and workspace helpers,
list/status and Markdown viewers, GitHub PR viewing, and optional Codex session
naming. Saved workspace and conversation data stay in the applications' own
directories. Keep this checkout at its installed location; command and skill
links use its source files.

## Installation

Install Herdr and Ghostty separately; install Codex only if you want agent
launching/naming, and `gh` only for the ordinary GitHub viewer. Helpers target
Herdr 0.9.5+ and Ghostty 1.3+; the pane chord uses `activate_key_table_once`.
The optional native Markdown window needs Apple's Swift command line tools;
use `htk mdview FILE --browser` if those tools are unavailable.

1. Clone or unpack this folder into its permanent location. Paths with spaces work.
2. Run `./setup.sh`. It provisions Python 3.12 from public sources and installs
   commands into `~/.local/bin` and the `htk` skill/startup note into `~/.codex`.
   An incompatible `uv` on PATH is left untouched; the bootstrap downloads a
   private pinned copy. It preserves unrelated files and prints a rollback command. Add
   `~/.local/bin` to PATH. Use `./setup.sh --help` for installation behavior.
3. Link `personal/shell/codex` to `~/.local/bin/codex` and source
   `personal/shell/aliases.zsh` from your `.zshrc`. This puts the HTK launcher
   first on PATH so each new Codex terminal gets its own app server and correct
   Herdr pane identity. Keep the separately installed Codex CLI on PATH too.
4. Back up existing Herdr/Ghostty configuration. For a fresh installation, copy
   `personal/herdr/config.toml` to `~/.config/herdr/config.toml` and
   `personal/ghostty/config` to `~/.config/ghostty/config`. Copy
   `personal/ghostty/themes/Monokai Pro` into `~/.config/ghostty/themes/`.
   If configuration already exists, merge the relevant tables/keys instead of
   replacing it. Use one `[keys]` table and avoid duplicate bindings. This personal
   Ghostty config starts/attaches Herdr in each new terminal surface.
   When these are your complete configs, symlink them to this checkout so future
   edits have one source of truth. The theme can also be symlinked.
5. Link the popup: `herdr plugin link /absolute/path/to/this-folder/htk-vim/plugins/keymap`.
   Quote that path if it contains spaces. Run `herdr config check`, then
   `herdr server reload-config`; reload Ghostty's configuration too.
6. Run `herdr integration install codex` for native session identity reporting.
   This is separate from HTK's skill: it updates Codex hooks and configuration.
   Test the integration in a new Codex session; keep existing conversations running.
7. Run `htk doctor`, `htk list --once --data '["Ready"]'`, and Command+/.

Setup leaves both application configs unchanged until you apply them yourself.
It does not install apps, model credentials or fonts. The personal preset uses
Roboto Mono Light for Powerline with Menlo as a fallback. Re-run setup after moving the tree;
relink the popup too. Installed launchers use the tree at its installed location.
Source `personal/shell/aliases.zsh` for `status`, `hconfig`, `gconfig`, and `hreload`.
The `codex` launcher finds the separately installed Codex CLI and adds
`--no-daemon`, preserving all other arguments, including `resume`.
Each terminal then owns its app server and Herdr pane environment. With Codex
0.169.0, the shared daemon can pass another pane's environment to SessionStart
hooks and save a conversation's resume ID against the wrong Herdr pane.
Existing Codex processes can keep running; their next launch through `codex`
uses the independent server. Keep the real Codex installation on PATH too.
Codex startup guidance lives in `~/.codex/AGENTS.md` and links the HTK skill;
fresh local Codex sessions load it across projects, including outside Herdr.
The personal Codex defaults in `personal/codex/config.toml` set YOLO mode
(`approval_policy = "never"`, `sandbox_mode = "danger-full-access"`). Merge those
top-level keys into `~/.codex/config.toml`, preserving model and project settings.
An already-running session can switch through `/permissions`. Codex separately
tracks trust for hooks; review and trust the installed Herdr SessionStart hook
through `/hooks` when prompted.

## Workflow and shortcuts

Personal appearance and keyboard bindings are in `personal/`.
Reusable configuration snippets are in `htk-vim/presets/`.
Command+H/L changes tabs; Command+J/K changes workspaces. Shift moves them.
Option+Command+H/J/K/L navigates panes; Shift+Option+Command moves panes;
Control+Option+Command creates them. Command+N creates a numbered workspace;
Shift+Command+N adds a Codex manager. Command+/ opens the shortcut reference.
Ghostty forwards these keys, including prefix aliases; upstream shortcuts can
still intercept input. It is best used in a dedicated Herdr terminal profile.
Shift+Enter preserves multiline agent input. Option+Command+C enters copy mode;
Option+Command+E edits scrollback; Option+Command+Z zooms; Option+Command+S enters
resize mode. Ctrl+N then Q detaches, S opens settings, W opens the workspace
picker, and Shift+R reloads Herdr configuration. Shift+Command+Comma reloads
Ghostty configuration. Normal clipboard shortcuts remain available.

Bindings requiring external fleet/tab-close helpers are omitted. Command+W is
left disabled in Herdr rather than replacing its confirmation behavior with an
unsafe close. Option+Command+X retains the bundled protected pane-close helper.
The web/server application and remote execution adapters are outside this tree.
HTK adds no service daemons or login registrations. Ghostty attaches to Herdr's
existing persistent server; configuration reloads do not require stopping it.

## Verification

`./verify.sh` checks file integrity and runs the bundled tests in a relocated
temporary copy with an isolated home. It does not touch live Herdr services or
open browser windows. It uses the managed runtime after setup; alternatively,
set `HTK_PORTABLE_PYTHON` to a Python 3.12+ executable. Node.js enables an
additional nonvisual Markdown refresh regression; it is optional at runtime.

`MANIFEST.json` lists every packaged file and SHA-256 checksum except itself.
Git metadata, Python caches, and Finder metadata are excluded. After intentional
source changes, run `python3.12 -B verify.py --update-manifest`, then `./verify.sh`.
`VALIDATION.json` records the checks run when this copy was prepared. Third-party
Markdown renderer license notices remain alongside their bundled code.
