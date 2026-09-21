# 10.3 Pre-flight validation

- Runs **synchronously on submit**.
- Checks: models assigned; credential present, decryptable, enabled; **live lightweight model check**; GitHub App installed on every target repo and the user meets the access policy; **the installation can write what publishing needs**; every PR link parses, is real, is deduped, and belongs to a covered repo; any `.codereview.yml` parses and validates.
- On failure: **no session is created**; the user is notified with a clear, actionable error.

## Publish prerequisites

A review is only worth running if its result can be posted, so the installation's ability to write
is validated *before* anything is queued — but only for what actually cannot be posted without it.

| What publishing writes | Endpoint | Scope it needs |
|---|---|---|
| Inline review comments | `POST /repos/{o}/{r}/pulls/{n}/comments` | `pull_requests: write` |
| Rolling summary comment | `POST /repos/{o}/{r}/issues/{n}/comments` | `pull_requests: write` |
| Check run | `POST /repos/{o}/{r}/check-runs` | `checks: write` |

- **Required: `pull_requests: write`.** A pull request conversation comment is an issue comment,
  but GitHub accepts it with Pull requests write — verified against a live installation that has no
  Issues scope at all and posts both comment kinds. Requiring `issues: write` would refuse a
  submission for a scope the calls do not use.
- **`checks: write` is a warning, not a refusal.** The check run is advisory (overview §10), posted
  last: without it the review still reaches the pull request as a comment, so pre-flight notices
  that the check run will be skipped rather than blocking the submission.
- The check reads the installation's own **granted** permissions (the App's declared scopes are not
  enough — an update has to be approved for the installation), once per installation per submit.
- The worker keeps its own refusal as a backstop: permissions can be revoked between submit and
  publish, and a refused *comment* still names the scope.

## The model gateway

A review cannot run without a gateway, so pre-flight is where that shows up — but as one problem
among the others, not as a failed request.

- The server opens a LiteLLM/OpenAI-compatible client at boot from `LITELLM_BASE_URL` +
  `LITELLM_MASTER_KEY`. Neither the live check nor any model call can happen without it, and an
  app that never opened one would refuse every submission however well the workspace was
  configured.
- A deployment with no key still starts and serves: reading, installing, settings and usage do not
  need a model.
- An unconfigured gateway is reported the way every other validation problem is — a notice naming
  what to set — so the user sees it *together with* a missing credential or model instead of one
  opaque error that hides the rest.
