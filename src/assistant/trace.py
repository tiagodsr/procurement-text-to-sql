"""Trace events as JSONL: record them per request, read them back, summarise p50/p95."""

from __future__ import annotations

import math
import threading
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assistant.schemas import RequestUsage, TraceEvent, Variant

_WRITE_LOCK = threading.Lock()


def new_request_id() -> str:
    return uuid.uuid4().hex


class Tracer:
    """The events of one request, kept in memory and appended to trace_dir/YYYY-MM-DD.jsonl."""

    def __init__(
        self,
        trace_dir: Path | None,
        request_id: str | None = None,
        variant: Variant | None = None,
    ) -> None:
        self.trace_dir = trace_dir  # None: memory only
        self.request_id = request_id or new_request_id()
        self.variant = variant
        self.events: list[TraceEvent] = []

    def record(self, **fields: Any) -> TraceEvent:
        event = TraceEvent(request_id=self.request_id, variant=self.variant, **fields)
        self.events.append(event)
        if self.trace_dir is not None:
            line = event.model_dump_json() + "\n"
            with _WRITE_LOCK:
                self.trace_dir.mkdir(parents=True, exist_ok=True)
                path = self.trace_dir / f"{event.ts:%Y-%m-%d}.jsonl"
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
        return event

    def usage(self) -> RequestUsage:
        steps = [e for e in self.events if e.kind != "request"]
        return RequestUsage(
            llm_calls=sum(e.kind == "llm" for e in steps),
            tool_calls=sum(e.kind == "tool" for e in steps),
            input_tokens=sum(e.input_tokens for e in steps),
            output_tokens=sum(e.output_tokens for e in steps),
            cost_usd=sum(e.cost_usd for e in steps),
            latency_ms=_request_latency(self.events),
            steps=list(self.events),
        )


def read_events(path: Path) -> list[TraceEvent]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [TraceEvent.model_validate_json(line) for line in lines if line.strip()]


def percentile(values: Iterable[float], q: float) -> float | None:
    """Linear interpolation between closest ranks (NumPy default, statistics 'inclusive')."""
    if not 0 <= q <= 100:
        raise ValueError("q must be between 0 and 100")
    ordered = sorted(values)
    if not ordered:
        return None
    pos = (len(ordered) - 1) * q / 100
    low, high = math.floor(pos), math.ceil(pos)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


@dataclass(frozen=True)
class Stats:
    n: int
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    cost_mean_usd: float | None
    cost_p95_usd: float | None


@dataclass(frozen=True)
class Summary:
    requests: Stats
    steps: dict[str, Stats]


def _stats(latencies: list[float], costs: list[float]) -> Stats:
    return Stats(
        n=len(latencies),
        latency_p50_ms=percentile(latencies, 50),
        latency_p95_ms=percentile(latencies, 95),
        cost_mean_usd=sum(costs) / len(costs) if costs else None,
        cost_p95_usd=percentile(costs, 95),
    )


def _request_latency(events: list[TraceEvent]) -> float:
    """The request event's wall clock if there is one, else the sum of the steps."""
    final = [e for e in events if e.kind == "request"]
    if final:
        return final[-1].latency_ms
    return sum(e.latency_ms for e in events)


def summarize(events: Iterable[TraceEvent]) -> Summary:
    by_request: dict[str, list[TraceEvent]] = defaultdict(list)
    by_step: dict[str, list[TraceEvent]] = defaultdict(list)
    for event in events:
        by_request[event.request_id].append(event)
        if event.kind != "request":
            by_step[event.step].append(event)
    requests = _stats(
        [_request_latency(evs) for evs in by_request.values()],
        [sum(e.cost_usd for e in evs if e.kind != "request") for evs in by_request.values()],
    )
    steps = {
        step: _stats([e.latency_ms for e in evs], [e.cost_usd for e in evs])
        for step, evs in sorted(by_step.items())
    }
    return Summary(requests=requests, steps=steps)


def format_summary(summary: Summary) -> str:
    def row(name: str, s: Stats) -> str:
        def num(value: float | None, digits: int) -> str:
            return "-" if value is None else f"{value:.{digits}f}"

        return (
            f"{name:16s} {s.n:>5}  {num(s.latency_p50_ms, 1):>9}  {num(s.latency_p95_ms, 1):>9}"
            f"  {num(s.cost_mean_usd, 6):>10}  {num(s.cost_p95_usd, 6):>10}"
        )

    header = f"{'':16s} {'n':>5}  {'p50 ms':>9}  {'p95 ms':>9}  {'mean USD':>10}  {'p95 USD':>10}"
    lines = [header, row("per request", summary.requests)]
    lines += [row(step, stats) for step, stats in summary.steps.items()]
    return "\n".join(lines)
