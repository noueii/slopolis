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
| **Metadata** | Read-only | Mandatory for every GitHub App; repository metadata and the triggering-access check. | `GET /repos/{owner}/{repo}`, `GET /repos/{owner}/{repo}/collaborators/{user}/permission` |
| **Contents** | Read-only | The review harness reads files, directory listings, and the repo config. | `GET /repos/{owner}/{repo}/contents/{path}` |
| **Pull requests** | **Read & write** | **Required to publish.** Read the PR, its diff and changed files; post the inline review comments *and* the rolling summary comment (a PR conversation comment is an issue comment, but GitHub accepts it with Pull requests write). | `GET /repos/{owner}/{repo}/pulls[/{n}][/files]`, `POST /repos/{owner}/{repo}/pulls/{n}/comments`, `POST /repos/{owner}/{repo}/issues/{n}/comments` |
| **Checks** | **Read & write** | Read CI state for the picker, then create/update the `slopolis` check run. Optional: without it the review still posts as comments and the check run is skipped. | `GET /repos/{owner}/{repo}/commits/{ref}/check-runs`, `POST|PATCH /repos/{owner}/{repo}/check-runs` |

**Issues is not required.** Nothing slopolis writes needs it: both comment kinds post with Pull
requests write, so do not grant it. Read-only is enough to *browse* (repositories, PR picker,
pre-flight), but publishing needs **Pull requests: Read & write**; a submission whose installation
lacks it is refused at pre-flight rather than after a review has been paid for.

**Events:** subscribe to **Installation**, **Installation repositories**, and **Repository** (see §9);
slopolis also answers a `ping` delivery. Everything else is accepted and ignored. Set **Active**,
point the **Webhook URL** at `{APP_URL}/api/github/webhook`, and generate a **Webhook secret** —
`GITHUB_WEBHOOK_SECRET` must carry the same value or every delivery is refused with
`401 invalid_signature` (there is no unauthenticated mode).

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
5. **Expire user authorization tokens** — **deselect.** The server keeps the user's token (sealed
   with `ENCRYPTION_KEY`) because it is the only thing that can answer "may this member read this
   repository?" (see spec 10.8 §Access). There is no refresh flow, so an expiring token would
   silently turn every access check into "unverifiable" after eight hours.
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
9. **Webhook → Active** — **select**, with the Webhook URL from §1 and a generated secret
   (`openssl rand -hex 32`) recorded as `GITHUB_WEBHOOK_SECRET` in §4.
10. **Permissions** — grant the repository permissions from §1. Metadata and Contents stay
    Read-only; **Pull requests is Read & write** (required to publish) and **Checks is Read &
    write** (the advisory check run). Do not grant Issues.
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
| Webhook secret | the value you typed under **Webhook → Secret** in §2 | `GITHUB_WEBHOOK_SECRET` |

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
openssl rand -hex 32         # GITHUB_WEBHOOK_SECRET (must match the App's Webhook secret)
```

A repo-root `.env` alone is **not** enough for `make api`: the server would see no credentials and
answer `503 oauth_not_configured`. Keep `apps/server/.env` and `apps/worker/.env` in sync (a
symlink works), or export the variables in your shell.

### Reaching a model

Reviews call a model, so a deployment needs a way to reach one. Two ways, in order of preference:

1. **Per-model credentials in the app** (recommended). Sign in, open **Providers & Models**, add a
   provider credential — LiteLLM first-class, base URL given **without `/v1`**, e.g.
   `http://127.0.0.1:4000` — import its models, and assign one to the Review role. Every call then
   uses that credential: pre-flight's live model check and the review itself. *Test connection*
   exercises the same base URL and key, so a passing test means a review can reach the provider.
2. **A deployment-wide gateway.** Set `LITELLM_BASE_URL` and `LITELLM_MASTER_KEY` (see
   `.env.example`) in both `apps/server/.env` and `apps/worker/.env`. This is the fallback for any
   model with no usable credential, and it is the only option when `ENCRYPTION_KEY` is unset — a
   stored credential cannot be decrypted without it.

With neither, reading the app works and a submission does not: pre-flight refuses it and names the
model plus both ways out.

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

Then in the browser open **<http://localhost:8000>**: with no session the app sends you straight to
GitHub, and approving the App lands you back on it signed in (`GET /api/me` returns your handle).
<http://localhost:8000/api/auth/github/login> starts the same flow directly. `make dev-api` runs the
API and the web app together if you prefer one command.

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
| `The redirect_uri is not associated with this application` (GitHub's own error page, after sign-in) | The App's **Callback URL** does not match what the server sends: `{APP_URL}/api/auth/github/callback`, e.g. `http://localhost:8000/api/auth/github/callback`. Check it with `curl -s -D - -o /dev/null localhost:8400/api/auth/github/login \| grep -i '^location'` (§5) and add the URL it shows to the App's Callback URLs (up to 10). A blank Callback URL field produces the same error. |
| Sign-in lands on the API root, not the app | `APP_URL` is `http://localhost:8400`. Set it to the web origin (`:8000`) in dev. |
| The app lands back on the sign-in gate | The round trip did not set a session: check the Callback URL, `APP_URL`, and the client secret. The gate stops redirecting after one attempt, so the second visit offers a retry instead of bouncing to GitHub again. |
| `NotImplementedError: Algorithm 'RS256' could not be found` | PyJWT's `crypto` extra is missing (`pyjwt[crypto]`), so no App JWT can be signed. Fixed by declaring it in `packages/core/pyproject.toml`; run `uv sync --all-packages`. |
| `InvalidKeyError: Could not parse the provided public key` | The private key is not the quoted multi-line form (§4), or belongs to a different App. |
| GitHub calls fail with 403 after the first request | A permission from §1 is missing. Update the App's permissions, then have an installation admin approve the change (Installations → **Review request**). |
| `403` on `upsert_check_run` (the review posted, the check run did not) | The installation lacks `checks: write` — the check run is the advisory surface, so the review still reaches the PR as comments and the target completes. Grant **Checks: Read & write** if you want the check run. |
| `403` on `upsert_summary_comment` / `post_inline_comments` (nothing posted) | The installation lacks **Pull requests: Read & write** — that one scope covers *both* comment kinds: a pull request conversation comment is an issue comment, but GitHub accepts it with Pull requests write (verified against an installation with no Issues scope at all). Issues is not required. Print what is granted — **declared** (the App's settings) and **granted** (what the installation approved) are two lists: `cd apps/worker && uv run python -c "import json,time,httpx,jwt;from slopolis_core.settings import get_settings as g;s=g();t=jwt.encode({'iat':int(time.time())-60,'exp':int(time.time())+300,'iss':s.github_app_id},s.github_app_private_key,algorithm='RS256');h={'Authorization':f'Bearer {t}','Accept':'application/vnd.github+json'};print('declared:',json.dumps(httpx.get('https://api.github.com/app',headers=h,timeout=20).json()['permissions'],sort_keys=True));print('granted:',json.dumps(httpx.get('https://api.github.com/app/installations',headers=h,timeout=20).json()[0]['permissions'],sort_keys=True))"`. Fix the App's Permissions, then approve the pending update on the installation page (`https://github.com/settings/installations/<id>`). Pre-flight refuses such a submission, so it fails *before* a review is paid for. |
| Review finishes but nothing appears on the PR | The publisher needs Pull requests/Checks/Issues **write**; the harness needs the App installed on that repository. |
| Worker logs a failed installation-token mint | No `github_installations` row for the PR's repository: open the app and install the App again, or hit `/api/github/setup` through the install button. |
| Setup redirect lands on the app with nothing recorded | The account is not signed in (the browser is sent to sign in first) or has no workspace yet (onboarding shows first); the installation is recorded on the next attempt. |

---

## 7. Current limitations

Verified against the code on this branch today; each is a candidate follow-up:

1. **Webhooks synchronize, they do not trigger.** `POST /api/github/webhook` (§9) keeps
   installations and repositories current. Comment triggers (`@slopolis review`), auto-triggers and
   thread replies are Phase 2, and no delivery creates a review session. Live pull-request and CI
   state is still read on demand, cached for 30 seconds.
2. **Repository reads follow each repository's own installation.** A workspace can hold several
   installations, and `/api/repositories` and pre-flight read each repository through the
   installation that grants it — a second installation (another org or account) is picked up by the
   next request, with no restart — while an installation that cannot mint a token keeps its rows
   listed without a live open-PR count. What is still true:
   - Adding a repository the App does not cover, or removing one, still happens **on GitHub**
     (item 3): the app can park a connected repository but cannot install or remove one.
   - Reading a repository whose installation cannot mint a token (a suspended installation, or the
     placeholder row an unsynced account gets) reports `503 github_not_configured`.
3. **Repository selection is split.** *Connect repository* starts the install flow
   (`/api/github/install` → GitHub's install page), and which accounts and repositories the App
   covers is chosen there. Inside the app, a connected repository can be **parked** (disabled):
   it stays listed with its history, and pre-flight refuses its pull requests by name.
4. **Access checks need a stored user token.** Reads are filtered by the viewer's own repository
   access (spec 10.8 §Access), which is verified with that user's token, sealed at sign-in. Without
   `ENCRYPTION_KEY` — or after a user revokes the App — nothing can be verified for repositories the
   viewer did not trigger, and the API answers `403 repo_access_unverified` rather than showing
   less. Sign-in itself still works without a vault.
5. **`CORS_ORIGINS` still defaults to `:5173`** while the web dev server runs on `:8000`. The dev
   proxy makes this invisible locally; a browser calling the API cross-origin would need the value
   updated.
6. **Env files are per-process.** The server reads `apps/server/.env` and the worker
   `apps/worker/.env`; only Docker Compose reads the repo-root `.env`. A single root file silently
   leaves `make api` unconfigured.

---

## 8. Where this lives in the code

| Concern | File |
|---|---|
| OAuth sign-in, callback, cookie, `/me`, `POST /auth/logout` | `apps/server/app/auth.py` |
| Install redirect and the setup callback | `apps/server/app/routers/github_install.py` |
| Recording an installation and its repositories | `apps/server/app/services/installation_sync.py` |
| Webhook ingest (signature check) and the sync it applies | `apps/server/app/routers/webhooks.py`, `apps/server/app/services/webhook_sync.py` |
| Per-viewer repository access (the user's token, cached checks) | `apps/server/app/services/repo_access.py` |
| Sealing credentials and user tokens (AES-256-GCM envelope) | `packages/core/slopolis_core/vault.py` |
| Agent run tree and event replay | `apps/server/app/routers/runs.py` |
| Per-installation GitHub clients on the server (repository reads, pre-flight) | `apps/server/app/services/github_clients.py` |
| App JWT: slug, installation lookup, repository listing | `packages/core/slopolis_core/github/app_installations.py` |
| Cookie signing and user decoding | `apps/server/app/deps.py` (`encode_user_id`, `decode_user_id`) |
| Settings (core + server) | `packages/core/slopolis_core/settings.py`, `apps/server/app/config.py` |
| App JWT and installation-token minting | `packages/core/slopolis_core/github/client.py`, `packages/core/slopolis_core/github/auth.py`, `apps/worker/worker/deps.py` |
| Review reads (files, diffs, `.codereview.yml`) | `packages/core/slopolis_core/github/repo_reads.py` |
| Publishing (summary comment, inline comments, check run) | `packages/core/slopolis_core/github/publisher.py` |
| Pre-flight access policy and repo coverage | `packages/core/slopolis_core/preflight/service.py` |

---

## 9. Webhooks

With a webhook secret configured (§1, §4), GitHub keeps the app's view of installations and
repositories current without anyone revisiting the install page. A delivery whose signature does
not verify is refused with `401 invalid_signature`; the endpoint never consumes an unauthenticated
body.

Subscribed events, and what each one writes:

| Event | Applied |
|---|---|
| `installation` | `created`/`unsuspend` upserts the installation and reconnects its repositories; `deleted`/`suspend` marks them `connected: false` (rows and history stay). |
| `installation_repositories` | `repositories_added` upserts and reconnects; `repositories_removed` marks `connected: false`. |
| `repository` | `renamed`/`transferred` updates the row's full name, visibility and branch — matched on GitHub's numeric repository id, so a rename never creates a second row; `deleted` marks it `connected: false`. |
| `ping` | Answered `202`; nothing is written. |

Every other event type is accepted and ignored (`202`), and deliveries are idempotent: replaying
the same one leaves the same rows.

Smoke-test the endpoint locally by signing a `ping` yourself:

```bash
SECRET=$(grep -m1 '^GITHUB_WEBHOOK_SECRET=' apps/server/.env | cut -d= -f2-)
BODY='{"zen":"Keep it logically awesome."}'
SIG=$(python3 -c "import hmac,hashlib,sys;print('sha256='+hmac.new(sys.argv[1].encode(),sys.argv[2].encode(),hashlib.sha256).hexdigest())" "$SECRET" "$BODY")
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8400/api/github/webhook \
  -H "X-GitHub-Event: ping" -H "X-Hub-Signature-256: $SIG" -d "$BODY"
# → 202 with a secret set, 401 without one
```

To let GitHub reach a local server, expose it (e.g. `gh webhook forward` or a tunnel) and use that
public URL as the App's Webhook URL; the secret is the part that matters for verification.
