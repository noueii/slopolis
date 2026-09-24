# 10.10 Workspace settings: caps and limits

The whole surface here is **pre-spawn**: every value is either a gate the submit path applies
before a session (and therefore a job) may exist, or a bound the queue applies to jobs that are
already queued. Nothing here changes what a review does once it runs.

- **Admin-only, audited.** Reading and writing settings requires a workspace admin; every change
  writes an `AuditLog` row.
- **Unset means unlimited.** Every cap defaults to `null` (no limit), so an existing deployment
  keeps behaving exactly as it did; a cap is opt-in.
- **Values are bounded at the edge**: an integer ≥ 1, or `null`.

## Stopgap caps (spec 10.2)

| Setting | Meaning | Enforced at |
|---|---|---|
| `maxConcurrentSessions` | Sessions in `queued`/`running` for the workspace | `POST /api/sessions`, before pre-flight |
| `maxSessionsPerUserPerDay` | Sessions the caller created since 00:00 UTC | `POST /api/sessions`, before pre-flight |

- The refusal is **409** with a distinct code (`session_limit_reached`,
  `user_daily_limit_reached`) and a message that names the cap and what is already running, so the
  user is not left guessing which switch to ask an admin about.
- Counted **before** pre-flight runs: a submission that cannot be accepted must not spend GitHub
  calls or live model checks, and must not create a session row. This is where the caps earn their
  name — they bound concurrency, not observed usage.

## Queue concurrency limits (spec 10.5)

| Setting | Meaning |
|---|---|
| `maxTargetsPerRepo` | Targets of one repository running at the same time |
| `maxTargetsPerInstallation` | Targets of one installation running at the same time |
| global pool size | Workers running at once (existing `WORKER_MAX_JOBS`) |

- Enforced **where jobs run**, not where they are enqueued: an ARQ queue has no per-key
  concurrency, so the worker acquires a slot before executing a target and releases it when the
  target finishes. Several workers therefore share the bound through Redis.
- A target that finds no free slot **waits rather than fails**: it defers itself and tries again,
  so a large submission drains in batches instead of erroring. The bound must never be the reason a
  target ends up `failed`.
- The worker is the only place these two numbers are read for enforcement; the API never refuses a
  submission because of them (that is `maxConcurrentSessions`' job).

## Trigger access (spec 10.2)

Who may trigger a review is a fact about the repository and the viewer, not a setting: pre-flight
demands the spec rule on every trigger, and there is nothing to configure per repository.

| Repository | Required |
|---|---|
| private | **read** |
| public | **write** |

- The rule is evaluated in pre-flight (and therefore at submit), and the refusal states it, naming
  what the viewer needs, e.g. "private repos need read access, public repos need write access".
- The bar follows the repository's own visibility, so it is applied where access is checked rather
  than stored on the row. On a public repository read is the baseline every GitHub user holds, so
  the write bar is what keeps a trigger to people who can push.

## API surface

| Method & path | Body | Returns |
|---|---|---|
| `GET /api/workspaces/settings` | — | `WorkspaceSettings` |
| `PATCH /api/workspaces/settings` | partial `WorkspaceSettings` | `WorkspaceSettings` |
| `PATCH /api/repositories/{id}` | `{enabled}` | `RepositorySummary` |

Settings hang off the existing `/api/workspaces` router (the caller's workspace is implied by their
session), so the surface has one prefix rather than two.

- `WorkspaceSettings` = `{maxConcurrentSessions, maxSessionsPerUserPerDay, maxTargetsPerRepo,
  maxTargetsPerInstallation}`, each `number | null`.
- `RepositorySummary` carries `enabled`, the workspace's own parking switch (spec 10.1). The
  endpoint carries that switch and audits each change; a body that omits it is **422**.
