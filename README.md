# Sentinel

Autonomous codebase auditor & refactoring agent — audits a Git repo, investigates findings, opens explained fix PRs. Human review gates every merge.

Full spec and phased build plan tracked privately (see local `sentinel-project-brief.md` / `roadmap.md`, gitignored).

## Status

Phase 4 done: Sentinel generates real fixes, validates them (ruff + pytest), and opens real PRs on GitHub via the API — mechanical fixes as normal PRs, risky (security) fixes as drafts labeled `needs-security-review`. Idempotent on re-run (no duplicate PRs), with a working kill switch and a PR review screen in the dashboard.

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
```
