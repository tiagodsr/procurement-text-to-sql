"""The fixed workflow: intake -> SQL -> guard -> read-only execute (repair up to twice) -> explain,
with exits for clarification, no data, out of scope, refusal and errors. Every step is traced."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from assistant import prompts
from assistant.config import Pricing, Settings
from assistant.diagnose import diagnose
from assistant.executor import DatabaseUnavailable, execute
from assistant.guard import GuardedSQL, check
from assistant.llm import Clock, LLMUnavailable, parse
from assistant.schemas import (
    AnswerStatus,
    ErrorInfo,
    ExplanationDraft,
    FinalAnswer,
    GeneratedSQL,
    GuardViolation,
    IntakeResult,
    QueryRequest,
    SqlError,
    SqlResult,
    Step,
)
from assistant.trace import Tracer

MAX_REPAIRS = 2
UNSAFE_RULES = {"write_or_ddl", "not_select", "select_into", "locking_clause"}
MAX_OUTPUT_TOKENS = {"intake": 400, "generate_sql": 1000, "repair_sql": 1000, "explain": 800}

OUT_OF_SCOPE = (
    "I can only answer questions about the procurement data: suppliers, contracts, the catalog, "
    "purchases, categories and plants."
)
REFUSED = (
    "I can only read the procurement data. I do not change data, read system information or "
    "follow instructions that try to change my rules."
)

Runner = Callable[[GuardedSQL], SqlResult | SqlError]


@dataclass(frozen=True)
class Deps:
    """What a request needs from outside; tests pass a fake client and a fake database."""

    client: Any
    settings: Settings
    pricing: Pricing
    run_sql: Runner = execute
    clock: Clock = time.perf_counter


class _Exit(Exception):
    """Ends the workflow early with these FinalAnswer fields."""

    def __init__(self, status: AnswerStatus, answer: str, **fields: Any) -> None:
        self.fields = {"status": status, "answer": answer, **fields}


def _error(kind: str, message: str) -> _Exit:
    answer = f"I could not answer this question ({message})."
    return _Exit("error", answer, error=ErrorInfo(kind=kind, message=message))


def answer(request: QueryRequest, deps: Deps, tracer: Tracer | None = None) -> FinalAnswer:
    """Run the workflow for one request; always returns a FinalAnswer."""
    tracer = tracer or Tracer(deps.settings.trace_dir, variant="workflow")
    started = deps.clock()
    try:
        fields = _run(request, deps, tracer)
    except _Exit as exit_:
        fields = exit_.fields
    except (LLMUnavailable, DatabaseUnavailable) as exc:
        fields = _error("upstream", str(exc)).fields
    tracer.record(
        step="request", kind="request", latency_ms=_ms(deps, started), status=fields["status"]
    )
    return FinalAnswer(
        request_id=tracer.request_id,
        question=request.question,
        variant="workflow",
        usage=tracer.usage(),
        **fields,
    )


def _run(request: QueryRequest, deps: Deps, tracer: Tracer) -> dict[str, Any]:
    asked = _user_input(request)
    route = _llm(deps, tracer, "intake", prompts.intake(), asked, IntakeResult)
    if route.route == "clarify":
        clarification = route.clarification
        raise _Exit("needs_clarification", clarification.question, clarification=clarification)
    if route.route == "out_of_scope":
        raise _Exit("out_of_scope", OUT_OF_SCOPE)
    if route.route == "unsafe":
        raise _Exit("refused", REFUSED)

    instructions = prompts.sql()
    query = _llm(deps, tracer, "generate_sql", instructions, asked, GeneratedSQL)
    for repairs in range(MAX_REPAIRS + 1):
        outcome = _guard_and_run(query.sql, deps, tracer)
        if isinstance(outcome, SqlResult):
            break
        failure = _describe(outcome)
        if repairs == MAX_REPAIRS:
            raise _error(
                "max_retries", f"the SQL still failed after {MAX_REPAIRS} repairs: {failure}"
            )
        failed = f"{asked}\n{_tag('failed_sql', query.sql)}\n{_tag('error', failure)}"
        query = _llm(deps, tracer, "repair_sql", instructions, failed, GeneratedSQL)

    result = outcome
    done = {"sql": result.sql, "result": result, "assumptions": query.assumptions}
    if result.empty:
        started = deps.clock()
        diagnostic = diagnose(result.sql, run=deps.run_sql)
        tracer.record(
            step="diagnose",
            kind="tool",
            latency_ms=_ms(deps, started),
            status="ok",
            sql=result.sql,
            detail={"filters_checked": len(diagnostic.checks)},
        )
        summary = f"No rows match the question. {diagnostic.summary}"
        return {"status": "no_data", "answer": summary, "diagnostic": diagnostic, **done}

    shown = f"{asked}\n{_result_input(query, result)}"
    draft = _llm(deps, tracer, "explain", prompts.explain(), shown, ExplanationDraft)
    if any(i >= len(result.rows) for i in draft.cited_rows):
        raise _error("invalid_output", "the explanation cited rows that the result does not have")
    return {
        "status": "answered",
        "answer": draft.answer,
        "cited_rows": draft.cited_rows,
        "caveats": draft.caveats,
        **done,
    }


def _llm[T: BaseModel](
    deps: Deps, tracer: Tracer, step: Step, instructions: str, input: str, output_type: type[T]
) -> T:
    """One structured call. A model refusal ends as `refused` (or `error` once rows were read);
    any other unusable output ends as `error`."""
    result = parse(
        deps.client,
        step=step,
        model=deps.settings.llm_model,
        instructions=instructions,
        input=input,
        output_type=output_type,
        pricing=deps.pricing,
        tracer=tracer,
        max_output_tokens=MAX_OUTPUT_TOKENS[step],
        clock=deps.clock,
    )
    if result.output is not None:
        return result.output
    if result.failure == "refusal" and step != "explain":
        raise _Exit("refused", REFUSED)
    raise _error("invalid_output", f"the {step} step returned unusable output: {result.detail}")


def _guard_and_run(sql: str, deps: Deps, tracer: Tracer) -> SqlResult | SqlError | GuardViolation:
    started = deps.clock()
    guarded = check(sql)
    if isinstance(guarded, GuardViolation):
        tracer.record(
            step="guard",
            kind="tool",
            latency_ms=_ms(deps, started),
            status="blocked",
            sql=sql,
            error=_describe(guarded),
        )
        if guarded.rule in UNSAFE_RULES:
            raise _Exit("refused", REFUSED)
        return guarded
    tracer.record(
        step="guard",
        kind="tool",
        latency_ms=_ms(deps, started),
        status="ok",
        sql=guarded.sql,
        detail={"limit": guarded.limit},
    )
    started = deps.clock()
    outcome = deps.run_sql(guarded)
    ok = isinstance(outcome, SqlResult)
    tracer.record(
        step="execute",
        kind="tool",
        latency_ms=_ms(deps, started),
        status="ok" if ok else "error",
        sql=guarded.sql,
        error=None if ok else _describe(outcome),
        detail={"rows": outcome.row_count} if ok else {},
    )
    return outcome


# --- the untrusted `input` of each call --------------------------------------------------------


def _tag(name: str, value: str) -> str:
    return f"<{name}>\n{value}\n</{name}>"


def _user_input(request: QueryRequest) -> str:
    text = _tag("question", request.question)
    if request.clarification:
        text += "\n" + _tag("clarification", request.clarification)
    return text


def _result_input(query: GeneratedSQL, result: SqlResult) -> str:
    shown = f"{len(result.rows)} of {result.row_count} rows"
    lines = [f"columns: {json.dumps(result.columns)}", f"{shown}, truncated: {result.truncated}"]
    lines += [f"[{i}] {json.dumps(row, ensure_ascii=False)}" for i, row in enumerate(result.rows)]
    assumptions = "\n".join(f"- {a}" for a in query.assumptions) or "none"
    return "\n".join(
        [
            _tag("sql", result.sql),
            _tag("assumptions", assumptions),
            _tag("result", "\n".join(lines)),
        ]
    )


def _describe(failure: SqlError | GuardViolation) -> str:
    if isinstance(failure, GuardViolation):
        return f"blocked by the SQL guard ({failure.rule}): {failure.message}"
    return f"database error ({failure.kind}): {failure.message}"


def _ms(deps: Deps, started: float) -> float:
    return round((deps.clock() - started) * 1000, 3)
