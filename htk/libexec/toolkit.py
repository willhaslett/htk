"""Productivity views for the terminal, with optional personal configuration."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
import uuid

# Tests also load this entry point directly, without adding libexec to sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from skill_compose import compose
from cli_support import ArgumentParser

ROOT = Path(__file__).resolve().parents[1]
CORE = {'mdview': 'mdview/mdview.py', 'name-session': 'session_name/name.py', 'list-view': 'list_view/viewer.py', 'snapshot-list': 'snapshot_list/viewer.py', 'status-list': 'snapshot_list/viewer.py', 'github-pr-status': 'github_pr_status/provider.py'}

COMMANDS = {'status': ('status-list', "Watch a workspace's persisted work records"), 'mdview': ('mdview', 'Live Markdown rendering in a reusable document window'), 'list': ('list-view', 'Render JSON rows from a file, data or command'), 'github-prs': ('github-pr-status', 'PR status using ordinary GitHub APIs'), 'name-session': ('name-session', 'Name a local agent conversation')}

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


def data_home():
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "htk-portable"


def runtime_home():
    explicit = os.environ.get("HTK_RUNTIME") or os.environ.get("DAILY_TOOLKIT_RUNTIME")
    if explicit:
        return Path(explicit).expanduser()
    return data_home() / "runtime"


def merge_settings(base, override):
    """Tables merge recursively; a leaf value or array replaces the inherited one."""
    result = deepcopy(base)
    for key, value in override.items():
        result[key] = (merge_settings(result[key], value)
                       if isinstance(result.get(key), dict) and isinstance(value, dict)
                       else deepcopy(value))
    return result


def read_layers(source, stack=()):
    if source in stack:
        raise ValueError("Profile inheritance cycle: " + " -> ".join(map(str, (*stack, source))))
    with source.open("rb") as handle:
        own = tomllib.load(handle)
    if own.get("schema") != 1:
        raise ValueError("Profile schema must be 1: " + str(source))
    parents = own.pop("extends", [])
    if not isinstance(parents, list) or any(not isinstance(item, str) for item in parents):
        raise ValueError("Profile extends must be an array of TOML paths.")
    config = {}
    skills = []
    for parent in parents:
        path = Path(parent).expanduser()
        inherited = read_layers((source.parent / path).resolve(), (*stack, source))
        skills.extend(inherited.pop("_skill_sources", []))
        config = merge_settings(config, inherited)
    # Resolve at the declaring file, before merging, so repos can move together.
    def absolute(value):
        path = Path(value).expanduser()
        return str((source.parent / path).resolve())
    profile = own.get("profile", {})
    for key in ("skill", "command_wrapper"):
        if key in profile:
            profile[key] = absolute(profile[key])
    if profile.get("skill"):
        skills.append(profile["skill"])
    for spec in own.get("commands", {}).values():
        if "script" in spec:
            spec["script"] = absolute(spec["script"])
    # Normalize the legacy spelling before inheritance so layer precedence and
    # relative source paths behave identically with either spelling.
    if "projects" in own:
        own["workspaces"] = merge_settings(own.pop("projects"), own.get("workspaces", {}))
    for workspace in own.get("workspaces", {}).values():
        if "status" in workspace:
            workspace["status"] = absolute(workspace["status"])
    config = merge_settings(config, own)
    config["_skill_sources"] = list(dict.fromkeys(skills))
    return config


def load_profile(path=None):
    path = path or os.environ.get("HTK_CONFIG") or os.environ.get("DAILY_TOOLKIT_CONFIG")
    if not path:
        config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config").expanduser()
        for name in ("htk", "daily-toolkit"):
            default = config_home / name / "toolkit.toml"
            if default.is_file():
                path = default
                break
    if not path:
        return read_layers(ROOT / "profiles/core.toml")
    source = Path(path).expanduser().resolve()
    config = read_layers(source)
    config["_path"] = str(source)
    config["_root"] = str(source.parent)
    for key in ("name", *(["skill_name"] if config.get("profile", {}).get("skill") else [])):
        if not NAME.fullmatch(config.get("profile", {}).get(key, "")):
            raise ValueError("Profile " + key + " must be a simple name.")
    if not isinstance(config["profile"].get("include_core_skill", False), bool):
        raise ValueError("Profile include_core_skill must be true or false.")
    if not isinstance(config["profile"].get("load_at_startup", False), bool):
        raise ValueError("Profile load_at_startup must be true or false.")
    for name, spec in config.get("commands", {}).items():
        if not NAME.fullmatch(name) or name in CORE or name in ("htk", "daily-toolkit", *COMMANDS):
            raise ValueError("Invalid extension command: " + name)
        if not isinstance(spec.get("script"), str):
            raise ValueError("Extension needs a Python script: " + name)
    for name in config["profile"].get("skill_aliases", []) + config["profile"].get("legacy_markers", []):
        if not NAME.fullmatch(name):
            raise ValueError("Invalid skill alias or legacy marker.")
    return config


def profile_path(config, value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else Path(config.get("_root", ROOT)) / path


def dispatch(config, command, args, *, prog=None):
    args = list(args)
    env = os.environ.copy()
    env["DAILY_TOOLKIT_ROOT"] = str(ROOT)
    env["HTK_ROOT"] = str(ROOT)
    env["HTK_CLI_PROG"] = prog or command
    if config.get("_path"):
        env["HTK_CONFIG"] = config["_path"]
    profile = config["profile"]
    if command in ("status-list", "snapshot-list"):
        workspaces = config.get("workspaces", {})
        if args in (["--workspaces"], ["--projects"]) or not args:
            if not args:
                from snapshot_list.arguments import make_parser
                help_parser = make_parser()
                help_parser.prog = prog or command
                help_parser.print_help()
                print()
            print("Usage: status WORKSPACE (or a file path)\n")
            for name, workspace in sorted(workspaces.items()):
                if workspace.get("status"):
                    print(f"{name:16} {profile_path(config, workspace['status'])}")
            if not any(workspace.get("status") for workspace in workspaces.values()):
                print("No status workspaces configured.")
            print("\nWorkspace aliases come from [workspaces.NAME] in " +
                  config.get("_path", "your toolkit.toml profile") + ".")
            return
        if args and args[0] in workspaces:
            name = args[0]
            workspace = workspaces[name]
            if not workspace.get("status"):
                raise ValueError(f"Workspace {name!r} has no status file configured; use status --workspaces.")
            args[0] = str(profile_path(config, workspace["status"]))
            if not any(item == "--title" or item.startswith("--title=") for item in args):
                args += ["--title", name.replace("-", " ").replace("_", " ").upper() + " STATUS"]
        elif args and not args[0].startswith("-"):
            name = args[0]
            if not Path(name).expanduser().is_file() and "/" not in name:
                raise ValueError(f"No workspace or file named {name!r}; use status --workspaces or an explicit file path.")
    if command in CORE:
        script = ROOT / "libexec" / CORE[command]
    else:
        spec = config.get("commands", {}).get(command)
        if not spec:
            raise ValueError("Unknown command: " + command)
        missing = [item for item in spec.get("requires", []) if not shutil.which(item)] if not any(
            item in ("--help", "-h") for item in args) else []
        if missing:
            raise ValueError(command + " needs optional integration commands: " + ", ".join(missing) +
                             ". See the profile's integration documentation; credentials are not installed by the toolkit.")
        script = profile_path(config, spec["script"])
        args = spec.get("default_args", []) + args
    launch = [sys.executable, str(script), *args]
    if profile.get("command_wrapper"):
        launch = [sys.executable, str(profile_path(config, profile["command_wrapper"])), command, *launch]
    os.execve(sys.executable, launch, env)


def guidance_block(config):
    if not config["profile"].get("skill"):
        return ""
    name = config["profile"]["name"]
    skill = config["profile"]["skill_name"]
    when = "At session startup, read" if config["profile"].get("load_at_startup") else "For tasks using the toolkit, consult"
    return (f"\n<!-- {name}:start -->\n## Workflow utilities\n\n"
            f"{when} the installed ${skill} skill's SKILL.md\n"
            f"(in the skills/{skill} directory of your Codex home). It describes the\n"
            "available tools and conventions. Supporting references provide detail\n"
            "when useful; the agent chooses how to approach the task.\n"
            f"<!-- {name}:end -->\n")


def update_guidance(original, config):
    text = original
    profile = config["profile"]
    names = [profile["name"], *profile.get("legacy_markers", [])]
    for name in names:
        start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
        if start not in text and end not in text:
            continue
        if text.count(start) != 1 or text.count(end) != 1 or text.index(end) < text.index(start):
            raise ValueError("Malformed startup guidance markers for " + name)
        first, last = text.index(start), text.index(end) + len(end)
        if first and text[first - 1] == "\n":
            first -= 1
        if text[last:last + 1] == "\n":
            last += 1
        text = text[:first] + text[last:]
    return text + (guidance_block(config) if profile.get("skill") else "")


def atomic_text(path, value, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    temporary.write_text(value)
    temporary.chmod(mode)
    os.replace(temporary, path)


def atomic_link(path, target):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    temporary.symlink_to(target)
    os.replace(temporary, path)


def active_installation(state, bin_dir):
    """Find the manifest owning this bin's current launcher, if still untouched."""
    pointer = state / "last-install.json"
    if not pointer.is_file():
        return None, {}
    path = Path(json.loads(pointer.read_text())["manifest"])
    if not path.is_file():
        return None, {}
    manifest = json.loads(path.read_text())
    for name in ("htk", "daily-toolkit"):
        launcher = bin_dir / name
        if launcher.is_symlink() and any(
                item["path"] == str(launcher) and item["new"] == os.readlink(launcher)
                for item in manifest["links"]):
            return path, manifest
    return None, {}


def install(config, bin_dir, codex_dir, state, replace=False):
    profile = config["profile"]
    state = state.resolve()
    identity = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    generation = state / "installations" / profile["name"] / identity
    commands = ["htk", "daily-toolkit", *CORE, *config.get("commands", {})]
    source_skill = profile_path(config, profile["skill"]) if profile.get("skill") else None
    if source_skill and not (source_skill / "SKILL.md").is_file():
        raise ValueError("Skill is missing: " + str(source_skill))
    assembled = None
    skill = source_skill
    if source_skill and (profile.get("include_core_skill", False) or len(config.get("_skill_sources", [])) > 1):
        assembled = composed_skill(config)
        skill = generation / "skills" / profile["skill_name"]
    links = [(bin_dir / name, generation / "bin" / name, True) for name in commands]
    if source_skill:
        links += [(codex_dir / "skills" / name, skill, False)
                  for name in [profile["skill_name"], *profile.get("skill_aliases", [])]]
    previous_path, previous = active_installation(state, bin_dir)
    for destination, target, command in links:
        if destination.exists() or destination.is_symlink():
            owned = any(item["path"] == str(destination) and item["new"] == os.readlink(destination)
                        for item in previous.get("links", [])) if destination.is_symlink() else False
            same = destination.is_symlink() and (owned or destination.resolve() == target.resolve() or
                   (not command and destination.resolve() == source_skill.resolve()) or
                   destination.resolve().is_relative_to(state / "installations" / profile["name"]))
            if not same and not (command and replace and destination.is_symlink()):
                raise ValueError("Refusing to replace " + str(destination) + "; command symlinks need --replace-command-link.")
    guidance = codex_dir / "AGENTS.md"
    if guidance.is_symlink():
        guidance = guidance.resolve()
    original = guidance.read_text() if guidance.exists() else None
    guidance_config = deepcopy(config)
    if previous and previous.get("guidance") == str(guidance):
        old_name = previous.get("profile_name", previous_path.parent.parent.name)
        if old_name != profile["name"]:
            guidance_config["profile"].setdefault("legacy_markers", []).append(old_name)
    updated = update_guidance(original or "", guidance_config)
    if original is None and not updated:
        updated = None
    prior = [{"path": str(destination), "old": os.readlink(destination) if destination.is_symlink() else None,
              "new": str(target)} for destination, target, _ in links]
    destinations = {str(destination) for destination, _, _ in links}
    retired = []
    for item in previous.get("links", []):
        path = Path(item["path"])
        if (item["path"] not in destinations and path.parent in (bin_dir, codex_dir / "skills")
                and path.is_symlink() and os.readlink(path) == item["new"]):
            retired.append({"path": str(path), "old": item["new"]})
    manifest = {"root": str(ROOT), "config": config.get("_path"), "profile_name": profile["name"],
                "previous_manifest": str(previous_path) if previous_path else None,
                "links": prior, "retired_links": retired,
                "guidance": str(guidance), "original_guidance": original, "installed_guidance": updated,
                "guidance_mode": guidance.stat().st_mode & 0o777 if guidance.exists() else 0o600}
    generation.mkdir(parents=True)
    atomic_text(generation / "manifest.json", json.dumps(manifest, indent=2))
    if assembled is not None:
        atomic_text(skill / "SKILL.md", assembled)
        # Preserve optional skill UI metadata and executable/resource directories.
        for child in source_skill.iterdir():
            if child.name != "SKILL.md":
                (skill / child.name).symlink_to(child.resolve(), target_is_directory=child.is_dir())
    for name in commands:
        env = {"HTK_RUNTIME": str(runtime_home()), "DAILY_TOOLKIT_RUNTIME": str(runtime_home())}
        if config.get("_path"):
            env["DAILY_TOOLKIT_CONFIG"] = config["_path"]
            env["HTK_CONFIG"] = config["_path"]
        content = "#!/bin/bash\nset -euo pipefail\n"
        for key, value in env.items():
            content += "export " + key + "=" + shlex.quote(value) + "\n"
        content += "exec " + shlex.quote(str(ROOT / "bin" / name if name in CORE or name in ("htk", "daily-toolkit") else ROOT / "bin/htk"))
        if name not in CORE and name not in ("htk", "daily-toolkit"):
            content += " run " + shlex.quote(name)
        content += ' "$@"\n'
        atomic_text(generation / "bin" / name, content, 0o755)
    changed_links = []
    removed_links = []
    try:
        if updated is not None:
            atomic_text(guidance, updated, manifest["guidance_mode"])
        for (destination, target, _), previous in zip(links, prior):
            atomic_link(destination, target)
            changed_links.append(previous)
        for item in retired:
            Path(item["path"]).unlink()
            removed_links.append(item)
    except OSError:
        for item in reversed(removed_links):
            atomic_link(Path(item["path"]), item["old"])
        for previous in reversed(changed_links):
            destination = Path(previous["path"])
            if previous["old"] is None:
                destination.unlink()
            else:
                atomic_link(destination, previous["old"])
        if original is None:
            guidance.unlink(missing_ok=True)
        else:
            atomic_text(guidance, original, manifest["guidance_mode"])
        print("Installation failed; previous links and guidance restored. Manifest: " + str(generation / "manifest.json"), file=sys.stderr)
        raise
    atomic_text(state / "last-install.json", json.dumps({"manifest": str(generation / "manifest.json")}))
    return generation / "manifest.json"


def rollback(manifest_path):
    manifest = json.loads(manifest_path.read_text())
    guidance = Path(manifest["guidance"])
    actual = guidance.read_text() if guidance.exists() else None
    if actual != manifest["installed_guidance"]:
        raise ValueError("Startup guidance changed after installation; inspect the manifest before restoring it.")
    for item in manifest["links"]:
        path = Path(item["path"])
        if not path.is_symlink() or os.readlink(path) != item["new"]:
            raise ValueError("Installed link changed; inspect before restoring: " + str(path))
    for item in manifest.get("retired_links", []):
        path = Path(item["path"])
        if path.exists() or path.is_symlink():
            raise ValueError("Retired link path changed; inspect before restoring: " + str(path))
    for item in reversed(manifest["links"]):
        path = Path(item["path"])
        if item["old"] is None:
            path.unlink()
        else:
            atomic_link(path, item["old"])
    for item in manifest.get("retired_links", []):
        atomic_link(Path(item["path"]), item["old"])
    if manifest["original_guidance"] is None:
        guidance.unlink(missing_ok=True)
    else:
        atomic_text(guidance, manifest["original_guidance"], manifest["guidance_mode"])
    pointer = manifest_path.parent.parent.parent.parent / "last-install.json"
    if pointer.is_file() and Path(json.loads(pointer.read_text())["manifest"]) == manifest_path:
        if manifest.get("previous_manifest"):
            atomic_text(pointer, json.dumps({"manifest": manifest["previous_manifest"]}))
        else:
            pointer.unlink()


def doctor(config):
    import curses
    import ssl
    print("Core: ready; Python " + sys.version.split()[0] + "; ssl, curses, local viewers available")
    for command, note in (("codex", "session rename"), ("herdr", "optional session-name workspace discovery"),
                          ("gh", "GitHub PR status")):
        path = shutil.which(command)
        print(note + ": " + (path or "unavailable (optional)"))
    for name, spec in config.get("commands", {}).items():
        missing = [item for item in spec.get("requires", []) if not shutil.which(item)]
        print(name + ": " + ("missing " + ", ".join(missing) if missing else "local executables present; access and remote runtime checked when used"))
    profile = config["profile"]
    if profile.get("include_core_skill", False):
        installed = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skills" / profile["skill_name"] / "SKILL.md"
        current = installed.is_file() and installed.read_text() == composed_skill(config)
        print("Assembled skill: " + ("current" if current else "not installed or sources changed; htk install refreshes it"))
    print("Applications, external logins and model access are installed separately.")


def composed_skill(config):
    sources = config.get("_skill_sources", [str(profile_path(config, config["profile"]["skill"]))])
    return compose(*(Path(directory) / "SKILL.md" for directory in sources))


def main():
    parser = ArgumentParser(prog="htk", description=__doc__,
        epilog="Views refresh until q/Ctrl-C. Use COMMAND --help for options. "
               "Legacy standalone command names and 'run COMMAND' remain supported.")
    parser.add_argument("--config", help="Profile TOML; optional for standalone viewers")
    parser.add_argument("--clients", action="store_true", help="Print JSON client commands, pane sizes and lifecycle metadata")
    sub = parser.add_subparsers(dest="action")
    for name, (_, description) in COMMANDS.items():
        command = sub.add_parser(name, help=description, add_help=False)
        command.add_argument("args", nargs=argparse.REMAINDER)
    run = sub.add_parser("run")
    run.add_argument("command")
    run.add_argument("args", nargs=argparse.REMAINDER)
    sub.add_parser("doctor", help="Inspect local capabilities and optional integrations")
    setup = sub.add_parser("install", help="Install versioned command launchers and optional profile skill")
    setup.add_argument("--config", dest="install_config")
    setup.add_argument("--bin-dir", type=Path, default=Path.home() / ".local/bin")
    setup.add_argument("--codex-dir", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    setup.add_argument("--state-dir", type=Path, default=data_home())
    setup.add_argument("--replace-command-link", action="store_true")
    setup.add_argument("--skill", metavar="DIRECTORY", help="Optional skill source directory")
    restore = sub.add_parser("rollback", help="Restore links and guidance from an installation manifest")
    restore.add_argument("manifest", type=Path)
    # Parse only the front command for views; their own parser owns all options.
    argv = sys.argv[1:]
    command_index = 2 if argv[:1] == ["--config"] else 0
    if argv and argv[0].startswith("--config="):
        command_index = 1
    direct = argv[command_index] if len(argv) > command_index else None
    forwarded = argv[command_index + 1:] if direct in COMMANDS else []
    args = parser.parse_args(argv[:command_index + 1] if direct in COMMANDS else argv)
    try:
        if args.clients:
            if args.action:
                parser.error("--clients is used without a command")
            print((ROOT / "libexec/client_catalog/clients.json").read_text(), end="")
            return 0
        if args.action is None:
            parser.error("a command is required")
        if args.action == "rollback":
            rollback(args.manifest)
            print("Previous links and startup guidance restored; runtime and user data retained.")
            return 0
        config = load_profile(getattr(args, "install_config", None) or args.config)
        if args.action in COMMANDS:
            dispatch(config, COMMANDS[args.action][0], forwarded, prog="htk " + args.action)
        elif args.action == "run":
            if args.command not in CORE and args.command not in config.get("commands", {}):
                run.print_help(sys.stderr)
            dispatch(config, args.command, args.args)
        elif args.action == "doctor":
            doctor(config)
        elif args.action == "install":
            if args.skill:
                source = Path(args.skill).expanduser().resolve()
                if not NAME.fullmatch(source.name):
                    raise ValueError("Skill directory needs a simple skill name.")
                directory = str(source)
                config["profile"].update(skill=directory, skill_name=source.name)
                config.setdefault("_skill_sources", []).append(directory)
            manifest = install(config, args.bin_dir.expanduser().absolute(), args.codex_dir.expanduser().absolute(),
                               args.state_dir.expanduser().absolute(), args.replace_command_link)
            print("Installed. Rollback: " + shlex.quote(str(args.bin_dir.expanduser().absolute() / "htk")) + " rollback " + shlex.quote(str(manifest)))
            if str(args.bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
                print("Add to your shell PATH: " + str(args.bin_dir))
    except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
