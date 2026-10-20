"""SQL guard: decides on the parsed tree (sqlglot, dialect postgres) whether SQL written by the LLM
may run, and forces an outer LIMIT. One layer of three: the app_ro role and the executor's READ ONLY
transaction do not depend on it. Decisions never look at the text, so comments cannot change them.

Demo:
    uv run python -m assistant.guard "SELECT name FROM suppliers WHERE status = 'active'"
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Literal, get_args

import sqlglot
from pydantic import Field
from sqlglot import exp

from assistant.schemas import Contract, GuardViolation

Rule = Literal[
    "too_long", "parse_error", "statement_count", "write_or_ddl", "not_select", "select_into",
    "locking_clause", "max_depth", "table_not_allowed", "function_not_allowed", "limit_invalid",
    "roundtrip_mismatch",
]  # fmt: skip
RULES: tuple[str, ...] = get_args(Rule)

# The real schema: 10 tables and 2 views in public (an integration test compares them).
TABLES = frozenset({
    "catalog_items", "categories", "contracts", "exchange_rates", "meta", "plants",
    "shopping_cart_items", "shopping_carts", "supplier_aliases", "suppliers",
    "v_completed_lines", "v_supplier_summary",
})  # fmt: skip

# sqlglot's canonical names, lowercase: DATE_TRUNC is timestamp_trunc, TO_CHAR time_to_str,
# STRING_AGG group_concat, POSITION/STRPOS str_position, BOOL_OR logical_or. CASE ... WHEN
# branches are `if` nodes. Unknown functions (pg_sleep, set_config, dblink, lo_import...) are
# anonymous, so they are denied.
FUNCTIONS = frozenset({
    # aggregates
    "count", "sum", "avg", "min", "max", "group_concat", "array_agg", "percentile_cont",
    "percentile_disc", "mode", "stddev", "stddev_samp", "stddev_pop", "variance", "logical_or",
    "logical_and",
    # window
    "row_number", "rank", "dense_rank", "percent_rank", "cume_dist", "ntile", "lag", "lead",
    "first_value", "last_value",
    # conditional, casts, arrays
    "case", "if", "coalesce", "nullif", "greatest", "least", "cast", "exists", "array", "unnest",
    # numbers
    "round", "abs", "ceil", "floor", "trunc", "sqrt", "pow",
    # dates (no current_date or now(): "today" is meta.as_of_date)
    "extract", "timestamp_trunc", "date_trunc", "time_to_str", "str_to_date", "date", "make_date",
    "age", "make_interval",
    # strings
    "lower", "upper", "initcap", "trim", "length", "concat", "substring", "split_part", "replace",
    "left", "right", "str_position",
})  # fmt: skip

HINTS = {
    "current_date": "today is meta.as_of_date, not the real date",
    "current_timestamp": "today is meta.as_of_date, not the real date",
    "localtimestamp": "today is meta.as_of_date, not the real date",
}
WRITES = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter,
          exp.TruncateTable, exp.Grant, exp.Revoke)  # fmt: skip
OTHER_STATEMENTS = (exp.Copy, exp.Set, exp.Command, exp.Transaction, exp.Commit, exp.Rollback,
                    exp.Use, exp.Analyze, exp.Describe, exp.Execute, exp.Show)  # fmt: skip
QUERIES = (exp.Select, exp.SetOperation)


class GuardConfig(Contract):
    tables: frozenset[str] = TABLES
    functions: frozenset[str] = FUNCTIONS
    max_limit: int = Field(default=1000, ge=1)
    max_query_depth: int = Field(default=5, ge=1)  # nested SELECTs, the outer one counts as 1
    max_tree_depth: int = Field(default=80, ge=1)  # nodes from the root to the deepest leaf
    max_chars: int = Field(default=10_000, ge=1)


class GuardedSQL(Contract):
    sql: str  # regenerated from the checked tree, comments dropped
    limit: Literal["added", "lowered", "kept"]


class _Blocked(Exception):
    def __init__(self, rule: Rule, message: str) -> None:
        self.violation = GuardViolation(rule=rule, message=message)


def check(sql: str, config: GuardConfig | None = None) -> GuardedSQL | GuardViolation:
    """Return the SQL to run (outer LIMIT forced) or the first rule that blocks it."""
    config = config or GuardConfig()
    try:
        if len(sql) > config.max_chars:
            raise _Blocked("too_long", f"the SQL is longer than {config.max_chars} characters")
        root = _parse_one(sql)
        _check_tree(root, config)
        limit = _force_limit(root, config.max_limit)
        return GuardedSQL(sql=_roundtrip(root), limit=limit)
    except _Blocked as blocked:
        return blocked.violation


def _parse_one(sql: str) -> exp.Expression:
    try:
        parsed = sqlglot.parse(sql, read="postgres")
    except Exception as exc:  # fail closed: any parser failure (or recursion) blocks the SQL
        raise _Blocked("parse_error", f"the SQL could not be parsed: {exc}") from None
    statements = [s for s in parsed if s is not None and not isinstance(s, exp.Semicolon)]
    if len(statements) != 1:
        raise _Blocked("statement_count", f"expected one statement, found {len(statements)}")
    return statements[0]


def _check_tree(root: exp.Expression, config: GuardConfig) -> None:
    if node := root.find(*WRITES):
        raise _Blocked("write_or_ddl", f"{node.key.upper()} is not allowed: the SQL must only read")
    bodies = [n.this for n in root.find_all(exp.CTE, exp.Subquery)]
    if (
        not isinstance(root, QUERIES)
        or root.find(*OTHER_STATEMENTS)
        or not all(isinstance(body, exp.Query) for body in bodies)
    ):
        raise _Blocked("not_select", "only a SELECT (or a UNION of SELECTs) is allowed")
    if root.find(exp.Into):
        raise _Blocked("select_into", "SELECT INTO creates a table and is not allowed")
    if root.find(exp.Lock):
        raise _Blocked("locking_clause", "FOR UPDATE / FOR SHARE locks rows and is not allowed")
    _check_depth(root, config)
    _check_tables(root, config.tables)
    _check_functions(root, config.functions)


def _check_depth(root: exp.Expression, config: GuardConfig) -> None:
    stack = [(root, 1, int(isinstance(root, exp.Select)))]  # iterative: no recursion on deep trees
    while stack:
        node, depth, queries = stack.pop()
        if depth > config.max_tree_depth or queries > config.max_query_depth:
            raise _Blocked("max_depth", "the query is nested too deeply")
        for child in node.iter_expressions():
            stack.append((child, depth + 1, queries + isinstance(child, exp.Select)))


def _name(identifier: exp.Expression | None) -> str:
    """How PostgreSQL resolves an identifier: unquoted names fold to lower case."""
    if not isinstance(identifier, exp.Identifier):
        return ""
    return identifier.this if identifier.quoted else identifier.this.lower()


def _check_tables(root: exp.Expression, allowed: frozenset[str]) -> None:
    ctes = {_name(getattr(cte.args.get("alias"), "this", None)) for cte in root.find_all(exp.CTE)}
    for cte in sorted(ctes):
        # Unqualified names that are not ours resolve in pg_catalog, so a CTE named pg_* could
        # make another reference read the real catalog; plain ASCII identifiers only.
        if cte.startswith("pg_") or not (cte.isascii() and cte.isidentifier()):
            raise _Blocked("table_not_allowed", f"CTE name {cte!r} is not allowed")
    for table in root.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            continue  # a function in FROM, checked with the functions
        name, schema = _name(table.this), _name(table.args.get("db"))
        if table.args.get("catalog") or schema not in ("", "public"):
            raise _Blocked("table_not_allowed", f"schema {schema or '?'!r} is not allowed")
        if name not in allowed and (schema or name not in ctes):
            raise _Blocked("table_not_allowed", f"table {name!r} is not in the allowlist")


def _check_functions(root: exp.Expression, allowed: frozenset[str]) -> None:
    for func in root.find_all(exp.Func):
        if isinstance(func, exp.Connector):
            continue  # AND / OR are logic, not calls (sqlglot 30 makes them Func subclasses)
        name = (func.name if isinstance(func, exp.Anonymous) else func.sql_name()).lower()
        if isinstance(func.parent, exp.Dot):
            raise _Blocked("function_not_allowed", f"schema-qualified function {name!r}")
        if name not in allowed:
            hint = f" ({HINTS[name]})" if name in HINTS else ""
            raise _Blocked("function_not_allowed", f"function {name!r} is not allowed{hint}")


def _force_limit(root: exp.Expression, maximum: int) -> Literal["added", "lowered", "kept"]:
    node = root.args.get("limit")
    if node is None:
        root.set("limit", exp.Limit(expression=exp.Literal.number(maximum)))
        return "added"
    key = "count" if isinstance(node, exp.Fetch) else "expression"
    value = node.args.get(key)
    if value is None and isinstance(node, exp.Fetch):
        return "kept"  # FETCH FIRST ROW ONLY: one row
    unbounded = isinstance(value, exp.Null) or (
        isinstance(value, exp.Var) and value.name.upper() == "ALL"
    )
    if not unbounded and not (isinstance(value, exp.Literal) and value.is_int):
        raise _Blocked("limit_invalid", "the outer LIMIT must be a whole number")
    if unbounded or int(value.name) > maximum:
        node.set(key, exp.Literal.number(maximum))
        return "lowered"
    return "kept"


def _roundtrip(root: exp.Expression) -> str:
    try:
        sql = root.sql(dialect="postgres", comments=False)
        same = sqlglot.parse_one(sql, read="postgres") == root
    except Exception:  # fail closed, as in parsing
        same, sql = False, ""
    if not same:
        raise _Blocked("roundtrip_mismatch", "the SQL does not survive a parse round trip")
    return sql


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sql", help="the SQL to check")
    parser.add_argument("--max-limit", type=int, default=GuardConfig().max_limit)
    args = parser.parse_args()
    verdict = check(args.sql, GuardConfig(max_limit=args.max_limit))
    ok = isinstance(verdict, GuardedSQL)
    print(json.dumps({"verdict": "ok" if ok else "blocked", **verdict.model_dump()}, indent=2))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
