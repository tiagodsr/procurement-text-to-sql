"""Zero-row diagnostic: drop one top-level WHERE/HAVING condition at a time, re-check each variant
with the guard, count its rows through the executor, and say which filter removed every row.

Demo (needs the db: `docker compose up -d --wait db`):
    uv run python -m assistant.diagnose \
      "SELECT cart_id FROM shopping_carts WHERE status = 'completed' AND created_at >= '2030-01-01'"
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

import sqlglot
from sqlglot import exp

from assistant.executor import execute
from assistant.guard import GuardConfig, GuardedSQL, check
from assistant.schemas import FilterCheck, NoDataDiagnostic, SqlError, SqlResult

MAX_FILTERS = 8
Runner = Callable[[GuardedSQL], SqlResult | SqlError]


def split_filters(sql: str) -> list[tuple[str, str]]:
    """(condition, SQL without that condition) for each top-level WHERE and HAVING condition."""
    try:
        root = sqlglot.parse_one(sql, read="postgres")
    except Exception:  # the SQL already ran, so this is unexpected: nothing to split
        return []
    if not isinstance(root, exp.Select):
        return []  # a UNION has no single top-level filter
    variants = []
    for clause, node_type in (("where", exp.Where), ("having", exp.Having)):
        node = root.args.get(clause)
        if node is None:
            continue
        condition = node.this.unnest()
        parts = list(condition.flatten()) if isinstance(condition, exp.And) else [condition]
        for i, part in enumerate(parts):
            rest = [p.copy() for j, p in enumerate(parts) if j != i]
            variant = root.copy()
            variant.set(clause, node_type(this=exp.and_(*rest)) if rest else None)
            variants.append((part.sql(dialect="postgres"), variant.sql(dialect="postgres")))
    return variants


def summarize(checks: list[FilterCheck], unchecked: int) -> str:
    if not checks and not unchecked:
        return "The query has no top-level filter to drop: the tables hold no matching rows."
    culprits = [c for c in checks if c.rows_without > 0]
    if len(culprits) == 1:
        c = culprits[0]
        text = f"The filter {c.filter} removed every row: without it the query returns rows."
    elif culprits:
        names = "; ".join(c.filter for c in culprits)
        text = f"Dropping any one of these filters returns rows: {names}."
    else:
        text = "No single filter explains it: dropping any one filter still returns no rows."
    if unchecked:
        text += f" {unchecked} filter(s) could not be checked."
    return text


def diagnose(
    sql: str, run: Runner = execute, guard_config: GuardConfig | None = None
) -> NoDataDiagnostic:
    """Diagnose a zero-row result of `sql` (the guarded SQL that ran)."""
    variants = split_filters(sql)
    checks, unchecked = [], max(0, len(variants) - MAX_FILTERS)
    for condition, variant in variants[:MAX_FILTERS]:
        guarded = check(variant, guard_config)
        result = run(guarded) if isinstance(guarded, GuardedSQL) else None
        if isinstance(result, SqlResult):
            checks.append(FilterCheck(filter=condition, rows_without=result.row_count))
        else:
            unchecked += 1
    return NoDataDiagnostic(summary=summarize(checks, unchecked), checks=checks)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sql", help="a query that returns no rows")
    guarded = check(parser.parse_args().sql)
    if not isinstance(guarded, GuardedSQL):
        sys.exit(f"blocked by the guard: {guarded.rule}: {guarded.message}")
    result = execute(guarded)
    if not isinstance(result, SqlResult):
        sys.exit(f"the query failed: {result.kind}: {result.message}")
    print(f"rows: {result.row_count}")
    print(diagnose(guarded.sql).model_dump_json(indent=2))


if __name__ == "__main__":
    main()
