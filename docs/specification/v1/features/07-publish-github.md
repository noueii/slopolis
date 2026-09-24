# 10.7 Publish to GitHub + app results

- On completion, a target posts: a **rolling summary comment** (edited in place across re-runs, carrying the session link, status, and a token/cost line), **inline line comments** per finding, and a **Check Run**.
- The Check Run **fails when findings reach error or critical**, otherwise succeeds.
- Reported severity defaults to **warning and above**, configurable per repo.
- Findings include GitHub `suggestion` blocks where a fix is safely applicable; findings that cannot map to a diff line appear in the summary.
- The app renders every finding and links it to its GitHub location (§What the app shows).

## What the app shows

The review's comments are readable without leaving the app: the session page lists each target's
findings under that pull request (spec 10.8), and each one is rendered as the comment it is.

- A finding reads like its comment on the pull request: the file and line it cites, the login the
  comment is posted as (`<slug>[bot]`, from the deployment's configured `GITHUB_APP_SLUG`), when the
  app wrote it, the body, and — where the fix is code — the literal replacement in a block labelled
  `suggestion`, the way GitHub renders one.
- **The code the comment sits on** is shown above it, as GitHub shows it: the hunk GitHub returned for
  that comment, with the hunk header, both line-number gutters, the `+`/`-` markers, and the commented
  line marked. The text is **GitHub's own** (`diff_hunk`), stored when the comment is written rather
  than recomputed, so the app cannot drift from what the reviewer saw; a comment posted before the app
  recorded hunks renders without one.
- A posted finding links to its comment's permalink — the pull request page anchored at the comment,
  `https://github.com/{owner}/{repo}/pull/{n}#discussion_r{comment_id}`, which is the URL GitHub's own
  `html_url` carries. The reader can land on the exact thread and reply there.
- A finding with no comment says so rather than wearing a comment's header. It has none in three cases:
  it could not map to a diff line, or fell below the repo's severity threshold (both go into the summary
  comment), or the publish was refused — which fails the target and names the scope.
- Both facts come from **what the app recorded**, never from a fresh read of the pull request: the link
  is the stored comment id, and the time is the moment the publisher stamped that comment — which is why
  a publish retry dates the comment, not the review. Rendering a session costs no GitHub call at all,
  so an unnamed author (no `GITHUB_APP_SLUG` configured) is left unnamed rather than looked up.
- **What it is not:** a mirror of the pull request's thread. Replies, reactions, other people's edits,
  and the rolling summary comment itself are not in the app — the app renders the comments it recorded
  posting, and the summary comment's id is not stored.

## A refused check run is not a failed review

The order is summary → inline comments → check run, and the check run is the **advisory** surface
(overview §10). So a check run GitHub refuses — an installation without `checks: write`, which is a
warning at pre-flight rather than a refusal — is recorded as a notice and the target still
**completes**: the findings reached the pull request, and the app marks the ones that posted as
posted.

- Only the comments are load-bearing: a refusal there fails the target (and names the scope), since
  that is the review the user asked for.
- The skipped check run is visible rather than silent: the run tree's node summary says so, and the
  log carries the reason.
- It is not retried: the comments are already on the pull request, and re-running the publish would
  duplicate them for an artifact nobody required.

## Suggestions are code, or they are prose

`suggestion` is a **literal replacement** for the cited line(s): GitHub renders it in a
`suggestion` block, and a human clicking *Commit suggestion* replaces that line with exactly that
text.

- The reviewer is told to put **replacement code** there — in the file's own language, only the lines
  being replaced — and `null` when the fix cannot be expressed as replacement code; the reasoning and
  any prose advice belong in `message`.
- The publisher emits a `suggestion` block **only** for text that is code-shaped. Anything that reads
  as prose is rendered as advice inside the comment body instead. The asymmetry is deliberate: a
  wrongly withheld apply button costs a click, a wrongly offered one can replace code with a
  sentence.
- The per-repo `output.suggestions` toggle still decides whether suggestions appear at all.

## Publishing is idempotent per finding

A run can fail *after* it has written to the pull request — a throttled write, a revoked permission,
a killed worker — and a retry must not stack a second copy of the same review.

- Each finding is stamped as posted **as soon as its comment exists** — before the next comment, and
  before the advisory check run — so nothing between the write and the bookkeeping can lose the link.
- Before posting, the publisher reconciles with what the pull request already holds: an existing
  slopolis comment on the same path and line, written by this App, marks that finding posted and is
  linked instead of duplicated. The same reconciliation repairs a target whose earlier attempt wrote
  comments but failed before recording them — its state catches up with GitHub.
- Matching is on **location and author**, because that is all GitHub keeps: a comment's body is not
  identity. A review comment carries no `performed_via_github_app`, so authorship is the author login
  (`<slug>[bot]`), which the deployment configures or the App reads for itself. A human's comment, or
  another App's, is never adopted; a comment GitHub has moved off the diff matches nothing and that
  finding is posted afresh.
- A finding the model re-worded is still the same location, so it adopts that comment and the comment
  is rewritten to what this publish would post now — the body is part of the link, not just the
  position.
- Two findings on one line cannot be told apart by GitHub, so the comments and findings at one
  location pair up in the order each list is in: the second finding posts its own comment rather than
  sharing the first's, and a retry re-pairs them against that same order instead of stacking more.
- A reconciliation GitHub refuses costs the duplicate it was preventing, never the review: the publish
  posts every comment anyway and logs the reason.
- The summary comment is edited in place, so re-publishing updates the rolling comment; when a pull
  request carries several — an earlier build posted one per publish — the newest is the one updated.
