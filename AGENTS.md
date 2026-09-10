# slopolis — Agent Guide

Read `docs/WORKFLOW.md` before contributing.

- **Specs:** `docs/specification/vN/` (v1 is the MVP), one file per feature.
- **Frontend:** `apps/web/` — React + Vite + Tailwind + shadcn. Mock data via MSW in `src/mocks/`.
- **Design artifacts:** `designs/`.
- **Non-negotiables:** request-level mocking only; the UI depends on `src/api/contract.ts`; delete mocks when a feature is wired; one worktree per feature; Conventional Commits; never commit secrets.

## Quick commands
- `cd apps/web && bun install && bun run dev`

The web app uses **Bun** as its package manager and dev/build runner. Tests use **Vitest**, run through Bun (e.g. `bunx vitest`); do not use `bun test`.
