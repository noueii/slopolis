# 10.3 Pre-flight validation

- Runs **synchronously on submit**.
- Checks: models assigned; credential present, decryptable, enabled; **live lightweight model check**; GitHub App installed on every target repo and the user meets the access policy; every PR link parses, is real, is deduped, and belongs to a covered repo; any `.codereview.yml` parses and validates.
- On failure: **no session is created**; the user is notified with a clear, actionable error.
