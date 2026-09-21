# slopolis — Agent Guide

Read `docs/WORKFLOW.md` before contributing.

- **Specs:** `docs/specification/vN/` (v1 is the MVP), one file per feature.
- **Frontend:** `apps/web/` — React + Vite + Tailwind + shadcn. Mock data via MSW in `src/mocks/`.
- **Design artifacts:** `.artifact/` (generated mockups; gitignored, never committed).
- **Non-negotiables:** request-level mocking only; the UI depends on `src/api/contract.ts`; delete mocks when a feature is wired; one worktree per feature; Conventional Commits; never commit secrets.

## Quick commands
- `make dev-mock` — web app + standalone mock API (default for UI work)
- `make dev-all` — real API + **ARQ worker** + web app (a submitted session actually runs)
- `make dev-api` — web app + real API only; nothing consumes the queue, so sessions stay `queued`
- `make worker` — the ARQ review worker on its own (needs Redis, the DB, `apps/worker/.env`)
- `make dev-browser-mock` — web app only, in-browser MSW mock (no server needed)
- `make mock` / `make api` — run one server on its own
- `make e2e` — the hermetic review-flow test (no GitHub writes)
- `cd apps/web && bun install` — install web deps

The web app uses **Bun** as its package manager and dev/build runner. Tests use **Vitest**, run through Bun (e.g. `bunx vitest`); do not use `bun test`.
