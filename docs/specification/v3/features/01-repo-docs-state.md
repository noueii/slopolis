# 12.1 Repository documentation state, coverage and drift

- **Scope:** the repository-scoped half of the feature — state, detection, and reporting. Ships
  without any write permission and without an agent.
- **Unit of work is a repository, not a submission.** The state object survives every session and
  every run.
- **Opt-in per repository, off by default.** Enabling and disabling are audited admin actions.
- **Deterministic only.** Coverage, drift and conventions are computed; no model is involved in
  this feature.
- **Findings reuse the v1 `Finding` shape** so the app can render gaps and drift with existing UI.
- **No writes to the customer's repository.** This feature reads and reports.

---

## 1. Goals / non-goals

**Goals**
1. Know, for a repository, which code surfaces are documented, which are not, and which documents
   have drifted since they were last verified.
2. Report that as findings the app already knows how to display, and as a repository-scoped view.
3. Survive sessions: state persists until the documentation actually catches up.
4. Cost nothing to enable on a repository with no documentation conventions at all.

**Non-goals (12.1)**
- Writing documentation, branches or pull requests (that is 12.2).
- Requiring frontmatter, a manifest, or any document convention as a precondition.
- Public documentation sites, audience filtering, export APIs.
- Cross-repository reporting.

---

## 2. State

`RepositoryDocsState` — one row per repository, created on first enablement:

| Field | Meaning |
|---|---|
| `enabled` | Opt-in flag; off by default |
| `scope` | Writable paths the agent may touch (12.2); also the documentation roots for coverage |
| `cadence` | Minimum interval between runs, and the schedule backstop interval |
| `budget` | Per-run token budget and diff cap, resolved from config with a workspace ceiling |
| `catalogue_digest` | Digest of the known document set (ids + content hashes) |
| `index_revision` | Code-index revision the catalogue was last verified against |
| `cursor_commit` | Merge commit of the last **merged** docs PR |
| `open_pr_number` | The rolling documentation PR, if any |
| `locked_by_run_id` | Claim held by the run currently executing; one per repository |
| `last_run_at`, `last_error` | Operational state for the UI and the backstop |

The state is derived where it can be: `cursor_commit` is recomputed from git history on every
run, never trusted from the previous row.

---

## 3. Configuration

`.codereview.yml` gains a `docs:` block, validated with Pydantic v2 like the rest of the file.
It expresses **intent only** — never models, never credentials (v1 §11, unchanged).

```yaml
docs:
  scope: ["docs/**"]            # writable paths (12.2) and documentation roots (12.1)
  ignore: ["docs/archive/**", "**/generated/*"]
  require_frontmatter: false    # opt-in declaration convention
  conventions: true             # evaluate the rules below
  audience: internal            # internal | contributors | public
  questions: pr                 # pr | issue  (where agent questions are routed)
```

Invalid config fails pre-flight, exactly as a malformed review config does today. Unknown keys
warn, never fail.

---

## 4. Detection

Deterministic pass, run in this order. Each step is independently testable.

| Step | Produces | Notes |
|---|---|---|
| Code index | Per-file symbols, imports, size, and declared surfaces (routes, exported types, config keys) | Incremental: only files changed since `index_revision` are re-parsed |
| Coverage | Files and surfaces no document claims | **File-level, not only entity-level** — a new module with no routes must still be reported. An explicit `ignore` list is the only way to exclude |
| Drift | Documents whose declared sources changed since they were last verified, with a churn estimate | Requires a declared source list; a document without one is reported as unverifiable rather than stale |
| Conventions | Violations of declared rules | Rules are evaluated against the index, not against a prompt |

Convention rules live in the repo config and are deliberately boring: forbidden pattern,
forbidden import (module boundaries), file size, required sections in a document, missing owner.
Anything expressible as a rule is a rule; the model never enforces a convention.

---

## 5. Findings

Findings reuse the v1 structure without modification:

| Category | Meaning |
|---|---|
| `gap` | Code surface or file with no documentation |
| `drift` | Document whose declared sources changed since verification |
| `convention` | Declared rule violated |

`path`, `line`, `severity` and `message` carry the same meaning as in reviews; `suggestion` holds
the remediation (the document to create, or the section to revisit). Severity floor and the
one-rolling-summary discipline from 10.7 apply unchanged.

---

## 6. API and UI

| Route | Behaviour |
|---|---|
| `GET /api/repositories/{owner}/{name}/docs` | State, coverage summary, drift queue, open PR, recent runs |
| `POST /api/repositories/{owner}/{name}/docs/runs` | Enqueue a manual run (12.2); 409 while locked |
| `PATCH /api/repositories/{owner}/{name}/docs` | Enable/disable, scope, cadence (admin only, audited) |

Access is decided by GitHub using the signed-in user's own token, with the 403/404 semantics of
10.1 — a user who cannot read the repository never learns that documentation state exists.

The UI surface is a **repository-scoped "Documentation" tab**, not an entry in the session
composer: current state, coverage, drift queue, the open docs PR, run history, and a manual run
button. Sessions remain the place where a run's detail (run tree, tokens, cost) is inspected.

---

## 7. Acceptance

1. Enabling a repository creates state, and a first run reports coverage and drift findings with
   no document conventions present anywhere in the repository.
2. Adding a source file that no document claims produces a `gap` finding naming that file.
3. Renaming a file referenced by a document produces a finding naming the now-missing source,
   not a silently clean state.
4. Editing a document's declared sources without touching the document produces a `drift` finding
   with a churn estimate.
5. A repository with documentation automation disabled produces no findings, no runs, and no writes.
6. Every route enforces the caller's own repository access; a user without access sees 403/404.
