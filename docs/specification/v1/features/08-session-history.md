# 10.8 Session history, permalink, live status

- Permalink: `/sessions/{id}`. Access requires **read access to any involved repo**; the page only shows content from repos the viewer can access.
- Live updates stream over **SSE**.
- Session detail exposes: status, progress steps, per-PR results, findings, model used, tokens/cost, a readable activity log, and **raw model/tool request-response payloads** (visible only to authorized viewers).
- Findings are listed **per target**, under that pull request, most severe first and then by path and
  line, each rendered as the comment it is — the code hunk it sits on, author, time, body, suggestion —
  and linking the comment it posted, or saying so when it has none (spec 10.7 §What the app shows).
- Findings ride the **detail read only**: `GET /api/sessions/{id}` carries them, `GET /api/sessions`
  carries `null`, because a page of sessions must not ship every finding of every session.
- The session list filters by **repo, user, status, and date**.
- Retention is **indefinite** in the MVP.

## Access

Workspace membership is not repository access: the installation token sees every repository the
App was granted, which is strictly more than any one member may read. Reads are therefore filtered
by the **viewer's own** GitHub access.

- The OAuth callback keeps the user's access token in the vault (spec 10.1), and every check uses
  that token — never the installation's.
- `GET /api/sessions` returns only sessions with **at least one target the viewer can read**, and
  the dashboard applies the same rule. `GET /api/sessions/{id}` returns only the accessible targets
  and their findings, and answers **404** when that leaves nothing to show.
- The **triggering user keeps access** to a session they submitted: pre-flight already verified
  their access at submit time, so no round trip is needed to show them their own review.
- A check that cannot be made (no stored token, revoked token, GitHub unreachable) is not a grant:
  the viewer simply does not see repositories they did not trigger, and the API reports
  **403 `repo_access_unverified`** rather than quietly showing less.
- Checks are cached per `(user, repo)` for a short window so a session list does not spend one
  GitHub call per row.
