"""Spec for assistant.schemas: the rules of each answer status, and LLM outputs that fit strict
structured outputs."""

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from assistant.schemas import (
    LLM_OUTPUTS,
    Clarification,
    ErrorInfo,
    FinalAnswer,
    IntakeResult,
    NoDataDiagnostic,
    QueryRequest,
    RequestUsage,
    SqlResult,
    TraceEvent,
)

SQL = "SELECT COUNT(*) FROM suppliers LIMIT 100"
SHAPE = {"sql": SQL, "columns": ["count"], "truncated": False, "db_ms": 2.0}
ONE_ROW = SqlResult(rows=[[96]], row_count=1, **SHAPE)
NO_ROWS = SqlResult(rows=[], row_count=0, **SHAPE)
CLARIFY = Clarification(question="Which one?", reason="entity_ambiguity", options=["A", "B"])
GAP = NoDataDiagnostic(summary="The year filter removed every row.")
FAILED = ErrorInfo(kind="max_retries", message="SQL failed twice")

# One valid answer per status.
EXAMPLES = {
    "answered": {"answer": "96 suppliers.", "sql": SQL, "result": ONE_ROW, "cited_rows": [0]},
    "no_data": {"answer": "No rows.", "sql": SQL, "result": NO_ROWS, "diagnostic": GAP},
    "needs_clarification": {"answer": CLARIFY.question, "clarification": CLARIFY},
    "out_of_scope": {"answer": "I only answer questions about procurement data."},
    "refused": {"answer": "I can only read data, not change it."},
    "error": {"answer": "I could not answer within the limits.", "error": FAILED},
}


def answer(status: str, **changes: Any) -> FinalAnswer:
    base = {"request_id": "r", "question": "How many suppliers?", "variant": "workflow"}
    return FinalAnswer.model_validate({**base, "status": status, **EXAMPLES[status], **changes})


def execute_event() -> TraceEvent:
    return TraceEvent(request_id="r", step="execute", kind="tool", latency_ms=3, status="ok")


@pytest.mark.parametrize("status", list(EXAMPLES))
def test_every_status_has_a_valid_example(status):
    assert answer(status).status == status


@pytest.mark.parametrize(
    ("status", "changes", "message"),
    [
        # answered needs the executed SQL and a non-empty preview or aggregate
        ("answered", {"sql": None, "result": None, "cited_rows": []}, "executed SQL"),
        ("answered", {"result": NO_ROWS, "cited_rows": []}, "at least one row"),
        # no_data needs zero rows and a diagnostic
        ("no_data", {"diagnostic": None}, "diagnostic"),
        ("no_data", {"result": ONE_ROW}, "zero rows"),
        # needs_clarification needs a clarification and runs no SQL
        ("needs_clarification", {"clarification": None}, "needs a clarification"),
        ("needs_clarification", {"sql": SQL, "result": ONE_ROW}, "no SQL result"),
        # out_of_scope and refused: no SQL, not in the fields and not in the trace
        ("out_of_scope", {"sql": SQL, "result": ONE_ROW}, "no SQL result"),
        ("refused", {"usage": RequestUsage(steps=[execute_event()])}, "trace has one"),
        # error needs error info, is the only status with it, and never carries a result
        ("error", {"error": None}, "error info"),
        ("answered", {"error": FAILED}, "error info"),
        # citations point at rows of the preview
        ("answered", {"cited_rows": [1]}, "cited_rows"),
        ("refused", {"answer": "  "}, "blank"),
    ],
)
def test_status_rules_reject_inconsistent_answers(status, changes, message):
    with pytest.raises(ValidationError, match=message):
        answer(status, **changes)


def test_entity_ambiguity_needs_two_options():
    with pytest.raises(ValidationError, match="at least two"):
        Clarification(question="Which one?", reason="entity_ambiguity", options=["Northwind"])


def test_intake_route_clarify_goes_with_a_clarification():
    assert IntakeResult(route="clarify", reason="two entities", clarification=CLARIFY)
    with pytest.raises(ValidationError, match="clarify"):
        IntakeResult(route="clarify", reason="two entities", clarification=None)
    with pytest.raises(ValidationError, match="clarify"):
        IntakeResult(route="sql", reason="clear", clarification=CLARIFY)


@pytest.mark.parametrize(
    "payload",
    [{"question": "   "}, {"question": "x" * 501}, {"question": "ok", "role": "admin"}],
    ids=["blank", "too-long", "extra-field"],
)
def test_invalid_requests_are_rejected(payload):
    with pytest.raises(ValidationError):
        QueryRequest.model_validate(payload)


# --- LLM outputs stay within strict structured outputs ----------------------------------------

STRICT_KEYWORDS = {
    "type", "properties", "required", "items", "enum", "const", "anyOf",
    "$ref", "$defs", "title", "description", "additionalProperties",
}  # fmt: skip


def walk(node: Any):
    """Every schema node; the keys of `properties` and `$defs` are names, not keywords."""
    if isinstance(node, dict):
        yield node
        for key, value in node.items():
            children = value.values() if key in ("properties", "$defs") else [value]
            for child in children:
                yield from walk(child)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


@pytest.mark.parametrize("model", LLM_OUTPUTS, ids=lambda m: m.__name__)
def test_llm_output_schemas_are_strict_compatible(model: type[BaseModel]):
    for node in walk(model.model_json_schema()):
        assert set(node) <= STRICT_KEYWORDS, f"{model.__name__}: {set(node) - STRICT_KEYWORDS}"
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
            assert set(node["required"]) == set(node["properties"]), "every field is required"
