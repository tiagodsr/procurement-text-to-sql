"""Executor: runs SQL the guard accepted, as app_ro, in a READ ONLY transaction with a timeout and
an EXPLAIN cost check, and always rolls back. Database text in errors and rows is untrusted data.

Demo (needs `make db-dev`):
    uv run python -m assistant.executor "SELECT name, status FROM suppliers ORDER BY name"
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import suppress
from datetime import date, datetime
from decimal import Decimal
from time import perf_counter
from typing import Any

import psycopg
from psycopg import sql as pgsql
from pydantic import Field

from assistant.config import APP_ROLE, Settings, load_settings
from assistant.guard import GuardedSQL, check
from assistant.schemas import Contract, JsonScalar, SqlError, SqlResult

MAX_MESSAGE_CHARS = 300
UNDEFINED = {"42P01", "42703", "42883", "42704", "3F000", "42P02"}  # table, column, function...
PERMISSION = {"42501", "25006"}  # insufficient privilege, read-only transaction


class ExecutorConfig(Contract):
    timeout_ms: int = Field(default=3000, ge=1, le=5000)  # the role's own limit is 5 s
    max_cost: float = Field(default=1_000_000, gt=0)  # EXPLAIN units; normal queries cost < 2,000
    preview_rows: int = Field(default=50, ge=1)
    connect_timeout_s: int = Field(default=5, ge=1)


class DatabaseUnavailable(RuntimeError):
    """The database cannot be reached: not a query error, so it never goes to repair."""


def error_kind(sqlstate: str | None) -> str:
    """SqlError.kind for a PostgreSQL SQLSTATE."""
    if sqlstate in PERMISSION:
        return "permission"
    if sqlstate in UNDEFINED:
        return "undefined_object"
    if sqlstate == "57014":
        return "timeout"
    if sqlstate and sqlstate.startswith("42"):  # syntax error or access rule violation
        return "syntax"
    return "other"


def is_outage(exc: psycopg.Error) -> bool:
    """Connection lost, server shutting down or out of resources: not the query's fault."""
    if exc.sqlstate is None:
        return isinstance(exc, psycopg.OperationalError)
    return exc.sqlstate.startswith(("08", "57P", "53"))


def plan_cost(explain: Any) -> float:
    """Total estimated cost from the output of EXPLAIN (FORMAT JSON)."""
    try:
        return float(explain[0]["Plan"]["Total Cost"])
    except (LookupError, TypeError, ValueError):
        raise ValueError("unexpected EXPLAIN output") from None


def to_json(value: object) -> JsonScalar:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    return str(value)


def build_result(
    sql: str, columns: list[str], rows: list[tuple[Any, ...]], preview_rows: int, db_ms: float
) -> SqlResult:
    preview = [[to_json(v) for v in row] for row in rows[:preview_rows]]
    return SqlResult(
        sql=sql,
        columns=columns,
        rows=preview,
        row_count=len(rows),
        truncated=len(rows) > len(preview),
        db_ms=round(db_ms, 3),
    )


def _message(exc: psycopg.Error) -> str:
    diag = exc.diag
    text = diag.message_primary or (str(exc).splitlines() or ["database error"])[0]
    if diag.message_hint:
        text += f" (hint: {diag.message_hint})"
    return text[:MAX_MESSAGE_CHARS]


def _run(conn: psycopg.Connection, sql: str, config: ExecutorConfig) -> SqlResult | SqlError:
    start = perf_counter()
    with conn.cursor() as cur:
        cur.execute(pgsql.SQL("SET LOCAL statement_timeout = {}").format(config.timeout_ms))
        # prepare=True: a prepared statement holds one command, so stacked SQL fails here too
        cur.execute(f"EXPLAIN (FORMAT JSON) {sql}", prepare=True)
        cost = plan_cost(cur.fetchone()[0])
        if cost > config.max_cost:
            message = f"estimated cost {cost:,.0f} is above the limit {config.max_cost:,.0f}"
            return SqlError(kind="cost_limit", message=message, sql=sql)
        cur.execute(sql, prepare=True)
        columns = [col.name for col in cur.description or []]
        rows = cur.fetchall() if cur.description else []
    return build_result(sql, columns, rows, config.preview_rows, (perf_counter() - start) * 1000)


def execute(
    guarded: GuardedSQL, config: ExecutorConfig | None = None, *, database_url: str | None = None
) -> SqlResult | SqlError:
    """Run guarded SQL once, read-only, and roll back. Raises DatabaseUnavailable on an outage."""
    config = config or ExecutorConfig()
    url = Settings(database_url=database_url).database_url if database_url else None
    url = url or load_settings().database_url  # both paths validate the app_ro user
    try:
        conn = psycopg.connect(url, connect_timeout=config.connect_timeout_s)
    except psycopg.Error as exc:
        raise DatabaseUnavailable(f"cannot connect to the database: {_message(exc)}") from None
    try:
        if conn.info.user != APP_ROLE:
            raise DatabaseUnavailable(f"connected as {conn.info.user!r}, not {APP_ROLE!r}")
        conn.read_only = True  # the next transaction starts with BEGIN READ ONLY
        return _run(conn, guarded.sql, config)
    except psycopg.Error as exc:
        if is_outage(exc):
            raise DatabaseUnavailable(f"database error: {_message(exc)}") from None
        return SqlError(kind=error_kind(exc.sqlstate), message=_message(exc), sql=guarded.sql)
    finally:
        with suppress(psycopg.Error):  # a broken connection cannot roll back; closing discards
            conn.rollback()
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sql", help="the SQL to check with the guard, then run")
    parser.add_argument("--timeout-ms", type=int, default=ExecutorConfig().timeout_ms)
    parser.add_argument("--max-cost", type=float, default=ExecutorConfig().max_cost)
    args = parser.parse_args()
    guarded = check(args.sql)
    if not isinstance(guarded, GuardedSQL):
        print(json.dumps({"verdict": "blocked", **guarded.model_dump()}, indent=2))
        sys.exit(1)
    config = ExecutorConfig(timeout_ms=args.timeout_ms, max_cost=args.max_cost)
    try:
        result = execute(guarded, config)
    except DatabaseUnavailable as exc:
        sys.exit(f"{exc}; is the database up? (make db-dev)")
    print(result.model_dump_json(indent=2))
    sys.exit(0 if isinstance(result, SqlResult) else 1)


if __name__ == "__main__":
    main()
