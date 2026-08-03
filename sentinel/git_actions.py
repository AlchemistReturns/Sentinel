import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from github import Github, GithubException, UnknownObjectException
from pydantic import BaseModel

from sentinel.config import settings
from sentinel.logging import get_logger

GITHUB_URL_RE = re.compile(r"github\.com[:/]([^/]+)/(.+?)(?:\.git)?$")


class ValidationResult(BaseModel):
    lint_passed: bool
    tests_passed: bool
    lint_output: str
    test_output: str

    @property
    def passed(self) -> bool:
        return self.lint_passed and self.tests_passed


def _run(cmd: list[str], cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


@contextmanager
def _locally_applied(repo_path: Path, file_path: str, old_snippet: str, new_snippet: str):
    """Temporarily applies a fix to the local working copy so it can be linted/tested,
    then restores the original content on exit -- the local clone is a read-only mirror
    for analysis and should never be left mutated."""
    target = repo_path / file_path
    original = target.read_text(encoding="utf-8", errors="ignore") if target.is_file() else None
    new_content = (original or "").replace(old_snippet, new_snippet, 1) if old_snippet else new_snippet
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new_content, encoding="utf-8")
    try:
        yield new_content
    finally:
        if original is None:
            target.unlink(missing_ok=True)
        else:
            target.write_text(original, encoding="utf-8")


def _ruff_error_count(repo_path: Path, file_path: str) -> tuple[int, str]:
    result = _run(
        [sys.executable, "-m", "ruff", "check", "--output-format=concise", file_path], repo_path
    )
    output = (result.stdout + result.stderr).strip()
    if result.returncode == 0:
        return 0, output or "No lint errors."

    lines = output.splitlines()
    summary = lines[-1] if lines else ""
    if summary.startswith("Found ") and len(summary.split()) > 1 and summary.split()[1].isdigit():
        return int(summary.split()[1]), output
    # Fallback: one finding per line, minus the summary line itself.
    return max(len(lines) - 1, 0), output


def validate_fix(repo_path: Path, file_path: str, old_snippet: str, new_snippet: str) -> ValidationResult:
    # Lint is judged as a *regression* check, not an all-clean check: real repos often
    # have pre-existing lint debt unrelated to the one finding we're fixing, and it would
    # be wrong to block a valid unused-import removal because some other line in the same
    # file has an unrelated style issue.
    before_count, _ = _ruff_error_count(repo_path, file_path)

    with _locally_applied(repo_path, file_path, old_snippet, new_snippet):
        after_count, lint_output = _ruff_error_count(repo_path, file_path)
        lint_passed = after_count <= before_count

        has_tests = (repo_path / "tests").is_dir() or any(repo_path.glob("test_*.py"))
        if has_tests:
            tests = _run([sys.executable, "-m", "pytest", "-q"], repo_path, timeout=120)
            tests_passed = tests.returncode == 0
            test_output = (tests.stdout + tests.stderr)[-2000:]
        else:
            tests_passed = True
            test_output = "No test suite found in repo -- skipped."

    return ValidationResult(
        lint_passed=lint_passed,
        tests_passed=tests_passed,
        lint_output=f"{before_count} error(s) before -> {after_count} after.\n{lint_output}"[-2000:],
        test_output=test_output,
    )


def _parse_owner_repo(repo_path: Path) -> tuple[str, str]:
    result = _run(["git", "remote", "get-url", "origin"], repo_path)
    url = result.stdout.strip()
    match = GITHUB_URL_RE.search(url)
    if not match:
        raise ValueError(f"Could not parse a GitHub owner/repo from remote url: {url!r}")
    return match.group(1), match.group(2)


def get_github_repo(repo_path: Path):
    owner, name = _parse_owner_repo(repo_path)
    gh = Github(settings.github_token)
    return gh.get_repo(f"{owner}/{name}")


def create_branch(gh_repo, branch_name: str) -> None:
    log = get_logger(repo=gh_repo.full_name, branch=branch_name)
    default_branch = gh_repo.default_branch
    base_sha = gh_repo.get_branch(default_branch).commit.sha
    try:
        gh_repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=base_sha)
        log.info("git.branch_created")
    except GithubException as e:
        if e.status == 422:
            log.info("git.branch_already_exists")
        else:
            raise


def commit_fix(gh_repo, branch_name: str, file_path: str, new_content: str, message: str) -> None:
    log = get_logger(repo=gh_repo.full_name, branch=branch_name, file=file_path)
    try:
        existing = gh_repo.get_contents(file_path, ref=branch_name)
        gh_repo.update_file(existing.path, message, new_content, existing.sha, branch=branch_name)
        log.info("git.file_updated")
    except UnknownObjectException:
        gh_repo.create_file(file_path, message, new_content, branch=branch_name)
        log.info("git.file_created")


def open_pr(
    gh_repo, branch_name: str, title: str, body: str, draft: bool = False, labels: list[str] | None = None
) -> str:
    pr = gh_repo.create_pull(
        title=title, body=body, head=branch_name, base=gh_repo.default_branch, draft=draft
    )
    if labels:
        try:
            pr.add_to_labels(*labels)
        except GithubException:
            pass  # repo may not have these labels defined; non-fatal
    get_logger(repo=gh_repo.full_name).info("git.pr_opened", pr_url=pr.html_url, draft=draft)
    return pr.html_url
