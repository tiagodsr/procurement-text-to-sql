"""The guard on normal queries: what passes, how the LIMIT is forced, and that nothing else changes.
The attacks are in tests/security/test_guard_attacks.py."""

import json
import sys

import pytest
import sqlglot
from pydantic import ValidationError

from assistant import guard
from assistant.guard import RULES, GuardConfig, GuardedSQL, check
from assistant.schemas import GuardViolation

# Queries like the ones the generator should write for this schema.
LEGITIMATE = {
    "simple filter": (
        "SELECT name, country_code FROM suppliers WHERE status = 'active' OR is_preferred"
    ),
    "completed carts": (
        "SELECT COUNT(*) FROM shopping_carts WHERE status = 'completed'"
        " AND completed_at >= DATE '2025-01-01' AND completed_at < DATE '2026-01-01'"
    ),
    "join and aggregate": (
        "SELECT s.name, SUM(l.total_value_eur) AS spend_eur FROM v_completed_lines AS l"
        " JOIN suppliers AS s ON s.supplier_id = l.supplier_id"
        " GROUP BY s.name ORDER BY spend_eur DESC LIMIT 5"
    ),
    "three-way join with currency conversion": (
        "SELECT c.name, ROUND(SUM(i.total_value * fx.rate_to_eur), 2) AS eur"
        " FROM shopping_cart_items AS i JOIN shopping_carts AS sc ON sc.cart_id = i.cart_id"
        " JOIN exchange_rates AS fx ON fx.currency = i.currency"
        " LEFT JOIN categories AS c ON c.category_id = i.category_id"
        " WHERE sc.status = 'completed' GROUP BY c.name"
    ),
    "CTE of SELECTs with as_of_date": (
        "WITH today AS (SELECT CAST(value AS DATE) AS d FROM meta WHERE key = 'as_of_date'),"
        " recent AS (SELECT l.* FROM v_completed_lines AS l CROSS JOIN today"
        " WHERE l.completed_at >= today.d - INTERVAL '90 days')"
        " SELECT supplier_id, COUNT(*) AS lines FROM recent GROUP BY supplier_id"
    ),
    "window function": (
        "SELECT supplier_id, spend_eur,"
        " RANK() OVER (ORDER BY spend_eur DESC) AS spend_rank,"
        " ROW_NUMBER() OVER (PARTITION BY country_code ORDER BY spend_eur DESC) AS rn"
        " FROM v_supplier_summary"
    ),
    "monthly trend": (
        "SELECT DATE_TRUNC('month', completed_at) AS month, SUM(total_value_eur) AS spend"
        " FROM v_completed_lines WHERE EXTRACT(YEAR FROM completed_at) = 2025"
        " GROUP BY 1 ORDER BY 1"
    ),
    "delay statistics": (
        "SELECT supplier_id, AVG(actual_days - promised_days) AS avg_delay,"
        " PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY actual_days) AS median_days,"
        " COUNT(*) FILTER (WHERE actual_days > promised_days) AS late_lines"
        " FROM v_completed_lines WHERE actual_days IS NOT NULL GROUP BY supplier_id"
        " HAVING COUNT(*) >= 10"
    ),
    "free text with several keywords": (
        "SELECT COUNT(*) FROM v_completed_lines"
        " WHERE item_description ILIKE ANY (ARRAY['%laptop%', '%notebook%', '%ordinateur%'])"
    ),
    "subquery and EXISTS": (
        "SELECT s.name FROM suppliers AS s WHERE s.supplier_id IN"
        " (SELECT supplier_id FROM contracts WHERE status = 'published')"
        " AND NOT EXISTS (SELECT 1 FROM supplier_aliases AS a WHERE a.supplier_id = s.supplier_id)"
    ),
    "brand aliases": (
        "SELECT a.alias, STRING_AGG(s.name, ', ' ORDER BY s.name) AS entities"
        " FROM supplier_aliases AS a JOIN suppliers AS s ON s.supplier_id = a.supplier_id"
        " WHERE LOWER(a.alias) = 'brabon' GROUP BY a.alias"
    ),
    "CASE and COALESCE": (
        "SELECT plant_id, SUM(CASE WHEN requester_department = 'IT' THEN 1 ELSE 0 END) AS it,"
        " COALESCE(NULLIF(COUNT(*), 0), 1) AS carts FROM shopping_carts GROUP BY plant_id"
    ),
    "category roll-up": (
        "SELECT g.name AS category_group, COUNT(*) AS items FROM catalog_items AS ci"
        " JOIN categories AS c ON c.category_id = ci.category_id"
        " JOIN categories AS g ON g.category_id = c.parent_id"
        " WHERE ci.active AND ci.lead_time_days <= 7 GROUP BY g.name"
    ),
    "union": (
        "SELECT supplier_id FROM contracts WHERE status = 'published'"
        " UNION SELECT supplier_id FROM catalog_items WHERE active"
    ),
    "to_char and qualified public table": (
        "SELECT TO_CHAR(created_at, 'YYYY-MM') AS month, COUNT(*) FROM public.shopping_carts"
        " GROUP BY 1 ORDER BY 1"
    ),
}


def _without_limit(sql: str) -> sqlglot.exp.Expression:
    tree = sqlglot.parse_one(sql, read="postgres")
    tree.set("limit", None)
    return tree


@pytest.mark.parametrize("sql", LEGITIMATE.values(), ids=LEGITIMATE.keys())
def test_legitimate_queries_pass(sql: str) -> None:
    assert isinstance(check(sql), GuardedSQL)


@pytest.mark.parametrize("sql", LEGITIMATE.values(), ids=LEGITIMATE.keys())
def test_the_guard_changes_nothing_but_the_outer_limit(sql: str) -> None:
    result = check(sql)
    assert isinstance(result, GuardedSQL)
    assert _without_limit(result.sql) == _without_limit(sql)
    again = check(result.sql)  # idempotent: the output is already in its final form
    assert again == GuardedSQL(sql=result.sql, limit="kept")


def test_canonical_sql_only_gains_a_limit() -> None:
    sql = "SELECT name FROM suppliers WHERE status = 'active' ORDER BY name"
    assert check(sql) == GuardedSQL(sql=sql + " LIMIT 1000", limit="added")


@pytest.mark.parametrize(
    ("sql", "expected", "action"),
    [
        ("SELECT name FROM suppliers", "SELECT name FROM suppliers LIMIT 1000", "added"),
        ("SELECT name FROM suppliers LIMIT 20", "SELECT name FROM suppliers LIMIT 20", "kept"),
        ("SELECT name FROM suppliers LIMIT 0", "SELECT name FROM suppliers LIMIT 0", "kept"),
        (
            "SELECT name FROM suppliers LIMIT 5000",
            "SELECT name FROM suppliers LIMIT 1000",
            "lowered",
        ),
        (
            "SELECT name FROM suppliers LIMIT ALL",
            "SELECT name FROM suppliers LIMIT 1000",
            "lowered",
        ),
        (
            "SELECT name FROM suppliers LIMIT NULL",
            "SELECT name FROM suppliers LIMIT 1000",
            "lowered",
        ),
        (
            "SELECT name FROM suppliers LIMIT 5000 OFFSET 10",
            "SELECT name FROM suppliers LIMIT 1000 OFFSET 10",
            "lowered",
        ),
        (
            "SELECT name FROM suppliers FETCH FIRST 5000 ROWS ONLY",
            "SELECT name FROM suppliers FETCH FIRST 1000 ROWS ONLY",
            "lowered",
        ),
        (
            "SELECT a FROM (SELECT supplier_id AS a FROM suppliers LIMIT 5000) AS t",
            "SELECT a FROM (SELECT supplier_id AS a FROM suppliers LIMIT 5000) AS t LIMIT 1000",
            "added",
        ),
        (
            "SELECT supplier_id FROM suppliers UNION SELECT supplier_id FROM contracts",
            "SELECT supplier_id FROM suppliers UNION SELECT supplier_id FROM contracts LIMIT 1000",
            "added",
        ),
    ],
)
def test_outer_limit_is_forced(sql: str, expected: str, action: str) -> None:
    assert check(sql) == GuardedSQL(sql=expected, limit=action)


def test_max_limit_comes_from_the_config() -> None:
    result = check("SELECT name FROM suppliers LIMIT 50", GuardConfig(max_limit=10))
    assert result == GuardedSQL(sql="SELECT name FROM suppliers LIMIT 10", limit="lowered")


def test_the_real_date_is_not_allowed() -> None:
    """'Today' is meta.as_of_date; the message tells the repair step what to use instead."""
    result = check("SELECT * FROM shopping_carts WHERE created_at > CURRENT_DATE - 30")
    assert isinstance(result, GuardViolation)
    assert result.rule == "function_not_allowed"
    assert "meta.as_of_date" in result.message


def test_unknown_table_is_blocked() -> None:
    result = check("SELECT * FROM purchase_orders")
    assert isinstance(result, GuardViolation) and result.rule == "table_not_allowed"


@pytest.mark.parametrize("sql", ["", "   ", "-- nothing", "SELEC name FROM suppliers", "SELECT ("])
def test_empty_or_broken_sql_is_a_violation_not_an_exception(sql: str) -> None:
    result = check(sql)
    assert isinstance(result, GuardViolation)
    assert result.rule in ("parse_error", "statement_count")


def test_rule_names_are_stable() -> None:
    assert RULES == (
        "too_long", "parse_error", "statement_count", "write_or_ddl", "not_select",
        "select_into", "locking_clause", "max_depth", "table_not_allowed",
        "function_not_allowed", "limit_invalid", "roundtrip_mismatch",
    )  # fmt: skip


def test_config_rejects_a_non_positive_limit() -> None:
    with pytest.raises(ValidationError):
        GuardConfig(max_limit=0)


def test_cli_prints_the_verdict_as_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["guard", "SELECT pg_sleep(1)"])
    with pytest.raises(SystemExit) as exited:
        guard.main()
    assert exited.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "blocked" and out["rule"] == "function_not_allowed"

    monkeypatch.setattr(sys, "argv", ["guard", "SELECT name FROM suppliers"])
    with pytest.raises(SystemExit) as exited:
        guard.main()
    assert exited.value.code == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "verdict": "ok",
        "sql": "SELECT name FROM suppliers LIMIT 1000",
        "limit": "added",
    }
