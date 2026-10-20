"""The Streamlit page: asks the API over HTTP and shows the whole answer (status, evidence, SQL,
cited rows, trace, cost, latency). A clarification is answered with buttons.

Run it:  uv run streamlit run src/assistant/ui.py   (the API must be up; API_URL points at it)
"""

from __future__ import annotations

import os

import httpx
import streamlit as st
from pydantic import ValidationError

from assistant.schemas import MAX_QUESTION_CHARS, FinalAnswer

API_URL = os.environ.get("API_URL", "http://127.0.0.1:8000").rstrip("/")
HEALTH_TIMEOUT_S = 2
QUERY_TIMEOUT_S = 120  # up to five LLM calls, each with its own 30 s limit

STATUS_LABELS = {
    "answered": "Answered",
    "needs_clarification": "Needs clarification",
    "no_data": "No data",
    "out_of_scope": "Out of scope",
    "refused": "Refused",
    "error": "Error",
}


class ApiProblem(Exception):
    """The API could not give an answer; the message is shown to the user."""


def check_health() -> tuple[list[str], bool]:
    """The warnings to show, and whether a question can be asked."""
    try:
        response = httpx.get(f"{API_URL}/health", timeout=HEALTH_TIMEOUT_S)
        response.raise_for_status()
        health = response.json()
    except (httpx.HTTPError, ValueError):
        return [f"The API is not reachable at {API_URL}. Start it with `docker compose up`."], False
    warnings = []
    if not health.get("llm_configured"):
        warnings.append("OPENAI_API_KEY is not set on the API, so questions cannot be answered.")
    if not health.get("database"):
        warnings.append("The API cannot reach the database.")
    return warnings, not warnings


def ask_api(question: str, clarification: str | None) -> FinalAnswer:
    payload = {"question": question, "clarification": clarification}
    try:
        response = httpx.post(f"{API_URL}/v1/query", json=payload, timeout=QUERY_TIMEOUT_S)
    except httpx.HTTPError:
        raise ApiProblem(f"The API is not reachable at {API_URL}.") from None
    if response.status_code != 200:
        detail = response.json().get("detail") if response.content else None
        text = detail if isinstance(detail, str) else "the request was not accepted"
        raise ApiProblem(f"The API answered {response.status_code}: {text}")
    try:
        return FinalAnswer.model_validate(response.json())
    except (ValidationError, ValueError):
        raise ApiProblem("The API answered with a body this page does not understand.") from None


def queue(question: str, clarification: str | None = None) -> None:
    """Button callback: the next run asks the API (callbacks run before the page is drawn)."""
    st.session_state.pending = (question, clarification)


def run_pending() -> None:
    pending = st.session_state.pop("pending", None)
    if pending is None:
        return
    with st.spinner("Working on it..."):
        try:
            st.session_state.final, st.session_state.problem = ask_api(*pending), None
        except ApiProblem as problem:
            st.session_state.final, st.session_state.problem = None, str(problem)


# --- drawing the answer ---------------------------------------------------------------------


def show_answer(final: FinalAnswer, can_ask: bool) -> None:
    st.subheader(STATUS_LABELS[final.status])
    if final.status == "error":
        st.error(final.answer)
    else:
        st.write(final.answer)
    if final.clarification:
        for i, option in enumerate(final.clarification.options):
            st.button(
                option,
                key=f"option-{i}",
                on_click=queue,
                args=(final.question, option),
                disabled=not can_ask,
            )
    for text in final.assumptions:
        st.caption(f"Assumption: {text}")
    for text in final.caveats:
        st.caption(f"Caveat: {text}")
    usage = final.usage
    cost, latency = st.columns(2)
    cost.metric("Cost", f"${usage.cost_usd:.4f}")
    latency.metric("Latency", f"{usage.latency_ms / 1000:.1f} s")

    if final.diagnostic:
        st.markdown("**Why there are no rows**")
        st.dataframe(
            [
                {"filter": c.filter, "rows without it": c.rows_without}
                for c in final.diagnostic.checks
            ],
            hide_index=True,
        )
    if final.sql:
        st.markdown("**SQL**")
        st.code(final.sql, language="sql")
    show_rows(final)
    if final.evidence:
        st.markdown("**Evidence** (documents the search found)")
        st.dataframe(
            [{"document": d.id, "score": d.score, "source": d.source} for d in final.evidence],
            hide_index=True,
        )
    st.markdown("**Trace**")
    st.dataframe(
        [
            {
                "step": e.step,
                "status": e.status,
                "model": e.model or "",
                "tokens in": e.input_tokens,
                "tokens out": e.output_tokens,
                "cost (USD)": e.cost_usd,
                "ms": e.latency_ms,
            }
            for e in usage.steps
            if e.kind != "request"
        ],
        hide_index=True,
    )


def show_rows(final: FinalAnswer) -> None:
    """The result rows, with the cited ones marked. Empty results (no_data) show no table."""
    result = final.result
    if result is None or result.empty:
        return
    shown = f"{len(result.rows)} of {result.row_count} rows"
    st.markdown(f"**Rows** ({shown}; ✓ = cited in the answer)")
    st.dataframe(
        [
            {
                "cited": "✓" if i in final.cited_rows else "",
                **dict(zip(result.columns, row, strict=True)),
            }
            for i, row in enumerate(result.rows)
        ],
        hide_index=True,
    )


def main() -> None:
    st.set_page_config(page_title="Procurement assistant", page_icon="🧾")
    st.title("Procurement assistant")
    st.caption("Ask about suppliers, contracts, the catalog and purchases (synthetic data).")
    warnings, can_ask = check_health()
    for text in warnings:
        st.warning(text)

    with st.form("ask"):
        question = st.text_input(
            "Question",
            max_chars=MAX_QUESTION_CHARS,
            placeholder="Which five suppliers had the highest spend in 2025?",
        )
        asked = st.form_submit_button("Ask", disabled=not can_ask)
    if asked and question.strip():
        queue(question)
    run_pending()

    if problem := st.session_state.get("problem"):
        st.error(problem)
    if final := st.session_state.get("final"):
        show_answer(final, can_ask)


main()
