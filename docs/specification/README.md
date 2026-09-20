# slopolis Specification

Versioned, feature-per-file specification for slopolis. Each version lives in its own directory. `overview.md` holds cross-cutting goals, decisions, and architecture; `features/` holds one file per feature.

## Versions

| Version | Status | Overview | Features |
|---|---|---|---|
| v1 | MVP approved; later phases scoped | [v1/overview.md](./v1/overview.md) | [01 account + GitHub App](./v1/features/01-account-github-app.md) · [02 provider & model config](./v1/features/02-provider-model-config.md) · [03 pre-flight](./v1/features/03-preflight-validation.md) · [04 session form](./v1/features/04-session-creation.md) · [05 queue + worker](./v1/features/05-queue-worker.md) · [06 review harness](./v1/features/06-review-harness.md) · [07 publish](./v1/features/07-publish-github.md) · [08 history + live status](./v1/features/08-session-history.md) · [09 usage + cost](./v1/features/09-usage.md) |
| v2 | DRAFT for review — the agent harness, in two versions | [v2/overview.md](./v2/overview.md) | [11.1 supervisor + sub-agents](./v2/features/01-supervisor-subagents.md) · [11.2 Magentic orchestration (parked)](./v2/features/02-magentic-orchestration.md) |
| v3 | DRAFT for review — repository documentation agent; sequenced after Phase 2 webhooks | [v3/overview.md](./v3/overview.md) | [12.1 docs state, coverage + drift](./v3/features/01-repo-docs-state.md) · [12.2 docs agent run + publish](./v3/features/02-docs-agent-runs.md) |

## v1 features

Status of each feature in the running system, so a reader can tell a specification
from an implementation. Update the column when a feature lands.

| # | Feature | File | Implemented |
|---|---|---|---|
| 10.1 | Account + GitHub App connection | [01-account-github-app.md](./v1/features/01-account-github-app.md) | yes — OAuth sign-in, install redirect + setup callback, installation and repository sync, workspace membership |
| 10.2 | Provider & model configuration (BYOK) | [02-provider-model-config.md](./v1/features/02-provider-model-config.md) | **read-only** — the model catalog and assignment are read from the database, but nothing can create a credential, import models, or assign one (no API, no screen). See the gaps below |
| 10.3 | Pre-flight validation | [03-preflight-validation.md](./v1/features/03-preflight-validation.md) | yes — link parsing and dedup, coverage, access policy, `.codereview.yml`, workspace readiness, live model check |
| 10.4 | Session creation form | [04-session-creation.md](./v1/features/04-session-creation.md) | yes — submit, auto-naming, per-target enqueue |
| 10.5 | Queue + worker execution | [05-queue-worker.md](./v1/features/05-queue-worker.md) | yes — ARQ worker consumes `review_target`, one job per PR target, cancellation via the API |
| 10.6 | Review harness (single agent) | [06-review-harness.md](./v1/features/06-review-harness.md) | yes — API-only context with hard caps (files, diff lines) and a per-run tool budget |
| 10.7 | Publish to GitHub + app results | [07-publish-github.md](./v1/features/07-publish-github.md) | yes — rolling summary comment, inline comments, check run |
| 10.8 | Session history, permalink, live status | [08-session-history.md](./v1/features/08-session-history.md) | API yes, including SSE; the SPA has no permalink routing yet (it is a state machine, so `/sessions/{id}` is not a URL) |
| 10.9 | Usage and cost tracking | [09-usage.md](./v1/features/09-usage.md) | API yes (totals, breakdowns, series); no usage screen yet (the nav item is a placeholder) |

## Current gaps

Known deviations from the specification, each a candidate next slice. Verified
against the code on `main` rather than planned:

1. **10.2 blocks the first review.** Pre-flight refuses to create a session without an enabled
   provider credential and an assigned model, and nothing can create either: there is no
   credential vault, no model import, no assignment endpoint, and the Providers & Models screen
   is a placeholder. Everything else in the review path is in place.
2. **No sign-in UI.** The web app renders the shell for a guest with a null user; sign-in is
   reached by visiting `/api/auth/github/login`. There is no install affordance either.
3. **The server's GitHub client is bound to one installation.** `main.py` builds it once at
   startup from the first installation (`from_app` takes `items[0]`), so with two installations
   `/api/repositories` and pre-flight only ever see one — and with none the client is disabled
   entirely. The worker already mints a token per installation per job.
4. **No OAuth `state`.** The callback accepts any `code`, so it is not bound to the browser that
   started the flow (login-CSRF).
5. **No webhooks.** Installations and repositories are recorded by the setup callback and
   refreshed only when the user reinstalls or changes the repository selection (Phase 2 adds
   webhooks).
6. **Per-user repository access is not enforced** on session and dashboard content; those reads
   are workspace-scoped, so a workspace member sees every session in the workspace.

## How to add a version

1. Create `docs/specification/vN/` with an `overview.md` and a `features/` directory.
2. Name feature files `NN-slug.md` (numeric prefix, kebab-case slug), one feature each.
3. Carry forward any decisions that still hold; change only what the new version changes.
4. Add a row to the **Versions** table above with the new version, its status, and links.
5. Keep older versions intact. Treat a version as frozen once a newer version supersedes it.
