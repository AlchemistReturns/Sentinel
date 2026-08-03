import hashlib
import re
import subprocess
from pathlib import Path

from sentinel.logging import get_logger

# Only plain https://github.com/<owner>/<repo> URLs are accepted. git's own URL syntax
# supports far more than that -- ssh://, and critically the "ext::" transport, which runs
# an arbitrary shell command (`git clone "ext::sh -c 'evil'"` is a real RCE). Frontend user
# input is an untrusted boundary the same way LLM output is; validate before ever handing
# a string to subprocess.
GITHUB_HTTPS_URL_RE = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")

# Sibling of the Sentinel project directory, not inside it -- otherwise a self-audit's
# file walk (sentinel/ingest.py) would wander into every cloned target repo too.
WORKDIR = Path(__file__).resolve().parent.parent.parent / ".sentinel_repos"


class InvalidRepoError(Exception):
    pass


def _slug(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def connect_repo(url: str, branch: str | None = None) -> dict:
    """Clones (or updates an existing clone of) a GitHub repo by URL, returning the local
    path Sentinel's other tools operate on. This is the boundary where a stranger's
    pasted URL first touches a shell command -- validated strictly before that happens."""
    match = GITHUB_HTTPS_URL_RE.match(url.strip())
    if not match:
        raise InvalidRepoError("Only https://github.com/<owner>/<repo> URLs are supported.")
    if branch and (not SAFE_BRANCH_RE.match(branch) or branch.startswith("-")):
        raise InvalidRepoError("Invalid branch name.")

    owner, name = match.group(1), match.group(2)
    clean_url = f"https://github.com/{owner}/{name}.git"
    log = get_logger(repo=clean_url, branch=branch)

    WORKDIR.mkdir(exist_ok=True)
    target = WORKDIR / _slug(clean_url)

    if (target / ".git").is_dir():
        log.info("repos.updating_existing_clone", path=str(target))
        _run(["git", "fetch", "origin"], cwd=target)
        checkout_ref = branch or _run(
            ["git", "remote", "show", "origin"], cwd=target
        ).stdout
        if branch:
            result = _run(["git", "checkout", branch], cwd=target)
            if result.returncode != 0:
                raise InvalidRepoError(f"Could not check out branch {branch!r}: {result.stderr[:300]}")
            _run(["git", "pull", "origin", branch], cwd=target)
        else:
            _run(["git", "pull"], cwd=target)
    else:
        log.info("repos.cloning", path=str(target))
        cmd = ["git", "clone", clean_url, str(target)]
        if branch:
            cmd = ["git", "clone", "-b", branch, clean_url, str(target)]
        result = _run(cmd, timeout=180)
        if result.returncode != 0:
            raise InvalidRepoError(f"Clone failed: {result.stderr[:300]}")

    current_branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=target).stdout.strip()
    log.info("repos.ready", path=str(target), branch=current_branch)
    return {"local_path": str(target), "branch": current_branch, "url": clean_url}
