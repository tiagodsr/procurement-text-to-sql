"""Spec for assistant.schemas: the status rules of docs/PLAN.md section 5 and other contracts."""

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from assistant.schemas import (
    LLM_OUTPUTS,
    Clarification,
    FinalAnswer,
    IntakeResult,
    QueryRequest,
    RequestUsage,
    SchemaContext,
    SqlResult,
    TraceEvent,
    examples,
)

EXAMPLES = examples()
SQL = EXAMPLES["answered"].sql
ONE_ROW = EXAMPLES["answered"].result
NO_ROWS = EXAMPLES["no_data"].result


def answer(status: str, **changes: Any) -> FinalAnswer:
    """Validate the example for `status` with some fields changed (model_copy would skip checks)."""
    return FinalAnswer.model_validate({**dict(EXAMPLES[status]), **changes})


def execute_event() -> TraceEvent:
    return TraceEvent(request_id="r", step="execute", kind="tool", latency_ms=3, status="ok")


# --- FinalAnswer: one valid example per status ------------------------------------------------


@pytest.mark.parametrize("status", list(EXAMPLES))
def test_every_status_has_a_valid_example(status):
    assert answer(status).status == status


# --- FinalAnswer: the status rules ------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "changes", "message"),
    [
        # answered needs the executed SQL and a non-empty preview or aggregate
        ("answered", {"sql": None, "result": None, "cited_rows": []}, "executed SQL"),
        ("answered", {"result": NO_ROWS, "cited_rows": []}, "at least one row"),
        # no_data needs zero rows and a diagnostic
        ("no_data", {"diagnostic": None}, "diagnostic"),
        ("no_data", {"result": ONE_ROW}, "zero rows"),
        ("no_data", {"sql": None, "result": None}, "executed SQL"),
        # needs_clarification needs a clarification and runs no SQL
        ("needs_clarification", {"clarification": None}, "needs a clarification"),
        ("needs_clarification", {"sql": SQL, "result": ONE_ROW}, "no SQL result"),
        # out_of_scope and refused: no SQL, not in the fields and not in the trace
        ("out_of_scope", {"sql": SQL, "result": ONE_ROW}, "no SQL result"),
        ("refused", {"sql": SQL}, "no SQL result"),
        ("out_of_scope", {"usage": RequestUsage(steps=[execute_event()])}, "trace has one"),
        ("refused", {"usage": RequestUsage(steps=[execute_event()])}, "trace has one"),
        # error needs error info, is the only status with it, and never carries a result
        ("error", {"error": None}, "error info"),
        ("error", {"sql": SQL, "result": ONE_ROW}, "no SQL result"),
        ("answered", {"error": EXAMPLES["error"].error}, "error info"),
        # fields that belong to one status only
        ("answered", {"clarification": EXAMPLES["needs_clarification"].clarification}, "only"),
        ("answered", {"diagnostic": EXAMPLES["no_data"].diagnostic}, "only"),
        # citations and consistency
        ("answered", {"cited_rows": [1]}, "cited_rows"),
        ("out_of_scope", {"cited_rows": [0]}, "cited_rows"),
        ("answered", {"sql": "SELECT 1"}, "result.sql"),
        ("refused", {"answer": "  "}, "blank"),
    ],
)
def test_status_rules_reject_inconsistent_answers(status, changes, message):
    with pytest.raises(ValidationError, match=message):
        answer(status, **changes)


def test_an_execute_event_is_fine_for_an_answered_question():
    assert answer("answered", usage=RequestUsage(steps=[execute_event()]))


# --- Clarification and IntakeResult -----------------------------------------------------------


def clarification(reason: str, options: list[str]) -> Clarification:
    return Clarification(question="Which one do you mean?", reason=reason, options=options)


def test_entity_ambiguity_needs_two_options():
    with pytest.raises(ValidationError, match="at least two"):
        clarification("entity_ambiguity", ["Northwind Tools"])
    assert clarification("entity_ambiguity", ["Northwind Tools", "Northwind Office"])


def test_a_vague_criterion_may_have_no_options():
    assert clarification("vague_criterion", []).options == []


@pytest.mark.parametrize(
    "options", [["A", "a"], ["A", " "], [str(i) for i in range(7)]], ids=["dup", "blank", "many"]
)
def test_bad_options_are_rejected(options):
    with pytest.raises(ValidationError):
        clarification("other", options)


def test_intake_route_clarify_goes_with_a_clarification():
    c = clarification("missing_period", [])
    assert IntakeResult(route="clarify", reason="no year", mentions=[], clarification=c)
    with pytest.raises(ValidationError, match="clarify"):
        IntakeResult(route="clarify", reason="no year", mentions=[], clarification=None)
    with pytest.raises(ValidationError, match="clarify"):
        IntakeResult(route="sql", reason="clear", mentions=[], clarification=c)


# --- QueryRequest -----------------------------------------------------------------------------


def test_question_is_stripped_and_defaults_to_variant_a():
    request = QueryRequest(question="  top suppliers in 2025?  ", clarification=" ")
    assert request.question == "top suppliers in 2025?"
    assert request.variant == "A"
    assert request.clarification is None


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "   "},
        {"question": "x" * 501},
        {"question": "ok", "variant": "C"},
        {"question": "ok", "role": "admin"},
        {"question": "ok", "clarification": "y" * 201},
    ],
    ids=["blank", "too-long", "variant", "extra-field", "long-clarification"],
)
def test_invalid_requests_are_rejected(payload):
    with pytest.raises(ValidationError):
        QueryRequest.model_validate(payload)


# --- results and context ----------------------------------------------------------------------


def test_sql_result_rows_match_the_columns():
    with pytest.raises(ValidationError, match="one value per column"):
        SqlResult(sql=SQL, columns=["a"], rows=[[1, 2]], row_count=1, truncated=False, db_ms=1)


def test_a_cut_preview_must_be_marked_truncated():
    with pytest.raises(ValidationError, match="truncated"):
        SqlResult(sql=SQL, columns=["a"], rows=[[1]], row_count=5, truncated=False, db_ms=1)
    assert SqlResult(sql=SQL, columns=["a"], rows=[[1]], row_count=5, truncated=True, db_ms=1)


def test_schema_context_is_ranked_by_score():
    docs = [
        {"id": "a", "score": 0.2, "doc_type": "table", "source": "x"},
        {"id": "b", "score": 0.9, "doc_type": "table", "source": "x"},
    ]
    with pytest.raises(ValidationError, match="sorted"):
        SchemaContext(query="q", docs=docs)
    assert SchemaContext(query="q", docs=docs[::-1])


# --- TraceEvent -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"model": None}, "needs a model"),
        ({"input_tokens": 10, "cached_input_tokens": 11}, "cached_input_tokens"),
        ({"output_tokens": 1, "reasoning_tokens": 2}, "reasoning_tokens"),
        ({"cost_usd": -0.01}, "greater than or equal"),
        ({"status": "answered"}, "not valid for a llm event"),
        ({"kind": "request", "step": "intake"}, "step 'request'"),
        ({"kind": "request", "step": "request", "model": None, "status": "ok"}, "not valid"),
    ],
)
def test_inconsistent_trace_events_are_rejected(fields, message):
    base = {
        "request_id": "r",
        "step": "intake",
        "kind": "llm",
        "model": "m",
        "latency_ms": 1,
        "status": "ok",
    }
    with pytest.raises(ValidationError, match=message):
        TraceEvent(**{**base, **fields})


def test_error_is_a_valid_status_for_steps_and_requests():
    assert TraceEvent(request_id="r", step="execute", kind="tool", latency_ms=1, status="error")
    assert TraceEvent(request_id="r", step="request", kind="request", latency_ms=1, status="error")


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
