import json
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel
from starlette.responses import Response

from sentinel import killswitch
from sentinel.cost import BudgetExceededError, BudgetTrackingCallback, check_daily_budget, record_spend
from sentinel.graph import build_graph, run_audit
from sentinel.logging import configure_logging, get_logger
from sentinel.queue import enqueue_audit, get_job_status
from sentinel.repos import InvalidRepoError, connect_repo

configure_logging()

app = FastAPI(title="Sentinel Orchestrator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory store: audit *results* are still process-local (full findings history is
# durable via the Postgres checkpointer per-thread, but there's no cross-audit index over
# it yet -- a real "list all audits ever run" view is Phase 6+ scope). Fine for a single
# long-lived orchestrator process; lost on restart.
_audits: dict[str, dict] = {}
_latest_audit_id: str | None = None


class AuditRequest(BaseModel):
    repo: str = "."


class ConnectRepoRequest(BaseModel):
    url: str
    branch: str | None = None


def _jsonable(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/kill-switch")
def get_kill_switch():
    return {"active": killswitch.is_active()}


@app.post("/api/kill-switch/activate")
def activate_kill_switch():
    killswitch.activate()
    get_logger().info("kill_switch.activated")
    return {"active": True}


@app.post("/api/kill-switch/deactivate")
def deactivate_kill_switch():
    killswitch.deactivate()
    get_logger().info("kill_switch.deactivated")
    return {"active": False}


@app.post("/api/repos/connect")
def connect_repo_endpoint(req: ConnectRepoRequest):
    """Clones (or updates) a GitHub repo by URL and returns the local path to feed into
    /api/audits or /ws/audits -- the onboarding step that means a stranger never has to
    pre-clone anything or hand-edit config."""
    try:
        return connect_repo(req.url, req.branch)
    except InvalidRepoError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/audits")
def create_audit(req: AuditRequest):
    # Sync on purpose: FastAPI runs `def` (not `async def`) path functions in a
    # threadpool, so this doesn't block the event loop despite being a long-running call.
    global _latest_audit_id
    result = run_audit(req.repo)
    _audits[result["audit_id"]] = result
    _latest_audit_id = result["audit_id"]
    return result


@app.post("/api/audits/enqueue")
def enqueue_audit_job(req: AuditRequest):
    """Enqueue an audit onto the Redis-backed job queue instead of running it inline --
    for concurrent audits across worker processes (`rq worker` / `SimpleWorker`)."""
    job_id = enqueue_audit(req.repo)
    return {"job_id": job_id}


@app.get("/api/audits/jobs/{job_id}")
def get_audit_job(job_id: str):
    try:
        return get_job_status(job_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Job not found: {e}")


@app.get("/api/audits")
def list_audits():
    """Audit history for this orchestrator process, oldest first -- powers the dashboard's
    trend chart. See the _audits docstring for the durability caveat."""
    return list(_audits.values())


@app.get("/api/audits/latest")
def get_latest_audit():
    if _latest_audit_id is None:
        raise HTTPException(status_code=404, detail="No audits have been run yet")
    return _audits[_latest_audit_id]


@app.get("/api/audits/{audit_id}")
def get_audit(audit_id: str):
    if audit_id not in _audits:
        raise HTTPException(status_code=404, detail="Audit not found")
    return _audits[audit_id]


@app.websocket("/ws/audits")
async def ws_audit(websocket: WebSocket):
    global _latest_audit_id
    await websocket.accept()
    repo = websocket.query_params.get("repo", ".")
    audit_id = str(uuid.uuid4())
    log = get_logger(audit_id=audit_id)

    try:
        check_daily_budget()
    except BudgetExceededError as e:
        await websocket.send_text(
            json.dumps({"event": "error", "message": str(e)}, default=str)
        )
        await websocket.close()
        return

    cb = BudgetTrackingCallback()
    config = {"configurable": {"thread_id": audit_id}, "callbacks": [cb]}
    final_state: dict = {"audit_id": audit_id, "repo_path": repo}
    error: str | None = None

    # No checkpointer here (durable=False): astream() needs an *async* checkpointer, and
    # psycopg's async mode can't run on Windows' default ProactorEventLoop -- switching
    # loop policies mid-process didn't take effect reliably under uvicorn on Windows.
    # Durable state matters for the queue/worker path (run_audit, sentinel/durable.py),
    # where a crashed worker needs to resume; a dropped WebSocket has no "resume" concept
    # anyway -- final_state is already built incrementally below from each streamed update,
    # which is all the recovery a live view needs on a mid-run budget cutoff.
    graph = build_graph(durable=False)

    try:
        async for namespace, update in graph.astream(
            {"audit_id": audit_id, "repo_path": repo},
            config=config,
            stream_mode="updates",
            subgraphs=True,
        ):
            if namespace == ():
                for node_update in update.values():
                    if isinstance(node_update, dict):
                        final_state.update(node_update)
            await websocket.send_text(
                json.dumps(
                    {
                        "event": "update",
                        "namespace": list(namespace),
                        "update": _jsonable(update),
                    },
                    default=str,
                )
            )
    except BudgetExceededError as e:
        error = str(e)
        log.error("ws.budget_cutoff", error=error)
    except WebSocketDisconnect:
        log.info("ws.disconnected")
        record_spend(cb.total_cost)
        return
    finally:
        record_spend(cb.total_cost)

    result = {
        "audit_id": audit_id,
        "repo": final_state.get("repo_path", repo),
        "findings": final_state.get("findings", []),
        "cost_usd": round(cb.total_cost, 4),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if error:
        result["error"] = error
    _audits[audit_id] = result
    _latest_audit_id = audit_id

    await websocket.send_text(json.dumps({"event": "done", **result}, default=str))
    try:
        await websocket.close()
    except RuntimeError:
        pass
