# Contributing to Sentinel

## Prerequisites

- Python 3.12+, [`uv`](https://docs.astral.sh/uv/)
- Node.js 22+, npm
- Docker (for Postgres+pgvector, Redis, Prometheus, Grafana)
- A GitHub personal access token (`repo` scope) if you're testing the PR-generation path
  against a real repo
- An OpenAI API key

## Setup

```bash
git clone <this repo>
cd Sentinel
cp .env.example .env   # fill in OPENAI_API_KEY, GITHUB_TOKEN, LANGCHAIN_API_KEY
docker compose up -d   # postgres+pgvector, redis, prometheus, grafana
uv sync

cd frontend
cp .env.local.example .env.local
npm install
```

## Running it

```bash
# backend CLI
uv run sentinel ingest --repo .
uv run sentinel audit --repo .

# orchestrator API + WS
uv run uvicorn sentinel.api:app --port 8000 --reload

# frontend
cd frontend && npm run dev

# optional: queue workers (only needed for /api/audits/enqueue, not the default inline path)
uv run rq worker sentinel-audits --worker-class rq.SimpleWorker --url redis://localhost:6379/0
```

Open `http://localhost:3000` — paste a GitHub URL or a local repo path, click Run audit.

**Never point Sentinel at a repo you care about while testing changes to `pr_node`,
`fixer.py`, or `git_actions.py`** — those write real branches/commits/PRs. Use a
disposable repo (see `sentinel-project-brief.md` / `roadmap.md` for how
`sentinel-test-target` was built and why).

## Project conventions

- **Python**: `uv` + `pyproject.toml`, no `requirements.txt`. Format/lint with `ruff`
  (already a dependency — `uv run ruff check .`).
- **TypeScript**: standard Next.js/ESLint conventions from `create-next-app`; shadcn/ui
  components live in `frontend/src/components/ui/` and are generated via
  `npx shadcn add <component>`, not hand-written.
- **Logging**: structured JSON via `structlog` (`sentinel/logging.py`). Every log call in
  a request/audit path should carry `audit_id` if one exists in scope — it's the
  correlation ID that ties a log line to a specific run across the queue, agents, and
  git actions.
- **Config**: env vars only, read via `sentinel/config.py`'s `Settings` class. Never
  hardcode an endpoint, model name, or credential — add a new `Settings` field with a
  sensible default instead.
- **Commits**: descriptive, explain *why* not just *what*. This project's own commit
  history (not covered by this doc) is the reference for tone/format.

## Testing status (read this before assuming coverage exists)

Sentinel does not yet have a test suite for its own codebase — the Test Analyst has
correctly flagged this on every self-audit throughout development (see
`design_decisions.md`). If you're adding a testable, non-trivial function (especially in
`sentinel/tools.py`, `sentinel/cache.py`, `sentinel/dedup.py`, or anything with a validated
input boundary like `sentinel/repos.py`'s URL regex), adding a `tests/` directory with
`pytest` tests for it is genuinely valuable — there's no existing suite that would make
this redundant.

For validating behavioral changes to the agent pipeline itself, the practical approach
used throughout this project's own development was:
1. Test the specific function/tool in isolation first (`uv run python -c "..."`) before
   wiring it into an agent — cheaper and faster to debug.
2. Run `sentinel audit --repo .` (self-audit) to sanity-check the full pipeline.
3. Run against `sentinel-test-target` (or another disposable repo) to validate anything
   touching real GitHub actions.
4. For dedup/idempotency/lock changes specifically: run the same audit twice in a row and
   diff the `pr_status` fields — this is how every dedup regression in this project was
   actually caught.

## Adding a new analyst

1. Add tools in `sentinel/tools.py` (or a new module) following the existing pattern:
   a `make_*_tool(repo_path: Path)` factory returning a `@tool`-decorated closure.
   Validate any path/input the tool touches — see `_within_repo` in `tools.py` and the
   URL validation in `repos.py` for the pattern.
2. Add a system prompt in `sentinel/analysts.py` following the existing three: state the
   grounding tools to use first, an explicit scope boundary, and append
   `sentinel.agent.CONVERGENCE_RULE`.
3. Add a `*_node(state: AuditState) -> dict` function calling `_run_analyst(...)` with
   your tools/prompt, returning `{"<name>_findings": findings}`.
4. Wire it into `build_graph()` in `sentinel/graph.py`: add the node, an edge from
   `scope`, and an edge to `diagnose`. Update `diagnose_node`'s risk-tier assignment for
   the new analyst.
5. Update `sentinel/state.py`'s `AuditState` with the new findings key.

## Adding a new fix-generation capability

`sentinel/fixer.py`'s `FIXABLE_ANALYSTS` set controls which analysts' findings get a
proposed fix at all. If you're extending it (e.g. to include `test` findings once
test-writing is implemented), the constraint that matters most: `old_snippet` must
remain something verifiably unique-and-exact in the file, checked before the fix is ever
proposed as usable (see `design_decisions.md`'s note on snippet-replace patches over raw
diffs) — don't relax that check to make a new fix category easier to implement.

## Where things live

See `docs/architecture.md` for the full repository layout and component breakdown.
