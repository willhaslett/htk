"""Offline gh-api stand-in for exercising the ordinary GitHub PR command."""
import json
import os
import sys


def pr(number, **changes):
    record = {
        "number": number, "title": f"Improve feature {number}",
        "url": f"https://github.com/example/project/pull/{number}",
        "state": "OPEN", "isDraft": False, "headRefName": f"feature-{number}",
        "mergedAt": None, "closedAt": None, "updatedAt": "2026-10-21T12:00:00Z",
        "author": {"login": "alice"}, "repository": {"nameWithOwner": "example/project"},
        "assignees": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        "reviewDecision": None, "reviewRequests": {"totalCount": 0},
        "headRefOid": f"head-{number}",
        "commits": {"nodes": [{"commit": {"oid": f"head-{number}", "statusCheckRollup": None}}]},
    }
    record.update(changes)
    return record


def page(nodes, next_cursor=None, total=None):
    return {"nodes": nodes, "issueCount": len(nodes) if total is None else total,
            "pageInfo": {"hasNextPage": next_cursor is not None, "endCursor": next_cursor}}


def response(body):
    query = body["variables"]["query"]
    cursor = body["variables"]["cursor"]
    draft = pr(11, title="Improve retry behavior [A2]", isDraft=True,
               assignees={"nodes": [{"login": "alice"}], "pageInfo": {"hasNextPage": False}},
               reviewDecision="APPROVED",
               commits={"nodes": [{"commit": {"oid": "head-11", "statusCheckRollup": {"state": "FAILURE"}}}]})
    approved = pr(12, title="B1: Document the public API", reviewDecision="APPROVED",
                  commits={"nodes": [{"commit": {"oid": "head-12", "statusCheckRollup": {"state": "SUCCESS"}}}]})
    requested = pr(13, reviewRequests={"totalCount": 1})
    assigned = pr(14, title="Tune queue defaults", author={"login": "bob"},
                  assignees={"nodes": [{"login": "alice"}], "pageInfo": {"hasNextPage": False}},
                  reviewDecision="CHANGES_REQUESTED")
    if "is:open" in query:
        assert "statusCheckRollup { state }" in body["query"]
        if "author:alice" in query:
            return page([draft, approved], "author-page-2", 3) if cursor is None else page([requested], total=3)
        if "assignee:alice" in query:
            return page([draft, assigned])
    if "is:merged" in query:
        if cursor is None:
            nodes = [pr(30 + i, state="MERGED", mergedAt=f"2026-09-{20-i:02d}T00:00:00Z",
                        closedAt=f"2026-09-{20-i:02d}T00:00:00Z") for i in range(10)]
            return page(nodes, "merged-page-2", 11)
        return page([pr(50, state="MERGED", mergedAt="2026-10-01T00:00:00Z",
                        closedAt="2026-10-01T00:00:00Z", updatedAt="2026-10-01T00:00:00Z")], total=11)
    if "is:unmerged" in query:
        return page([pr(60, state="CLOSED", isDraft=True, closedAt="2026-09-28T00:00:00Z")])
    raise ValueError("Unexpected fixture query")


if __name__ == "__main__":
    assert sys.argv[1:3] == ["api", "--hostname"]
    assert sys.argv[4:] == ["graphql", "--input", "-"]
    body = json.load(sys.stdin)
    mode = os.environ.get("FAKE_GH_MODE")
    if mode == "error":
        print("private-service-error-token", file=sys.stderr)
        raise SystemExit(1)
    if mode == "malformed":
        print("not json")
    else:
        print(json.dumps({"data": {"search": response(body)}}))
