"""`ask "<question>"`: answer one question in the terminal, with evidence, SQL, rows and trace.

Each trace line is one step with its model, tokens, cost and latency. Needs the db and the API key.
"""

from __future__ import annotations

import argparse
import sys

from pydantic import ValidationError

from assistant.config import load_pricing, load_settings
from assistant.llm import make_client
from assistant.schemas import FinalAnswer, QueryRequest
from assistant.workflow import Deps, answer

MAX_ROWS_SHOWN = 20


def render(final: FinalAnswer) -> str:
    lines = [f"[{final.status}] {final.answer}"]
    if final.clarification:
        lines += [f"  - {option}" for option in final.clarification.options]
        lines.append(f'Reply with: ask "{final.question}" --clarification "<your answer>"')
    lines += [f"Assumption: {a}" for a in final.assumptions]
    lines += [f"Caveat: {c}" for c in final.caveats]
    if final.evidence:
        docs = ", ".join(f"{d.id} ({d.score:.2f})" for d in final.evidence)
        lines.append(f"Evidence: {docs}")
    if final.sql:
        lines += ["", "SQL:", final.sql]
    if final.result and final.result.rows:
        result = final.result
        lines += ["", f"Rows ({len(result.rows)} of {result.row_count}, * = cited):"]
        lines.append("      " + " | ".join(result.columns))
        for i, row in enumerate(result.rows[:MAX_ROWS_SHOWN]):
            mark = "*" if i in final.cited_rows else " "
            lines.append(f"  {mark}[{i}] " + " | ".join(str(v) for v in row))
    usage = final.usage
    lines += ["", "Trace:"]
    for e in usage.steps:
        if e.kind == "request":
            continue
        tokens = f"{e.input_tokens:>6} in {e.output_tokens:>5} out" if e.kind != "tool" else ""
        lines.append(
            f"  {e.step:13s} {e.status:8s} {e.model or '':14s} {tokens:20s}"
            f" ${e.cost_usd:.6f} {e.latency_ms:>9.1f} ms"
        )
    lines.append(
        f"Total: {usage.llm_calls} LLM calls, {usage.input_tokens} in / {usage.output_tokens} out"
        f" tokens, ${usage.cost_usd:.6f}, {usage.latency_ms:.0f} ms"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ask", description=__doc__.splitlines()[0])
    parser.add_argument("question")
    parser.add_argument("--clarification", help="your answer to the assistant's question")
    parser.add_argument("--json", action="store_true", help="print the full answer as JSON")
    args = parser.parse_args(argv)
    settings = load_settings()
    if not settings.llm_configured:
        sys.exit(
            "OPENAI_API_KEY is not set: use `uv run --env-file .env ask ...` (see .env.example)"
        )
    try:
        request = QueryRequest(question=args.question, clarification=args.clarification)
    except ValidationError as exc:
        sys.exit(f"invalid request: {exc.errors()[0]['msg']}")
    deps = Deps(
        client=make_client(settings),
        settings=settings,
        pricing=load_pricing(settings.pricing_path),
    )
    final = answer(request, deps)
    print(final.model_dump_json(indent=2) if args.json else render(final))
    sys.exit(1 if final.status == "error" else 0)


if __name__ == "__main__":
    main()
