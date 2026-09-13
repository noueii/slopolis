# 10.7 Publish to GitHub + app results

- On completion, a target posts: a **rolling summary comment** (edited in place across re-runs, carrying the session link, status, and a token/cost line), **inline line comments** per finding, and a **Check Run**.
- The Check Run **fails when findings reach error or critical**, otherwise succeeds.
- Reported severity defaults to **warning and above**, configurable per repo.
- Findings include GitHub `suggestion` blocks where a fix is safely applicable; findings that cannot map to a diff line appear in the summary.
- The app renders every finding and links it to its GitHub location.
