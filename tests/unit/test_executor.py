"""Executor pieces that need no database: SQLSTATE mapping, EXPLAIN parsing, preview and values."""

from datetime import date, datetime
from decimal import Decimal

import psycopg
import pytest
from psycopg import errors

from assistant.executor import ExecutorConfig, build_result, error_kind, is_outage, plan_cost


@pytest.mark.parametrize(
    ("sqlstate", "kind"),
    [
        ("42601", "syntax"),  # syntax_error
        ("42803", "syntax"),  # grouping_error: the query is wrong, repair can fix it
        ("42804", "syntax"),  # datatype_mismatch
        ("42P01", "undefined_object"),  # undefined_table
        ("42703", "undefined_object"),  # undefined_column
        ("42883", "undefined_object"),  # undefined_function
        ("3F000", "undefined_object"),  # invalid_schema_name
        ("42501", "permission"),  # insufficient_privilege
        ("25006", "permission"),  # read_only_sql_transaction
        ("57014", "timeout"),  # query_canceled (statement_timeout)
        ("22012", "other"),  # division_by_zero
        ("22007", "other"),  # invalid_datetime_format
        (None, "other"),
    ],
)
def test_sqlstate_maps_to_a_kind(sqlstate, kind) -> None:
    assert error_kind(sqlstate) == kind


@pytest.mark.parametrize(
    ("exc", "outage"),
    [
        (psycopg.OperationalError("connection lost"), True),  # no SQLSTATE: client side
        (errors.AdminShutdown(), True),  # 57P01
        (errors.ConnectionFailure(), True),  # 08006
        (errors.UndefinedColumn(), False),
        (errors.QueryCanceled(), False),  # a timeout is the query's fault, not an outage
        (psycopg.ProgrammingError("bad use"), False),
    ],
)
def test_outages_are_told_apart_from_query_errors(exc, outage) -> None:
    assert is_outage(exc) is outage


def test_plan_cost_reads_the_total_cost() -> None:
    explain = [{"Plan": {"Node Type": "Limit", "Startup Cost": 0.0, "Total Cost": 1023.34}}]
    assert plan_cost(explain) == pytest.approx(1023.34)


@pytest.mark.parametrize("explain", [[], [{}], [{"Plan": {}}], None, "text"])
def test_unexpected_explain_output_is_an_error(explain) -> None:
    with pytest.raises(ValueError, match="unexpected EXPLAIN output"):
        plan_cost(explain)


def test_preview_is_cut_and_marked_truncated() -> None:
    rows = [(i, f"name {i}") for i in range(7)]
    result = build_result("SELECT 1", ["id", "name"], rows, preview_rows=3, db_ms=1.23456)
    assert result.rows == [[0, "name 0"], [1, "name 1"], [2, "name 2"]]
    assert (result.row_count, result.truncated, result.db_ms) == (7, True, 1.235)


def test_short_result_is_not_truncated() -> None:
    result = build_result("SELECT 1", ["n"], [(1,), (2,)], preview_rows=50, db_ms=0)
    assert (result.rows, result.row_count, result.truncated) == ([[1], [2]], 2, False)


def test_zero_rows() -> None:
    result = build_result("SELECT 1", ["n"], [], preview_rows=50, db_ms=0)
    assert (result.rows, result.row_count, result.truncated) == ([], 0, False)


def test_database_values_become_json_scalars() -> None:
    row = (Decimal("979605.76"), date(2025, 1, 31), datetime(2025, 1, 31, 8, 30), True, None, "x")
    result = build_result("SELECT 1", list("abcdef"), [row], preview_rows=1, db_ms=0)
    assert result.rows == [[979605.76, "2025-01-31", "2025-01-31T08:30:00", True, None, "x"]]


def test_config_cannot_exceed_the_role_timeout() -> None:
    with pytest.raises(ValueError):
        ExecutorConfig(timeout_ms=6000)
