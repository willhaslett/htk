"""Status argument definitions shared by the viewer and source discovery."""

import os
from pathlib import Path

from cli_support import ArgumentParser


def make_parser():
    parser = ArgumentParser(prog=os.environ.get("HTK_CLI_PROG", "status-list"),
        description="Watch status from task data, a saved Markdown document or a configured overall view.",
        epilog="The installed command also accepts a configured workspace alias; --workspaces lists aliases and sources. "
               "With no arguments it shows help and configured sources. snapshot-list remains an alias.")
    parser.add_argument("file", nargs="?", type=Path, help="Workspace alias, task JSON, Markdown status document or archive")
    parser.add_argument("--all", action="store_true", help="Show the configured overall status view")
    parser.add_argument("--title", help="File view title (defaults to the document heading)")
    parser.add_argument("--once", action="store_true", help="Print the latest status without watching")
    parser.add_argument("--json", action="store_true", help="Emit one list-view JSON document")
    parser.add_argument("--hide-completed", action="store_true", help="Hide finished tasks in a JSON file source")
    return parser
