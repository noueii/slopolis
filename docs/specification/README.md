# slopolis Specification

Versioned, feature-per-file specification for slopolis. Each version lives in its own directory. `overview.md` holds cross-cutting goals, decisions, and architecture; `features/` holds one file per feature.

## Versions

| Version | Status | Overview | Features |
|---|---|---|---|
| v1 | MVP approved; later phases scoped | [v1/overview.md](./v1/overview.md) | [01 account + GitHub App](./v1/features/01-account-github-app.md) · [02 provider & model config](./v1/features/02-provider-model-config.md) · [03 pre-flight](./v1/features/03-preflight-validation.md) · [04 session form](./v1/features/04-session-creation.md) · [05 queue + worker](./v1/features/05-queue-worker.md) · [06 review harness](./v1/features/06-review-harness.md) · [07 publish](./v1/features/07-publish-github.md) · [08 history + live status](./v1/features/08-session-history.md) · [09 usage + cost](./v1/features/09-usage.md) |

## v1 features

| # | Feature | File |
|---|---|---|
| 10.1 | Account + GitHub App connection | [01-account-github-app.md](./v1/features/01-account-github-app.md) |
| 10.2 | Provider & model configuration (BYOK) | [02-provider-model-config.md](./v1/features/02-provider-model-config.md) |
| 10.3 | Pre-flight validation | [03-preflight-validation.md](./v1/features/03-preflight-validation.md) |
| 10.4 | Session creation form | [04-session-creation.md](./v1/features/04-session-creation.md) |
| 10.5 | Queue + worker execution | [05-queue-worker.md](./v1/features/05-queue-worker.md) |
| 10.6 | Review harness (single agent) | [06-review-harness.md](./v1/features/06-review-harness.md) |
| 10.7 | Publish to GitHub + app results | [07-publish-github.md](./v1/features/07-publish-github.md) |
| 10.8 | Session history, permalink, live status | [08-session-history.md](./v1/features/08-session-history.md) |
| 10.9 | Usage and cost tracking | [09-usage.md](./v1/features/09-usage.md) |

## How to add a version

1. Create `docs/specification/vN/` with an `overview.md` and a `features/` directory.
2. Name feature files `NN-slug.md` (numeric prefix, kebab-case slug), one feature each.
3. Carry forward any decisions that still hold; change only what the new version changes.
4. Add a row to the **Versions** table above with the new version, its status, and links.
5. Keep older versions intact. Treat a version as frozen once a newer version supersedes it.
