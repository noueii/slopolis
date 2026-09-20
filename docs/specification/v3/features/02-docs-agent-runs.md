# 12.2 Documentation agent run and publish

- **Scope:** the run that maintains documentation, and the docs-only pull request it produces.
- **One job per repository**, not per merge: a run coalesces every merge since the cursor.
- **Read-only during the run.** The agent explores and proposes patches; nothing reaches the
  repository until the publish phase (v2's "no side effects until publish", unchanged).
- **One choke point owns the write.** A patch touching anything outside `docs.scope` is refused,
  and there is no code-write tool to refuse.
- **Never auto-merge.** The pull request is a proposal; a human merges it.

---

## 1. Goals / non-goals

**Goals**
1. Keep a repository's documentation true as the code changes, without the developer having to
   remember a documentation step.
2. Produce a **reviewable** documentation pull request, not a stream of unreviewable commits.
3. Preserve intent: recruit missing rationale from the authors instead of inventing it.
4. Be idempotent and self-healing across failed, skipped, or unmerged runs.
5. Bound cost and review effort per run.

**Non-goals**
- Writing, formatting or refactoring **application code**. There is no code-write tool.
- Auto-merging, auto-approving, or bypassing review.
- Generating documentation from a merged diff alone and committing it silently to the base ref.
- Cross-repository runs, monorepo-wide sweeps, and public documentation sites.
- Replacing the customer's existing documentation platform. The output is a pull request.

---

## 2. Trigger and pre-flight

Triggers: `pull_request` closed and merged into the base ref; `push` to the base ref; a scheduled
backstop; and a manual run from the repository's documentation tab.

Pre-flight must pass before enqueueing — the same synchronous posture as 10.3:

1. Documentation automation is enabled for this repository.
2. The installation carries `contents: write` and the permission is approved. If not, the run
   degrades to comment mode (§6) or refuses with a clear, actionable reason.
3. `docs.scope` is configured and non-empty.
4. A provider credential and a model are assigned for the documentation role.
5. Caps and cadence allow a run now.
6. No other documentation run holds the repository lock.

---

## 3. Run shape

A documentation run is a session with `kind = docs` whose target is the repository plus base ref,
so it inherits the permalink, SSE progress, run tree (v2), findings and usage attribution without
a parallel stack.

| Phase | Contents |
|---|---|
| **Deterministic pass** | The 12.1 detection: index, coverage, drift, conventions. This is the work queue |
| **Agent pass** | Read-only tools plus a documentation-write **proposal**; produces document edits, a change entry, and questions |
| **Publish** | Applies the proposals to a branch under the scope chokepoint, then opens or updates one pull request |

### What the agent must read (the thoroughness contract)

Reading only hunks is insufficient; the agent reads:

1. The full diff of every merge since the cursor, **plus the complete files touched**.
2. The pre-change revision of each touched file, to separate new behaviour from moved code.
3. PR titles, descriptions, review comments and commit messages for those merges.
4. **Tests and validation/error strings** — after the fact, these are the strongest surviving
   evidence of intended behaviour and edge cases.
5. Every existing document whose declared sources or surfaces cover the touched code.
6. The repository's convention rules and any declared document frontmatter.

### What it writes

- Edits to existing documents, in place, preserving sections it did not need to change.
- A per-merge change entry under the configured change log location, when the change is
  behaviour-affecting.
- New documents only for undeclared surfaces it can evidence.
- `## Questions for the author(s)` — every gap the evidence cannot close.

### Evidence rules

Every claim in a written document carries a pointer to the file and revision it was read from.
Anything unsupported goes to the questions section rather than into prose. An agent that may say
"unknown" has a cheap alternative to inventing intent, and this is the rule that keeps the
feature from manufacturing confident, wrong documentation.

---

## 4. Publish

| Aspect | Rule |
|---|---|
| Branch | Rolling `docs/auto` (configurable to per-run), always rebuilt from the base ref at the start of a run |
| Artifact | One docs-only pull request, opened or updated in place. Never a PR per merge |
| Body contract | Merges covered; documents changed; surfaces newly declared; uncovered areas; **questions for the author(s)** |
| Check run | Posted on the documentation PR; conclusion follows the same severity floor rules as 10.7 |
| Comment mode | Without `contents: write`, the same patch is posted as a comment on the repository's documentation surface, and no branch is created |
| Merge | Human only. No auto-merge, no auto-approve, no bypass |
| Commit hygiene | Conventional Commits; the docs PR is squash-merged like any other change |

---

## 5. Bounds, idempotency and failure

| Concern | Rule |
|---|---|
| Coalescing | One job per repository per trigger window; a run covers everything merged since the cursor |
| Cursor | `cursor_commit` = the merge commit of the last **merged** docs PR, recomputed from git. A failed or unmerged run loses nothing and is re-derived on the next trigger |
| Idempotency | A rerun with no new merges performs **zero writes**; an existing docs PR is updated, never duplicated, and its identity is the branch, not the title |
| Review effort | A diff cap bounds the PR. Exceeding it splits the work by area into multiple runs rather than producing one unreviewable PR |
| Concurrency | One run per repository, held by `locked_by_run_id`. A second trigger while locked is coalesced, not queued as a duplicate |
| Cost | Per-run token budget plus per-repository cadence, checked before the run and between agent steps; cancellation is cooperative |
| Failure | Fail closed: unreadable repository or index state means no partial writes and a failed run with an actionable reason |
| Retries | Per v1 10.5 semantics: retry with backoff at the run level. A run that fails twice surfaces rather than looping |

---

## 6. Degraded mode

When the installation has not approved `contents: write`, the feature stays useful:

- Detection reports normally (12.1 needs no permission).
- The agent still produces the patch, which is posted as a **comment** containing the proposed
  document changes, clearly marked as unapplied.
- The UI states that branch publishing is unavailable and why, with the link to approve the
  permission. This is never a silent failure and never a partial write.

---

## 7. Acceptance

1. Merging a behaviour-affecting PR produces a documentation PR within the configured cadence,
   whose body names the merges covered and lists any questions for the authors.
2. Re-running with no new merges produces zero writes and no new branch or PR.
3. A repository with documentation disabled is never written to, whatever the trigger.
4. A patch attempting to modify a file outside `docs.scope` — including via a relative path or
   symlink — is refused and the run reports the refusal. A fixture that attempts a code-path
   write fails the build.
5. An installation without `contents: write` produces a comment containing the patch and creates
   no branch.
6. Two concurrent triggers result in one run and one PR, with the second coalesced.
7. A run whose inputs contain instructions aimed at the agent does not acquire any capability
   beyond the read tools and the scoped patch; the scope chokepoint still governs publish.
8. Nothing produced by this feature is ever merged automatically, and the app records which
   merges the cursor has consumed.
