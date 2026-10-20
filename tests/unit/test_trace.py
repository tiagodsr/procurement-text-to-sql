"""Spec for assistant.trace: JSONL events, per-request usage and p50/p95."""

import random
import statistics

import pytest

from assistant.trace import Tracer, percentile, read_events, summarize


def llm(tracer: Tracer, latency_ms: float, cost: float = 0.0, **extra) -> None:
    fields = {"step": "intake", "kind": "llm", "model": "m", "status": "ok"}
    tracer.record(latency_ms=latency_ms, cost_usd=cost, **fields, **extra)


# --- percentile -------------------------------------------------------------------------------


def test_percentile_hand_computed_values():
    assert percentile([10, 20, 30, 40], 50) == 25
    assert percentile([40, 10, 30, 20], 95) == pytest.approx(38.5)  # order does not matter
    assert percentile([100, 200, 400], 95) == pytest.approx(380)


def test_percentile_matches_the_standard_library():
    rng = random.Random(42)
    values = [rng.uniform(50, 5_000) for _ in range(50)]
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    assert percentile(values, 50) == pytest.approx(cuts[49])
    assert percentile(values, 95) == pytest.approx(cuts[94])


# --- Tracer -----------------------------------------------------------------------------------


def test_events_carry_the_request_id_and_variant():
    tracer = Tracer(None, request_id="req-1", variant="baseline")
    llm(tracer, 10)
    assert tracer.events[0].request_id == "req-1"
    assert tracer.events[0].variant == "baseline"


def test_each_event_is_one_json_line_and_reads_back_equal(tmp_path):
    tracer = Tracer(tmp_path / "traces")
    llm(tracer, 10, cost=0.001)
    tracer.record(step="execute", kind="tool", latency_ms=4, status="ok", sql="SELECT 1")
    [path] = (tmp_path / "traces").glob("*.jsonl")
    assert path.name == f"{tracer.events[0].ts:%Y-%m-%d}.jsonl"
    assert len(path.read_text().splitlines()) == 2
    assert read_events(path) == tracer.events


def test_usage_totals_one_request():
    tracer = Tracer(None)
    llm(tracer, 100, cost=0.002, input_tokens=1000, output_tokens=200)
    llm(tracer, 50, cost=0.001, input_tokens=500, output_tokens=100)
    tracer.record(step="execute", kind="tool", latency_ms=5, status="ok")
    usage = tracer.usage()
    assert (usage.llm_calls, usage.tool_calls) == (2, 1)
    assert (usage.input_tokens, usage.output_tokens) == (1500, 300)
    assert usage.cost_usd == pytest.approx(0.003)
    assert usage.latency_ms == 155  # no request event: sum of the steps
    tracer.record(step="request", kind="request", latency_ms=170, status="answered")
    assert tracer.usage().latency_ms == 170  # wall clock of the whole request
    assert len(tracer.usage().steps) == 4


# --- summarize --------------------------------------------------------------------------------


def test_summary_is_per_request_and_per_step():
    events = []
    for latency, cost in [(100, 0.001), (200, 0.002), (400, 0.004)]:
        tracer = Tracer(None)
        llm(tracer, latency * 0.75, cost=cost)
        tracer.record(step="execute", kind="tool", latency_ms=latency * 0.25, status="ok")
        events += tracer.events
    summary = summarize(events)
    assert summary.requests.n == 3
    assert summary.requests.latency_p50_ms == pytest.approx(200)
    assert summary.requests.latency_p95_ms == pytest.approx(380)
    assert summary.requests.cost_mean_usd == pytest.approx(0.007 / 3)
    assert summary.requests.cost_p95_usd == pytest.approx(0.0038)
    assert set(summary.steps) == {"intake", "execute"}
    assert summary.steps["execute"].latency_p50_ms == pytest.approx(50)


def test_summary_uses_the_request_event_and_never_counts_it_as_a_step():
    tracer = Tracer(None)
    llm(tracer, 100, cost=0.001)
    tracer.record(step="request", kind="request", latency_ms=130, status="answered")
    summary = summarize(tracer.events)
    assert summary.requests.latency_p50_ms == 130
    assert summary.requests.cost_mean_usd == pytest.approx(0.001)
    assert "request" not in summary.steps
