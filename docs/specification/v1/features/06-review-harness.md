# 10.6 Review harness (single agent)

- One agent runs per PR target — **no orchestration in the MVP**.
- **Context via the GitHub API, no clone:** bounded tools (read a file, list a directory, list changed files) with hard caps on call count and file size; results cached within the job. Designed behind an interface so a clone-backed provider can be added later.
- **Single pass** produces all findings for the PR.
- Output is **strict structured JSON** — `path`, `line`, `severity`, `category`, `message`, `suggestion`, `confidence` — validated before use. Severity set: `info`, `warning`, `error`, `critical`.
- The optional session prompt is **appended on top of** built-in review behavior and repo instructions.
- If a repo exceeds limits or the tool budget is exhausted, **fall back to diff-only and clearly note the limitation**; the review always completes.
