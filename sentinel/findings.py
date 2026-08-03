from pydantic import BaseModel, Field


class Finding(BaseModel):
    file_path: str = Field(description="Path to the file, relative to the repo root")
    line: int = Field(description="1-indexed line number of the issue")
    symbol: str = Field(description="Name of the unused import/symbol")
    evidence: str = Field(description="The offending source line(s)")
    explanation: str = Field(description="Why this is flagged as unused")


class FindingsReport(BaseModel):
    findings: list[Finding]
