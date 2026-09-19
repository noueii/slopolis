# slopolis infrastructure

Self-hosting / local dev stack: Postgres, Redis, an optional LiteLLM gateway,
and a Caddy config for serving the built SPA.

## Layout

```
infra/
  docker-compose.yml   # postgres, redis, litellm (profile: litellm)
  .env.example         # copy to ../.env and fill in
  litellm/config.yaml  # LiteLLM gateway config (no keys)
  caddy/Caddyfile      # SPA static serving + /api reverse proxy
```

## Start the data services

Postgres and Redis need no secrets:

```bash
docker compose -f infra/docker-compose.yml up -d postgres redis
```

They expose:

| Service  | Port | Named volume |
|----------|------|--------------|
| postgres | 5432 | `pgdata`     |
| redis    | 6379 | `redisdata`  |

Matching connection strings (defaults, from `infra/.env.example`):

```bash
DATABASE_URL=postgresql://slopolis:slopolis@localhost:5432/slopolis
REDIS_URL=redis://localhost:6379/0
```

To override credentials, copy the example env file to the repo root and edit it:

```bash
cp infra/.env.example .env
```

Compose loads `../.env` with `required: false`, so the stack still comes up
with the `slopolis/slopolis/slopolis` defaults when no `.env` exists.

## Enable the LiteLLM gateway

LiteLLM is behind the `litellm` profile so it can be skipped when unused. It
must be enabled explicitly with `--profile litellm`:

```bash
docker compose -f infra/docker-compose.yml --profile litellm up -d
```

It listens on `:4000` and reads `LITELLM_MASTER_KEY`, `OPENAI_BASE_URL`, and
`OPENAI_API_KEY` from the environment (see `infra/litellm/config.yaml`). Set
those in `.env` or `infra/.env.example`; no provider keys are hardcoded.

Check status without starting anything:

```bash
docker compose -f infra/docker-compose.yml config
```

## Caddy static hosting

`infra/caddy/Caddyfile` serves the built SPA from `/srv` with `try_files` SPA
fallback and reverse-proxies `/api/*` to `server:8400` (the backend on the
compose network). It listens on `:80` with no hardcoded domain, so it works on
localhost or behind any reverse proxy. Build the web app and mount its output
at `/srv` in your deployment; Caddy itself is not part of this compose file
(P0.0/P0.7 boundary — add it when a deploy target exists).
