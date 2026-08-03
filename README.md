# Sentinel

Autonomous codebase auditor & refactoring agent — audits a Git repo, investigates findings, opens explained fix PRs. Human review gates every merge.

Full spec and phased build plan tracked privately (see local `sentinel-project-brief.md` / `roadmap.md`, gitignored).

## Status

Phase 2 done: a LangGraph audit graph runs Security, Quality, and Test analyst sub-agents in parallel, synthesizes their findings with a risk tier, and streams the whole run live over WebSocket to the dashboard.

## Stack

LangGraph + Deep Agents, LangChain, OpenAI, Postgres+pgvector, Redis, Next.js, Docker Compose.

## Getting started

```bash
cp .env.example .env   # fill in OPENAI_API_KEY, GITHUB_TOKEN, etc.
docker compose up -d   # postgres+pgvector, redis, prometheus, grafana
uv run sentinel ingest --repo .
uv run sentinel query --repo . --text "your question about the codebase"
uv run sentinel investigate --repo .   # single quality-analyst agent (Phase 1)
uv run sentinel audit --repo .         # full multi-agent graph: security + quality + test (Phase 2)

# orchestrator API + dashboard
uv run uvicorn sentinel.api:app --port 8000
cd frontend && cp .env.local.example .env.local && npm install && npm run dev
# open http://localhost:3000, click "Run audit"
```
