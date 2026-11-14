"""Watch workspace status from task data or a saved Markdown status document."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from snapshot_list.arguments import make_parser
from toolkit import load_profile
from source import ENTRY, document
from snapshot_list.task_data import parse_tasks
from snapshot_list.work_records import source_document, task_document


def status_document(path, title=None, hide_completed=False):
    text = path.read_text()
    if path.suffix.lower() == ".json" or text.lstrip().startswith("{"):
        return task_document(parse_tasks(text), title, path, hide_completed)
    if hide_completed:
        raise ValueError("--hide-completed requires a JSON task source")
    # Saved status tables and plain work lists are inputs to the same viewer.
    # A malformed table must report an error rather than become a plain list.
    if any(line.lstrip().startswith("|") or ENTRY.match(line) for line in text.splitlines()):
        return document(path, title)
    return source_document(text, title or "STATUS", path)


def overall(args, parser):
    if args.file is not None:
        parser.error("--all cannot be combined with a file or workspace alias")
    if args.title is not None or args.hide_completed:
        parser.error("--title and --hide-completed apply to file views, not --all")
    try:
        command = load_profile().get("views", {}).get("status", {}).get("all_command")
        if (not isinstance(command, list) or not command
                or any(not isinstance(item, str) or not item or "\0" in item for item in command)):
            parser.exit(1, "Overall status is not configured; set [views.status].all_command in the active profile.\n")
        if args.json:
            os.execvp(command[0], command)
        renderer = Path(__file__).resolve().parents[1] / "list_view/viewer.py"
        launch = [sys.executable, str(renderer), "--refresh", "15"]
        if args.once or not (sys.stdin.isatty() and sys.stdout.isatty()):
            launch.append("--once")
        os.execv(sys.executable, [*launch, "--", *command])
    except (OSError, ValueError) as error:
        parser.exit(1, f"Overall status unavailable: {error}\n")


def main(argv=None):
    parser = make_parser()
    args = parser.parse_args(argv)
    if args.all:
        return overall(args, parser)
    if args.file is None:
        parser.error("a source file, workspace alias or --all is required")
    path = args.file.expanduser().absolute()
    if args.json:
        try:
            print(json.dumps(status_document(path, args.title, args.hide_completed), ensure_ascii=False))
        except (OSError, UnicodeError, ValueError) as error:
            print(f"Status unavailable: {error}", file=sys.stderr)
            return 1
        return 0
    renderer = Path(__file__).resolve().parents[1] / "list_view/viewer.py"
    command = [sys.executable, str(renderer), "--refresh", "1"]
    if args.once or not (sys.stdin.isatty() and sys.stdout.isatty()):
        command.append("--once")
    command.extend(["--", sys.executable, str(Path(__file__).resolve()), str(path), "--json"])
    if args.title:
        command.extend(["--title", args.title])
    if args.hide_completed:
        command.append("--hide-completed")
    os.execv(sys.executable, command)


if __name__ == "__main__":
    raise SystemExit(main())
