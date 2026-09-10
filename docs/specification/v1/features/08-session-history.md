# 10.8 Session history, permalink, live status

- Permalink: `/sessions/{id}`. Access requires **read access to any involved repo**; the page only shows content from repos the viewer can access.
- Live updates stream over **SSE**.
- Session detail exposes: status, progress steps, per-PR results, findings, model used, tokens/cost, a readable activity log, and **raw model/tool request-response payloads** (visible only to authorized viewers).
- The session list filters by **repo, user, status, and date**.
- Retention is **indefinite** in the MVP.
