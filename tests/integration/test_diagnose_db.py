"""diagnose.py on real queries, as app_ro: it must name the filter that removed the rows."""

import pytest

from assistant.diagnose import diagnose
from assistant.executor import execute
from assistant.guard import GuardedSQL, check
from assistant.schemas import SqlResult

pytestmark = pytest.mark.integration


def run_guarded(sql: str) -> tuple[str, SqlResult]:
    guarded = check(sql)
    assert isinstance(guarded, GuardedSQL), guarded
    result = execute(guarded)
    assert isinstance(result, SqlResult), result
    return guarded.sql, result


def test_finds_the_date_filter_that_removed_every_row() -> None:
    # The data ends on 2026-09-30, so a 2030 start date leaves nothing (a "false zero").
    sql, result = run_guarded(
        "SELECT cart_id FROM shopping_carts"
        " WHERE status = 'completed' AND created_at >= '2030-01-01'"
    )
    assert result.row_count == 0
    diagnostic = diagnose(sql)
    checks = {c.filter: c.rows_without for c in diagnostic.checks}
    assert checks == {"status = 'completed'": 0, "created_at >= '2030-01-01'": 1000}
    assert diagnostic.summary.startswith("The filter created_at >= '2030-01-01' removed every row")


def test_finds_a_having_filter_on_a_join() -> None:
    sql, result = run_guarded(
        "SELECT s.name, SUM(l.total_value_eur) AS spend FROM v_completed_lines AS l"
        " JOIN suppliers AS s ON s.supplier_id = l.supplier_id"
        " WHERE s.country_code = 'PT' AND l.completed_at >= '2025-01-01'"
        " GROUP BY s.name HAVING SUM(l.total_value_eur) > 100000000"
    )
    assert result.row_count == 0
    culprits = [c.filter for c in diagnose(sql).checks if c.rows_without > 0]
    assert culprits == ["SUM(l.total_value_eur) > 100000000"]


def test_two_filters_that_each_empty_the_result_are_not_blamed_alone() -> None:
    sql, result = run_guarded(
        "SELECT cart_id FROM shopping_carts WHERE status = 'lost' AND created_at >= '2030-01-01'"
    )
    assert result.row_count == 0
    diagnostic = diagnose(sql)
    assert [c.rows_without for c in diagnostic.checks] == [0, 0]
    assert diagnostic.summary.startswith("No single filter explains it")
