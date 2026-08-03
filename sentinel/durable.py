import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row

from sentinel.config import settings

_checkpointer: PostgresSaver | None = None


def get_checkpointer() -> PostgresSaver:
    """A process-wide Postgres-backed checkpointer. Durable audit state means a dying
    worker's job can be resumed by another (via the same thread_id = audit_id) instead of
    restarting from scratch -- and it doubles as recovery for a per-audit budget cutoff
    (sentinel/cost.py): whatever nodes committed before the cutoff are still there."""
    global _checkpointer
    if _checkpointer is None:
        conn = psycopg.Connection.connect(
            settings.database_url, autocommit=True, prepare_threshold=0, row_factory=dict_row
        )
        _checkpointer = PostgresSaver(conn)
        _checkpointer.setup()
    return _checkpointer
