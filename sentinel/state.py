from typing import TypedDict


class AuditState(TypedDict, total=False):
    audit_id: str
    repo_path: str
    python_files: list[str]
    security_findings: list[dict]
    quality_findings: list[dict]
    test_findings: list[dict]
    findings: list[dict]
