# GitHub App setup

slopolis uses **one GitHub App** for two things (spec 10.1):

- **Sign-in** — user-to-server OAuth. The browser flow at `/api/auth/github/login` sets a signed
  httpOnly session cookie.
- **Repository access** — installation tokens. The review harness reads PRs, diffs, files, and
  `.codereview.yml` with them; the publisher writes the summary comment, inline comments, and the
  check run.

This guide creates that App and puts its credentials where the server and worker read them
(`.env` at the repo root). Ten minutes, and the only fiddly part is the private key.

---

## 1. Permissions to grant

**Repository permissions** (leave Organization and Account permissions at **No access**):

| Permission | Level | Why | Where it is used |
|---|---|---|---|
| **Metadata** | Read-only | Mandatory for every GitHub App; repository metadata and the triggering-access check. | `GET /repos/{owner}/{repo}`, `GET /repos/{owner}/{repo}/collaborators/{user}/permission`, `GET /installation/repositories` |
| **Contents** | Read-only | The review harness reads files, directory listings, and the repo config. | `GET /repos/{owner}/{repo}/contents/{path}` |
| **Pull requests** | **Read & write** | Read the PR, its diff, and changed files; post inline review comments. | `GET /repos/{owner}/{repo}/pulls[/{number}][/files]`, `POST /repos/{owner}/{repo}/pulls/{number}/comments` |
| **Checks** | **Read & write** | Read CI state, then create/update the `slopolis` check run. | `GET /repos/{owner}/{repo}/commits/{ref}/check-runs`, `POST|PATCH /repos/{owner}/{repo}/check-runs` |
| **Issues** | **Read & write** | Read the PR conversation and maintain the rolling summary comment (PR conversation comments are issue comments). | `GET /repos/{owner}/{repo}/issues/{number}`, `POST|PATCH /repos/{owner}/{repo}/issues/{number}/comments` |

Read-only is enough to *browse* (repositories, PR picker, pre-flight), but the review cannot
publish without the three write scopes — the check run and comments are the deliverable.

**Events:** none. There is no webhook endpoint yet (Phase 2), so leave **Active** deselected and
the webhook URL and secret empty. `GITHUB_WEBHOOK_SECRET` exists in the settings but nothing
consumes it today.

---

## 2. Create the App

1. GitHub → profile picture → **Settings** → **Developer settings** → **GitHub Apps** →
   **New GitHub App**. Create it under a personal account, or under an organization you own.
2. **GitHub App name** — e.g. `slopolis-dev`. The slug GitHub derives (`slopolis-dev`) appears in
   the App's public URL and in the private-key filename.
3. **Homepage URL** — your `APP_URL` (see §4), e.g. `http://localhost:8000`.
4. **Callback URL** — **`{APP_URL}/api/auth/github/callback`**, e.g.
   `http://localhost:8000/api/auth/github/callback`. `http://localhost` is accepted for local
   development, and up to 10 callback URLs are allowed, so add the deployed one later.
   The server builds this URL as `APP_URL + /api/auth/github/callback`; if the two disagree,
   GitHub rejects the sign-in.
5. **Expire user authorization tokens** — **deselect.** The server keeps no user token (only a
   signed cookie carrying the user id), so expiring tokens would just force a re-login after 8
   hours with no way to refresh.
6. **Request user authorization (OAuth) during installation** — optional. Selecting it makes
   GitHub send the browser to the Callback URL with a `code` right after an install, which this
   server accepts (the callback only requires `code`). Leaving it deselected means users sign in
   through the app first; either is fine.
7. **Setup URL** — **`{APP_URL}/api/github/setup`**, e.g.
   `http://localhost:8000/api/github/setup`. GitHub sends the browser here after an install, and
   this is where slopolis learns the `installation_id` and records the installation plus the
   repositories it covers.
8. **Redirect on update** — **select.** Adding or removing repositories then re-syncs through the
   same URL instead of waiting for a manual reinstall.
9. **Webhook → Active** — deselect (see §1).
10. **Permissions** — grant the five repository permissions from §1. Metadata, Contents stay
   Read-only; Pull requests, Checks, Issues are Read & write.
11. **Where can this GitHub App be installed?** — **Any account** if you review organization
    repositories, otherwise **Only on this account**.
12. **Create GitHub App.**

---

## 3. Collect the credentials

| Value | Where | Environment variable |
|---|---|---|
| App ID | top of the App's settings page, e.g. `5007508` | `GITHUB_APP_ID` |
| Client ID | "About" section, e.g. `Iv23liwr…` | `GITHUB_CLIENT_ID` |
| Client secret | **Generate a new client secret** (shown once — copy it) | `GITHUB_CLIENT_SECRET` |
| Private key | **Generate a private key** → downloads `<slug>.YYYY-MM-DD.private-key.pem` | `GITHUB_APP_PRIVATE_KEY` |

App ID and Client ID are different values; both are required. GitHub never shows the private key
again — the downloaded file is the only copy. You can generate additional keys (and revoke old
ones) from the same section at any time.

---

## 4. Put them in the environment

Copy the template to **the directory the process runs from** — `pydantic-settings` resolves
`env_file=".env"` against the working directory, and `make api` starts uvicorn from
`apps/server`:

```bash
cp .env.example apps/server/.env   # what `make api` reads
cp .env.example apps/worker/.env   # what `make worker` reads (same values)
# Docker Compose is the exception: it reads the repo-root .env (infra/.env.example documents it)
openssl rand -base64 32      # ENCRYPTION_KEY
openssl rand -hex 32         # COOKIE_SECRET (signs the session cookie)
```

A repo-root `.env` alone is **not** enough for `make api`: the server would see no credentials and
answer `503 oauth_not_configured`. Keep `apps/server/.env` and `apps/worker/.env` in sync (a
symlink works), or export the variables in your shell.

**The private key must be a quoted multi-line value.** `python-dotenv` parses those; a bare
multi-line paste does not, and the current settings class does not un-escape `\n` either:

```bash
{ printf 'GITHUB_APP_PRIVATE_KEY="'; cat ~/Downloads/slopolis-dev.*.private-key.pem; printf '"\n'; } >> apps/server/.env
```

`GITHUB_APP_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----<newline>…<newline>-----END PRIVATE KEY-----"`
is the shape that works; a single line with literal `\n` escapes fails with
`InvalidKeyError: Could not parse the provided public key` (a validator that accepts both forms is
a tracked follow-up).

The settings that matter, in full:

```dotenv
APP_URL=http://localhost:8000
DATABASE_URL=postgresql+asyncpg://slopolis:slopolis@localhost:5432/slopolis
REDIS_URL=redis://localhost:6379/0
ENCRYPTION_KEY=<openssl rand -base64 32>
COOKIE_SECRET=<openssl rand -hex 32>

GITHUB_APP_ID=5007508
GITHUB_CLIENT_ID=Iv23liwr…
GITHUB_CLIENT_SECRET=<client secret>
GITHUB_APP_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----
…
-----END PRIVATE KEY-----"

LITELLM_BASE_URL=http://localhost:4000
LITELLM_MASTER_KEY=sk-…
```

- **`APP_URL`** is both the OAuth `redirect_uri` base and where the callback sends the browser
  afterwards, so in local development point it at the **web** origin (`http://localhost:8000`,
  whose Vite dev server proxies `/api` to the API). The default (`http://localhost:8400`) is the
  API's own origin: sign-in then lands on the API root instead of the app.
- `ENCRYPTION_KEY` encrypts secrets at rest; `COOKIE_SECRET` signs the session cookie. Both are
  required for a real deployment — the cookie secret has an insecure development default.
- Keep `.env` out of version control (it is gitignored); it holds the App's full credentials.
- **Worktrees:** `make wt-config` copies `.env*` from the primary checkout into new worktrees, so
  keep the real files in the primary (`~/workspace/github.com/noueii/slopolis`).

---

## 5. Verify

```bash
docker compose -f infra/docker-compose.yml --env-file infra/.env.example up -d postgres redis
make migrate            # alembic upgrade head
make api                # API on :8400
# in another shell:
make dev                # web on :8000, proxying /api to the API

curl -s -o /dev/null -w '%{http_code}\n' localhost:8400/api/me   # 401 without a cookie
curl -s -D - -o /dev/null localhost:8400/api/auth/github/login | grep -i '^location'
# → https://github.com/login/oauth/authorize?client_id=…&scope=read:user user:email repo&redirect_uri=…
```

Then in the browser open **<http://localhost:8000/api/auth/github/login>**, approve the App, and
you land back on the app signed in (`GET /api/me` returns your handle). `make dev-api` runs the API and
the web app together if you prefer one command.

Install the App to give slopolis something to review:

```bash
curl -s -D - -o /dev/null localhost:8400/api/github/install | grep -i '^location'
# → https://github.com/apps/<slug>/installations/new
```

Pick the repositories on GitHub's install page; the browser returns to the Setup URL, and the
installation plus its repositories are recorded for your workspace — they show up in
`GET /api/repositories` and in the app's repository picker. Re-running the install (or changing the
selection, with "Redirect on update" on) refreshes the same rows instead of duplicating them.

---

## 6. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `503 oauth_not_configured` on login | `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` are unset in `.env`. |
| `422` on the callback | The callback requires `code`; visiting `/api/auth/github/callback` directly (or a stale bookmark) has none. Start from the login URL. |
| GitHub says the redirect URI is not associated with the App | The Callback URL in the App settings and `APP_URL + /api/auth/github/callback` disagree. |
| Sign-in lands on the API root, not the app | `APP_URL` is `http://localhost:8400`. Set it to the web origin (`:8000`) in dev. |
| `NotImplementedError: Algorithm 'RS256' could not be found` | PyJWT's `crypto` extra is missing (`pyjwt[crypto]`), so no App JWT can be signed. Fixed by declaring it in `packages/core/pyproject.toml`; run `uv sync --all-packages`. |
| `InvalidKeyError: Could not parse the provided public key` | The private key is not the quoted multi-line form (§4), or belongs to a different App. |
| GitHub calls fail with 403 after the first request | A permission from §1 is missing. Update the App's permissions, then have an installation admin approve the change (Installations → **Review request**). |
| Review finishes but nothing appears on the PR | The publisher needs Pull requests/Checks/Issues **write**; the harness needs the App installed on that repository. |
| Worker logs a failed installation-token mint | No `github_installations` row for the PR's repository: open the app and install the App again, or hit `/api/github/setup` through the install button. |
| Setup redirect lands on the app with nothing recorded | The account is not signed in (the browser is sent to sign in first) or has no workspace yet (onboarding shows first); the installation is recorded on the next attempt. |

---

## 7. Current limitations

Verified against the code on `main` today; each is a candidate follow-up:

1. **No webhooks, so no live sync.** Installations and repositories are recorded by the setup
   callback (§2) and refreshed only when the user installs again or changes the selection. A
   repository renamed or removed on GitHub keeps its old row until then (it is marked
   `connected: false` only when a later sync reports it missing).
2. **No sign-in UI.** The web app renders the shell with a null user for guests; sign-in is reached
   by visiting `/api/auth/github/login` directly. A sign-in screen with a 401 fallback is a
   follow-up.
3. **No OAuth `state`.** The callback accepts any `code`, so it is not bound to the browser that
   started the flow (login-CSRF). Adding a signed, single-use, browser-bound state is a follow-up.
4. **`GITHUB_WEBHOOK_SECRET` is unused** until webhooks land (Phase 2), and `CORS_ORIGINS` still
   defaults to the old dev port `:5173` while the web dev server runs on `:8000`.
5. **Env files are per-process.** The server reads `apps/server/.env` and the worker
   `apps/worker/.env`; only Docker Compose reads the repo-root `.env`. A single root file silently
   leaves `make api` unconfigured.

---

## 8. Where this lives in the code

| Concern | File |
|---|---|
| OAuth sign-in, callback, cookie, `/me`, `POST /auth/logout` | `apps/server/app/auth.py` |
| Install redirect and the setup callback | `apps/server/app/routers/github_install.py` |
| Recording an installation and its repositories | `apps/server/app/services/installation_sync.py` |
| App JWT: slug, installation lookup, repository listing | `packages/core/slopolis_core/github/app_installations.py` |
| Cookie signing and user decoding | `apps/server/app/deps.py` (`encode_user_id`, `decode_user_id`) |
| Settings (core + server) | `packages/core/slopolis_core/settings.py`, `apps/server/app/config.py` |
| App JWT and installation-token minting | `packages/core/slopolis_core/github/client.py`, `packages/core/slopolis_core/github/auth.py`, `apps/worker/worker/deps.py` |
| Review reads (files, diffs, `.codereview.yml`) | `packages/core/slopolis_core/github/repo_reads.py` |
| Publishing (summary comment, inline comments, check run) | `packages/core/slopolis_core/github/publisher.py` |
| Pre-flight access policy and repo coverage | `packages/core/slopolis_core/preflight/service.py` |
