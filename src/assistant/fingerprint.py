"""Engine-independent data fingerprint: row counts and a SHA-256 per table.

The same rows give the same checksum whether they come from PostgreSQL, SQLite or the generator's
own insert tuples: each value is turned into a canonical string and the lines of a table are
sorted, so column types (REAL vs NUMERIC, 0/1 vs BOOLEAN, TEXT vs DATE) and row order do not matter.

Demo:
    python -m assistant.fingerprint .local/sqlite_reference.db
    python -m assistant.fingerprint postgresql://app_ro:...@127.0.0.1:55432/procurement --views
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

NULL = r"\N"


@dataclass(frozen=True)
class TableSum:
    rows: int
    sha256: str


@dataclass(frozen=True)
class Fingerprint:
    tables: dict[str, TableSum]
    overall: str


def canonical(value: Any) -> str:
    """One string per value, identical across engines for the same logical value."""
    if value is None:
        return NULL
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float | Decimal):
        number = Decimal(repr(value)) if isinstance(value, float) else value
        return "0" if number == 0 else format(number.normalize(), "f")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    return text.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")


def table_sum(rows: Iterable[Sequence[Any]]) -> TableSum:
    lines = sorted("\t".join(canonical(v) for v in row) for row in rows)
    digest = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    return TableSum(rows=len(lines), sha256=digest)


def fingerprint(tables: Mapping[str, Iterable[Sequence[Any]]]) -> Fingerprint:
    sums = {name: table_sum(tables[name]) for name in sorted(tables)}
    summary = "\n".join(f"{name}:{s.rows}:{s.sha256}" for name, s in sums.items())
    return Fingerprint(tables=sums, overall=hashlib.sha256(summary.encode()).hexdigest())


def read_postgres(conn: Any, views: bool = False) -> dict[str, list[tuple[Any, ...]]]:
    """All base tables (or views) of schema public, read through a psycopg connection."""
    from psycopg import sql

    kind = "VIEW" if views else "BASE TABLE"
    names = [
        r[0]
        for r in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = %s ORDER BY table_name",
            (kind,),
        )
    ]
    query = sql.SQL("SELECT * FROM {}")
    return {n: conn.execute(query.format(sql.Identifier(n))).fetchall() for n in names}


def read_sqlite(conn: sqlite3.Connection, views: bool = False) -> dict[str, list[tuple[Any, ...]]]:
    kind = "view" if views else "table"
    names = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name",
            (kind,),
        )
    ]
    return {n: conn.execute(f'SELECT * FROM "{n}"').fetchall() for n in names}


def read_target(target: str, views: bool = False) -> dict[str, list[tuple[Any, ...]]]:
    """A PostgreSQL DSN (postgresql://...) or a path to a SQLite file."""
    if target.startswith(("postgresql://", "postgres://")):
        import psycopg

        with psycopg.connect(target) as conn:
            return read_postgres(conn, views)
    with closing(sqlite3.connect(f"file:{target}?mode=ro", uri=True)) as conn:
        return read_sqlite(conn, views)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", help="postgresql:// DSN or SQLite file path")
    parser.add_argument("--views", action="store_true", help="fingerprint the views instead")
    args = parser.parse_args()
    fp = fingerprint(read_target(args.target, args.views))
    for name, s in fp.tables.items():
        print(f"{name:22s} {s.rows:>7,}  {s.sha256[:16]}")
    print(f"{'overall':22s} {'':>7}  {fp.overall}")


if __name__ == "__main__":
    main()
