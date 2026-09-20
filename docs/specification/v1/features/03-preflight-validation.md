# 10.3 Pre-flight validation

- Runs **synchronously on submit**.
- Checks: models assigned; credential present, decryptable, enabled; **live lightweight model check**; GitHub App installed on every target repo and the user meets the access policy; every PR link parses, is real, is deduped, and belongs to a covered repo; any `.codereview.yml` parses and validates.
- On failure: **no session is created**; the user is notified with a clear, actionable error.

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
