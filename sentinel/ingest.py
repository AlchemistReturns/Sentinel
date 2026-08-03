import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector

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
    chunk_index: int
    content: str
    content_hash: str


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


def chunk_file(path: Path, repo_path: Path) -> list[Chunk]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    if not lines:
        return []

    rel_path = str(path.relative_to(repo_path))
    chunks = []
    step = CHUNK_LINES - CHUNK_OVERLAP
    idx = 0
    for start in range(0, len(lines), step):
        window = lines[start : start + CHUNK_LINES]
        content = "\n".join(window)
        if not content.strip():
            continue
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        chunks.append(
            Chunk(file_path=rel_path, chunk_index=idx, content=content, content_hash=content_hash)
        )
        idx += 1
        if start + CHUNK_LINES >= len(lines):
            break
    return chunks


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
        file_chunks = chunk_file(file_path, repo)
        all_chunks.extend(file_chunks)

    log.info("ingest.chunked", files=files_seen, chunks=len(all_chunks))

    if not all_chunks:
        log.info("ingest.done", files=files_seen, chunks=0)
        return {"audit_id": audit_id, "files": files_seen, "chunks": 0}

    documents = [
        Document(
            page_content=c.content,
            metadata={
                "file_path": c.file_path,
                "chunk_index": c.chunk_index,
                "content_hash": c.content_hash,
                "repo_path": str(repo),
                "audit_id": audit_id,
            },
        )
        for c in all_chunks
    ]

    embeddings = OpenAIEmbeddings(model=settings.embedding_model, api_key=settings.openai_api_key)
    collection_name = collection_name_for(repo)

    vectorstore = PGVector(
        embeddings=embeddings,
        collection_name=collection_name,
        connection=settings.pgvector_url,
        use_jsonb=True,
        pre_delete_collection=True,
    )
    vectorstore.add_documents(documents)

    log.info(
        "ingest.done",
        files=files_seen,
        chunks=len(all_chunks),
        collection=collection_name,
    )
    return {
        "audit_id": audit_id,
        "files": files_seen,
        "chunks": len(all_chunks),
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
