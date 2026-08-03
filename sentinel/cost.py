from datetime import date, timedelta

import redis
from langchain_community.callbacks import OpenAICallbackHandler

from sentinel.config import settings

_redis = redis.from_url(settings.redis_url, decode_responses=True)

PER_AUDIT_CAP_USD = 0.50
DAILY_CAP_USD = 5.00


class BudgetExceededError(Exception):
    pass


class BudgetTrackingCallback(OpenAICallbackHandler):
    """Raises once this audit's running OpenAI cost crosses the per-audit cap -- a real
    hard cutoff, not just a post-hoc log line. LangGraph checkpointing (see graph.py) means
    an audit cut off mid-run still leaves a resumable checkpoint with whatever findings
    were already committed."""

    def __init__(self, cap_usd: float = PER_AUDIT_CAP_USD):
        super().__init__()
        self.cap_usd = cap_usd

    def on_llm_end(self, response, **kwargs):
        super().on_llm_end(response, **kwargs)
        if self.total_cost > self.cap_usd:
            raise BudgetExceededError(
                f"Per-audit budget ${self.cap_usd:.2f} exceeded (spent ${self.total_cost:.4f})"
            )


def _daily_key() -> str:
    return f"sentinel:daily_spend:{date.today().isoformat()}"


def daily_spend() -> float:
    val = _redis.get(_daily_key())
    return float(val) if val else 0.0


def record_spend(amount: float) -> None:
    if amount <= 0:
        return
    key = _daily_key()
    _redis.incrbyfloat(key, amount)
    _redis.expire(key, int(timedelta(days=2).total_seconds()))


def check_daily_budget() -> None:
    spent = daily_spend()
    if spent >= DAILY_CAP_USD:
        raise BudgetExceededError(f"Daily budget ${DAILY_CAP_USD:.2f} exceeded (spent ${spent:.4f})")
