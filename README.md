# Sentinel

Autonomous codebase auditor & refactoring agent — audits a Git repo, investigates findings, opens explained fix PRs. Human review gates every merge.

Full spec and phased build plan tracked privately (see local `sentinel-project-brief.md` / `roadmap.md`, gitignored).

## Status

Phase 5 done: durable Postgres-backed audit state, a Redis task queue with a worker pool (`rq worker`), a fallback chain (model retry, tool retry-with-backoff), hard per-audit ($0.50) and daily ($5.00) spend caps, a distributed lock so concurrent workers never collide on the same repo's PRs, and an audit-history trend chart in the dashboard. Verified with 2 real concurrent worker processes and 3 simultaneous audit jobs.

## Stack

LangGraph + Deep Agents, LangChain, OpenAI, Postgres+pgvector, Redis, Next.js, Docker Compose.

## Getting started

```bash
cp .env.example .env   # fill in OPENAI_API_KEY, GITHUB_TOKEN, etc.
docker compose up -d   # postgres+pgvector, redis, prometheus, grafana
uv run sentinel ingest --repo .
uv run sentinel query --repo . --text "your question about the codebase"
uv run sentinel investigate --repo .   # single quality-analyst agent (Phase 1)
uv run sentinel audit --repo .         # full multi-agent graph: findings + fixes + real PRs (Phase 2-4)
# point --repo at a real GitHub-backed clone (not this repo) to see PRs actually open, e.g.:
# uv run sentinel audit --repo ../sentinel-test-target

# orchestrator API + dashboard
uv run uvicorn sentinel.api:app --port 8000
cd frontend && cp .env.local.example .env.local && npm install && npm run dev
# open http://localhost:3000, click "Run audit"

# metrics: http://localhost:8000/metrics (Prometheus scrapes this via host.docker.internal)

# queue + workers (optional -- POST /api/audits still runs inline without these)
uv run rq worker sentinel-audits --worker-class rq.SimpleWorker --url redis://localhost:6379/0
# POST /api/audits/enqueue {"repo": "..."} then GET /api/audits/jobs/{job_id}
```
