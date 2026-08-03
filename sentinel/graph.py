from pathlib import Path

from langgraph.graph import END, START, StateGraph

from sentinel.analysts import quality_analyst_node, security_analyst_node, test_analyst_node
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


def diagnose_node(state: AuditState) -> dict:
    log = get_logger(audit_id=state["audit_id"])
    merged: list[dict] = []
    for f in state.get("security_findings", []):
        merged.append({**f, "analyst": "security", "risk_tier": "risky"})
    for f in state.get("quality_findings", []):
        merged.append({**f, "analyst": "quality", "risk_tier": "mechanical"})
    for f in state.get("test_findings", []):
        merged.append({**f, "analyst": "test", "risk_tier": "mechanical"})
    log.info("diagnose.done", total=len(merged))
    return {"findings": merged}


def propose_node(state: AuditState) -> dict:
    get_logger(audit_id=state["audit_id"]).info("propose.skipped", reason="Phase 4 scope")
    return {}


def validate_node(state: AuditState) -> dict:
    get_logger(audit_id=state["audit_id"]).info("validate.skipped", reason="Phase 4 scope")
    return {}


def pr_node(state: AuditState) -> dict:
    get_logger(audit_id=state["audit_id"]).info("pr.skipped", reason="Phase 4 scope")
    return {}


def build_graph():
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

    return graph.compile()
