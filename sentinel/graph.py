import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from langchain_openai import OpenAIEmbeddings
from langgraph.graph import END, START, StateGraph

from sentinel import dedup, killswitch, lock
from sentinel.analysts import quality_analyst_node, security_analyst_node, test_analyst_node
from sentinel.cache import semantic_lookup
from sentinel.config import settings
from sentinel.cost import BudgetExceededError, BudgetTrackingCallback, check_daily_budget, record_spend
from sentinel.durable import get_checkpointer
from sentinel.findings_store import index_findings
from sentinel.fixer import generate_fix
from sentinel.git_actions import commit_fix, create_branch, get_github_repo, open_pr, validate_fix
from sentinel.ingest import iter_source_files
from sentinel.logging import get_logger
from sentinel.state import AuditState


def ingest_node(state: AuditState) -> dict:
    log = get_logger(audit_id=state["audit_id"])
    repo = Path(state["repo_path"]).resolve()
    log.info("ingest.start", repo=str(repo))
    return {"repo_path": str(repo)}


def scope_node(state: AuditState) -> dict:
    log = get_logger(audit_id=state["audit_id"])
    repo = Path(state["repo_path"])
    python_files = [
        str(p.relative_to(repo)) for p in iter_source_files(repo) if p.suffix == ".py"
    ]
    log.info("scope.done", python_files=len(python_files))
    return {"python_files": python_files}


def _finding_signature(f: dict) -> str:
    return f"{f['analyst']}:{f['symbol']}:{f['explanation'][:200]}"


def diagnose_node(state: AuditState) -> dict:
    log = get_logger(audit_id=state["audit_id"])
    merged: list[dict] = []
    for f in state.get("security_findings", []):
        merged.append({**f, "analyst": "security", "risk_tier": "risky"})
    for f in state.get("quality_findings", []):
        merged.append({**f, "analyst": "quality", "risk_tier": "mechanical"})
    for f in state.get("test_findings", []):
        merged.append({**f, "analyst": "test", "risk_tier": "mechanical"})

    repo = state["repo_path"]
    if merged:
        embeddings = OpenAIEmbeddings(
            model=settings.embedding_model, api_key=settings.openai_api_key
        )
        signatures = [_finding_signature(f) for f in merged]
        vectors = embeddings.embed_documents(signatures)
        cache_hits = 0
        for f, sig, vec in zip(merged, signatures, vectors):
            is_hit = semantic_lookup(repo, sig, vec)
            f["semantic_cache_hit"] = is_hit
            cache_hits += is_hit

        index_findings(repo, state["audit_id"], merged)
        log.info(
            "diagnose.done",
            total=len(merged),
            semantic_cache_hits=cache_hits,
            semantic_cache_misses=len(merged) - cache_hits,
        )
    else:
        log.info("diagnose.done", total=0)

    return {"findings": merged}


def propose_node(state: AuditState) -> dict:
    log = get_logger(audit_id=state["audit_id"])
    if killswitch.is_active():
        log.info("propose.halted", reason="kill switch active")
        return {}

    repo = Path(state["repo_path"])
    findings = state.get("findings", [])
    proposed = 0
    for f in findings:
        fix = generate_fix(repo, f)
        f["proposed_fix"] = fix.model_dump() if fix else None
        proposed += bool(fix)
    log.info("propose.done", total=len(findings), proposed=proposed)
    return {"findings": findings}


def validate_node(state: AuditState) -> dict:
    log = get_logger(audit_id=state["audit_id"])
    if killswitch.is_active():
        log.info("validate.halted", reason="kill switch active")
        return {}

    repo = Path(state["repo_path"])
    findings = state.get("findings", [])
    validated = 0
    for f in findings:
        pf = f.get("proposed_fix")
        if not pf:
            continue
        result = validate_fix(repo, f["file_path"], pf["old_snippet"], pf["new_snippet"])
        f["validation"] = result.model_dump()
        f["validation"]["passed"] = result.passed
        validated += 1
    log.info("validate.done", validated=validated)
    return {"findings": findings}


def pr_node(state: AuditState) -> dict:
    log = get_logger(audit_id=state["audit_id"])
    if killswitch.is_active():
        log.info("pr.halted", reason="kill switch active")
        return {}

    repo = Path(state["repo_path"])
    repo_str = state["repo_path"]
    findings = state.get("findings", [])
    gh_repo = None
    opened = 0

    # Only the git-writing step needs mutual exclusion per repo -- analysis is read-only
    # and safe to run concurrently. Bounded retry: a concurrent audit's PR phase is brief.
    owner = state["audit_id"]
    got_lock = False
    for _ in range(5):
        if lock.acquire(repo_str, owner):
            got_lock = True
            break
        time.sleep(2)
    if not got_lock:
        log.info("pr.lock_contended", repo=repo_str)
        for f in findings:
            if f.get("proposed_fix") and (f.get("validation") or {}).get("passed"):
                f["pr_status"] = "lock_contended"
        return {"findings": findings}

    try:
        for f in findings:
            pf = f.get("proposed_fix")
            val = f.get("validation")

            if not pf:
                f["pr_status"] = "not_auto_fixable"
                continue
            if not val or not val.get("passed"):
                f["pr_status"] = "validation_failed"
                continue

            if killswitch.is_active():
                f["pr_status"] = "halted"
                continue

            key = dedup.finding_key(repo_str, f)
            existing_pr = dedup.already_handled(key)
            if existing_pr:
                f["pr_url"] = existing_pr
                f["pr_status"] = "duplicate_skipped"
                continue

            if gh_repo is None:
                gh_repo = get_github_repo(repo)

            is_risky = f.get("risk_tier") == "risky"
            branch_name = f"sentinel/fix-{f['analyst']}-{key[:10]}"
            file_path = f["file_path"]
            target = repo / file_path
            original = target.read_text(encoding="utf-8", errors="ignore") if target.is_file() else ""
            new_content = (
                original.replace(pf["old_snippet"], pf["new_snippet"], 1)
                if pf["old_snippet"]
                else pf["new_snippet"]
            )

            create_branch(gh_repo, branch_name)
            commit_fix(
                gh_repo,
                branch_name,
                file_path,
                new_content,
                message=f"Sentinel: fix {f['symbol']} in {file_path}",
            )

            title = f"Sentinel: {f['analyst']} fix — {f['symbol']} in {file_path}"
            body = (
                f"**Analyst:** {f['analyst']} · **Risk tier:** {f['risk_tier']}\n\n"
                f"**Issue:** {f['explanation']}\n\n"
                f"**Evidence:**\n```\n{f['evidence']}\n```\n\n"
                f"**Fix:** {pf['explanation']}\n\n"
                f"**Validation:** lint {'passed' if val['lint_passed'] else 'failed'}, "
                f"tests {'passed' if val['tests_passed'] else 'failed'}\n"
                f"```\n{val['lint_output']}\n```\n"
                + (
                    "\n⚠️ **Risky fix — always requires human review before merge.**\n"
                    if is_risky
                    else ""
                )
                + "\n_Opened automatically by Sentinel. Never auto-merged._"
            )
            labels = ["sentinel-auto-fix"] + (["needs-security-review"] if is_risky else [])

            pr_url = open_pr(gh_repo, branch_name, title, body, draft=is_risky, labels=labels)
            dedup.mark_handled(key, pr_url)
            f["pr_url"] = pr_url
            f["pr_status"] = "opened_draft" if is_risky else "opened"
            opened += 1
    finally:
        lock.release(repo_str, owner)

    log.info("pr.done", opened=opened)
    return {"findings": findings}


def build_graph(durable: bool = True, checkpointer=None):
    """checkpointer overrides `durable` when given -- used for the async WS path, which
    needs an AsyncPostgresSaver rather than the sync PostgresSaver singleton (LangGraph's
    sync checkpointer raises NotImplementedError on the async methods `astream` calls)."""
    graph = StateGraph(AuditState)

    graph.add_node("ingest", ingest_node)
    graph.add_node("scope", scope_node)
    graph.add_node("security_analyst", security_analyst_node)
    graph.add_node("quality_analyst", quality_analyst_node)
    graph.add_node("test_analyst", test_analyst_node)
    graph.add_node("diagnose", diagnose_node)
    graph.add_node("propose", propose_node)
    graph.add_node("validate", validate_node)
    graph.add_node("pr", pr_node)

    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "scope")
    graph.add_edge("scope", "security_analyst")
    graph.add_edge("scope", "quality_analyst")
    graph.add_edge("scope", "test_analyst")
    graph.add_edge("security_analyst", "diagnose")
    graph.add_edge("quality_analyst", "diagnose")
    graph.add_edge("test_analyst", "diagnose")
    graph.add_edge("diagnose", "propose")
    graph.add_edge("propose", "validate")
    graph.add_edge("validate", "pr")
    graph.add_edge("pr", END)

    if checkpointer is None and durable:
        checkpointer = get_checkpointer()
    return graph.compile(checkpointer=checkpointer)


def _budget_denied_result(audit_id: str, repo_path: str, error: str) -> dict:
    get_logger(audit_id=audit_id).error("audit.budget_denied", error=error)
    return {"audit_id": audit_id, "repo": repo_path, "findings": [], "error": error}


def run_audit(repo_path: str) -> dict:
    """Single entrypoint for a synchronous end-to-end audit run: daily-budget pre-check,
    per-audit hard cost cutoff, and thread_id wiring for the Postgres checkpointer. Used by
    both the CLI and the REST endpoint so this logic lives in exactly one place."""
    audit_id = str(uuid.uuid4())
    log = get_logger(audit_id=audit_id)

    try:
        check_daily_budget()
    except BudgetExceededError as e:
        return _budget_denied_result(audit_id, repo_path, str(e))

    graph = build_graph()
    cb = BudgetTrackingCallback()
    config = {"configurable": {"thread_id": audit_id}, "callbacks": [cb]}
    error = None
    try:
        final_state = graph.invoke({"audit_id": audit_id, "repo_path": repo_path}, config=config)
    except BudgetExceededError as e:
        error = str(e)
        log.error("audit.budget_cutoff", error=error)
        snapshot = graph.get_state(config)
        final_state = snapshot.values if snapshot else {"repo_path": repo_path}
    finally:
        record_spend(cb.total_cost)

    result = {
        "audit_id": audit_id,
        "repo": final_state.get("repo_path", repo_path),
        "findings": final_state.get("findings", []),
        "cost_usd": round(cb.total_cost, 4),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if error:
        result["error"] = error
    log.info("audit.run_complete", cost_usd=result["cost_usd"], findings=len(result["findings"]))
    return result
