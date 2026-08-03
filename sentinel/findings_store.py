import hashlib
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector

from sentinel.config import settings
from sentinel.logging import get_logger


def _findings_collection_name(repo_path: Path) -> str:
    digest = hashlib.sha256(str(repo_path.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"sentinel_findings_{repo_path.name}_{digest}"


def _finding_text(f: dict) -> str:
    return f"{f.get('analyst', '')} finding in {f['file_path']} :: {f['symbol']} -- {f['explanation']}"


def index_findings(repo_path: str, audit_id: str, findings: list[dict]) -> int:
    """Persist this audit's findings into a per-repo pgvector collection so future audits
    can be compared against history. Returns the number of findings indexed."""
    if not findings:
        return 0

    repo = Path(repo_path).resolve()
    log = get_logger(audit_id=audit_id)

    embeddings = OpenAIEmbeddings(model=settings.embedding_model, api_key=settings.openai_api_key)
    vectorstore = PGVector(
        embeddings=embeddings,
        collection_name=_findings_collection_name(repo),
        connection=settings.pgvector_url,
        use_jsonb=True,
    )

    now = datetime.now(timezone.utc).isoformat()
    documents = [
        Document(
            page_content=_finding_text(f),
            metadata={
                "audit_id": audit_id,
                "repo_path": str(repo),
                "analyst": f.get("analyst"),
                "risk_tier": f.get("risk_tier"),
                "file_path": f["file_path"],
                "symbol": f["symbol"],
                "indexed_at": now,
            },
        )
        for f in findings
    ]
    vectorstore.add_documents(documents)
    log.info("findings.indexed", count=len(documents))
    return len(documents)


def query_findings_history(repo_path: str, text: str, k: int = 5) -> list[Document]:
    repo = Path(repo_path).resolve()
    embeddings = OpenAIEmbeddings(model=settings.embedding_model, api_key=settings.openai_api_key)
    vectorstore = PGVector(
        embeddings=embeddings,
        collection_name=_findings_collection_name(repo),
        connection=settings.pgvector_url,
        use_jsonb=True,
    )
    return vectorstore.similarity_search(text, k=k)
