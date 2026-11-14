"""Semantic task data, independent of a particular view or workflow."""

import json


def parse_tasks(text):
    """Read version 1 task data; IDs are unique across the entire tree."""
    try:
        source = json.loads(text)
    except (ValueError, RecursionError) as error:
        raise ValueError(f"invalid task JSON: {error}") from None

    def fields(value, required, optional, location):
        if not isinstance(value, dict):
            raise ValueError(f"{location} must be an object")
        missing = set(required) - value.keys()
        unknown = value.keys() - set(required) - set(optional)
        if missing:
            raise ValueError(f"{location} missing fields: {', '.join(sorted(missing))}")
        if unknown:
            raise ValueError(f"{location} unknown fields: {', '.join(sorted(unknown))}")

    def string(value, location, nonempty=False):
        if not isinstance(value, str) or (nonempty and not value.strip()):
            raise ValueError(f"{location} must be a {'nonempty ' if nonempty else ''}string")

    fields(source, ("version", "title", "tasks"), (), "source")
    if type(source["version"]) is not int or source["version"] != 1:
        raise ValueError("source.version must be 1")
    string(source["title"], "source.title", True)
    ids = set()

    def tasks(items, location, depth=0):
        if not isinstance(items, list):
            raise ValueError(f"{location} must be an array")
        if depth > 32:
            raise ValueError("task children may nest at most 32 levels")
        for index, task in enumerate(items):
            where = f"{location}[{index}]"
            fields(task, ("id", "title", "state"), ("next", "notes", "children"), where)
            for key in ("id", "title", "state"):
                string(task[key], where + "." + key, True)
            if task["id"] != task["id"].strip():
                raise ValueError(f"{where}.id must have no leading or trailing whitespace")
            if task["id"] in ids:
                raise ValueError(f"duplicate task ID: {task['id']}")
            ids.add(task["id"])
            for key in ("next", "notes"):
                if key in task:
                    string(task[key], where + "." + key)
            if "children" in task:
                tasks(task["children"], where + ".children", depth + 1)

    tasks(source["tasks"], "tasks")
    return source
