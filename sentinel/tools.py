import re
import subprocess
from pathlib import Path

from langchain_core.tools import tool

from sentinel.ingest import iter_source_files

SECRET_PATTERNS = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    (
        "generic_api_key",
        re.compile(r"(?i)(api[_-]?key|apikey)\s*[:=]\s*['\"][A-Za-z0-9/+_\-]{16,}['\"]"),
    ),
    (
        "generic_secret",
        re.compile(r"(?i)(secret|password|passwd|token)\s*[:=]\s*['\"][^'\"\s]{6,}['\"]"),
    ),
]

SAFE_GIT_REF = re.compile(r"^[A-Za-z0-9_./~^\-]+$")


def _within_repo(repo_root: Path, file_path: str) -> Path | None:
    target = (repo_root / file_path).resolve()
    if repo_root not in target.parents and target != repo_root:
        return None
    return target


def make_tools(repo_path: Path):
    """Base read-only tools: listing and reading source files. Shared by every analyst."""
    repo_root = repo_path.resolve()

    @tool
    def list_python_files() -> list[str]:
        """List all Python source files in the repository, as paths relative to the repo root."""
        return [
            str(p.relative_to(repo_root))
            for p in iter_source_files(repo_root)
            if p.suffix == ".py"
        ]

    @tool
    def read_source_file(file_path: str) -> str:
        """Read the full contents of a source file, given its path relative to the repo root."""
        target = _within_repo(repo_root, file_path)
        if target is None:
            return "Error: path escapes repository root, refusing to read."
        if not target.is_file():
            return f"Error: {file_path} is not a file."
        return target.read_text(encoding="utf-8", errors="ignore")

    return [list_python_files, read_source_file]


def make_git_diff_tool(repo_path: Path):
    repo_root = repo_path.resolve()

    @tool
    def git_diff(commit_range: str = "HEAD~5..HEAD") -> str:
        """Show the diff for a git commit range (default: last 5 commits), to correlate
        findings with recent changes. commit_range must be a plain git ref expression
        (branch names, HEAD, ~, ^, ., / and - only -- no shell metacharacters)."""
        if not SAFE_GIT_REF.match(commit_range):
            return "Error: invalid commit range."
        try:
            result = subprocess.run(
                ["git", "diff", commit_range],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (subprocess.SubprocessError, OSError) as e:
            return f"Error running git diff: {e}"
        if result.returncode != 0:
            return f"Error: {result.stderr[:500]}"
        return result.stdout[:8000] or "No changes in this range."

    return git_diff


def make_secret_scan_tool(repo_path: Path):
    repo_root = repo_path.resolve()

    @tool
    def scan_hardcoded_secrets() -> list[dict]:
        """Regex-scan all Python files for patterns that look like hardcoded credentials
        (AWS keys, API keys, passwords, tokens). Returns raw matches for you to interpret
        and prioritize -- a match is a lead, not a confirmed vulnerability. (Placeholder for
        real SAST tooling: semgrep/pip-audit are wired in during Phase 3.)"""
        matches = []
        for path in iter_source_files(repo_root):
            if path.suffix != ".py":
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, start=1):
                for kind, pattern in SECRET_PATTERNS:
                    if pattern.search(line):
                        matches.append(
                            {
                                "file_path": str(path.relative_to(repo_root)),
                                "line": i,
                                "kind": kind,
                                "evidence": line.strip()[:200],
                            }
                        )
        return matches[:200]

    return scan_hardcoded_secrets


def make_test_files_tool(repo_path: Path):
    repo_root = repo_path.resolve()

    @tool
    def list_test_files() -> list[str]:
        """List Python test files in the repository (test_*.py, *_test.py, or under a
        tests/ directory)."""
        results = []
        for p in iter_source_files(repo_root):
            if p.suffix != ".py":
                continue
            rel = p.relative_to(repo_root)
            if p.name.startswith("test_") or p.name.endswith("_test.py") or "tests" in rel.parts:
                results.append(str(rel))
        return results

    return list_test_files
