# slopolis Specification

Versioned, feature-per-file specification for slopolis. Each version lives in its own directory. `overview.md` holds cross-cutting goals, decisions, and architecture; `features/` holds one file per feature.

## Versions

| Version | Status | Overview | Features |
|---|---|---|---|
| v1 | MVP approved; later phases scoped | [v1/overview.md](./v1/overview.md) | [01 account + GitHub App](./v1/features/01-account-github-app.md) · [02 provider & model config](./v1/features/02-provider-model-config.md) · [03 pre-flight](./v1/features/03-preflight-validation.md) · [04 session form](./v1/features/04-session-creation.md) · [05 queue + worker](./v1/features/05-queue-worker.md) · [06 review harness](./v1/features/06-review-harness.md) · [07 publish](./v1/features/07-publish-github.md) · [08 history + live status](./v1/features/08-session-history.md) · [09 usage + cost](./v1/features/09-usage.md) · [10 settings + caps](./v1/features/10-workspace-settings.md) |
| v2 | V1.1 built; V1.2–V1.4 deferred | [v2/overview.md](./v2/overview.md) | [01 supervisor + sub-agents](./v2/features/01-supervisor-subagents.md) · [02 Magentic orchestration](./v2/features/02-magentic-orchestration.md) (parked) |
| v3 | Implemented; wiring caveats tracked in §7 | [v3/overview.md](./v3/overview.md) | [01 pull-request inbox](./v3/features/01-pull-request-inbox.md) |

## v1 features

Status of each feature in the running system, so a reader can tell a specification
from an implementation. Update the column when a feature lands.

| # | Feature | File | Implemented |
|---|---|---|---|
| 10.1 | Account + GitHub App connection | [01-account-github-app.md](./v1/features/01-account-github-app.md) | yes — OAuth sign-in with a browser-bound `state`, install redirect + setup callback, webhook sync of installations and repositories, per-repository installation reads, in-app enable/disable of a connected repository, workspace membership |
| 10.2 | Provider & model configuration (BYOK) | [02-provider-model-config.md](./v1/features/02-provider-model-config.md) | yes — AES-256-GCM credential vault, credential CRUD with a live test-connection, model import + manual catalog, role→model assignments (admin-only, audited) and the Providers & Models screen |
| 10.3 | Pre-flight validation | [03-preflight-validation.md](./v1/features/03-preflight-validation.md) | yes — link parsing and dedup, coverage, access policy, `.codereview.yml`, workspace readiness, live model check; a parked repository is refused by name |
| 10.4 | Session creation form | [04-session-creation.md](./v1/features/04-session-creation.md) | yes — submit, auto-naming, per-target enqueue, the session's `main` agent run created at submit · **entry surface replaced by v3's inbox** (selecting rows, not pasting links) |
| 10.5 | Queue + worker execution | [05-queue-worker.md](./v1/features/05-queue-worker.md) | yes — ARQ worker consumes `review_target`, one job per PR target, cancellation via the API; each job owns that target's PR orchestrator run |
| 10.6 | Review harness (single agent) | [06-review-harness.md](./v1/features/06-review-harness.md) | yes — API-only context with hard caps (files, diff lines) and a per-run tool budget; it is the `reviewer` sub-agent of the v2 run tree |
| 10.7 | Publish to GitHub + app results | [07-publish-github.md](./v1/features/07-publish-github.md) | yes — rolling summary comment, inline comments, check run |
| 10.8 | Session history, permalink, live status | [08-session-history.md](./v1/features/08-session-history.md) | yes — `/sessions/{id}` is a real SPA route, SSE carries session + `agent` events, the run tree and its event replay have endpoints, and every session read is filtered by the viewer's own repository access · **the dashboard surface is replaced by v3's inbox**, which shows live progress on the PR row itself |
| 10.9 | Usage and cost tracking | [09-usage.md](./v1/features/09-usage.md) | yes — API totals, breakdowns by model / repository / user, a daily series, and the Usage screen |
| 10.10 | Workspace settings: caps and limits | [10-workspace-settings.md](./v1/features/10-workspace-settings.md) | yes — admin-only session caps enforced at submit (before pre-flight), per-repo / per-installation run limits leased through Redis by the worker, and the Settings screen |

## v2 features

| # | Feature | File | Status |
|---|---|---|---|
| 11.1 | Harness V1 — hierarchical supervisor with sub-agents | [v2/features/01-supervisor-subagents.md](./v2/features/01-supervisor-subagents.md) | **V1.1 built** — run rows and the event log are persisted and streamed, the tree and its replay are served, the UI renders the live tree; V1.2–V1.4 open (see gaps) |
| 11.2 | Harness V2 — Magentic orchestration | [v2/features/02-magentic-orchestration.md](./v2/features/02-magentic-orchestration.md) | parked |

## v3 features

| # | Feature | File | Status |
|---|---|---|---|
| 12.1 | Pull-request inbox | [v3/features/01-pull-request-inbox.md](./v3/features/01-pull-request-inbox.md) | **Wired** — `GET /api/pull-requests` joins open pull requests with the sessions that reviewed them and reports staleness from `session_targets.reviewed_sha` (migration `0008`); the UI keeps its mock only in mock mode (see §7 of the feature) |

## Current gaps

Known deviations from the specification, each a candidate next slice. Verified
against the code on this branch rather than planned:

1. **Harness V1.2–V1.4 are deferred, not next.** V1.1 is in place (persisted run rows, the
   event log, the tree and replay endpoints, the live tree UI, and the single-pass reviewer as the
   one sub-agent). The model-driven fan-out is deliberately parked for now: `spawn_subagents`
   exists in the runtime and is tested, but no deployed agent calls it, so aspect sub-agents,
   PR-level findings merge/dedupe, full cancellation propagation and the golden-PR quality harness
   wait until the delegation phase is picked up again (v2 §13). Until then the review path stays
   single-pass per target.
2. **Comment triggers are Phase 2.** `POST /api/github/webhook` synchronizes installations and
   repositories only; `@slopolis review`, auto-triggers and thread replies do not exist, and no
   webhook delivery creates a session.
3. **Installing and removing repositories still happens on GitHub.** The app can park and
   re-enable a connected repository, but a repository the App does not cover is added by
   reinstalling on GitHub's page — there is no in-app install or removal.
4. **Access checks are only as good as the stored user token.** With no `ENCRYPTION_KEY` — or
   after a user revokes the App — nothing can be verified for repositories the viewer did not
   trigger, and the API says so (`403 repo_access_unverified`) instead of showing less.
5. **GitHub reads stay per-repository and partially per-pull-request.** Counts and CI state are
   cached for 30s, and the inbox reads are tiered (one listing per repository; detail + checks +
   compare per returned row, cached — v3 §6). What is still unoptimized: `sort=size_desc` and the
   `checks` filter must hydrate the **whole filtered set**, because the order and the filter are
   numbers only the expensive tier has, and a set larger than the tool budget (100 reads) degrades
   its tail to zeros / `none` rather than erroring.
6. **The Review templates screen is mock-only** — no spec file, no table, no API. It should
   become v2's `AgentDefinition` surface once custom agents are in scope.
7. **`CORS_ORIGINS` still defaults to the old dev port `:5173`** while the web dev server runs
   on `:8000`.
8. **The inbox shows only what this workspace's installation can read.** A viewer's own access
   still decides which rows and which review joins they see (the rule the dashboard used), but a
   repository the App does not cover is invisible here for the same reason it is un-reviewable:
   it has to be added on GitHub's install page (gap 3).
9. **Reviews made before `0008_target_reviewed_sha` never recorded a commit** and therefore read
   as "commit not recorded" and count as stale until they are reviewed again. Nothing can backfill
   them — the commit each one covered was never stored — so this is a transitional set that
   shrinks as the workspace re-reviews.

## How to add a version

1. Create `docs/specification/vN/` with an `overview.md` and a `features/` directory.
2. Name feature files `NN-slug.md` (numeric prefix, kebab-case slug), one feature each.
3. Carry forward any decisions that still hold; change only what the new version changes.
4. Add a row to the **Versions** table above with the new version, its status, and links.
5. Keep older versions intact. Treat a version as frozen once a newer version supersedes it.
