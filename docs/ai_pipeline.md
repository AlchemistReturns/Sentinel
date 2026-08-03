# AI Pipeline

How Sentinel actually uses LLMs, end to end — models, prompts, tools, retrieval,
caching, and the safety rails wrapped around all of it.

---

## 1. Models

| Role | Model | Why |
|---|---|---|
| Analysts (security/quality/test), Fixer | `gpt-4.1-mini` (`SENTINEL_AGENT_MODEL`) | Cheap enough to run three parallel agents plus per-finding patch generation without runaway cost; strong enough for tool-calling and structured output. |
| Fallback (on any transient model failure) | `gpt-4.1-nano` (`SENTINEL_FALLBACK_MODEL`) | One retry on a cheaper/faster model before giving up on that analyst entirely — see §5. |
| Embeddings | `text-embedding-3-small` (`SENTINEL_EMBEDDING_MODEL`) | Source-code chunking, semantic cache signatures, findings-history search. |

All three are env-var overridable; no model choice is hardcoded.

---

## 2. The audit graph (LangGraph `StateGraph`)

```
START → ingest → scope → [security_analyst, quality_analyst, test_analyst] (parallel)
      → diagnose → propose → validate → pr → END
```

- **ingest/scope** — no LLM calls. Resolve the repo path, walk the file tree.
- **Three analysts** — run concurrently (LangGraph schedules sync node functions in a
  thread pool when the graph is invoked asynchronously). Each is an independent
  `langchain.agents.create_agent` tool-calling loop with its own system prompt, tool set,
  and structured output schema.
- **diagnose** — deterministic Python, no LLM call. Merges the three findings lists,
  assigns risk tier by rule (not model judgment: `security` → `risky`,
  `quality`/`test` → `mechanical`), runs each finding through the semantic cache, persists
  to findings history.
- **propose** — one structured-output LLM call per fixable finding (not an agent — no
  tools needed, the finding + file content is handed directly).
- **validate** — no LLM call. `ruff` + `pytest`, deterministic.
- **pr** — no LLM call. GitHub API actions, deterministic.

Only 4 of the 9 nodes ever call an LLM. The rest are exactly as deterministic and
auditable as regular code — a conscious choice: use the LLM where judgment is genuinely
required (investigation, diagnosis phrasing, patch generation), not for orchestration
logic that doesn't need it.

---

## 3. The three analysts

### Security Analyst
**Tools**: `run_semgrep` (`--config=auto`, real SAST), `run_dependency_audit`
(`pip-audit`, real CVE data), `scan_hardcoded_secrets` (regex-based, supplementary),
`read_source_file`, `git_diff`.

**Grounding principle** (brief §5): the LLM *interprets and prioritizes* real tool
output, it does not freehand-detect vulnerabilities from raw code reading. The prompt
explicitly routes through the three grounding tools first; `read_source_file` is only
for verifying a lead in context, and dependency CVEs are reported directly without
needing a file read at all (there's nothing to read — it's a version string).

### Quality Analyst
**Tools**: `list_python_files`, `read_source_file`, `git_diff`.

Scoped tightly to unused imports (not general "code smells" — an explicit, verifiable
issue class chosen deliberately as the Phase 1 starting point specifically *because* it's
unambiguous). Hard scope constraint in the prompt after a real incident (see
`design_decisions.md` §2.3) forbids reporting anything else, "regardless of what you say
about it in your reasoning."

### Test Analyst
**Tools**: `list_python_files`, `list_test_files`, `read_source_file`.

Read-only gap detection — finds functions/classes never referenced by any test file.
Explicitly does not write tests (see `design_decisions.md` §1) or run the test suite
itself (that's `validate_node`'s job, on *proposed fixes*, not on the audit itself).

### Shared structured output strategy
All three (plus the Fixer) use `ToolStrategy(Schema, handle_errors=True)` rather than
native prompted JSON mode — forces the final answer through a tool call, which proved
far more reliable across models than asking for free-text JSON (see
`design_decisions.md` §2.2).

### Shared convergence rule
Every analyst prompt ends with the same hard rule: never repeat an identical tool call,
submit the final report as soon as there's enough evidence, use the limited tool-call
budget deliberately. Backed by an explicit `recursion_limit` (`MAX_ANALYST_STEPS = 70`)
on every invocation — LangGraph's own default is effectively unbounded (see
`design_decisions.md` §2.1).

---

## 4. RAG & retrieval

### Ingestion (`sentinel/ingest.py`)
Python files are parsed with `ast` and chunked by top-level function/class (plus one
"module preamble" chunk for imports/module-level code) — not raw line windows.
Non-Python or unparseable files fall back to a line-window chunker. Each chunk gets a
stable id (`sha256(file_path::symbol)`), embedded via OpenAI, stored in a per-repo
pgvector collection (`langchain-postgres`).

### Content-hash cache
Before embedding a chunk, its content hash is checked against Redis
(`sentinel/cache.py`). Unchanged chunks are skipped entirely — no OpenAI call, no
pgvector write. Verified: a repeat `sentinel ingest` on an unchanged repo does zero
embedding calls.

### Semantic cache
Every finding's signature (`analyst:symbol:explanation`) is embedded and checked against
previously-cached finding embeddings for the repo (cosine similarity, threshold 0.90).
Near-duplicates are tagged `semantic_cache_hit: true` — e.g. the same class of issue
recurring across many files in one audit gets flagged as a repeat rather than reasoned
about from scratch each time it's *diagnosed* (note: this dedups the diagnosis-tagging
step, not the analyst's own investigation — see the honest scope note in
`design_decisions.md`).

### Findings history
Every audit's findings are persisted into a separate per-repo pgvector collection
(`sentinel/findings_store.py`), with `audit_id`/`analyst`/`risk_tier` metadata — the
foundation for "is this a regression or a known issue" queries, not yet consumed by a UI.

---

## 5. Safety rails around the LLM

| Rail | Mechanism | What it prevents |
|---|---|---|
| PR-only boundary | `pr_node` only ever creates branches/commits/PRs via the GitHub API, never merges | Autonomous merges — the fundamental non-negotiable |
| Draft PR for risky fixes | `open_pr(..., draft=is_risky)` + `needs-security-review` label | Accidental fast-merge of a security fix |
| Human review always | Risk tier assigned deterministically by rule, not LLM judgment | The model can't decide something is "safe enough" to skip review |
| Kill switch | Redis flag (`sentinel/killswitch.py`), checked centrally in every analyst invocation plus propose/validate/pr | Runaway costs or actions once a human notices something wrong |
| Recursion limit | `MAX_ANALYST_STEPS` on every agent invocation | Non-converging tool-call loops (real bug, see `design_decisions.md` §2.1) |
| Per-audit spend cap | `BudgetTrackingCallback` raises mid-run once cost crosses $0.50 | A single audit burning unbounded tokens |
| Daily spend cap | Redis counter checked before starting any audit, $5.00 | Cumulative runaway cost across many audits |
| Fallback chain | Model failure → retry once on a cheaper model; network-tool failure → retry with exponential backoff (`tenacity`) | Transient failures crashing the whole audit |
| Idempotency | Dedup key (`analyst:file_path:symbol`) in Redis before any PR is opened | Duplicate PRs on re-run |
| Distributed lock | Redis `SET NX EX`, scoped to `pr_node` per repo | Two concurrent workers generating conflicting fixes for the same file |
| Input validation | Regex-validated GitHub URLs before any clone; path-traversal checks before any file read; `old_snippet` must be unique in the file before any patch is applied | Untrusted input (user-pasted URLs, LLM-proposed paths/snippets) reaching a subprocess or filesystem write unchecked |

The throughline: **the LLM proposes, code enforces.** Every consequential action (a
branch, a commit, a PR, a file read outside the repo root, a shell command) is gated by
deterministic code that the model can request but never bypass.
