# slopolis

slopolis is a self-hostable GitHub PR review agent. The first MVP is app-triggered:
you sign in with GitHub, paste one or more PR URLs plus an optional prompt into the web
app, and the service validates the request, queues it, runs the review, and posts
structured findings back to GitHub (summary comment, inline comments, and a check run)
while rendering everything in the app. Agent orchestration, a mentionable `@slopolis`
bot, chat mode, and budgets come in later phases.

**Locked stack**

- Frontend: React + Vite + TypeScript SPA, Tailwind + shadcn/ui
- Backend: Python + FastAPI (async), Pydantic v2
- Worker: ARQ on Redis
- Data: Postgres + SQLAlchemy 2.0 (async) + Alembic
- GitHub: single GitHub App (OAuth login + installation tokens), githubkit client
- Model gateway: LiteLLM, plus any OpenAI-compatible endpoint
- Deploy: Docker Compose (self-hosted first, multi-tenant later)

**Phase order**

1. App-triggered review MVP (current)
2. GitHub comment flow (`@slopolis review`)
3. Agent orchestration (orchestrator, per-PR managers, sub-agents)
4. Chat mode
5. Extensibility and scale (rule packs, more SCMs, budgets, Helm/SLOs)

**Self-hosting:** one Docker Compose stack brings up the SPA, API server, ARQ worker,
Postgres, Redis, and a LiteLLM gateway. Configure it with a `.env` file and bootstrap in
a single command.

Full design, architecture, and decisions: [docs/specification/README.md](./docs/specification/README.md)
