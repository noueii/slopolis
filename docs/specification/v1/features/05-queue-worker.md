# 10.5 Queue + worker execution

- **ARQ + Redis**; the server never reviews in-request.
- A session **fans out one sub-job per PR target, run in parallel**; the session is complete when all targets finish.
- Users can **cancel a running session** from the app.
- Failures retry **per PR target** with exponential backoff, then mark that target failed and notify.
- **Concurrency limits** (all configurable): per-repo, per-installation, and global pool size.
- Jobs have timeouts and are **durable across worker restarts**.
- App-triggered sessions are never automatically cancelled by newer activity.

The two per-key limits and the session caps that gate submission are specified in
[10-workspace-settings.md](./10-workspace-settings.md).
