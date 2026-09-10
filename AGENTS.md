# slopolis — Agent Guide

Read `docs/WORKFLOW.md` before contributing.

- **Specs:** `docs/specification/vN/` (v1 is the MVP), one file per feature.
- **Frontend:** `apps/web/` — React + Vite + Tailwind + shadcn. Mock data via MSW in `src/mocks/`.
- **Design artifacts:** `.artifact/` (generated mockups; gitignored, never committed).
- **Non-negotiables:** request-level mocking only; the UI depends on `src/api/contract.ts`; delete mocks when a feature is wired; one worktree per feature; Conventional Commits; never commit secrets.

## Quick commands
- `make dev-mock` — web app + standalone mock API (default for UI work)
- `make dev-api` — web app + real API (once `apps/server` exists)
- `make dev-worker` — web app only, in-browser mock worker (no server needed)
- `make mock` / `make api` — run one server on its own
- `cd apps/web && bun install` — install web deps

The web app uses **Bun** as its package manager and dev/build runner. Tests use **Vitest**, run through Bun (e.g. `bunx vitest`); do not use `bun test`.
