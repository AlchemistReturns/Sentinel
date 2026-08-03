import hashlib

import redis

from sentinel.config import settings

_redis = redis.from_url(settings.redis_url, decode_responses=True)

DEDUP_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days


def finding_key(repo: str, finding: dict) -> str:
    # Deliberately excludes free-text fields (evidence/explanation): the LLM doesn't
    # reproduce identical wording run to run, which would defeat dedup. analyst+file+symbol
    # is the stable identity of "this issue, on this element, per this analyst".
    raw = f"{repo}:{finding['analyst']}:{finding['file_path']}:{finding['symbol']}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _redis_key(key: str) -> str:
    return f"sentinel:pr_dedup:{key}"


def already_handled(key: str) -> str | None:
    """Returns the PR URL already opened for this finding, or None if it hasn't been
    handled before."""
    return _redis.get(_redis_key(key))


def mark_handled(key: str, pr_url: str) -> None:
    _redis.set(_redis_key(key), pr_url, ex=DEDUP_TTL_SECONDS)
