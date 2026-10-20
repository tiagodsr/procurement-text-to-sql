"""The one door to the OpenAI API: structured calls and embeddings, each traced with tokens,
latency and cost. The client is always a parameter, so tests pass an offline fake."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import openai
from openai.lib._parsing._responses import type_to_text_format_param  # private: DECISIONS.md
from pydantic import BaseModel, ValidationError

from assistant.config import Pricing, Settings, cost_usd, price_for
from assistant.schemas import Step, TraceEvent
from assistant.trace import Tracer

Clock = Callable[[], float]
Failure = Literal["refusal", "incomplete", "invalid_output"]


class LLMUnavailable(RuntimeError):
    """The API could not be reached or answered with an error (the workflow ends as `error`)."""


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    output: T | None  # validated, or None when failure is set
    failure: Failure | None
    detail: str | None
    event: TraceEvent


def make_client(settings: Settings) -> openai.OpenAI:
    """The only place a real client is built."""
    if settings.openai_api_key is None:
        raise LLMUnavailable("OPENAI_API_KEY is not set")
    key = settings.openai_api_key.get_secret_value()
    return openai.OpenAI(api_key=key, timeout=settings.llm_timeout_s, max_retries=2)


def _elapsed_ms(clock: Clock, started: float) -> float:
    return round((clock() - started) * 1000, 3)  # microseconds; hides float subtraction noise


def _upstream(tracer: Tracer, step: Step, kind: str, model: str, ms: float, exc: Exception):
    reason = f"{type(exc).__name__} {getattr(exc, 'status_code', '')}".strip()
    tracer.record(
        step=step, kind=kind, model=model, latency_ms=ms, status="upstream_error", error=reason
    )
    return LLMUnavailable(f"{model}: {reason}")


def _read_output[T: BaseModel](response: Any, output_type: type[T]):
    for item in response.output:
        for content in getattr(item, "content", None) or []:
            if content.type == "refusal":
                return None, "refusal", content.refusal
    if response.status == "incomplete":
        reason = response.incomplete_details.reason if response.incomplete_details else None
        return None, "incomplete", f"incomplete: {reason}"
    try:
        return output_type.model_validate_json(response.output_text), None, None
    except ValidationError as exc:
        first = exc.errors()[0]
        return None, "invalid_output", f"{exc.error_count()} error(s): {first['msg']}"


def parse[T: BaseModel](
    client: Any,
    *,
    step: Step,
    model: str,
    instructions: str,  # trusted: built from code and knowledge files
    input: str,  # untrusted: the question, tool and database output
    output_type: type[T],
    pricing: Pricing,
    tracer: Tracer,
    max_output_tokens: int | None = None,
    clock: Clock = time.perf_counter,
) -> LLMResult[T]:
    price = price_for(pricing, model, "chat")
    options = {} if max_output_tokens is None else {"max_output_tokens": max_output_tokens}
    started = clock()
    try:
        response = client.responses.create(
            model=model,
            instructions=instructions,
            input=input,
            text={"format": type_to_text_format_param(output_type)},
            store=False,
            **options,
        )
    except openai.APIError as exc:
        raise _upstream(tracer, step, "llm", model, _elapsed_ms(clock, started), exc) from exc
    latency_ms = _elapsed_ms(clock, started)
    output, failure, detail = _read_output(response, output_type)
    usage = response.usage
    tokens = {
        "input_tokens": usage.input_tokens if usage else 0,
        "cached_input_tokens": usage.input_tokens_details.cached_tokens or 0 if usage else 0,
        "output_tokens": usage.output_tokens if usage else 0,
    }
    event = tracer.record(
        step=step,
        kind="llm",
        model=model,
        **tokens,
        reasoning_tokens=usage.output_tokens_details.reasoning_tokens or 0 if usage else 0,
        cost_usd=float(cost_usd(price, **tokens)),
        latency_ms=latency_ms,
        status=failure or "ok",
        error=detail if usage else "usage missing from the response",
    )
    return LLMResult(output=output, failure=failure, detail=detail, event=event)


def embed(
    client: Any,
    texts: Sequence[str],
    *,
    model: str,
    pricing: Pricing,
    tracer: Tracer,
    step: Step = "embed",
    batch_size: int = 100,
    clock: Clock = time.perf_counter,
) -> list[list[float]]:
    price = price_for(pricing, model, "embedding")
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = list(texts[start : start + batch_size])
        started = clock()
        try:
            response = client.embeddings.create(model=model, input=batch)
        except openai.APIError as exc:
            raise _upstream(
                tracer, step, "embedding", model, _elapsed_ms(clock, started), exc
            ) from exc
        data = sorted(response.data, key=lambda d: d.index)
        complete = [d.index for d in data] == list(range(len(batch)))
        tokens = response.usage.prompt_tokens
        tracer.record(
            step=step,
            kind="embedding",
            model=model,
            input_tokens=tokens,
            cost_usd=float(cost_usd(price, input_tokens=tokens)),
            latency_ms=_elapsed_ms(clock, started),
            status="ok" if complete else "invalid_output",
            detail={"texts": len(batch)},
        )
        if not complete:
            raise LLMUnavailable(f"{model}: {len(data)} vectors for {len(batch)} texts")
        vectors.extend(d.embedding for d in data)
    return vectors
