# slopolis — Review inbox (v3)

**Spec version:** v3
**Status:** implemented — inbox, endpoint and mock; held open for the wiring caveats in §7
**Last updated:** 2026-09-21
**Scope:** the app's entry surface — how a review is started

> v1 remains the approved MVP and v2 the agent harness; both carry forward unchanged except where
> this version overrides them. v3 changes **where a review starts**, not what a review does.

---

## 1. The change in one line

> Instead of one review instance at a time behind a form, the app opens on a **list of open pull
> requests across every connected repository**; selecting rows raises a **sticky dock** holding the
> prompt and the preset.

---

## 2. Why

The v1 flow makes the composer the destination and the pull request a form field inside it. That
inverts the work: a user arrives knowing which PR they care about and then has to find it in a
picker that only lists one repository at a time (v1 §10.4, "paste PR URLs").

A list-first inbox also carries information the form cannot: **slopolis's own relationship to each
PR** — never reviewed, reviewing right now, reviewed at a commit that has since moved on. That join
is the reason to open this app instead of GitHub's PR list, and it makes multi-select the natural
gesture, which is what sessions already support (one submission, N targets).

---

## 3. Locked decisions

| Decision | Choice |
|---|---|
| Entry surface | Pull-request inbox at `/`; sessions stay a separate history surface |
| Dock placement | Bottom, sticky, spanning the list; not a top bar, not a right rail |
| Dock content | One-shot review request (prompt + preset + attachments). Phase-4 chat mode is **not** pulled in |
| Selection | Multi-select; row click toggles; selection drives the dock |
| Review state | Joined per PR from the session targets that reviewed it |
| Staleness | `reviewed_sha` vs the PR's current head SHA: a differing SHA is behind (counted by GitHub's compare, `null` when it will not), an equal SHA is current, and a review that recorded no SHA claims neither |
| Legacy reviews | A review recorded before `reviewed_sha` existed reports `reviewedSha: null`, `commitsSinceReview: null`, and **counts as stale** — the app cannot call it current, and the row says "commit not recorded" rather than inventing a distance |
| Filtering | Server-side (`q`, repo, review state, checks, drafts), URL-addressable later |
| Cost | Tiered GitHub reads: one list call per repository, CI rollup hydrated lazily |
| Paste a URL | **Dropped.** Every reviewable PR is in the inbox — an uncovered repository is refused by pre-flight (README gap 3) — and the paste affordance was already removed in `85fc0b2` |

---

## 4. Relationship to v1

| v1 decision | v3 change |
|---|---|
| 10.4: "Users paste PR URLs, one per line" | **Changed:** selecting from the inbox is the only path; the picker dialog and the paste affordance are both gone, because a repository the App does not cover is refused by pre-flight anyway |
| 10.4: "sessions are auto-named from their targets" | **Unchanged.** |
| 10.4: "duplicate/concurrent sessions are allowed" | **Unchanged** — and now visible, since a PR row can carry a review already in flight |
| 10.8: dashboard lists running and recent sessions | **Changed:** live progress moves onto the PR row; finished work is the row's review state; the full history stays on the Sessions screen |
| 10.8: sessions are workspace- and access-scoped | **Unchanged**; the inbox applies the same viewer-access rule to the PRs it lists |
| 10.3: pre-flight runs at submit | **Unchanged** — the dock still runs pre-flight before creating the session |
| Provider/model config, queue, worker, publish, usage | **Unchanged.** |

---

## 5. Surfaces this version replaces

- `PullRequestPicker` (the repo→PR modal) — replaced by the list itself plus a repository filter.
- `ReviewList` (running / queued / finished tabs) — live state folds into the PR row; history stays
  on `/sessions`.
- `AnalyticsStrip` — the workspace totals live on `/usage`, which already renders them.
- `useStickyCollapse` and the collapsed composer bar — the dock is always bottom-docked, so
  scroll-position tracking is no longer needed.

---

## 6. Feature files

- [01 pull-request inbox](./features/01-pull-request-inbox.md) — the list, its review states, the
  dock, and the API surface behind them.

---

## 7. Wiring status and what is still open

**Landed.** `GET /api/pull-requests` is real (`apps/server/app/routers/pull_requests.py`), the review
join reads `session_targets.reviewed_sha` (migration `0008_target_reviewed_sha`, recorded by the
worker's review attempt), and `GET /api/dashboard` plus the per-repository `/pulls` picker endpoint
are deleted in the same cutover. The inbox mock stays registered **only in mock mode**: with
`VITE_MOCK=off` the UI talks to the real endpoint, so a self-hosted deployment never shows invented
pull requests.

Still open, and each a candidate slice rather than a gap in what shipped:

- **The checks facet carries no count.** CI state is read per row by the expensive tier, so a count
  over rows nobody hydrated would be a guess; the facet lists the four states without hints.
- **`sort=size_desc` and the `checks` filter hydrate the whole filtered set**, because the order and
  the filter are numbers only the expensive tier has. Both are bounded by the per-request GitHub
  tool budget (100 reads), and the tail degrades to zeros / `none` rather than erroring.
- **A tool budget larger than the page's needs is not yet split differently**: the cheap tier is one
  listing per repository, the expensive tier is per returned row.
- **Filters are not URL-addressable.** The route model stores a nav id only; a filtered inbox cannot
  be shared as a link yet.
- **Chat mode** (v1 roadmap Phase 4) — the dock is laid out so a message list can sit above its
  input, but no thread is built here.
- **Comment triggers** (Phase 2) — a PR reviewed through an `@slopolis` mention is out of scope; the
  inbox reflects app-submitted sessions only.
- **Untracked reviews are transitional.** Rows reviewed before `0008` report "commit not recorded"
  and count as stale until they are reviewed again; there is no backfill, because the commit each
  one covered was never recorded anywhere.
