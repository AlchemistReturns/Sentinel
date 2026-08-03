import uuid
from pathlib import Path

from langchain.agents import create_agent

from sentinel.config import settings
from sentinel.findings import FindingsReport
from sentinel.logging import get_logger
from sentinel.tools import make_tools

SYSTEM_PROMPT = """You are Sentinel's Quality Analyst, a meticulous senior code reviewer.

Your task for this run: find unused imports in the Python files of this repository.

Process:
1. Call list_python_files to see what's in the repo.
2. Call read_source_file on each file to inspect its imports and check whether each
   imported name is actually referenced later in the file.
3. Only report an import as unused if you are confident it is never referenced anywhere
   in the file (not in code, not in __all__, not in decorators, not in string type
   annotations).
4. When you have reviewed every Python file, report your findings.

Be conservative: false positives are worse than a missed edge case. If a file has no
unused imports, do not report anything for it.
"""


def run_investigation(repo_path: str, model: str | None = None) -> dict:
    audit_id = str(uuid.uuid4())
    log = get_logger(audit_id=audit_id)
    repo = Path(repo_path).resolve()

    tools = make_tools(repo)
    agent = create_agent(
        model=model or settings.agent_model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        response_format=FindingsReport,
    )

    log.info("investigate.start", repo=str(repo))
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "Audit this repository for unused imports."}]},
        config={
            "run_name": "sentinel-quality-analyst",
            "tags": ["sentinel", "quality-analyst"],
            "metadata": {"audit_id": audit_id, "repo": str(repo)},
        },
    )
    report: FindingsReport = result["structured_response"]
    log.info("investigate.done", findings=len(report.findings))

    return {
        "audit_id": audit_id,
        "repo": str(repo),
        "findings": [f.model_dump() for f in report.findings],
    }
