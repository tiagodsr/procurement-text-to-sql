"""Spec for assistant.ui, offline: Streamlit's AppTest runs the page against a fake API."""

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from assistant.config import REPO_ROOT
from assistant.schemas import (
    Clarification,
    ErrorInfo,
    FilterCheck,
    FinalAnswer,
    NoDataDiagnostic,
    RequestUsage,
    RetrievedDoc,
    SqlResult,
    TraceEvent,
)

PAGE = str(REPO_ROOT / "src" / "assistant" / "ui.py")
QUESTION = "Which supplier had the highest spend?"
SQL = "SELECT name, spend_eur FROM v_supplier_summary ORDER BY spend_eur DESC LIMIT 2"
HEALTHY = {"status": "ok", "llm_configured": True, "database": True}
USAGE = RequestUsage(
    cost_usd=0.0031,
    latency_ms=2500.0,
    steps=[
        TraceEvent(
            request_id="r1", step="intake", kind="llm", model="gpt-4.1-mini",
            input_tokens=900, output_tokens=40, cost_usd=0.0004, latency_ms=700.0, status="ok",
        ),
        TraceEvent(
            request_id="r1", step="request", kind="request", latency_ms=2500.0, status="answered"
        ),
    ],
)  # fmt: skip
DOC = RetrievedDoc(id="table:suppliers", doc_type="table", score=0.61, source="knowledge/t.md#s")


def result(*rows: list) -> SqlResult:
    return SqlResult(
        sql=SQL, columns=["name", "spend_eur"], rows=list(rows), row_count=len(rows),
        truncated=False, db_ms=1.0,
    )  # fmt: skip


def final(status: str, answer: str, **fields) -> FinalAnswer:
    return FinalAnswer(
        request_id="r1", question=QUESTION, variant="workflow", status=status, answer=answer,
        evidence=[DOC], usage=USAGE, **fields,
    )  # fmt: skip


ANSWERED = final(
    "answered", "Alpha GmbH spent the most.", sql=SQL, cited_rows=[0],
    result=result(["Alpha GmbH", 10.0], ["Beta SAS", 5.0]),
)  # fmt: skip
NO_DATA = final(
    "no_data", "No rows match the question. The filter year = 2030 removed every row.",
    sql=SQL, result=result(),
    diagnostic=NoDataDiagnostic(
        summary="x", checks=[FilterCheck(filter="year = 2030", rows_without=12)]
    ),
)  # fmt: skip
ASK_WHICH = final(
    "needs_clarification", "Which Velria do you mean?",
    clarification=Clarification(
        question="Which Velria do you mean?", reason="entity_ambiguity",
        options=["Velria Tools SAS", "Velria America Inc."],
    ),
)  # fmt: skip
BROKEN = final(
    "error", "I could not answer this question (down).",
    error=ErrorInfo(kind="upstream", message="down"),
)  # fmt: skip


class FakeApi:
    """Stands in for the page's HTTP calls: a health body, then one reply per POST /v1/query
    (a FinalAnswer, or an HTTP error code)."""

    def __init__(self, monkeypatch, health=HEALTHY, replies=()):
        self.health, self.replies, self.posted = health, list(replies), []
        monkeypatch.setattr(httpx, "get", self.get)
        monkeypatch.setattr(httpx, "post", self.post)

    def get(self, url, **kwargs):
        if self.health is None:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json=self.health, request=httpx.Request("GET", url))

    def post(self, url, json, **kwargs):
        self.posted.append(json)
        reply = self.replies.pop(0)
        request = httpx.Request("POST", url)
        if isinstance(reply, int):
            body = {"detail": "OPENAI_API_KEY is not set on the server"}
            return httpx.Response(reply, json=body, request=request)
        if isinstance(reply, str):
            return httpx.Response(200, text=reply, request=request)
        return httpx.Response(200, json=reply.model_dump(mode="json"), request=request)


def open_page() -> AppTest:
    return AppTest.from_file(PAGE, default_timeout=30).run()


def ask(page: AppTest, question: str = QUESTION) -> AppTest:
    page.text_input[0].input(question)
    return page.button[0].click().run()


def texts(elements) -> list[str]:
    return [element.value for element in elements]


# --- warnings ---------------------------------------------------------------------------------


def test_a_warning_and_a_disabled_button_when_the_api_is_down(monkeypatch):
    FakeApi(monkeypatch, health=None)
    page = open_page()
    assert "not reachable" in page.warning[0].value
    assert page.button[0].disabled


def test_a_warning_and_a_disabled_button_when_the_key_is_missing(monkeypatch):
    FakeApi(monkeypatch, {**HEALTHY, "llm_configured": False})
    page = open_page()
    assert "OPENAI_API_KEY" in page.warning[0].value
    assert page.button[0].disabled


def test_a_warning_when_the_database_is_down(monkeypatch):
    FakeApi(monkeypatch, {**HEALTHY, "database": False})
    assert "database" in open_page().warning[0].value


def test_no_warning_when_everything_is_up(monkeypatch):
    FakeApi(monkeypatch)
    page = open_page()
    assert not page.warning
    assert not page.button[0].disabled


# --- answers ----------------------------------------------------------------------------------


def test_an_answer_shows_sql_cited_rows_evidence_trace_cost_and_latency(monkeypatch):
    api = FakeApi(monkeypatch, replies=[ANSWERED])
    page = ask(open_page())
    assert api.posted == [{"question": QUESTION, "clarification": None}]
    assert page.subheader[0].value == "Answered"
    assert "Alpha GmbH spent the most." in texts(page.markdown)
    assert page.code[0].value == SQL
    rows, evidence, trace = (d.value for d in page.dataframe)
    assert rows["cited"].tolist() == ["✓", ""]
    assert rows["name"].tolist() == ["Alpha GmbH", "Beta SAS"]
    assert evidence["document"].tolist() == ["table:suppliers"]
    assert trace["step"].tolist() == ["intake"]  # the request event is the total, not a step
    assert [m.value for m in page.metric] == ["$0.0031", "2.5 s"]


def test_no_data_shows_why_and_no_table_of_empty_rows(monkeypatch):
    FakeApi(monkeypatch, replies=[NO_DATA])
    page = ask(open_page())
    assert page.subheader[0].value == "No data"
    assert not [m for m in texts(page.markdown) if m.startswith("**Rows**")]
    assert len(page.dataframe) == 3  # the filter check, the evidence and the trace: no rows
    assert page.dataframe[0].value["filter"].tolist() == ["year = 2030"]


def test_a_clarification_is_answered_with_a_button(monkeypatch):
    api = FakeApi(monkeypatch, replies=[ASK_WHICH, ANSWERED])
    page = ask(open_page())
    assert [b.label for b in page.button[1:]] == ["Velria Tools SAS", "Velria America Inc."]
    assert not page.code  # no SQL was written

    page = page.button[2].click().run()
    assert api.posted[1] == {"question": QUESTION, "clarification": "Velria America Inc."}
    assert page.subheader[0].value == "Answered"
    assert len(page.button) == 1  # the options are gone


# --- problems ---------------------------------------------------------------------------------


def test_an_api_refusal_is_shown_as_an_error_with_its_reason(monkeypatch):
    FakeApi(monkeypatch, replies=[503])
    page = ask(open_page())
    assert "503" in page.error[0].value
    assert "OPENAI_API_KEY" in page.error[0].value
    assert not page.subheader


def test_an_error_answer_is_shown_as_an_error(monkeypatch):
    FakeApi(monkeypatch, replies=[BROKEN])
    page = ask(open_page())
    assert page.subheader[0].value == "Error"
    assert "could not answer" in page.error[0].value


def test_a_blank_question_is_not_sent(monkeypatch):
    api = FakeApi(monkeypatch)
    ask(open_page(), "   ")
    assert api.posted == []


@pytest.mark.parametrize("body", ["not json", '{"status": "answered"}'])
def test_a_body_that_is_not_an_answer_is_reported(monkeypatch, body):
    FakeApi(monkeypatch, replies=[body])
    page = ask(open_page())
    assert "does not understand" in page.error[0].value
