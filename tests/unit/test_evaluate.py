"""Spec for assistant.evaluate, offline: the golden set's shape, the row matching, the scores of
real FinalAnswers from the fake workflow, the cost estimate and the confirmation before spending."""

from collections import Counter

import pytest

from assistant import evaluate
from assistant.evaluate import estimate_usd, load_cases, matches, report, same, score
from assistant.schemas import GeneratedSQL, IntakeResult
from tests.fake_llm import PRICING
from tests.unit.test_workflow import QUERY, FakeDB, reply, rows, run

DEV, HOLDOUT = load_cases("dev"), load_cases("holdout")
CATEGORIES = {"simple", "join", "trap", "ambiguous", "no_data", "unsafe"}


# --- the golden set ---------------------------------------------------------------------------


def test_sixteen_cases_twelve_for_development_and_four_held_out():
    assert (len(DEV), len(HOLDOUT)) == (12, 4)
    assert len({c["id"] for c in DEV + HOLDOUT}) == 16


def test_development_covers_every_category_twice():
    assert Counter(c["category"] for c in DEV) == dict.fromkeys(CATEGORIES, 2)
    assert {c["category"] for c in HOLDOUT} <= CATEGORIES


def test_answered_cases_have_reference_rows_and_traps_a_second_way():
    for case in DEV + HOLDOUT:
        answered = case["expected_status"] == "answered"
        assert answered == ("reference_sql" in case) == ("expected_rows" in case), case["id"]
        assert (case["category"] == "trap") == ("check_sql" in case), case["id"]


# --- execution accuracy -------------------------------------------------------------------------


def test_numbers_match_within_rounding_and_text_without_case():
    assert same(13544518.61, 13544518.53)
    assert same(54.2, 54.23)
    assert not same(54, 54.23)
    assert not same(623172.73, 573318.91)  # USD summed as if it were EUR
    assert same("venlis office S.R.O.", "Venlis Office s.r.o.")
    assert not same(True, 1)


def test_rows_match_with_extra_columns_in_any_order():
    expected = [["Alpha"], ["Beta"]]
    assert matches([["Beta", 2.0], ["Alpha", 1.0]], expected)
    assert not matches([["Alpha", 1.0]], expected)  # a row missing
    assert not matches([["Alpha"], ["Beta"], ["Gamma"]], expected)  # a row too many
    assert not matches([["Alpha"], ["Alpha"]], expected)  # each expected row needs its own row


# --- scores of real answers from the fake workflow -----------------------------------------------


def case(category="simple", expected="answered", **extra):
    return {"id": "c", "category": category, "expected_status": expected, **extra}


def test_an_answer_with_the_expected_rows_scores_both_ways():
    final, _, _ = run([reply(QUERY)], FakeDB(rows(["Alpha GmbH", 10.0])), variant="baseline")
    row = score(case(expected_rows=[[10.0]]), final)
    assert (row["status_ok"], row["rows_ok"], row["variant"]) == (True, True, "baseline")
    assert row["cost_usd"] == final.usage.cost_usd > 0
    assert not score(case(expected_rows=[[11.0]]), final)["rows_ok"]
    assert score(case(), final)["rows_ok"] is None  # no expected rows: not scored


def test_an_unsafe_question_that_reaches_the_database_is_counted():
    final, _, _ = run([reply(QUERY)], FakeDB(rows(["Alpha GmbH", 10.0])), variant="baseline")
    row = score(case("unsafe", "refused"), final)
    assert (row["status_ok"], row["unsafe_executed"]) == (False, True)


def test_a_write_blocked_by_the_guard_is_counted_and_nothing_runs():
    write = GeneratedSQL(sql="UPDATE suppliers SET status = 'active'", assumptions=[])
    final, _, db = run([reply(write)], FakeDB(), variant="baseline")
    row = score(case("unsafe", "refused"), final)
    assert (row["status_ok"], row["unsafe_executed"], row["write_blocked"]) == (True, False, True)
    assert db.ran == []


def test_the_workflow_refuses_at_intake_without_any_sql():
    unsafe = IntakeResult(route="unsafe", reason="asks to change data", clarification=None)
    final, _, _ = run([reply(unsafe)])
    row = score(case("unsafe", "refused"), final)
    assert (row["status_ok"], row["unsafe_executed"], row["write_blocked"]) == (True, False, False)


def test_the_report_has_a_column_per_variant_and_a_line_per_case():
    good, _, _ = run([reply(QUERY)], FakeDB(rows(["Alpha GmbH", 10.0])), variant="baseline")
    rows_ = [{**score(case(expected_rows=[[10.0]]), good), "variant": v} for v in ("workflow", "x")]
    text = report("dev", "test-chat", rows_, ("workflow", "x"))
    assert "| Metric | workflow | x |" in text
    assert "| execution accuracy | 1/1 | 1/1 |" in text
    assert "| c | simple | answered | answered (yes) | yes | answered (yes) | yes |" in text


# --- cost before spending -------------------------------------------------------------------------


def test_the_estimate_is_priced_and_grows_with_cases_and_steps():
    one = estimate_usd(DEV[:1], ("baseline",), PRICING, "test-chat")
    assert 0 < one < estimate_usd(DEV[:1], ("workflow",), PRICING, "test-chat")
    assert estimate_usd(DEV[:2], ("baseline",), PRICING, "test-chat") > one


@pytest.mark.parametrize("answer", ["n", "", "yes please", EOFError])
def test_anything_but_y_means_no(answer):
    def ask(_: str) -> str:
        if answer is EOFError:
            raise EOFError
        return answer

    assert not evaluate.confirm(0.01, 2, ask)
    assert evaluate.confirm(0.01, 2, lambda _: " Y ")


def test_no_means_no_client_is_ever_built(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("builtins.input", lambda _: "n")
    monkeypatch.setattr(evaluate, "make_client", lambda _: pytest.fail("a client was built"))
    with pytest.raises(SystemExit, match="Nothing was run"):
        evaluate.main(["--max-cases", "1"])


def test_the_holdout_runs_only_once_and_whole(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("asked to spend"))
    with pytest.raises(SystemExit, match="runs once"):
        evaluate.main(["--split", "holdout", "--max-cases", "1"])
    done = tmp_path / "report-holdout.md"
    done.write_text("ran")
    monkeypatch.setattr(evaluate, "report_path", lambda _: done)
    monkeypatch.setattr(evaluate, "REPO_ROOT", tmp_path)
    with pytest.raises(SystemExit, match="runs once"):
        evaluate.main(["--split", "holdout"])
