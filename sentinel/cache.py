import hashlib
import math

import redis
from prometheus_client import Counter

from sentinel.config import settings

_redis = redis.from_url(settings.redis_url, decode_responses=True)

CONTENT_HASH_HITS = Counter(
    "sentinel_content_hash_cache_hits_total",
    "Ingest content-hash cache hits (unchanged chunk, embedding skipped)",
)
CONTENT_HASH_MISSES = Counter(
    "sentinel_content_hash_cache_misses_total",
    "Ingest content-hash cache misses (new or changed chunk, embedded)",
)
SEMANTIC_CACHE_HITS = Counter(
    "sentinel_semantic_cache_hits_total",
    "Semantic cache hits (near-duplicate finding across files/audits)",
)
SEMANTIC_CACHE_MISSES = Counter(
    "sentinel_semantic_cache_misses_total",
    "Semantic cache misses (novel finding)",
)

SEMANTIC_SIMILARITY_THRESHOLD = 0.90
SEMANTIC_CACHE_TTL_SECONDS = 60 * 60 * 24 * 7  # 1 week


def _repo_key(prefix: str, repo: str) -> str:
    digest = hashlib.sha256(repo.encode("utf-8")).hexdigest()[:16]
    return f"sentinel:{prefix}:{digest}"


def check_content_hash(repo: str, chunk_id: str, content_hash: str) -> bool:
    """Returns True (cache hit) if this chunk's content hash matches what's stored;
    otherwise records the new hash and returns False (cache miss)."""
    key = f"{_repo_key('chash', repo)}:{chunk_id}"
    cached = _redis.get(key)
    if cached == content_hash:
        CONTENT_HASH_HITS.inc()
        return True
    _redis.set(key, content_hash)
    CONTENT_HASH_MISSES.inc()
    return False


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def semantic_lookup(repo: str, signature: str, embedding: list[float]) -> bool:
    """Checks `embedding` against previously cached finding-signature embeddings for this
    repo. Returns True (cache hit) if a near-duplicate is already cached; otherwise stores
    this embedding under `signature` and returns False (cache miss).

    Small-scale brute-force cosine similarity in Python -- fine at the hundreds-of-findings
    scale a single repo audit produces; a real vector index would be overkill here.
    """
    set_key = _repo_key("semset", repo)
    members = _redis.smembers(set_key)

    for member in members:
        cached_raw = _redis.get(f"{_repo_key('sem', repo)}:{member}")
        if cached_raw is None:
            continue
        cached_embedding = [float(x) for x in cached_raw.split(",")]
        if _cosine_similarity(embedding, cached_embedding) >= SEMANTIC_SIMILARITY_THRESHOLD:
            SEMANTIC_CACHE_HITS.inc()
            return True

    entry_id = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
    _redis.set(
        f"{_repo_key('sem', repo)}:{entry_id}",
        ",".join(str(x) for x in embedding),
        ex=SEMANTIC_CACHE_TTL_SECONDS,
    )
    _redis.sadd(set_key, entry_id)
    _redis.expire(set_key, SEMANTIC_CACHE_TTL_SECONDS)
    SEMANTIC_CACHE_MISSES.inc()
    return False
