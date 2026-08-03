import hashlib

import redis

from sentinel.config import settings

_redis = redis.from_url(settings.redis_url, decode_responses=True)

LOCK_TTL_SECONDS = 600  # self-healing: an owner that crashes mid-PR-generation doesn't
# wedge the repo forever


def _key(repo: str) -> str:
    digest = hashlib.sha256(repo.encode("utf-8")).hexdigest()[:16]
    return f"sentinel:repo_lock:{digest}"


def acquire(repo: str, owner: str) -> bool:
    return bool(_redis.set(_key(repo), owner, nx=True, ex=LOCK_TTL_SECONDS))


def release(repo: str, owner: str) -> None:
    key = _key(repo)
    if _redis.get(key) == owner:
        _redis.delete(key)
