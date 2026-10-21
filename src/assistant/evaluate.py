"""Evaluation: the golden set (evals/cases.json) through the workflow and the baseline, scored on
status, execution accuracy, unsafe SQL executed, cost and latency. It prints a cost estimate and
asks before spending anything.

    make eval                                                            # dev split, both variants
    uv run --env-file .env python -m assistant.evaluate --max-cases 2     # a cheap smoke run
    uv run --env-file .env python -m assistant.evaluate --split holdout   # once, at the end
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from assistant import prompts
from assistant.config import REPO_ROOT, Pricing, cost_usd, load_pricing, load_settings, price_for
from assistant.llm import make_client
from assistant.retrieve import CASES_PATH, load_index
from assistant.schemas import FinalAnswer, JsonScalar, QueryRequest, Variant
from assistant.trace import percentile
from assistant.workflow import MAX_OUTPUT_TOKENS, MAX_REPAIRS, UNSAFE_RULES, Deps, answer

VARIANTS: tuple[Variant, ...] = ("workflow", "baseline")
CHARS_PER_TOKEN = 4  # a rough average for English text; used only for the estimate
RESULT_CHARS = 3000  # a typical result preview given to the explain step; estimate only


def load_cases(split: str, path: Path = CASES_PATH) -> list[dict[str, Any]]:
    return [case for case in json.loads(path.read_text("utf-8")) if case["split"] == split]


def report_path(split: str) -> Path:
    return REPO_ROOT / "evals" / f"report-{split}.md"


# --- execution accuracy -------------------------------------------------------------------------


def _number(value: JsonScalar) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def same(actual: JsonScalar, expected: JsonScalar) -> bool:
    """Numbers within 0.1% (or 0.05, for rounding), text without case, anything else equal."""
    if _number(actual) and _number(expected):
        return math.isclose(actual, expected, rel_tol=1e-3, abs_tol=0.05)
    if isinstance(actual, str) and isinstance(expected, str):
        return actual.strip().casefold() == expected.strip().casefold()
    return type(actual) is type(expected) and actual == expected  # True is not 1


def _contains(row: list[JsonScalar], wanted: list[JsonScalar]) -> bool:
    left = list(row)
    for value in wanted:
        hit = next((i for i, v in enumerate(left) if same(v, value)), None)
        if hit is None:
            return False
        left.pop(hit)
    return True


def matches(rows: list[list[JsonScalar]], expected: list[list[JsonScalar]]) -> bool:
    """Same number of rows, and each expected row inside a different result row: extra columns
    and any row order are fine."""
    if len(rows) != len(expected):
        return False
    left = list(rows)
    for wanted in expected:
        hit = next((i for i, row in enumerate(left) if _contains(row, wanted)), None)
        if hit is None:
            return False
        left.pop(hit)
    return True


# --- scoring ------------------------------------------------------------------------------------


def score(case: dict[str, Any], final: FinalAnswer) -> dict[str, Any]:
    """One report row. `rows_ok` is None for cases without expected rows."""
    steps = final.usage.steps
    rows_ok = None
    if "expected_rows" in case:
        rows_ok = final.result is not None and matches(final.result.rows, case["expected_rows"])
    return {
        "id": case["id"],
        "category": case["category"],
        "variant": final.variant,
        "expected": case["expected_status"],
        "status": final.status,
        "status_ok": final.status == case["expected_status"],
        "rows_ok": rows_ok,
        "unsafe_executed": case["category"] == "unsafe"
        and any(e.step == "execute" and e.status == "ok" for e in steps),
        "write_blocked": any(e.detail.get("rule") in UNSAFE_RULES for e in steps),
        "cost_usd": final.usage.cost_usd,
        "latency_ms": final.usage.latency_ms,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    with_rows = [r["rows_ok"] for r in rows if r["rows_ok"] is not None]
    latencies = [r["latency_ms"] for r in rows]
    cost = sum(r["cost_usd"] for r in rows)
    return {
        "status accuracy": f"{sum(r['status_ok'] for r in rows)}/{len(rows)}",
        "execution accuracy": f"{sum(with_rows)}/{len(with_rows)}",
        "unsafe questions with SQL executed": sum(r["unsafe_executed"] for r in rows),
        "writes blocked by the guard": sum(r["write_blocked"] for r in rows),
        "cost total (USD)": f"{cost:.4f}",
        "cost per question (USD)": f"{cost / len(rows):.5f}" if rows else "-",
        "latency p50 (ms)": f"{percentile(latencies, 50) or 0:.0f}",
        "latency p95 (ms)": f"{percentile(latencies, 95) or 0:.0f}",
    }


def _line(cells: list[Any]) -> str:
    return "| " + " | ".join(str(cell) for cell in cells) + " |"


def report(split: str, model: str, rows: list[dict[str, Any]], variants: tuple[str, ...]) -> str:
    def mark(ok: bool | None) -> str:
        return "-" if ok is None else ("yes" if ok else "no")

    cases = list(dict.fromkeys(r["id"] for r in rows))
    by = {(r["id"], r["variant"]): r for r in rows}
    summaries = {v: summarize([r for r in rows if r["variant"] == v]) for v in variants}
    lines = [
        f"# Evaluation: {split} split",
        "",
        f"{len(cases)} questions, model `{model}`. Written by `python -m assistant.evaluate`.",
        "",
        _line(["Metric", *variants]),
        _line(["---"] * (len(variants) + 1)),
    ]
    lines += [_line([m, *(summaries[v][m] for v in variants)]) for m in summaries[variants[0]]]
    headers = [h for v in variants for h in (f"{v} status", f"{v} rows")]
    lines += ["", _line(["Case", "Category", "Expected", *headers])]
    lines.append(_line(["---"] * (len(headers) + 3)))
    for case_id in cases:
        first = by[(case_id, variants[0])]
        cells = []
        for v in variants:
            r = by[(case_id, v)]
            cells += [f"{r['status']} ({mark(r['status_ok'])})", mark(r["rows_ok"])]
        lines.append(_line([case_id, first["category"], first["expected"], *cells]))
    return "\n".join(lines) + "\n"


# --- cost estimate and confirmation -------------------------------------------------------------


def estimate_usd(
    cases: list[dict], variants: tuple[str, ...], pricing: Pricing, model: str
) -> float:
    """A ceiling without repairs: every planned call at its output limit, the SQL step with every
    table doc and example. Input tokens are counted as characters / 4. Embeddings cost < 0.0001."""
    price = price_for(pricing, model, "chat")
    every_doc = prompts.sql(prompts.knowledge("tables.md"), prompts.knowledge("examples.md"))
    calls = {
        "workflow": [
            (prompts.intake(), 0, "intake"),
            (every_doc, 0, "generate_sql"),
            (prompts.explain(), RESULT_CHARS, "explain"),
        ],
        "baseline": [(prompts.baseline(), 0, "generate_sql")],
    }
    total = Decimal(0)
    for case in cases:
        for variant in variants:
            for instructions, extra, step in calls[variant]:
                chars = len(instructions) + len(case["question"]) + extra
                total += cost_usd(
                    price,
                    input_tokens=chars // CHARS_PER_TOKEN,
                    output_tokens=MAX_OUTPUT_TOKENS[step],
                )
    return float(total)


def confirm(estimate: float, runs: int, ask: Callable[[str], str] | None = None) -> bool:
    question = (
        f"{runs} runs, estimated at most ${estimate:.4f} (each repair adds one SQL call, up to"
        f" {MAX_REPAIRS} per question). Run? [y/N] "
    )
    try:
        return (ask or input)(question).strip().casefold() == "y"
    except EOFError:
        return False


# --- run ----------------------------------------------------------------------------------------


def run_cases(cases: list[dict], variants: tuple[Variant, ...], deps: Deps) -> list[dict]:
    rows = []
    for case in cases:
        for variant in variants:
            final = answer(QueryRequest(question=case["question"]), deps, variant=variant)
            row = score(case, final)
            print(
                f"{case['id']:22s} {variant:9s} {final.status:20s} status {mark_ok(row)}"
                f" ${row['cost_usd']:.5f} {row['latency_ms']:>7.0f} ms",
                flush=True,
            )
            rows.append(row)
    return rows


def mark_ok(row: dict[str, Any]) -> str:
    rows = "" if row["rows_ok"] is None else f", rows {'ok' if row['rows_ok'] else 'WRONG'}"
    return ("ok" if row["status_ok"] else "WRONG") + rows


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", choices=["dev", "holdout"], default="dev")
    parser.add_argument("--variant", choices=[*VARIANTS, "both"], default="both")
    parser.add_argument("--max-cases", type=int, help="run only the first N cases (no report)")
    args = parser.parse_args(argv)
    settings = load_settings()
    if not settings.llm_configured:
        sys.exit("OPENAI_API_KEY is not set: use `uv run --env-file .env ...` (see .env.example)")
    path = report_path(args.split)
    if args.split == "holdout" and (path.exists() or args.max_cases is not None):
        sys.exit(f"The holdout runs once, whole: {path.relative_to(REPO_ROOT)} must not exist.")
    cases = load_cases(args.split)[: args.max_cases]
    variants = VARIANTS if args.variant == "both" else (args.variant,)
    pricing = load_pricing(settings.pricing_path)
    estimate = estimate_usd(cases, variants, pricing, settings.llm_model)
    if not confirm(estimate, len(cases) * len(variants)):
        sys.exit("Nothing was run.")
    deps = Deps(
        client=make_client(settings),
        settings=settings,
        pricing=pricing,
        index=load_index(settings.embedding_model),
    )
    rows = run_cases(cases, variants, deps)
    text = report(args.split, settings.llm_model, rows, variants)
    print("\n" + text)
    if args.max_cases is None:
        path.write_text(text, encoding="utf-8")
        print(f"Written to {path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
