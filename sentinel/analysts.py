from pathlib import Path

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langgraph.errors import GraphRecursionError

from sentinel.agent import CONVERGENCE_RULE, MAX_ANALYST_STEPS
from sentinel.agent import SYSTEM_PROMPT as QUALITY_SYSTEM_PROMPT
from sentinel.config import settings
from sentinel.findings import FindingsReport
from sentinel.logging import get_logger
from sentinel.state import AuditState
from sentinel.tools import (
    make_git_diff_tool,
    make_secret_scan_tool,
    make_test_files_tool,
    make_tools,
)

SECURITY_SYSTEM_PROMPT = (
    """You are Sentinel's Security Analyst, part of a multi-agent code review team.

Your task for this run: investigate this repository for hardcoded credentials and secrets.

Process:
1. Call scan_hardcoded_secrets to get a list of regex-based leads (raw matches, not confirmed
   vulnerabilities -- your job is to interpret and prioritize them, not to freehand-detect
   vulnerabilities from scratch).
2. If scan_hardcoded_secrets returns zero leads, submit your final report immediately with
   an empty findings list. Do not call read_source_file or any other tool speculatively --
   there is nothing to investigate without a lead.
3. For each lead, call read_source_file (once per file) to see it in context and judge
   whether it is a real hardcoded secret, a false positive (e.g. a test fixture, an example
   placeholder, an env var name that merely mentions "secret"), or something else entirely.
4. Optionally call git_diff once to see if a leaked credential was introduced recently.
5. Report only leads you believe are genuine hardcoded credentials, with your reasoning.

Be conservative: false positives are worse than a missed edge case. If nothing looks like a
real secret, report nothing.
"""
    + CONVERGENCE_RULE
)

TEST_SYSTEM_PROMPT = (
    """You are Sentinel's Test Analyst, part of a multi-agent code review team.

Your task for this run: find Python functions and classes that have no corresponding test.

Process:
1. Call list_python_files and call list_test_files to see what source and test files exist.
2. Call read_source_file on source files (skip test files themselves) to see their public
   functions/classes.
3. Call read_source_file on test files to check whether each function/class is referenced by
   name anywhere in the tests.
4. Report source functions/classes that are never referenced by any test file as findings
   (use the function or class name as the "symbol").

Be conservative: only report clearly untested, non-trivial functions (skip trivial
one-line getters, __init__ boilerplate, and private helpers prefixed with a single
underscore unless they contain real logic). If the repository has no tests directory at
all, report that as a single finding on the repo root instead of one per function.
"""
    + CONVERGENCE_RULE
)


def _run_analyst(
    *,
    audit_id: str,
    repo: Path,
    tools: list,
    system_prompt: str,
    user_message: str,
    run_name: str,
) -> list[dict]:
    log = get_logger(audit_id=audit_id, analyst=run_name)
    agent = create_agent(
        model=settings.agent_model,
        tools=tools,
        system_prompt=system_prompt,
        response_format=ToolStrategy(FindingsReport, handle_errors=True),
    )
    log.info("analyst.start")
    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": user_message}]},
            config={
                "run_name": run_name,
                "tags": ["sentinel", run_name],
                "metadata": {"audit_id": audit_id, "repo": str(repo)},
                "recursion_limit": MAX_ANALYST_STEPS,
            },
        )
    except GraphRecursionError:
        log.error("analyst.recursion_limit_hit", limit=MAX_ANALYST_STEPS)
        return []
    report: FindingsReport = result["structured_response"]
    log.info("analyst.done", findings=len(report.findings))
    return [f.model_dump() for f in report.findings]


def security_analyst_node(state: AuditState) -> dict:
    repo = Path(state["repo_path"])
    tools = [*make_tools(repo), make_secret_scan_tool(repo), make_git_diff_tool(repo)]
    findings = _run_analyst(
        audit_id=state["audit_id"],
        repo=repo,
        tools=tools,
        system_prompt=SECURITY_SYSTEM_PROMPT,
        user_message="Investigate this repository for hardcoded credentials and secrets.",
        run_name="security-analyst",
    )
    return {"security_findings": findings}


def quality_analyst_node(state: AuditState) -> dict:
    repo = Path(state["repo_path"])
    tools = [*make_tools(repo), make_git_diff_tool(repo)]
    findings = _run_analyst(
        audit_id=state["audit_id"],
        repo=repo,
        tools=tools,
        system_prompt=QUALITY_SYSTEM_PROMPT,
        user_message="Audit this repository for unused imports.",
        run_name="quality-analyst",
    )
    return {"quality_findings": findings}


def test_analyst_node(state: AuditState) -> dict:
    repo = Path(state["repo_path"])
    tools = [*make_tools(repo), make_test_files_tool(repo)]
    findings = _run_analyst(
        audit_id=state["audit_id"],
        repo=repo,
        tools=tools,
        system_prompt=TEST_SYSTEM_PROMPT,
        user_message="Find untested functions and classes in this repository.",
        run_name="test-analyst",
    )
    return {"test_findings": findings}
