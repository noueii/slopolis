# 10.7 Publish to GitHub + app results

- On completion, a target posts: a **rolling summary comment** (edited in place across re-runs, carrying the session link, status, and a token/cost line), **inline line comments** per finding, and a **Check Run**.
- The Check Run **fails when findings reach error or critical**, otherwise succeeds.
- Reported severity defaults to **warning and above**, configurable per repo.
- Findings include GitHub `suggestion` blocks where a fix is safely applicable; findings that cannot map to a diff line appear in the summary.
- The app renders every finding and links it to its GitHub location.

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
