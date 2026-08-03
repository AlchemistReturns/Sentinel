import ast
import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector

from sentinel.cache import check_content_hash
from sentinel.config import settings
from sentinel.logging import get_logger

IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".next",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}

CHUNK_LINES = 60
CHUNK_OVERLAP = 8


@dataclass
class Chunk:
    file_path: str
    symbol: str
    content: str
    content_hash: str
    chunk_id: str


def _make_chunk(rel_path: str, symbol: str, content: str) -> Chunk:
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    chunk_id = hashlib.sha256(f"{rel_path}::{symbol}".encode("utf-8")).hexdigest()[:24]
    return Chunk(
        file_path=rel_path, symbol=symbol, content=content, content_hash=content_hash,
        chunk_id=chunk_id,
    )


def _is_text_file(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            sample = f.read(4096)
        if b"\x00" in sample:
            return False
        sample.decode("utf-8")
        return True
    except (UnicodeDecodeError, OSError):
        return False


def iter_source_files(repo_path: Path):
    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        if not _is_text_file(path):
            continue
        yield path


def _symbol_chunks(source: str, rel_path: str) -> list[Chunk] | None:
    """Chunk a Python file by top-level function/class definitions. Returns None if the
    file isn't parseable Python (caller falls back to line-window chunking)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None

    lines = source.splitlines()
    top_level = [
        n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]

    chunks: list[Chunk] = []
    first_def_line = top_level[0].lineno if top_level else len(lines) + 1
    preamble = "\n".join(lines[: first_def_line - 1]).strip()
    if preamble:
        chunks.append(_make_chunk(rel_path, "<module>", preamble))

    for node in top_level:
        segment = "\n".join(lines[node.lineno - 1 : node.end_lineno])
        if segment.strip():
            chunks.append(_make_chunk(rel_path, node.name, segment))

    return chunks or None


def _line_window_chunks(source: str, rel_path: str) -> list[Chunk]:
    lines = source.splitlines()
    if not lines:
        return []

    chunks = []
    step = CHUNK_LINES - CHUNK_OVERLAP
    idx = 0
    for start in range(0, len(lines), step):
        window = lines[start : start + CHUNK_LINES]
        content = "\n".join(window)
        if content.strip():
            chunks.append(_make_chunk(rel_path, f"L{start + 1}-{start + len(window)}", content))
            idx += 1
        if start + CHUNK_LINES >= len(lines):
            break
    return chunks


def chunk_file(path: Path, repo_path: Path) -> list[Chunk]:
    source = path.read_text(encoding="utf-8", errors="ignore")
    if not source.strip():
        return []

    rel_path = str(path.relative_to(repo_path))
    if path.suffix == ".py":
        chunks = _symbol_chunks(source, rel_path)
        if chunks is not None:
            return chunks
    return _line_window_chunks(source, rel_path)


def collection_name_for(repo_path: Path) -> str:
    digest = hashlib.sha256(str(repo_path.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"sentinel_{repo_path.name}_{digest}"


def ingest_repo(repo_path: str) -> dict:
    audit_id = str(uuid.uuid4())
    log = get_logger(audit_id=audit_id)

    repo = Path(repo_path).resolve()
    log.info("ingest.start", repo=str(repo))

    all_chunks: list[Chunk] = []
    files_seen = 0
    for file_path in iter_source_files(repo):
        files_seen += 1
        all_chunks.extend(chunk_file(file_path, repo))

    log.info("ingest.chunked", files=files_seen, chunks=len(all_chunks))

    if not all_chunks:
        log.info("ingest.done", files=files_seen, chunks=0)
        return {"audit_id": audit_id, "files": files_seen, "chunks": 0}

    changed = [c for c in all_chunks if not check_content_hash(str(repo), c.chunk_id, c.content_hash)]
    cache_hits = len(all_chunks) - len(changed)
    log.info("ingest.content_hash_cache", hits=cache_hits, misses=len(changed))

    documents = [
        Document(
            page_content=c.content,
            metadata={
                "file_path": c.file_path,
                "symbol": c.symbol,
                "content_hash": c.content_hash,
                "repo_path": str(repo),
                "audit_id": audit_id,
            },
        )
        for c in changed
    ]
    ids = [c.chunk_id for c in changed]

    embeddings = OpenAIEmbeddings(model=settings.embedding_model, api_key=settings.openai_api_key)
    collection_name = collection_name_for(repo)

    vectorstore = PGVector(
        embeddings=embeddings,
        collection_name=collection_name,
        connection=settings.pgvector_url,
        use_jsonb=True,
    )
    if documents:
        vectorstore.add_documents(documents, ids=ids)

    log.info(
        "ingest.done",
        files=files_seen,
        chunks=len(all_chunks),
        embedded=len(documents),
        cache_hits=cache_hits,
        collection=collection_name,
    )
    return {
        "audit_id": audit_id,
        "files": files_seen,
        "chunks": len(all_chunks),
        "embedded": len(documents),
        "cache_hits": cache_hits,
        "collection": collection_name,
    }


def query_repo(repo_path: str, text: str, k: int = 5) -> list[Document]:
    repo = Path(repo_path).resolve()
    collection_name = collection_name_for(repo)
    embeddings = OpenAIEmbeddings(model=settings.embedding_model, api_key=settings.openai_api_key)
    vectorstore = PGVector(
        embeddings=embeddings,
        collection_name=collection_name,
        connection=settings.pgvector_url,
        use_jsonb=True,
    )
    return vectorstore.similarity_search(text, k=k)
