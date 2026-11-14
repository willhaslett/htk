# GitHub PR status

`github-pr-status --user LOGIN` watches authored and assigned pull requests on
github.com through the optional `gh` CLI. Authenticate using your ordinary
`gh auth login` setup. `--hostname HOST` selects another GitHub host supported
by `gh`; the toolkit does not provision credentials. No profile is required.

```sh
github-pr-status --user octocat
github-pr-status --user octocat --once
github-pr-status --user octocat --hostname github.example.com --json
```

The list refreshes every ten seconds (`--refresh SECONDS` changes that). It uses
`list-view`, with `q`/Ctrl-C to close. Initially j selects the first PR and k
selects the last. Further j/k or arrow keys select adjacent PRs, wrapping at both
ends and using Calendar's yellow reverse-video highlight; Enter opens the
selected PR in the browser.
Selection stays with the PR across refreshes and scrolls into view. Page Up/Down
and Home/End remain available for scrolling. `--once` prints one
snapshot; `--json` emits one list-view document. A failed refresh keeps the last
successful list with a stale indicator. It stores no PR data or credentials.

The four sections show all authored open PRs, open PRs assigned to the login,
the last ten authored merged PRs, and the last ten authored PRs closed without
merging. The query covers all visible repositories on the selected host.
Self-assigned PRs appear in both current sections. Assigned means GitHub issue
assignees, not requested or historical reviewers. Draft status stays separate;
drafts with assignees are highlighted.

Status uses GitHub's review decision: Approved, Changes requested, or In review
when review is required/requested. Otherwise it shows Open. This does not infer
a custom merge queue or organization-specific lifecycle. Build is the latest
head commit's aggregate `statusCheckRollup.state`, without fetching individual
check runs; missing summaries are blank. Historical builds are blank.

The PR column links a local `[A1]` title label, `A1: ...` title prefix, or
`owner/A1-description` branch label; otherwise it links the PR number. Links
use the terminal's normal gesture (Control-click in Herdr). Columns are PR,
Title, Assigned, Status, and Build, leaving room for the title.

Four independent GraphQL searches fetch authored, assigned, merged, and closed
PRs; they paginate as needed. History is ordered by merge/close event time,
even when an older PR gets a new comment. GitHub's 1,000-result search limit or
an incomplete response fails the refresh if completeness cannot be established.
Each API call has a 20-second timeout; the watched provider has a 60-second
budget. Large accounts may hit this budget, reported as a failed refresh.

`presentation.py` provides the compact layout over plain normalized records.
`provider.py` supplies ordinary GitHub data and the shared GitHub history client.
Profile extensions can reuse the presentation and history client. Each adapter
normalizes its service's lifecycle into shared phases such as `in_review` and
`unassigned`, and supplies its own build summary.

Offline validation is in `tests/test_github_pr_status.py`; its fake `gh` uses
ordinary GitHub-shaped GraphQL responses, including pagination and errors.
