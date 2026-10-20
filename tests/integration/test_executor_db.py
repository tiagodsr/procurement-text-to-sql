"""The executor against the compose database, as app_ro. Some cases build a GuardedSQL by hand to
test the executor's own layers (timeout, read-only transaction, stacked statements) without the
guard, which would block them first."""

import pytest

from assistant.executor import DatabaseUnavailable, ExecutorConfig, execute
from assistant.guard import TABLES, GuardedSQL, check
from assistant.schemas import SqlError, SqlResult

pytestmark = pytest.mark.integration


def run(sql: str, config: ExecutorConfig | None = None, **kwargs) -> SqlResult | SqlError:
    """Bypass the guard on purpose: the executor must hold even then."""
    return execute(GuardedSQL(sql=sql, limit="kept"), config, **kwargs)


def guarded(sql: str) -> GuardedSQL:
    result = check(sql)
    assert isinstance(result, GuardedSQL), result
    return result


def test_runs_a_legitimate_query(database_url) -> None:
    sql = (
        "SELECT s.name, SUM(l.total_value_eur) AS spend_eur FROM v_completed_lines AS l"
        " JOIN suppliers AS s ON s.supplier_id = l.supplier_id"
        " GROUP BY s.name ORDER BY spend_eur DESC"
    )
    result = execute(guarded(sql), ExecutorConfig(preview_rows=5), database_url=database_url)
    assert isinstance(result, SqlResult), result
    assert result.columns == ["name", "spend_eur"]
    assert result.row_count == 96 and len(result.rows) == 5 and result.truncated
    assert all(isinstance(v, float) for _, v in result.rows)
    assert result.sql.endswith("LIMIT 1000")


def test_count_matches_the_data(database_url) -> None:
    result = execute(guarded("SELECT COUNT(*) FROM shopping_cart_items"), database_url=database_url)
    assert result.rows == [[17160]]


def test_the_guard_allowlist_is_the_real_schema(database_url) -> None:
    sql = "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    result = run(sql, database_url=database_url)
    assert {row[0] for row in result.rows} == TABLES


def test_exploding_cross_join_is_stopped_by_the_explain_check(database_url) -> None:
    """The query the guard lets through (tests/security/test_guard_attacks.py): a 17,160^3 product
    whose COUNT(*) a LIMIT cannot bound. EXPLAIN refuses it before it runs."""
    sql = (
        "SELECT COUNT(*) FROM shopping_cart_items AS a CROSS JOIN shopping_cart_items AS b"
        " CROSS JOIN shopping_cart_items AS c"
    )
    result = execute(guarded(sql), database_url=database_url)
    assert isinstance(result, SqlError) and result.kind == "cost_limit", result
    assert "above the limit" in result.message


def test_pg_sleep_beyond_the_timeout_is_cancelled(database_url) -> None:
    result = run("SELECT pg_sleep(2)", ExecutorConfig(timeout_ms=200), database_url=database_url)
    assert isinstance(result, SqlError) and result.kind == "timeout", result


def test_the_transaction_is_read_only_with_the_local_timeout(database_url) -> None:
    sql = "SELECT current_setting('transaction_read_only'), current_setting('statement_timeout')"
    result = run(sql, ExecutorConfig(timeout_ms=1234), database_url=database_url)
    assert result.rows == [["on", "1234ms"]]


def test_a_write_is_refused_by_the_read_only_transaction(database_url) -> None:
    result = run("DELETE FROM meta", database_url=database_url)
    assert isinstance(result, SqlError) and result.kind == "permission", result


def test_stacked_statements_fail_in_the_database(database_url) -> None:
    result = run("SELECT 1; DROP TABLE suppliers", database_url=database_url)
    assert isinstance(result, SqlError) and result.kind == "syntax", result
    assert "multiple commands" in result.message


@pytest.mark.parametrize(
    ("sql", "kind"),
    [
        ("SELECT nme FROM suppliers", "undefined_object"),
        ("SELECT * FROM supplier", "undefined_object"),
        ("SELECT name FROM suppliers WHERE", "syntax"),
        ("SELECT name, COUNT(*) FROM suppliers", "syntax"),  # grouping error
        ("SELECT * FROM pg_authid", "permission"),
        ("SELECT 1 / 0", "other"),
    ],
)
def test_errors_are_typed_for_the_repair_step(database_url, sql, kind) -> None:
    result = run(sql, database_url=database_url)
    assert isinstance(result, SqlError) and result.kind == kind, result
    assert result.sql == sql and len(result.message) <= 300


def test_the_repair_step_gets_the_database_hint(database_url) -> None:
    result = run("SELECT nme FROM suppliers", database_url=database_url)
    assert "suppliers.name" in result.message


def test_a_wrong_role_is_refused_before_connecting() -> None:
    url = "postgresql://postgres:secret@127.0.0.1:55432/procurement"
    with pytest.raises(ValueError, match="app_ro"):
        run("SELECT 1", database_url=url)


def test_an_unreachable_database_is_an_outage_not_a_query_error() -> None:
    url = "postgresql://app_ro:app_ro_local@127.0.0.1:1/procurement"
    with pytest.raises(DatabaseUnavailable):
        run("SELECT 1", ExecutorConfig(connect_timeout_s=1), database_url=url)
