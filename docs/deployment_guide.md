# Sentinel — Deployment Guide

*This is a planning document, not a deployment — nothing here has been provisioned or deployed.
The goal is that everything below is a **config-and-manifest exercise**,
not a rewrite: every service is already separate, already config-driven via env vars,
and already stateless where it needs to be.*

---

## 1. Current state

| Concern | Status today |
|---|---|
| Containerization | Infra (Postgres+pgvector, Redis, Prometheus, Grafana) runs in Docker Compose. App services (orchestrator, worker, frontend) run locally via `uv run` / `npm run dev` — deliberately deferred (see `roadmap.md` Phase 1 notes) to keep iteration fast; `docker-compose.yml` already has commented-out service stubs for all three. |
| Config | 100% env vars, no hardcoded endpoints/secrets. Backend: `.env` read via `sentinel/config.py` (`python-dotenv`). Frontend: `.env.local`, `NEXT_PUBLIC_API_URL`. |
| State | Stateless workers by design — all state in Postgres (LangGraph checkpointer, pgvector) or Redis (cache, dedup, locks, kill switch, budgets, queue). No in-process state survives a restart *except* the orchestrator's in-memory `_audits` dict (audit-result cache for the REST/dashboard view — documented limitation, see `sentinel/api.py`). |
| Health | Orchestrator has `GET /health`. Frontend has no dedicated health route yet (Next.js `/` returning 200 is sufficient for most LB health checks, but see §6). Worker health is implicit via RQ's Redis heartbeat. |
| Logging | Structured JSON to stdout (`sentinel/logging.py`, `structlog`) — already twelve-factor, flows to any log aggregator unchanged. |
| Secrets in code/images | None. `GITHUB_TOKEN`, `OPENAI_API_KEY`, `LANGCHAIN_API_KEY` are read from env at runtime only. |

---

## 2. Target container topology

```
                        ┌─────────────┐
  users ───────────────▶│  frontend   │  Next.js (Node 22 runtime)
                        └──────┬──────┘
                               │ REST + WS
                        ┌──────▼──────┐
                        │ orchestrator│  FastAPI (uvicorn)
                        └──┬───────┬──┘
                           │       │
                  ┌────────▼┐   ┌──▼─────────┐
                  │ worker  │   │ worker (N) │  RQ SimpleWorker → standard
                  │ (RQ)    │   │            │  forking Worker on Linux
                  └────┬────┘   └─────┬──────┘
                       │              │
              ┌────────▼──────────────▼────────┐
              │        Redis (managed)          │  queue, cache, locks,
              └──────────────────────────────────┘  kill switch, budgets
              ┌──────────────────────────────────┐
              │   Postgres + pgvector (managed)   │  checkpoints, embeddings,
              └──────────────────────────────────┘  findings history
```

Five deployable units: `frontend`, `orchestrator`, `worker` (horizontally scalable,
N replicas), `postgres` (managed), `redis` (managed). Prometheus/Grafana are optional —
see §7.

---

## 3. Dockerfiles needed

None exist yet (app services run locally today). These are what `docker-compose.yml`'s
commented-out stubs are waiting on — writing them is the actual unblocking work, not a
redesign.

### `orchestrator` / `worker` (shared base — same Python image, different entrypoint)

```dockerfile
FROM python:3.12-slim AS base
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY sentinel/ ./sentinel/
ENV PATH="/app/.venv/bin:$PATH"

FROM base AS orchestrator
EXPOSE 8000
HEALTHCHECK CMD curl -f http://localhost:8000/health || exit 1
CMD ["uvicorn", "sentinel.api:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS worker
# Linux has os.fork() -- use the standard forking Worker, not SimpleWorker
# (SimpleWorker exists only because Windows dev machines have no fork()).
CMD ["rq", "worker", "sentinel-audits", "--url", "$REDIS_URL"]
```

Also needs `semgrep`, `git`, `ruff` on `PATH` inside the image (semgrep and ruff install
via `uv sync` already since they're project dependencies; `git` needs an explicit
`apt-get install -y git` — the base `python:3.12-slim` image doesn't include it, and
`sentinel/git_actions.py`, `sentinel/repos.py`, and the `git_diff` tool all shell out to
it).

### `frontend`

```dockerfile
FROM node:22-slim AS deps
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

FROM node:22-slim AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ .
RUN npm run build

FROM node:22-slim AS runtime
WORKDIR /app
COPY --from=build /app/.next ./.next
COPY --from=build /app/public ./public
COPY --from=build /app/package.json ./package.json
COPY --from=build /app/node_modules ./node_modules
EXPOSE 3000
CMD ["npm", "start"]
```

(Switching `next.config.ts` to `output: "standalone"` would shrink this considerably —
worth doing at actual build time, not required for the plan.)

---

## 4. Environment variables (secrets manager mapping)

Every one of these is already externalized — cloud deploy is "populate the secrets
manager," not "find and remove hardcoded values."

| Variable | Used by | Secret? | Cloud equivalent |
|---|---|---|---|
| `OPENAI_API_KEY` | orchestrator, worker | Yes | Secrets Manager / Parameter Store (SecureString) |
| `GITHUB_TOKEN` | orchestrator, worker | Yes | Secrets Manager — scope to `repo` on a dedicated bot account, not a personal token |
| `LANGCHAIN_API_KEY` | orchestrator, worker | Yes | Secrets Manager |
| `LANGCHAIN_TRACING_V2`, `LANGCHAIN_PROJECT` | orchestrator, worker | No | Plain env var |
| `DATABASE_URL` | orchestrator, worker | Yes (embeds password) | Secrets Manager, or IAM-based auth (RDS IAM tokens) if the managed Postgres supports it |
| `REDIS_URL` | orchestrator, worker | Sometimes (if AUTH enabled) | Secrets Manager or plain env var if the managed Redis is in a private subnet with no AUTH |
| `SENTINEL_AGENT_MODEL`, `SENTINEL_FALLBACK_MODEL`, `SENTINEL_EMBEDDING_MODEL` | orchestrator, worker | No | Plain env var (has defaults, only needed to override) |
| `NEXT_PUBLIC_API_URL` | frontend (build + runtime) | No | Plain env var — **must** point at the orchestrator's public URL, not `localhost` |

`NEXT_PUBLIC_*` vars are baked into the frontend build, not read at container start —
the frontend image needs to be built per-environment (or use a runtime-config workaround)
if the orchestrator URL differs between staging/prod.

---

## 5. Managed service mapping

| Local (Docker Compose) | Managed equivalent | Notes |
|---|---|---|
| `pgvector/pgvector:pg16` | AWS RDS for PostgreSQL (pgvector is a supported extension since RDS PG 15+) / Cloud SQL for PostgreSQL with pgvector / Neon / Supabase | Must run `CREATE EXTENSION vector;` once — `sentinel/ingest.py`'s `PGVector` class and `sentinel/durable.py`'s `PostgresSaver.setup()` both assume it's already enabled and will fail loudly (not silently) if not. |
| `redis:7-alpine` | AWS ElastiCache for Redis / Memorystore / Upstash | No persistence requirements beyond what's already TTL'd (`sentinel/cache.py`, `sentinel/dedup.py`, `sentinel/killswitch.py` all set explicit TTLs or are fine to lose on restart) — a cache-tier instance is sufficient, doesn't need Redis's AOF/RDB durability. |
| `prom/prometheus` + `grafana/grafana` | Managed Prometheus (AWS Managed Prometheus / Grafana Cloud) or keep self-hosted in-cluster | Optional — `/metrics` is a standard Prometheus-format endpoint regardless of scraper. |
| N/A (repo clones on local disk, `.sentinel_repos/`) | **Needs a decision** — see §8 | Not a drop-in managed-service swap; flagged separately because it's a real architectural gap, not a config change. |

---

## 6. Orchestrator + worker manifests (conceptual)

Either ECS/Fargate or Kubernetes works cleanly given the containers above are already
stateless. Sketch (Kubernetes-flavored, translates directly to an ECS task
definition + service):

- **`orchestrator` Deployment**: 1-2 replicas behind a Service + Ingress/ALB (WebSocket
  support required — `/ws/audits` needs the load balancer configured for WS upgrade,
  sticky sessions *not* required since state lives in Postgres/Redis, not in-process,
  except the `_audits` in-memory cache noted in §1, which means the dashboard's "latest
  audit" view can be inconsistent across replicas until that's moved to Postgres — a
  known gap, not silently glossed over).
- **`worker` Deployment**: N replicas (start at 2-3 per the roadmap), no Service needed
  (workers only pull from Redis, nothing connects to them). Horizontal Pod Autoscaler on
  queue depth (RQ exposes queue length via Redis `LLEN`) would be the natural scaling
  signal.
- **`frontend` Deployment**: 1-2 replicas behind its own Service + Ingress, or skip
  containerizing it entirely and deploy to Vercel (arguably the simpler path for a
  Next.js app specifically — no manifest needed at all, `NEXT_PUBLIC_API_URL` becomes a
  Vercel project env var).
- **Health checks**: orchestrator's `/health` already exists — wire it as the
  liveness/readiness probe. Add a trivial `GET /` 200 check for the frontend (Next.js
  serves this by default). Worker liveness is RQ's own heartbeat mechanism (a worker that
  stops updating its Redis heartbeat key is considered dead and its in-flight job
  requeued) — no custom probe needed.
- **Graceful shutdown**: `uvicorn` and `rq worker` both handle `SIGTERM` correctly by
  default (finish in-flight request/job, then exit) — orchestrators depend on this and
  it costs nothing extra here since neither was overridden.

---

## 7. Networking & CORS

- `sentinel/api.py`'s `CORSMiddleware` currently allows only `http://localhost:3000` —
  **must** be updated to the production frontend origin(s) before deploy. This is the one
  hardcoded-for-dev value in the whole backend; everything else is already env-driven.
- WebSocket URL scheme: `frontend/src/lib/api.ts`'s `liveAuditSocketUrl` derives `ws://`
  from `NEXT_PUBLIC_API_URL` by string-replacing `http` → `ws`. Once the orchestrator is
  behind TLS, `NEXT_PUBLIC_API_URL=https://...` correctly derives `wss://` — no code
  change needed, just confirm the load balancer/ingress passes WS upgrade headers through
  (ALB and most Ingress controllers need this explicitly enabled).
- Prometheus scrape target (`infra/prometheus.yml`) currently points at
  `host.docker.internal:8000` (a Windows-dev-only workaround for the orchestrator running
  outside Docker) — becomes a normal in-cluster service DNS name
  (`orchestrator.default.svc.cluster.local:8000` or equivalent) once the orchestrator is
  containerized per §3.

---

## 8. Known gap: local disk state in `sentinel/repos.py`

`connect_repo()` clones GitHub repos to `.sentinel_repos/` on local disk, keyed by a hash
of the URL, and reuses that clone on subsequent runs (fetch+pull instead of re-clone).
This works cleanly for a single-instance dev setup but doesn't drop in cleanly to a
multi-replica cloud deployment: if `worker` replica A clones a repo and replica B later
picks up a job for the same repo, B has no access to A's local clone.

Three real options, not a false choice:

1. **Shared volume** (EFS/Azure Files/Filestore mounted into every worker) — simplest
   conceptually, adds a network-filesystem dependency and its latency/cost.
2. **Re-clone per job, always** — simplest to implement (drop the "reuse existing clone"
   fast path entirely), costs a full clone's worth of time per audit instead of a
   fetch+pull. Fine for small-to-medium repos, worse for large ones.
3. **Object storage as the durability layer** — clone locally to a job's ephemeral disk,
   but push/pull the `.git` directory to/from S3/GCS between runs instead of relying on
   local disk. More engineering, best of both (fast repeat audits, no shared-filesystem
   dependency).

No implementation was picked here — this is explicitly the one item in this whole guide
that's a real design decision requiring product input (expected audit frequency per repo,
expected repo sizes), not just a manifest to write. Flagged honestly rather than papered
over.

---

## 9. What does *not* need to change

Worth stating explicitly, since it's the point of having built this way from Phase 0
onward:

- Cost budgets (`sentinel/cost.py`) — Redis-backed, no code change for cloud.
- Kill switch (`sentinel/killswitch.py`) — same.
- Idempotency / dedup (`sentinel/dedup.py`) — same.
- Distributed lock (`sentinel/lock.py`) — same; this is precisely the mechanism that
  makes multi-replica `worker` deployments safe for concurrent PR generation.
- LangSmith tracing — already a cloud SaaS dependency, zero change.
- Structured logging — already stdout JSON, flows into CloudWatch/Stackdriver/whatever
  unchanged.

---

## 10. Summary

| Task | Effort |
|---|---|
| Write 2 Dockerfiles (shared base for orchestrator/worker + one for frontend) | Small — templates above are close to final |
| Provision managed Postgres (with pgvector extension) + managed Redis | Small — no schema migration tooling needed beyond what already runs on startup |
| Populate secrets manager with 4 secret values | Small |
| Write ECS task defs or K8s manifests for 3 deployable units | Medium — mostly boilerplate given the containers are already stateless |
| Update CORS origin + Prometheus scrape target (2 config values) | Trivial |
| Decide + implement repo-clone durability strategy (§8) | Medium — the one genuine open design question |

Nothing above requires touching agent logic, the graph, tools, or safety rails — the
"deploy-ready architecture" principle held.
