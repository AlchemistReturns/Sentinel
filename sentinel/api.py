from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sentinel.agent import run_investigation
from sentinel.logging import configure_logging

configure_logging()

app = FastAPI(title="Sentinel Orchestrator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory store, fine for Phase 1 (no live streaming, no durable state yet).
_audits: dict[str, dict] = {}
_latest_audit_id: str | None = None


class AuditRequest(BaseModel):
    repo: str = "."


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/audits")
def create_audit(req: AuditRequest):
    global _latest_audit_id
    result = run_investigation(req.repo)
    _audits[result["audit_id"]] = result
    _latest_audit_id = result["audit_id"]
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
