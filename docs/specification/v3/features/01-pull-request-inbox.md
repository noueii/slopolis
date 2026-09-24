# 12.1 Pull-request inbox

The app's entry surface: every open pull request across the workspace's connected repositories,
with what slopolis knows about each one, and a dock that turns a selection into a review session.

---

## 1. The list

One row per open pull request, flat (not grouped by repository), newest activity first by default.
A repository filter does the grouping job when it is wanted.

| Element | Source |
|---|---|
| Title | PR title; links to GitHub |
| `owner/name #number` | repository + number |
| Review state | joined from session targets (§2) |
| Diff size | `+additions −deletions · changedFiles files` |
| CI rollup | `PullRequestChecks` (`passing` / `failing` / `pending` / `none`) |
| Author | avatar + handle |
| Last activity | relative time from `updatedAt` |
| Draft | badged, and filtered out by default |

Rows are the selection surface: **clicking a row toggles its selection** rather than navigating.
The checkbox appears on hover and stays visible on selected rows. The title carries the external
link, and the session reference carries the link to history.

**Excluded:** closed and merged pull requests. A session outlives its PR, so history for a merged
PR lives on `/sessions`; the inbox is for open work only.

---

## 2. Review state

The column that makes the inbox worth opening. It is derived per PR by joining the workspace's
session targets on `(repository, number)`:

| State | Meaning | Row affordance |
|---|---|---|
| `never` | No session target has ever covered this PR | — |
| `queued` | A target is enqueued | progress is unknown; show `queued` |
| `running` | A target is executing | progress `0–100` + current step, live |
| `reviewed` | The latest target finished and recorded the commit it read | link to the session, findings count |
| `failed` | The latest target failed or was cancelled and no later attempt succeeded | link to the session, "retry available" |

**Staleness is orthogonal to state, and is decided by two SHAs.** The app compares the commit the
review recorded (`reviewedSha`) against the pull request's current head:

| SHAs | `commitsSinceReview` | Row |
|---|---|---|
| equal | `0` | `Reviewed · N findings` |
| different | the commit count from GitHub's compare | `N commits behind` |
| different, counted by nobody | `null` | `New commits since review` |
| no recorded SHA (reviewed before `0008`) | `null` | `Reviewed · commit not recorded` |

`0` only ever means *measured and current*; a distance the app cannot count is `null`, never a
guess. A review with no recorded commit makes no freshness claim but is still **selected by the
`stale` filter and counted in the backlog**: it cannot be called current, and the only way to
settle it is another review. A stale review is the highest-value row on the screen, and its natural
action is the same as any other row — select it and say what to look at.

An in-flight review (`queued`/`running`) outranks a completed one for the same PR: a new session
on a PR already reviewed shows as running, then returns to `reviewed` at the new head.

---

## 3. Filters and search

Server-side, mirroring the Sessions screen's shape (debounced `q`, single-select facets, paged):

| Control | Values |
|---|---|
| Search `q` | PR title, repository full name, `#number`, `owner/name#number`, author handle |
| Repository | any connected repository, or all |
| Review | `never`, `queued`, `running`, `reviewed`, `stale`, `failed`, or all |
| Checks | `passing`, `failing`, `pending`, `none`, or all |
| Drafts | hidden by default; a switch includes them |
| Sort | `updated_desc` (default), `size_desc`, `staleness_desc`, `created_desc` |

`stale` means *reviewed with the head not known to be covered* — a differing SHA, or no recorded
SHA at all — and `needsReview` is `never` plus `stale`. It is decided from the two SHAs, which the
cheap tier already has, so the filter is exact before anything is hydrated.

The response carries `summary` (open / needs review / stale / running) and `filterOptions` with
counts, so the header can state the backlog without a second request. Repository options carry their
open-PR counts; the checks facet carries none, because CI state is only known for rows the expensive
tier read.

---

## 4. The dock

A bottom-docked panel, spanning the list's width, present whenever something is selected. It is
the only way a session is submitted: a row click selects, and the selection raises the dock.

| Mode | Contents |
|---|---|
| **Hidden** | Nothing selected (and no session just created from this dock) |
| **Compact** | Selected chips (`owner/name#123`, removable), preset selector, one-line prompt, `Review` |
| **Expanded** | The full composer: multiline prompt, attachments, pre-flight notices, submit, created-session panel |

- Selecting raises the compact dock; focusing the prompt (or the expand affordance) switches to the
  expanded composer, and the collapse affordance returns to compact.
- A session just created keeps the dock open until `Start another` is clicked, even if the
  selection is cleared underneath it.
- Clearing the selection hides the dock.
- Selection survives filter changes: a chip stays in the dock even when its row is filtered out.
- Shift-click extends a range; the list header selects or clears the rows on the current page.
- `⌘/ctrl+Enter` submits from the prompt.

Mobile: the dock is bottom-docked with a safe-area inset and a single scrollable chip row; rows
become two lines (title/repo, then review state + CI).

---

## 5. States

| State | Rendering |
|---|---|
| Loading | Row skeletons under a working filter bar |
| Empty (no repositories connected) | Install-the-GitHub-App call to action |
| Empty (filters match nothing) | "No pull requests match" with a clear-filters action |
| Empty (repositories connected, no open PRs) | "Nothing open" with a repository link |
| Error | Inline error above the list with a retry; the filter bar stays usable |

The mock serves all of them (`default` / `empty` / `error` / `slow` scenarios) so UI work can be
done offline; the real endpoint serves the last three from real conditions.

---

## 6. API surface

```
GET /api/pull-requests?q=&repo=&review=&checks=&drafts=&sort=&page=&pageSize=
```

```ts
interface PullRequestReview {
  state: "never" | "queued" | "running" | "reviewed" | "failed"
  sessionId: string | null       // session holding this PR's latest review
  reviewedSha: string | null     // head SHA the last completed review covered
  commitsSinceReview: number | null  // 0 = current, N = behind, null = not counted
  findingsCount: number
  progress: number | null        // 0–100 while queued/running
  step: string | null            // current step while queued/running
  reviewedAt: string | null
}

interface PullRequestListItem extends OpenPullRequest {
  headBranch: string
  headSha: string
  review: PullRequestReview
}

interface PullRequestListResponse extends Paginated<PullRequestListItem> {
  summary: { total: number; needsReview: number; stale: number; running: number }
  filterOptions: {
    repositories: FilterOption[]
    reviews: FilterOption[]
    checks: FilterOption[]
  }
  generatedAt: string
}
```

Submission is unchanged: `POST /api/sessions/preflight` then `POST /api/sessions` with
`prUrls`, `prompt`, `preset`.

### GitHub cost

A repository's PR list costs `1 + 2 × open PRs` calls if every row is read fully (a detail read and
a check-runs read per PR), which a workspace-wide inbox must not pay on every page view. Reads are
tiered in `apps/server/app/routers/_pull_reads.py` and `pull_requests.py`:

1. **Cheap tier** — one `list_open_pull_requests` per connected, enabled repository, TTL-cached per
   installation. It carries the head SHA, so filtering, sorting, paging, the summary and the review
   join all happen without a per-PR read, **including staleness**.
2. **Expensive tier** — `get_pull_request` (diff size) and `list_check_runs` (CI) per row, plus one
   `compare_commits` for a reviewed row whose SHA differs from the head. Cached per pull request,
   bounded concurrency, and hydrated for the returned page — or for the whole filtered set when the
   answer needs every row (`sort=size_desc`, the `checks` filter), bounded by the tool budget.
3. A pull request GitHub refuses degrades that row to zeros and `checks.state = "none"`; a
   repository it refuses is logged and skipped; only every repository failing is a 502.

---

## 7. Status

**Landed.** `GET /api/pull-requests` is implemented in `apps/server/app/routers/pull_requests.py`
(join in `_pull_request_query.py`, reads in `_pull_reads.py`), `GET /api/dashboard` and the
per-repository `/pulls` picker endpoint are deleted with it, and `session_targets.reviewed_sha`
exists (`0008_target_reviewed_sha`) and is written by the worker's review attempt — never by a
publish-only retry, which re-reads the pull request and would otherwise claim a review covers
commits it never read.

**The mock is dev-only now.** `src/mocks/pullRequests.ts` keeps the inbox dataset for
`make dev-mock` and design work, exactly like the sessions, providers and usage mocks, and is
**bypassed under `VITE_MOCK=off`** so a deployment against the real API never shows invented pull
requests.

Still open, each a candidate slice:

- **The checks facet carries no count** — CI state is read per row by the expensive tier, so a
  count over rows nobody hydrated would be a guess.
- **`sort=size_desc` and the `checks` filter hydrate the whole filtered set**, because the order
  and the filter are numbers only the expensive tier has. Bounded by the per-request tool budget.
- **Filters are not URL-addressable.** The route model stores a nav id only; sharing a filtered
  view is a later slice.
- **Untracked reviews are transitional.** A review recorded before `0008` reports "commit not
  recorded" and counts as stale until it is reviewed again; the commit it covered was never
  recorded anywhere, so there is nothing to backfill.
