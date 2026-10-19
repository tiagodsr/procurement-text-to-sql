"""Pydantic contracts shared by every step: request, LLM outputs, tool results, trace events and
the final answer. The validators of FinalAnswer enforce the status rules of docs/PLAN.md section 5.

Demo:
    python -m assistant.schemas FinalAnswer     # JSON schema of one model
    python -m assistant.schemas --examples      # one valid FinalAnswer per status
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from typing import Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

AnswerStatus = Literal[
    "answered", "needs_clarification", "no_data", "out_of_scope", "refused", "error"
]
StepStatus = Literal[
    "ok", "refusal", "invalid_output", "incomplete", "upstream_error", "blocked", "error"
]
Step = Literal[
    "intake", "retrieve", "resolve_entity", "generate_sql", "repair_sql", "guard",
    "execute", "verify", "explain", "agent", "embed", "request",
]  # fmt: skip
Variant = Literal["baseline", "A", "B"]
JsonScalar = str | int | float | bool | None

MAX_QUESTION_CHARS = 500
MAX_CLARIFICATION_CHARS = 200
MAX_OPTIONS = 6
MAX_MENTIONS = 5


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _not_blank(value: str, name: str) -> str:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value


# --- request ---------------------------------------------------------------------------------


class QueryRequest(Contract):
    question: str
    variant: Variant = "A"
    clarification: str | None = None  # the user's reply to a clarification question (untrusted)

    @field_validator("question")
    @classmethod
    def _question(cls, value: str) -> str:
        value = _not_blank(value, "question").strip()
        if len(value) > MAX_QUESTION_CHARS:
            raise ValueError(f"question is longer than {MAX_QUESTION_CHARS} characters")
        return value

    @field_validator("clarification")
    @classmethod
    def _clarification(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        if len(value.strip()) > MAX_CLARIFICATION_CHARS:
            raise ValueError(f"clarification is longer than {MAX_CLARIFICATION_CHARS} characters")
        return value.strip()


# --- LLM outputs (strict structured outputs: every field required, limits in validators) -----


class Clarification(Contract):
    question: str
    reason: Literal["entity_ambiguity", "vague_criterion", "missing_period", "other"]
    options: list[str]

    @model_validator(mode="after")
    def _options(self) -> Self:
        _not_blank(self.question, "clarification question")
        if any(not option.strip() for option in self.options):
            raise ValueError("clarification options must not be blank")
        if len({option.strip().casefold() for option in self.options}) != len(self.options):
            raise ValueError("clarification options must be distinct")
        if len(self.options) > MAX_OPTIONS:
            raise ValueError(f"at most {MAX_OPTIONS} clarification options")
        if self.reason == "entity_ambiguity" and len(self.options) < 2:
            raise ValueError("entity ambiguity needs at least two options")
        return self


class IntakeResult(Contract):
    route: Literal["sql", "clarify", "out_of_scope", "unsafe"]
    reason: str
    mentions: list[str]  # names of suppliers, categories or plants, for resolve_entity
    clarification: Clarification | None

    @model_validator(mode="after")
    def _route(self) -> Self:
        _not_blank(self.reason, "reason")
        if len(self.mentions) > MAX_MENTIONS:
            raise ValueError(f"at most {MAX_MENTIONS} mentions")
        if (self.route == "clarify") != (self.clarification is not None):
            raise ValueError("route 'clarify' needs a clarification, and only that route has one")
        return self


class GeneratedSQL(Contract):
    sql: str
    assumptions: list[str]

    @field_validator("sql")
    @classmethod
    def _sql(cls, value: str) -> str:
        return _not_blank(value, "sql")


class ExplanationDraft(Contract):
    answer: str
    cited_rows: list[int]  # indexes into the result preview; range checked in FinalAnswer
    caveats: list[str]

    @model_validator(mode="after")
    def _citations(self) -> Self:
        _not_blank(self.answer, "answer")
        if any(i < 0 for i in self.cited_rows) or len(set(self.cited_rows)) != len(self.cited_rows):
            raise ValueError("cited_rows must be distinct, non-negative row indexes")
        return self


LLM_OUTPUTS: tuple[type[Contract], ...] = (
    IntakeResult,
    Clarification,
    GeneratedSQL,
    ExplanationDraft,
)


# --- tools and execution ---------------------------------------------------------------------


class RetrievedDoc(Contract):
    id: str
    score: float
    doc_type: Literal["table", "glossary", "example"]
    table: str | None = None
    source: str


class SchemaContext(Contract):
    query: str
    docs: list[RetrievedDoc]

    @model_validator(mode="after")
    def _ranked(self) -> Self:
        scores = [doc.score for doc in self.docs]
        if scores != sorted(scores, reverse=True):
            raise ValueError("docs must be sorted by score, highest first")
        return self


class EntityCandidate(Contract):
    kind: Literal["supplier", "category", "plant"]
    id: int
    name: str
    matched: str  # the name or alias that matched the mention


class GuardViolation(Contract):
    rule: str
    message: str

    @field_validator("rule")
    @classmethod
    def _rule(cls, value: str) -> str:
        return _not_blank(value, "rule")


class SqlResult(Contract):
    sql: str  # the SQL that ran, with the guard's forced LIMIT
    columns: list[str]
    rows: list[list[JsonScalar]]  # preview
    row_count: int = Field(ge=0)
    truncated: bool
    db_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("every row must have one value per column")
        if len(self.rows) > self.row_count:
            raise ValueError("the preview cannot hold more rows than row_count")
        if self.row_count > len(self.rows) and not self.truncated:
            raise ValueError("a preview shorter than row_count must be marked truncated")
        return self


class SqlError(Contract):
    kind: Literal["syntax", "undefined_object", "timeout", "cost_limit", "permission", "other"]
    message: str  # database text: untrusted data
    sql: str


class FilterCheck(Contract):
    filter: str
    rows_without: int = Field(ge=0)  # rows returned when only this filter is dropped


class NoDataDiagnostic(Contract):
    summary: str
    checks: list[FilterCheck] = []

    @field_validator("summary")
    @classmethod
    def _summary(cls, value: str) -> str:
        return _not_blank(value, "summary")


# --- verification, errors, usage, final answer -----------------------------------------------


class CheckResult(Contract):
    name: str
    passed: bool
    detail: str = ""


class Confidence(Contract):
    score: float = Field(ge=0, le=1)
    checks: list[CheckResult] = []

    @computed_field
    @property
    def level(self) -> Literal["high", "medium", "low"]:
        return "high" if self.score >= 0.8 else "medium" if self.score >= 0.5 else "low"


class ErrorInfo(Contract):
    kind: Literal["max_steps", "max_retries", "timeout", "budget", "upstream", "invalid_output"]
    message: str


class TraceEvent(Contract):
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    request_id: str
    variant: Variant | None = None
    step: Step
    kind: Literal["llm", "embedding", "tool", "request"]
    model: str | None = None
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)
    latency_ms: float = Field(ge=0)
    status: StepStatus | AnswerStatus
    sql: str | None = None
    error: str | None = None
    detail: dict[str, JsonScalar] = {}

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.kind in ("llm", "embedding") and not self.model:
            raise ValueError(f"a {self.kind} event needs a model")
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens cannot exceed input_tokens")
        if self.reasoning_tokens > self.output_tokens:
            raise ValueError("reasoning_tokens cannot exceed output_tokens")
        if (self.kind == "request") != (self.step == "request"):
            raise ValueError("kind 'request' goes with step 'request' only")
        allowed = get_args(AnswerStatus) if self.kind == "request" else get_args(StepStatus)
        if self.status not in allowed:
            raise ValueError(f"status {self.status!r} is not valid for a {self.kind} event")
        return self


class RequestUsage(Contract):
    llm_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0)
    steps: list[TraceEvent] = []


class FinalAnswer(Contract):
    request_id: str
    question: str
    variant: Variant
    status: AnswerStatus
    answer: str  # plain language; for needs_clarification, the question to the user
    sql: str | None = None
    result: SqlResult | None = None
    clarification: Clarification | None = None
    diagnostic: NoDataDiagnostic | None = None
    evidence: list[RetrievedDoc] = []
    cited_rows: list[int] = []
    assumptions: list[str] = []
    confidence: Confidence | None = None
    error: ErrorInfo | None = None
    usage: RequestUsage = RequestUsage()

    @model_validator(mode="after")
    def _status_rules(self) -> Self:
        _not_blank(self.answer, "answer")
        status, ran = self.status, self.sql is not None or self.result is not None
        if (self.error is not None) != (status == "error"):
            raise ValueError("error info belongs to status 'error', and that status needs it")
        if self.clarification is not None and status != "needs_clarification":
            raise ValueError("a clarification belongs to status 'needs_clarification' only")
        if self.diagnostic is not None and status != "no_data":
            raise ValueError("a diagnostic belongs to status 'no_data' only")
        if self.result is not None and self.result.sql != self.sql:
            raise ValueError("result.sql must be the answer's sql")
        preview = len(self.result.rows) if self.result else 0
        if any(not 0 <= i < preview for i in self.cited_rows):
            raise ValueError("cited_rows must point at rows of the result preview")

        if status in ("answered", "no_data") and (self.sql is None or self.result is None):
            raise ValueError(f"status {status!r} needs the executed SQL and its result")
        if status == "answered" and not self.result.rows:
            raise ValueError("an answered question needs at least one row (or aggregate)")
        if status == "no_data" and (self.result.row_count != 0 or self.diagnostic is None):
            raise ValueError("no_data needs zero rows and a diagnostic of the filters")
        if status == "needs_clarification" and self.clarification is None:
            raise ValueError("needs_clarification needs a clarification")
        if status in ("needs_clarification", "out_of_scope", "refused", "error") and ran:
            raise ValueError(f"status {status!r} means no SQL result is returned")
        if status in ("out_of_scope", "refused") and any(
            e.step == "execute" for e in self.usage.steps
        ):
            raise ValueError(f"status {status!r} means no SQL was executed, but the trace has one")
        return self


def examples() -> dict[str, FinalAnswer]:
    """One valid FinalAnswer per status (demo and test fixtures)."""
    sql = "SELECT COUNT(*) FROM suppliers LIMIT 100"
    base = {"request_id": "demo", "question": "How many suppliers do we have?", "variant": "A"}
    shape = {"sql": sql, "columns": ["count"], "truncated": False, "db_ms": 2.0}
    one_row = SqlResult(rows=[[96]], row_count=1, **shape)
    no_rows = SqlResult(rows=[], row_count=0, **shape)
    clarify = Clarification(question="Which one?", reason="entity_ambiguity", options=["A", "B"])
    gap = NoDataDiagnostic(summary="The year filter removed every row.")
    cases = {
        "answered": {"answer": "96 suppliers.", "sql": sql, "result": one_row, "cited_rows": [0]},
        "no_data": {"answer": "No rows.", "sql": sql, "result": no_rows, "diagnostic": gap},
        "needs_clarification": {"answer": clarify.question, "clarification": clarify},
        "out_of_scope": {"answer": "I only answer questions about procurement data."},
        "refused": {"answer": "I can only read data, not change it."},
        "error": {
            "answer": "I could not answer within the limits.",
            "error": ErrorInfo(kind="max_retries", message="SQL failed twice"),
        },
    }
    return {s: FinalAnswer(status=s, **base, **fields) for s, fields in cases.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("model", nargs="?", help="model name, e.g. FinalAnswer")
    parser.add_argument("--examples", action="store_true", help="print one answer per status")
    args = parser.parse_args()
    if args.examples:
        for status, answer in examples().items():
            print(f"{status:20s} {answer.model_dump_json(exclude_defaults=True)}")
        return
    model = globals().get(args.model or "")
    if not (isinstance(model, type) and issubclass(model, Contract)):
        sys.exit(f"unknown model {args.model!r}")
    print(json.dumps(model.model_json_schema(), indent=2))


if __name__ == "__main__":
    main()
