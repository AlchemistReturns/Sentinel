# Sentinel

Autonomous codebase auditor & refactoring agent — audits a Git repo, investigates findings, opens explained fix PRs. Human review gates every merge.

Full spec and phased build plan tracked privately (see local `sentinel-project-brief.md` / `roadmap.md`, gitignored).

## Status

Phase 1 done: a single LangChain agent investigates a repo for unused imports (LangSmith-traced), served via a minimal FastAPI orchestrator and rendered in a Next.js dashboard.

## Stack

LangGraph + Deep Agents, LangChain, OpenAI, Postgres+pgvector, Redis, Next.js, Docker Compose.

## Getting started

```bash
cp .env.example .env   # fill in OPENAI_API_KEY, GITHUB_TOKEN, etc.
docker compose up -d   # postgres+pgvector, redis, prometheus, grafana
uv run sentinel ingest --repo .
uv run sentinel query --repo . --text "your question about the codebase"
uv run sentinel investigate --repo .   # runs the quality-analyst agent, prints findings JSON

# orchestrator API + dashboard
uv run uvicorn sentinel.api:app --port 8000
cd frontend && cp .env.local.example .env.local && npm install && npm run dev
# open http://localhost:3000, click "Run audit"
```
