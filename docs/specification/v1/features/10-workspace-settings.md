# 10.10 Workspace settings: caps, limits, and access policy

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

## Per-repository access policy (spec 10.2)

`repositories.required_access` is a per-repository override of the triggering rule:

| Value | Effect |
|---|---|
| `default` | The spec rule: private repos need **read**, public repos need **write**. |
| `read` | Loosens: any read access is enough, private or public. |
| `write` | Tightens: write access is required, private or public. |

- The rule is evaluated in pre-flight (and therefore at submit), and the refusal names what was
  **required** and what the viewer has, e.g. "write access is required on `acme/api`".
- The override is a workspace decision per repository, set on the repository itself (the same
  surface that parks it), not in a global table: a loosened public repository and a tightened
  private one are properties of that repository's row.

## API surface

| Method & path | Body | Returns |
|---|---|---|
| `GET /api/workspaces/settings` | — | `WorkspaceSettings` |
| `PATCH /api/workspaces/settings` | partial `WorkspaceSettings` | `WorkspaceSettings` |
| `PATCH /api/repositories/{id}` | `{enabled?, requiredAccess?}` | `RepositorySummary` |

Settings hang off the existing `/api/workspaces` router (the caller's workspace is implied by their
session), so the surface has one prefix rather than two.

- `WorkspaceSettings` = `{maxConcurrentSessions, maxSessionsPerUserPerDay, maxTargetsPerRepo,
  maxTargetsPerInstallation}`, each `number | null`.
- `RepositorySummary` gains `requiredAccess`. The existing `enabled` field is unchanged; one
  endpoint now carries both per-repository switches and audits each change.
- A `requiredAccess` value outside the three literals is **422**.
