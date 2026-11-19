"""Structured review outputs (Pydantic-validated)."""
import hashlib
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Category = Literal["bug", "security", "quality", "maintainability", "performance"]
Severity = Literal["critical", "high", "medium", "low", "info"]
Status = Literal["confirmed", "possible"]

SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


class LLMFinding(BaseModel):
    """What the model must return for each issue (JSON schema sent to Gemini)."""

    title: str = Field(description="Short title of the issue, under 15 words")
    description: str = Field(description="What is wrong, in 1-3 sentences")
    category: Category
    severity: Severity
    confidence: float = Field(description="0.0-1.0: how sure you are this is a real problem")
    file: str = Field(description="Path of the changed file, exactly as given")
    start_line: int = Field(description="First NEW-file line number of the issue")
    end_line: int = Field(description="Last NEW-file line number of the issue")
    evidence: str = Field(description="Exact code copied verbatim from the changed lines")
    explanation: str = Field(default="", description="Why it is a problem; cite context labels like [C2]")
    suggested_fix: str = Field(default="", description="Concrete fix, code if possible")
    context_refs: list[str] = Field(default_factory=list, description="Context labels used, e.g. ['C1']")

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, value: float) -> float:
        return min(1.0, max(0.0, float(value)))

    @field_validator("start_line", "end_line")
    @classmethod
    def _positive_line(cls, value: int) -> int:
        if value < 1:
            raise ValueError("line numbers are 1-based")
        return value


class LLMFindingList(BaseModel):
    findings: list[LLMFinding] = Field(default_factory=list)


class ReviewFinding(BaseModel):
    """A finding after validation; this is what gets stored and posted."""

    title: str
    description: str
    category: Category
    severity: Severity
    confidence: float
    status: Status
    source: str                         # "llm:bug", "llm:security", "rule:SEC002", ...
    file: str
    start_line: int
    end_line: int
    comment_line: int | None = None     # set only when GitHub accepts an inline comment there
    evidence: str = ""
    explanation: str = ""
    suggested_fix: str = ""
    context_refs: list[str] = Field(default_factory=list)
    evidence_verified: bool = False
    in_changed_lines: bool = False
    sensitive_file: bool = False

    @property
    def fingerprint(self) -> str:
        title = re.sub(r"\W+", " ", self.title.lower()).strip()
        key = f"{self.file}|{self.category}|{self.start_line}|{self.end_line}|{title}"
        return hashlib.sha256(key.encode()).hexdigest()

    @property
    def severity_rank(self) -> int:
        return SEVERITY_RANK[self.severity]
