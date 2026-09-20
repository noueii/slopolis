# Development Workflow

## Principles
1. **Spec-first** — behavior changes start in `docs/specification/`.
2. **Feature-per-file** — every feature has its own spec file.
3. **Contract-first** — the UI depends on the API contract, never directly on mock or real data sources.
4. **Mock-before-backend** — design features as interactive mocks, then wire them to the backend.
5. **Small, reviewable changes** — one feature per worktree and branch.

## Repository layout
```
apps/web/            # React SPA (shell + features + mock layer)
apps/server/         # FastAPI backend (added later)
apps/worker/         # ARQ worker (added later)
packages/            # shared code (added later)
docs/specification/  # versioned specs
.artifact/           # mock design artifacts (generated, gitignored)
```

## Spec versions
- Specs live in `docs/specification/vN/`.
- `v1/` is the MVP. `overview.md` holds goals, decisions, and architecture; `features/NN-slug.md` holds one feature each.
- New or changed scope gets a new version directory. Update `docs/specification/README.md` (index and status) whenever a version is added or changes.

## The feature loop: spec → contract → mock → wire
1. **Spec** — write or update `docs/specification/vN/features/NN-slug.md`.
2. **Contract** — add the feature's API types to `apps/web/src/api/contract.ts`.
3. **Mock** — add MSW handlers in `apps/web/src/mocks/<feature>.ts` with realistic data plus loading, empty, and error states.
4. **Design** — build the UI under `apps/web/src/features/<feature>/`; iterate interactively; keep `brief.md`, `prompt.md`, `critique.md`, and `screenshot.png` in `.artifact/<slug>/` (gitignored, not committed).
5. **Approve** — review the running mock in a browser, on desktop and mobile.
6. **Wire** — implement the backend to the contract, unregister the feature's mock handlers, and delete the mock.
7. **Verify** — add a contract test and a smoke test.

## Mocking policy
- Request-level mocking via **MSW** only.
- The **standalone mock API** (`apps/web/src/mocks/server.ts`) serves the handlers over HTTP; the UI only ever calls `/api`.
- The UI never imports from `src/mocks/`.
- Every mock simulates success, loading, empty, and error states.
- Delete a feature's mock once that feature is wired.

## Branches and worktrees
- `main` is always green.
- One worktree per feature:
  `git worktree add ../.worktrees/<name> -b <type>/<name>`
- Branch types: `feat/`, `fix/`, `docs/`, `mock/`, `chore/`.
- Conventional Commits (`feat:`, `fix:`, `docs:`, ...).
- Open a PR and squash-merge into `main`.

## Definition of Done
- [ ] Spec updated
- [ ] Contract updated
- [ ] UI implemented with mock states
- [ ] Reviewed in the browser (desktop and mobile)
- [ ] Types, lint, and diagnostics pass
- [ ] Mock deleted if the feature is wired
- [ ] Docs index updated

## Running things
- Web deps: `cd apps/web && bun install`
- `make dev-mock` — web app + standalone mock API (default for UI work)
- `make dev-api` — web app + real API. **No worker**: a submitted session stays `queued` forever
  until something consumes the queue.
- `make dev-all` — the whole local stack: real API + **ARQ worker** + web app. Use this when a
  submitted session has to actually run.
- `make worker` — just the ARQ worker, in its own shell (`uv run arq worker.main.WorkerSettings`).
  It needs Redis and the DB from `infra/docker-compose.yml`, and `apps/worker/.env`.
  **Restart it after pulling**: a worker keeps the code it started with, and a job whose arguments
  changed (a new job parameter, a renamed function) fails inside ARQ with a `TypeError` that never
  touches the target — leaving it `queued` with no job. The worker logs a build fingerprint at
  startup, so compare it with the checkout when a queue looks stuck.
- `make dev-browser-mock` — web app only, against the in-browser MSW mock; no API, no worker.
  (Formerly `dev-worker`: that name collided with the ARQ worker, which it is not.)
- `make dev` — web app only; `MOCK_MODE=server|worker|off`, `/api` proxied for server/off
- `make mock` / `make api` — run just the mock / real API server
- Reviewing a session **writes to GitHub**: the worker posts the rolling summary comment, the
  inline comments, and a check run for each target (`worker/jobs/publish.py`). The hermetic
  `make e2e` run exercises the same path with fakes and writes nothing.
- Web tooling: the web app uses **Bun** as its package manager and dev/build runner (`bun install`, `bun run dev`, `bun run build`).
- Web tests: use **Vitest**, run through Bun (e.g. `bunx vitest`). Do not use `bun test`.
- Mock vs real: `MOCK_MODE=server` proxies `/api` to the mock API (`:8300`), `off` proxies to the real API (`:8400`), and `worker` serves mocks in-browser with no server. The web dev server runs on `:8000`.
- Database: `make migrate` applies `packages/db` migrations to the dev database. Nothing migrates at
  boot (`RUN_MIGRATIONS` is set in some `.env` files but read by no code).
- Model calls: every model call — pre-flight's live check and the worker's review — runs on the
  **credential linked to the model** (Providers & Models: the credential a model was imported
  through), using that credential's base URL and key. `LITELLM_BASE_URL` + `LITELLM_MASTER_KEY`
  (`apps/server/.env`, `apps/worker/.env`) is the **fallback** for a model with no usable
  credential, and the only option when `ENCRYPTION_KEY` is unset, since a stored key cannot be
  decrypted. With neither, reading the app works and a submission is refused with a notice naming
  the model and both ways out. Base URLs are given **without `/v1`**.
- The dev database is **shared by every worktree** (one `slopolis-postgres-1` container, one
  `slopolis` database), so a worktree whose `packages/db/slopolis_db/migrations` diverged from
  `main` can leave it stamped with a revision this checkout cannot resolve: `alembic current` fails
  with `Can't locate revision identified by …`, and the server dies on missing columns such as
  `column users.workspace_id does not exist`. Back up, reset the schema, and migrate:

  ```bash
  docker exec slopolis-postgres-1 pg_dump -U slopolis -d slopolis --no-owner > /tmp/slopolis-dev.sql
  docker exec slopolis-postgres-1 psql -U slopolis -d slopolis -c 'drop schema public cascade; create schema public;'
  make migrate
  ```
