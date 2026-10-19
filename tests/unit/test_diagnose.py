"""diagnose.py without a database: filter splitting, and the diagnostic with a fake runner."""

import pytest

from assistant.diagnose import MAX_FILTERS, diagnose, split_filters
from assistant.guard import GuardConfig, GuardedSQL
from assistant.schemas import SqlError, SqlResult


def rows(n: int, sql: str = "SELECT 1") -> SqlResult:
    return SqlResult(sql=sql, columns=["n"], rows=[[1]] * n, row_count=n, truncated=False, db_ms=1)


class FakeRunner:
    """Answers by which filter is missing from the SQL; records what it was asked to run."""

    def __init__(self, answers: dict[str, SqlResult | SqlError]) -> None:
        self.answers, self.calls = answers, []

    def __call__(self, guarded: GuardedSQL) -> SqlResult | SqlError:
        self.calls.append(guarded.sql)
        missing = [f for f in self.answers if f not in guarded.sql]
        assert len(missing) == 1, guarded.sql
        return self.answers[missing[0]]


def test_each_and_condition_is_dropped_once() -> None:
    sql = "SELECT a FROM t WHERE x = 1 AND y = 2 AND z = 3 LIMIT 10"
    assert split_filters(sql) == [
        ("x = 1", "SELECT a FROM t WHERE y = 2 AND z = 3 LIMIT 10"),
        ("y = 2", "SELECT a FROM t WHERE x = 1 AND z = 3 LIMIT 10"),
        ("z = 3", "SELECT a FROM t WHERE x = 1 AND y = 2 LIMIT 10"),
    ]


def test_or_and_parenthesised_groups_stay_one_filter() -> None:
    sql = "SELECT a FROM t WHERE x = 1 AND (y = 2 OR z = 3) AND (u = 4 AND v = 5)"
    assert [f for f, _ in split_filters(sql)] == ["x = 1", "y = 2 OR z = 3", "u = 4 AND v = 5"]


def test_a_single_condition_drops_the_where_clause() -> None:
    assert split_filters("SELECT a FROM t WHERE x = 1") == [("x = 1", "SELECT a FROM t")]


def test_outer_parentheses_are_ignored() -> None:
    assert [f for f, _ in split_filters("SELECT a FROM t WHERE (x = 1 AND y = 2)")] == [
        "x = 1",
        "y = 2",
    ]


def test_having_conditions_are_split_too() -> None:
    sql = "SELECT a, COUNT(*) FROM t WHERE x = 1 GROUP BY a HAVING COUNT(*) > 5"
    assert split_filters(sql) == [
        ("x = 1", "SELECT a, COUNT(*) FROM t GROUP BY a HAVING COUNT(*) > 5"),
        ("COUNT(*) > 5", "SELECT a, COUNT(*) FROM t WHERE x = 1 GROUP BY a"),
    ]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT a FROM t",
        "SELECT a FROM t WHERE x = 1 UNION SELECT b FROM u WHERE y = 2",
        "SELECT a FROM (SELECT a FROM t WHERE x = 1) AS s",  # nested filters are not split
        "not sql at all (",
    ],
)
def test_nothing_to_split(sql: str) -> None:
    assert split_filters(sql) == []


def test_finds_the_filter_that_removed_every_row() -> None:
    sql = "SELECT cart_id FROM shopping_carts WHERE status = 'completed' AND plant_id = 99"
    run = FakeRunner({"status = 'completed'": rows(0), "plant_id = 99": rows(40)})
    result = diagnose(sql, run)
    assert [(c.filter, c.rows_without) for c in result.checks] == [
        ("status = 'completed'", 0),
        ("plant_id = 99", 40),
    ]
    assert result.summary.startswith("The filter plant_id = 99 removed every row")


def test_every_variant_goes_through_the_guard() -> None:
    sql = "SELECT cart_id FROM shopping_carts WHERE status = 'completed' AND plant_id = 99"
    run = FakeRunner({"status = 'completed'": rows(0), "plant_id = 99": rows(1)})
    diagnose(sql, run)
    assert all(call.endswith("LIMIT 1000") for call in run.calls)  # the guard's forced LIMIT


def test_several_filters_can_each_explain_it() -> None:
    sql = "SELECT cart_id FROM shopping_carts WHERE plant_id = 99 AND status = 'lost'"
    run = FakeRunner({"plant_id = 99": rows(3), "status = 'lost'": rows(5)})
    assert diagnose(sql, run).summary.startswith("Dropping any one of these filters returns rows")


def test_no_single_filter_explains_it() -> None:
    sql = "SELECT cart_id FROM shopping_carts WHERE plant_id = 99 AND status = 'lost'"
    run = FakeRunner({"plant_id = 99": rows(0), "status = 'lost'": rows(0)})
    assert diagnose(sql, run).summary.startswith("No single filter explains it")


def test_no_filter_at_all() -> None:
    result = diagnose("SELECT cart_id FROM shopping_carts", FakeRunner({}))
    assert result.checks == [] and "no top-level filter" in result.summary


def test_failed_and_blocked_variants_are_reported_as_unchecked() -> None:
    timeout = SqlError(kind="timeout", message="cancelled", sql="...")
    sql = "SELECT cart_id FROM shopping_carts WHERE plant_id = 99 AND status = 'lost'"
    run = FakeRunner({"plant_id = 99": timeout, "status = 'lost'": rows(2)})
    result = diagnose(sql, run)
    assert [c.filter for c in result.checks] == ["status = 'lost'"]
    assert result.summary.endswith("1 filter(s) could not be checked.")

    blocked = "SELECT cart_id FROM shopping_carts WHERE plant_id = 99 AND status = 'lost'"
    result = diagnose(blocked, FakeRunner({}), guard_config=GuardConfig(tables=frozenset()))
    assert result.checks == [] and "2 filter(s) could not be checked" in result.summary


def test_at_most_max_filters_are_run() -> None:
    conditions = [f"plant_id <> {i}" for i in range(MAX_FILTERS + 3)]
    sql = "SELECT cart_id FROM shopping_carts WHERE " + " AND ".join(conditions)
    calls = []
    result = diagnose(sql, lambda g: calls.append(g) or rows(0))
    assert len(calls) == MAX_FILTERS and len(result.checks) == MAX_FILTERS
    assert "3 filter(s) could not be checked" in result.summary
