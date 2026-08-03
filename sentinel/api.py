import json
import uuid

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sentinel.graph import build_graph
from sentinel.logging import configure_logging, get_logger

configure_logging()

app = FastAPI(title="Sentinel Orchestrator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory store, fine through Phase 2 (no durable state yet -- that's Phase 5).
_audits: dict[str, dict] = {}
_latest_audit_id: str | None = None


class AuditRequest(BaseModel):
    repo: str = "."


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


@app.post("/api/audits")
async def create_audit(req: AuditRequest):
    global _latest_audit_id
    audit_id = str(uuid.uuid4())
    graph = build_graph()
    final_state = await graph.ainvoke({"audit_id": audit_id, "repo_path": req.repo})
    result = {
        "audit_id": audit_id,
        "repo": final_state["repo_path"],
        "findings": final_state.get("findings", []),
    }
    _audits[audit_id] = result
    _latest_audit_id = audit_id
    return result


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
    graph = build_graph()
    final_state: dict = {"audit_id": audit_id, "repo_path": repo}

    try:
        async for namespace, update in graph.astream(
            {"audit_id": audit_id, "repo_path": repo},
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

        result = {
            "audit_id": audit_id,
            "repo": final_state["repo_path"],
            "findings": final_state.get("findings", []),
        }
        _audits[audit_id] = result
        _latest_audit_id = audit_id

        await websocket.send_text(json.dumps({"event": "done", **result}, default=str))
    except WebSocketDisconnect:
        log.info("ws.disconnected")
        return

    try:
        await websocket.close()
    except RuntimeError:
        pass
