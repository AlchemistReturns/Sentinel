# Design Decisions & Challenges

This is the honest engineering log: real problems hit while building Sentinel, the
options considered, what was chosen, and why. Nothing here is retrofitted — these are the
actual bugs and tradeoffs encountered phase by phase (full blow-by-blow in `roadmap.md`,
which is kept locally and not published — this file is the durable, shareable version of
that record).

---

## 1. Scope decisions

### OpenAI, mixed model tier
Single-provider (OpenAI) rather than multi-provider abstraction — the added flexibility
of a provider-agnostic layer wasn't worth the complexity for a project whose point is
agent orchestration and safety rails, not provider portability. Mixed tier
(`gpt-4.1-mini` for analysts/fixer, a cheaper fallback for retries) mirrors the brief's
own layered-fallback philosophy.

### Python-only audit scope
Real SAST/dependency tools (`bandit`, `pip-audit`, `semgrep`'s Python rules) are
Python-native; a single-ecosystem MVP end-to-end beats a shallow multi-language pass.
Widening to JS/TS is a natural extension, not a redesign — `iter_source_files` and the
chunking pipeline are already language-agnostic at the file-walking level.

### Auth dropped entirely (Phase 6)
NextAuth/signed-cookie gating was in the original roadmap; cut when it became clear it
was orthogonal to the project's actual goal — demonstrating agent orchestration and
production-safety judgment, not auth infrastructure (a solved, well-trodden problem).
Every consequential action stays safe by construction regardless of who's logged in:
PR-only (never auto-merge), kill switch, spend caps, idempotency. Auth would have gated
*who* can click the button, not made any action itself safer — the wrong thing to spend
scope on here.

### Test-writing excluded from auto-fix (Phase 4)
The Fixer (`sentinel/fixer.py`) only handles `quality` and `security` findings.
Generating a *new*, correct test is a materially harder problem than a scoped
snippet-replace edit (need to understand test framework conventions, fixtures, what
"correct" even means for an untested function) — deferred rather than rushed into a
fix generator that would produce low-quality tests nobody trusts.

---

## 2. Real bugs found while building — and how they were fixed

### 2.1 LangGraph's default recursion limit is effectively unbounded (Phase 2)
**Symptom**: the Security Analyst spun in a non-converging tool-call loop — 50+ calls,
no error, no stop. **Root cause**: the installed `langgraph` version's default
`recursion_limit` is `10007` (`langgraph/_internal/_config.py`), not a sane small number
— for all practical purposes, unbounded. **Fix**: explicit `recursion_limit` on every
analyst invocation (`MAX_ANALYST_STEPS` in `sentinel/agent.py`, tuned 30 → 50 → 70 as
analysts gained more tools and legitimately needed more rounds) with a graceful
`GraphRecursionError` catch, plus a shared "don't repeat tool calls, converge once you
have enough" rule appended to every analyst's system prompt. This is a stopgap — real
per-audit cost/step budgets (Phase 5's `sentinel/cost.py`) are the durable fix.

### 2.2 Native structured output silently fails on some models (Phase 2)
**Symptom**: `StructuredOutputValidationError` — the model's final answer wasn't valid
JSON (extra text/markdown fences around it), an intermittent failure specific to
"native" prompted JSON mode. **Fix**: switched every agent from
`response_format=FindingsReport` to `response_format=ToolStrategy(FindingsReport,
handle_errors=True)` — forces the model to emit its final answer as a tool call instead
of free-text JSON, which is far more reliable across models.

### 2.3 Quality Analyst reported out-of-scope findings despite its own reasoning saying not to (Phase 4)
**Symptom**: caught mid-audit — the Quality Analyst reported `AWS_ACCESS_KEY`/
`DB_PASSWORD` as "unused" findings, with the `explanation` field literally reading *"our
scope is imports, so we do not report this"* — correct reasoning, followed by including
it in the final structured output anyway. Sentinel auto-opened two incorrect PRs proposing
to delete security-relevant constants. **What actually happened**: nothing dangerous —
both PRs sat open, unmerged, clearly attributable; the human-review boundary caught
exactly what it exists to catch. **Fix**: added a hard scope constraint to the prompt
("an `import` statement is the ONLY thing you may report... regardless of what you say
about it in your reasoning") — verified clean on the next run. Left in the record rather
than quietly patched, because it's a concrete demonstration of *why* the PR-only,
human-review architecture matters more than any one prompt fix.

### 2.4 Idempotency dedup keyed on LLM-generated free text (Phase 4)
**Symptom**: re-running an audit against the same repo opened duplicate PRs for findings
that were semantically identical to already-fixed ones. **Root cause**: the dedup key
included the finding's `evidence` field — free text the LLM doesn't reproduce
byte-identically run to run. **Fix**: rekeyed on `analyst:file_path:symbol` only (the
stable identity of "this issue on this code element"), dropping all free-text fields.
Verified with two consecutive live runs post-fix: every previously-fixed finding
correctly showed `duplicate_skipped`, zero new PRs.

### 2.5 Lint validation as an all-clean gate would have blocked valid fixes (Phase 4)
**Caught before shipping, not after**: real repos carry pre-existing lint debt unrelated
to any one finding. `sentinel-test-target/app.py` has three unrelated unused imports; an
"all-clean" gate would have wrongly blocked a valid single-import removal for not also
fixing the other two. **Fix**: `validate_fix` (`sentinel/git_actions.py`) compares ruff
error count on the target file *before* vs *after* the patch — a fix passes if it
doesn't increase errors, not only if the file reaches zero.

### 2.6 WebSocket live-audit path crashed on the Postgres checkpointer (Phase 5)
**Symptom**: `NotImplementedError` from `checkpointer.aget_tuple` — `graph.astream()`
needs an *async* checkpointer, but `build_graph()`'s default is `PostgresSaver` (sync
only). **First fix attempt**: `AsyncPostgresSaver` — surfaced a second, Windows-specific
problem: `psycopg`'s async mode can't run on Windows' default `ProactorEventLoop`, and
setting `asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())` at module
import time didn't reliably take effect under uvicorn. **Actual fix**: stepped back and
asked what the checkpointer was even for on this path — the live WS view already builds
`final_state` incrementally from every streamed update, independent of any checkpointer.
A dropped WebSocket has no "resume" concept the way a crashed worker does. So the WS path
runs with `durable=False`; the CLI/REST/queue path (the one with a real worker-death
recovery story) keeps the sync `PostgresSaver` unchanged. Simpler and correct, not a
workaround.

### 2.7 Cloned-repo working directory would have polluted self-audits (Phase 6)
**Caught during design, not after shipping**: the first draft of `sentinel/repos.py`
placed cloned repos inside the Sentinel project directory. Sentinel auditing itself would
then walk into every repo ever onboarded through the UI. **Fix**: `WORKDIR` is a sibling
of the Sentinel project directory, not inside it; `.sentinel_repos` added to
`IGNORED_DIRS` as defense in depth regardless.

### 2.8 A pasted GitHub URL is a new untrusted-input boundary (Phase 6)
Not a bug that shipped — a risk identified and closed before it could become one. Adding
GitHub-URL onboarding meant user-pasted strings would reach `subprocess.run(["git",
"clone", url, ...])`. git's own URL syntax supports far more than `https://` — notably
an `ext::` transport that runs an arbitrary shell command
(`git clone "ext::sh -c 'evil'"` is a real RCE). Treated the same way LLM output is
treated elsewhere in this codebase (never trusted for authorization): a strict
`^https://github\.com/[\w.-]+/[\w.-]+$` regex validates the URL before it ever reaches a
subprocess call. Branch names get the same treatment (reject anything starting with `-`,
which git could parse as a flag instead of a ref).

---

## 3. Deliberate design choices worth explaining

### PR-only, never auto-merge — architecture, not a feature
Every fix, mechanical or risky, produces a GitHub PR. Nothing in Sentinel has a code path
that merges anything. Risky (security) fixes additionally open as **draft** PRs with a
`needs-security-review` label — a concrete GitHub-native mechanism for "always requires
human review" beyond just "a PR exists somewhere": draft PRs resist accidental
fast-merging.

### Snippet-replace patches, not raw diffs
The Fixer asks the LLM for `{old_snippet, new_snippet}` rather than a unified diff.
`old_snippet` must appear exactly once in the file (verified before ever proposing it) —
LLMs are unreliable at line-offset arithmetic in raw diffs; a verified string-replace is
the same pattern coding agents use internally, and it fails safely (rejects the fix) if
the model's snippet doesn't match reality.

### Local validation, remote mutation
Patches are applied to the local clone's working tree just long enough to run
`ruff`/`pytest`, then reverted — the clone stays a pristine read-only mirror. The actual
branch/commit/PR happens entirely through the GitHub API, so no local git push
credentials are ever needed, just `GITHUB_TOKEN`.

### Distributed lock scoped narrowly
The Redis lock (`sentinel/lock.py`) guards only `pr_node`'s git-writing section, not the
whole graph. Analysis (all three analysts) is read-only and safe to run fully
concurrently across workers; only branch/commit/PR creation for a given repo needs mutual
exclusion. A repo-wide lock for the whole pipeline would have been simpler to write and
meaningfully slower under concurrency for no safety benefit.

### Semantic cache as brute-force cosine similarity, not a vector index
At the scale one audit run produces (dozens to low hundreds of findings), a real vector
index would be over-engineering. Redis stores finding-signature embeddings directly;
`sentinel/cache.py` computes cosine similarity in plain Python across the small candidate
set. Simple, correct, and fast enough — the honest tradeoff is it wouldn't scale to
thousands of findings without revisiting.

### Cost budgets as a real mid-run circuit breaker, not just a log line
`BudgetTrackingCallback` extends LangChain's `OpenAICallbackHandler` and *raises* the
instant running cost crosses the per-audit cap — it doesn't just record spend after the
fact. Combined with the durable checkpointer, a budget cutoff mid-run still leaves
whatever findings were already committed, recoverable rather than lost.
