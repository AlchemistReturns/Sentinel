from pathlib import Path

from langchain_core.tools import tool

from sentinel.ingest import iter_source_files


def make_tools(repo_path: Path):
    repo_root = repo_path.resolve()

    @tool
    def list_python_files() -> list[str]:
        """List all Python source files in the repository, as paths relative to the repo root."""
        return [
            str(p.relative_to(repo_root))
            for p in iter_source_files(repo_root)
            if p.suffix == ".py"
        ]

    @tool
    def read_source_file(file_path: str) -> str:
        """Read the full contents of a source file, given its path relative to the repo root."""
        target = (repo_root / file_path).resolve()
        if repo_root not in target.parents and target != repo_root:
            return "Error: path escapes repository root, refusing to read."
        if not target.is_file():
            return f"Error: {file_path} is not a file."
        return target.read_text(encoding="utf-8", errors="ignore")

    return [list_python_files, read_source_file]
