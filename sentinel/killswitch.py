import redis

from sentinel.config import settings

_redis = redis.from_url(settings.redis_url, decode_responses=True)

KILL_KEY = "sentinel:kill_switch"


def is_active() -> bool:
    return _redis.get(KILL_KEY) == "1"


def activate() -> None:
    _redis.set(KILL_KEY, "1")


def deactivate() -> None:
    _redis.set(KILL_KEY, "0")
