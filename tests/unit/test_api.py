"""Spec for assistant.api, offline: a fake LLM and a fake database behind the real routes."""

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from assistant import api
from assistant.config import Settings
from assistant.retrieve import Index, load_docs
from assistant.schemas import (
    Clarification,
    ExplanationDraft,
    FinalAnswer,
    GeneratedSQL,
    IntakeResult,
    SqlResult,
)
from assistant.workflow import Deps
from tests.fake_llm import PRICING, FakeOpenAI, Reply, connection_error

DOCS = load_docs()
INDEX = Index("test-embed", DOCS, [[1.0, float(j)] for j in range(len(DOCS))])
SQL = "SELECT name, spend_eur FROM v_supplier_summary ORDER BY spend_eur DESC LIMIT 2"
QUESTION = {"question": "Which supplier had the highest spend?"}
UNREACHABLE_DB = "postgresql://app_ro:x@127.0.0.1:1/procurement"  # nothing listens on port 1


def reply(output: BaseModel) -> Reply:
    return Reply(output.model_dump_json(), input_tokens=1000, output_tokens=100, seconds=0.5)


def to_sql(sql: str) -> SqlResult:
    rows = [["Alpha GmbH", 10.0], ["Beta SAS", 5.0]]
    return SqlResult(
        sql=sql, columns=["name", "spend_eur"], rows=rows, row_count=2, truncated=False, db_ms=1.0
    )


def client_for(tmp_path, replies: list[Reply], *, key=True, database_url: str | None = None):
    """A TestClient whose workflow uses the fake LLM and a fake database."""
    settings = Settings(
        openai_api_key="sk-test" if key else None,
        llm_model="test-chat",
        embedding_model="test-embed",
        trace_dir=tmp_path,
        **({"database_url": database_url} if database_url else {}),
    )
    fake = FakeOpenAI(replies)
    deps = Deps(
        client=fake,
        settings=settings,
        pricing=PRICING,
        index=INDEX,
        run_sql=lambda guarded: to_sql(guarded.sql),
        clock=fake.clock,
    )
    app = api.app
    app.dependency_overrides[api.get_settings] = lambda: settings
    app.dependency_overrides[api.get_deps] = lambda: deps
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_overrides():
    yield
    api.app.dependency_overrides.clear()


ROUTE = IntakeResult(route="sql", reason="clear question", clarification=None)
ANSWER = [
    reply(ROUTE),
    reply(GeneratedSQL(sql=SQL, assumptions=["All purchase history."])),
    reply(ExplanationDraft(answer="Alpha GmbH spent the most.", cited_rows=[0], caveats=[])),
]


# --- POST /v1/query ---------------------------------------------------------------------------


def test_a_question_returns_the_answer_with_evidence_sql_rows_trace_cost_and_latency(tmp_path):
    response = client_for(tmp_path, ANSWER).post("/v1/query", json=QUESTION)
    assert response.status_code == 200
    body = response.json()
    final = FinalAnswer.model_validate(body)  # the body is the FinalAnswer contract
    assert final.status == "answered"
    assert final.answer == "Alpha GmbH spent the most."
    assert final.sql == final.result.sql and final.sql.startswith("SELECT name, spend_eur")
    assert final.cited_rows == [0]
    assert len(final.evidence) == 8 and final.evidence[0].score > 0
    assert [e["step"] for e in body["usage"]["steps"]][-1] == "request"
    assert body["usage"]["cost_usd"] > 0
    assert body["usage"]["latency_ms"] == 1500.0


def test_a_clarification_comes_back_with_its_options_and_no_rows(tmp_path):
    clarification = Clarification(
        question="Which Velria do you mean?",
        reason="entity_ambiguity",
        options=["Velria Tools SAS", "Velria America Inc.", "Both"],
    )
    route = IntakeResult(route="clarify", reason="two entities", clarification=clarification)
    body = client_for(tmp_path, [reply(route)]).post("/v1/query", json=QUESTION).json()
    assert body["status"] == "needs_clarification"
    assert body["clarification"]["options"] == ["Velria Tools SAS", "Velria America Inc.", "Both"]
    assert body["result"] is None and body["sql"] is None


def test_the_clarification_reaches_the_workflow_as_data(tmp_path):
    client = client_for(tmp_path, ANSWER)
    reply_to_ask = {**QUESTION, "clarification": "Both"}
    assert client.post("/v1/query", json=reply_to_ask).json()["status"] == "answered"
    fake = api.app.dependency_overrides[api.get_deps]().client
    assert "<clarification>\nBoth\n</clarification>" in fake.calls[0][1]["input"]


def test_a_failing_service_is_a_200_with_status_error_and_the_cost_so_far(tmp_path):
    down = Reply(error=connection_error())
    body = client_for(tmp_path, [down]).post("/v1/query", json=QUESTION).json()
    assert body["status"] == "error"
    assert body["error"]["kind"] == "upstream"
    assert body["usage"]["steps"][0]["status"] == "upstream_error"


def test_a_missing_key_is_503_and_nothing_is_called(tmp_path):
    client = client_for(tmp_path, ANSWER, key=False)
    del api.app.dependency_overrides[api.get_deps]  # the real dependency decides
    response = client.post("/v1/query", json=QUESTION)
    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "   "},
        {"question": "x" * 501},
        {"question": "ok", "clarification": "y" * 201},
        {"question": "ok", "sql": "DROP TABLE suppliers"},
        {},
    ],
)
def test_an_invalid_request_is_422_and_nothing_is_called(tmp_path, payload):
    client = client_for(tmp_path, ANSWER)
    assert client.post("/v1/query", json=payload).status_code == 422
    assert api.app.dependency_overrides[api.get_deps]().client.calls == []


# --- GET /health ------------------------------------------------------------------------------


def test_health_says_what_the_questions_need(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "database_answers", lambda settings: True)
    body = client_for(tmp_path, []).get("/health").json()
    assert body == {"status": "ok", "llm_configured": True, "database": True}


def test_health_still_answers_without_a_key_or_a_database(tmp_path):
    client = client_for(tmp_path, [], key=False, database_url=UNREACHABLE_DB)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "llm_configured": False, "database": False}


def test_the_docs_page_and_the_schema_list_both_routes(tmp_path):
    client = client_for(tmp_path, [])
    assert client.get("/docs").status_code == 200
    assert set(client.get("/openapi.json").json()["paths"]) == {"/health", "/v1/query"}
