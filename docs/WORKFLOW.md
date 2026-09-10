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
designs/             # mock design artifacts
```

## Spec versions
- Specs live in `docs/specification/vN/`.
- `v1/` is the MVP. `overview.md` holds goals, decisions, and architecture; `features/NN-slug.md` holds one feature each.
- New or changed scope gets a new version directory. Update `docs/specification/README.md` (index and status) whenever a version is added or changes.

## The feature loop: spec → contract → mock → wire
1. **Spec** — write or update `docs/specification/vN/features/NN-slug.md`.
2. **Contract** — add the feature's API types to `apps/web/src/api/contract.ts`.
3. **Mock** — add MSW handlers in `apps/web/src/mocks/<feature>.ts` with realistic data plus loading, empty, and error states.
4. **Design** — build the UI under `apps/web/src/features/<feature>/`; iterate interactively; keep `brief.md`, `prompt.md`, `critique.md`, and `screenshot.png` in `designs/<slug>/`.
5. **Approve** — review the running mock in a browser, on desktop and mobile.
6. **Wire** — implement the backend to the contract, unregister the feature's mock handlers, and delete the mock.
7. **Verify** — add a contract test and a smoke test.

## Mocking policy
- Request-level mocking via **MSW** only.
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
- Web: `cd apps/web && bun install && bun run dev`
- Web tooling: the web app uses **Bun** as its package manager and dev/build runner (`bun install`, `bun run dev`, `bun run build`).
- Web tests: use **Vitest**, run through Bun (e.g. `bunx vitest`). Do not use `bun test`.
- Mock mode: MSW starts automatically in development (or when `VITE_MOCK=1`).
- Backend and worker commands are added in later phases.
