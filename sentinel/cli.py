import json

import typer

from sentinel.agent import run_investigation
from sentinel.graph import run_audit
from sentinel.ingest import ingest_repo, query_repo
from sentinel.logging import configure_logging

app = typer.Typer(help="Sentinel — autonomous codebase auditor")


@app.command()
def ingest(repo: str = typer.Option(..., "--repo", help="Path to the target repository")):
    configure_logging()
    result = ingest_repo(repo)
    typer.echo(
        f"{result['chunks']} chunks from {result['files']} files: "
        f"{result['embedded']} embedded, {result['cache_hits']} cache hits "
        f"(audit_id={result['audit_id']})"
    )


@app.command()
def query(
    repo: str = typer.Option(..., "--repo", help="Path to the target repository"),
    text: str = typer.Option(..., "--text", help="Query text"),
    k: int = typer.Option(5, "--k", help="Number of results"),
):
    configure_logging()
    results = query_repo(repo, text, k=k)
    for doc in results:
        typer.echo(f"--- {doc.metadata['file_path']} :: {doc.metadata['symbol']} ---")
        typer.echo(doc.page_content[:300])
        typer.echo("")


@app.command()
def investigate(repo: str = typer.Option(..., "--repo", help="Path to the target repository")):
    configure_logging()
    result = run_investigation(repo)
    typer.echo(json.dumps(result, indent=2))


@app.command()
def audit(repo: str = typer.Option(..., "--repo", help="Path to the target repository")):
    """Run the full multi-agent audit graph (security, quality, test analysts in parallel)."""
    configure_logging()
    result = run_audit(repo)
    typer.echo(json.dumps(result, indent=2))


if __name__ == "__main__":
    app()
