from pathlib import Path

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from sentinel.config import settings
from sentinel.logging import get_logger

FIXABLE_ANALYSTS = {"quality", "security"}

FIXER_SYSTEM_PROMPT = """You are Sentinel's Fixer. You are given one finding from a code
review and the current content of the affected file. Produce the smallest possible edit
that fixes the finding.

Rules:
- `old_snippet` must be copied EXACTLY (verbatim, including whitespace/indentation) from
  the file content you were given, and must be unique in the file.
- `new_snippet` is what should replace it. To delete a line entirely, set `new_snippet` to
  an empty string.
- Only fix the one issue described. Do not refactor, reformat, or touch unrelated code.
- If you cannot confidently produce a safe, minimal, exact-match fix (e.g. the finding is
  too vague, or applies to the whole repo rather than one file), set `unfixable` to true
  and leave the snippets empty.
"""


class ProposedFix(BaseModel):
    old_snippet: str = Field(description="Exact existing text to replace, or empty if unfixable")
    new_snippet: str = Field(description="Replacement text, or empty string to delete")
    explanation: str = Field(description="One sentence: what this edit does and why")
    unfixable: bool = Field(default=False, description="True if no safe automated fix exists")


def generate_fix(repo_path: Path, finding: dict) -> ProposedFix | None:
    log = get_logger(analyst=finding.get("analyst"), file=finding.get("file_path"))

    if finding.get("analyst") not in FIXABLE_ANALYSTS:
        return None

    target = (repo_path / finding["file_path"]).resolve()
    if repo_path.resolve() not in target.parents and target != repo_path.resolve():
        log.error("fixer.path_escape", file_path=finding["file_path"])
        return None
    if not target.is_file():
        log.info("fixer.skip_no_file", file_path=finding["file_path"])
        return None

    file_content = target.read_text(encoding="utf-8", errors="ignore")
    if len(file_content) > 20000:
        file_content = file_content[:20000]

    model = ChatOpenAI(model=settings.agent_model, api_key=settings.openai_api_key)
    structured = model.with_structured_output(ProposedFix)

    user_message = (
        f"Finding (from the {finding.get('analyst')} analyst):\n"
        f"- file: {finding['file_path']}\n"
        f"- symbol: {finding.get('symbol')}\n"
        f"- evidence: {finding.get('evidence')}\n"
        f"- explanation: {finding.get('explanation')}\n\n"
        f"Current file content:\n```\n{file_content}\n```"
    )

    try:
        result: ProposedFix = structured.invoke(
            [
                {"role": "system", "content": FIXER_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ]
        )
    except Exception as e:
        log.error("fixer.llm_error", error=str(e))
        return None

    if result.unfixable or not result.old_snippet:
        log.info("fixer.unfixable")
        return None

    if file_content.count(result.old_snippet) != 1:
        log.error("fixer.snippet_not_unique", occurrences=file_content.count(result.old_snippet))
        return None

    return result
