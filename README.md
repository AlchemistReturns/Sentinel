# Sentinel

Autonomous codebase auditor & refactoring agent — audits a Git repo, investigates findings, opens explained fix PRs. Human review gates every merge.

Full spec and phased build plan tracked privately (see local `sentinel-project-brief.md` / `roadmap.md`, gitignored).

## Status

Repo scaffolding done (Python project via `uv`, infra skeleton via Docker Compose). No agent code yet.

## Stack

LangGraph + Deep Agents, LangChain, OpenAI, Postgres+pgvector, Redis, Next.js, Docker Compose.

## Getting started

```bash
cp .env.example .env   # fill in OPENAI_API_KEY, GITHUB_TOKEN, etc.
docker compose up -d   # postgres+pgvector, redis, prometheus, grafana
uv run python -c "print('ok')"
```
