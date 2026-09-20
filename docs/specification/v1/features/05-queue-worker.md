# 10.5 Queue + worker execution

- **ARQ + Redis**; the server never reviews in-request.
- A session **fans out one sub-job per PR target, run in parallel**; the session is complete when all targets finish.
- Users can **cancel a running session** from the app.
- Failures retry **per PR target** with exponential backoff, then mark that target failed and notify.
- **Concurrency limits** (all configurable): per-repo, per-installation, and global pool size.
- Jobs have timeouts and are **durable across worker restarts**.
- App-triggered sessions are never automatically cancelled by newer activity.

The two per-key limits and the session caps that gate submission are specified in
[10-workspace-settings.md](./10-workspace-settings.md).

## Manual retry

A target whose retries are exhausted is **retryable by hand**, because the usual cause is a
condition outside the run that someone just fixed — a permission granted, a rate limit passed, a
provider back up — and resubmitting the pull request would pay for the same review twice.

- `POST /api/sessions/{id}/retry`, optional `{targetIds: [...]}`; the default is every target in a
  retryable state (`failed`, `cancelled`). A target the queue already owns (`queued`, `running`) is
  refused rather than duplicated — **except** a `queued` target whose job the queue lost, which is
  recovered instead (below).
- A retry puts those targets back to `queued`, moves the session back to `queued`, and enqueues the
  **same per-target jobs the submit path enqueues** — nothing else is rewritten: earlier attempts
  stay in the history and the run tree's nodes are reused, so the new attempt lands under the same
  PR orchestrator.
- Retry does **not** re-run pre-flight. The links, coverage and access were validated at submit;
  if one of them changed since (a parked repository, a revoked permission, a deleted PR) the run
  reports it with its own message instead of a second validation pass silently disagreeing with
  what the user sees in the session.
- Starting a new attempt supersedes the previous attempt's findings: unposted rows are dropped,
  posted ones stay (they link to a comment that exists on the pull request).
- `409 nothing_to_retry` when no target is retryable, `409 target_running` when the caller asked for
  a target the queue owns, `404 session_not_found` (with the usual per-viewer access rules) when the
  session is not the caller's to act on.

### A queued target whose job is gone

`queued` normally means the queue owns the target, so a retry refuses it. It does not mean that when
the job died before it could touch the target — an argument the worker's build rejects (the
`TypeError` an older worker raises for the `mode` a newer server sends), or a queue that dropped the
job. The target then sits `queued` with no job and no attempt, and a re-enqueue is the only way out.

- The retry endpoint recovers such a target: one that has been `queued` for longer than
  `QUEUE_STALE_AFTER_S` (**120 s**, the server's own threshold) **and** has no attempt currently
  `running`. An attempt row is the evidence a worker took the job — it is opened before the attempt
  does anything — so a running one keeps the target refused whatever its age.
- A recovered target is re-queued like a failed one, in the mode its last attempt calls for, and the
  response reports it as `queued` rather than a conflict. The threshold is what keeps the
  double-enqueue guard: a fresh `queued` target, or a `running` one of any age, is still refused.

### A worker that does not match its checkout

A worker keeps the code it started with, so a job enqueued by a newer server can arrive at a build
that does not accept its arguments; the job then fails inside ARQ without ever touching the target,
and a stack trace in the worker's log is the only evidence of it.

- The worker logs a **build fingerprint** once at startup — the git revision of its checkout, or the
  newest mtime of its own sources when the revision cannot be read — so an operator can compare a
  running worker with the checkout before reading a stuck queue as a real failure. The worker's own
  loggers are given a handler at startup, because ARQ's default config covers only its own: without
  that, the worker's records — this one and the per-target lines the job writes — are dropped.
- `review_target` accepts exactly `review` and `publish`. Any other `mode` fails the job naming the
  mode and the target it arrived on, instead of falling through to a review nobody asked for.

### Retrying a run that only failed to publish

A review is the expensive part; publishing is a write. When a target's last attempt failed **with a
GitHub error after the model had already run** — the attempt recorded tokens, so the review exists —
a retry **publishes the review it already has** instead of buying the same answer again.

- The decision is made where the target's state is known (the retry endpoint) and travels with the
  job, so the worker never guesses and never re-reviews on its own initiative.
- A **publish retry makes no model call**: it re-reads the pull request (for the head commit) and the
  repository config, rebuilds the summary, inline and check payloads from the **persisted findings**,
  and posts them. It records an attempt like any other, marked as a publish retry, so the history
  says what happened.
- Anything else is unchanged: pre-flight is not re-run, the session returns to `queued`, the run
  tree's nodes are reused.
- A failed target reports which retry it will get — `retryAction: "publish"` when a review is already
  on hand, `"review"` when the model has to run again, absent when the target is not retryable — so
  the UI can say what the button will do rather than surprising the user with a second model call.
