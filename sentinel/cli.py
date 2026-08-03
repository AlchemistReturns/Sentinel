import json

import typer

from sentinel.agent import run_investigation
from sentinel.ingest import ingest_repo, query_repo
from sentinel.logging import configure_logging

app = typer.Typer(help="Sentinel — autonomous codebase auditor")


@app.command()
def ingest(repo: str = typer.Option(..., "--repo", help="Path to the target repository")):
    configure_logging()
    result = ingest_repo(repo)
    typer.echo(f"Indexed {result['chunks']} chunks from {result['files']} files (audit_id={result['audit_id']})")


@app.command()
def query(
    repo: str = typer.Option(..., "--repo", help="Path to the target repository"),
    text: str = typer.Option(..., "--text", help="Query text"),
    k: int = typer.Option(5, "--k", help="Number of results"),
):
    configure_logging()
    results = query_repo(repo, text, k=k)
    for doc in results:
        typer.echo(f"--- {doc.metadata['file_path']} (chunk {doc.metadata['chunk_index']}) ---")
        typer.echo(doc.page_content[:300])
        typer.echo("")


@app.command()
def investigate(repo: str = typer.Option(..., "--repo", help="Path to the target repository")):
    configure_logging()
    result = run_investigation(repo)
    typer.echo(json.dumps(result, indent=2))


if __name__ == "__main__":
    app()
