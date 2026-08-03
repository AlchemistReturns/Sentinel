from redis import Redis
from rq import Queue
from rq.job import Job

from sentinel.config import settings

_redis = Redis.from_url(settings.redis_url)

AUDIT_QUEUE_NAME = "sentinel-audits"
audit_queue = Queue(AUDIT_QUEUE_NAME, connection=_redis)


def enqueue_audit(repo_path: str) -> str:
    job = audit_queue.enqueue("sentinel.graph.run_audit", repo_path, job_timeout=600)
    return job.id


def get_job_status(job_id: str) -> dict:
    job = Job.fetch(job_id, connection=_redis)
    return {
        "job_id": job.id,
        "status": job.get_status(),
        "result": job.return_value(),
        "enqueued_at": job.enqueued_at.isoformat() if job.enqueued_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "ended_at": job.ended_at.isoformat() if job.ended_at else None,
    }
