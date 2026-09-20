# 10.1 Account + GitHub App connection

- **One GitHub App** provides both login (user-to-server OAuth) and repo access (installation tokens).
- Install offers **all repositories or selected repositories**, at the user's choice.
- **Multiple installations/orgs** are supported from day one.
- The app tracks users, installations, and repositories.
- Triggering requires GitHub repo access: **write access on public repos, read access on private repos** (see 10.2).

## Sign-in

`GET /api/auth/github/login` is the only sign-in entry point: it redirects the browser to
GitHub's consent screen, and `GET /api/auth/github/callback` sets the signed httpOnly session
cookie and returns the browser to `APP_URL`. There is no password, no local account, and no
sign-in form in the app.

- A visitor without a session never sees the shell: when `GET /api/me` reports no account, the
  SPA sends the browser to `/api/auth/github/login` instead of rendering the app.
- **One attempt per tab.** A browser that has already been to GitHub and came back without a
  session is not redirected again — it gets an explicit retry — so a failing callback cannot
  bounce the visitor between the app and GitHub.
- The login route is a browser navigation, so a deployment without OAuth credentials answers it
  with **503 `oauth_not_configured`**; the gate reports that message instead of navigating into
  the raw error body.

## Onboarding and workspace membership

Signing in is not the same as belonging somewhere. The OAuth callback creates the **account**;
membership is a separate step, because a self-hosted deployment may be invite-only.

- A new account is stored with **no workspace** (`users.workspace_id` is nullable).
- `GET /api/me` carries `workspace: {id, name, slug} | null`. `null` is the onboarding signal,
  and the UI must show the onboarding gate instead of the app shell.
- `POST /api/workspaces` `{name}` creates a workspace, attaches the caller, and makes them its
  **admin** (201). The slug is derived from the name and kept unique. A caller who already
  belongs to one gets **409 `already_in_workspace`** — v1 keeps one workspace per account.
- `GET /api/workspaces` lists the workspaces the caller belongs to (v1: at most one).
- Every workspace-scoped route (`/repositories`, `/dashboard`, `/sessions`, `/models`, …) answers
  **409 `no_workspace`** for an account without one, so an empty account never looks like an
  empty workspace.
- **Invitations are deferred** with multi-tenancy (Phase 5): today the onboarding screen states
  that an admin must invite you and offers a refresh, and no invitation rows exist yet.
