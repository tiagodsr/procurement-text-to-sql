"""One-off parity check: PostgreSQL build versus the SQLite reference database.

Compares row counts and a SHA-256 per table (see assistant.fingerprint), reading from PostgreSQL
only the columns the SQLite table has. Views are reported for information only.

Usage:
    python scripts/parity_check.py --sqlite .local/sqlite_reference.db --pg <build DSN>
Exit code 0 when every table matches, 1 otherwise.
"""

import argparse
import sqlite3
import sys
from contextlib import closing

import psycopg
from psycopg import sql

from assistant.fingerprint import TableSum, table_sum


def sqlite_relations(conn, kind):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%' ORDER BY name",
        (kind,),
    )
    return [r[0] for r in rows]


def compare(lite, pg, kind):
    """Rows of (name, sqlite sum, postgres sum or None) for every SQLite table or view."""
    out = []
    for name in sqlite_relations(lite, kind):
        cols = [r[1] for r in lite.execute(f'PRAGMA table_info("{name}")')]
        lite_sum = table_sum(lite.execute(f'SELECT * FROM "{name}"'))
        query = sql.SQL("SELECT {} FROM {}").format(
            sql.SQL(", ").join(map(sql.Identifier, cols)), sql.Identifier(name)
        )
        try:
            pg_sum = table_sum(pg.execute(query).fetchall())
        except psycopg.Error as exc:
            pg.rollback()
            print(f"  {name}: {exc.__class__.__name__}: {exc}".rstrip())
            pg_sum = None
        out.append((name, lite_sum, pg_sum))
    return out


ROW = "| {:22s} | {:>11} | {:>8} | {:16s} | {:16s} | {:5s} |"


def report(title, results):
    print(f"\n{title}")
    print(ROW.format("table", "sqlite rows", "pg rows", "sqlite sha256", "pg sha256", "match"))
    print(ROW.format("-" * 22, "-" * 11, "-" * 8, "-" * 16, "-" * 16, "-----"))
    for name, a, b in results:
        b = b or TableSum(rows=-1, sha256="missing")
        ok = "yes" if a == b else "NO"
        print(ROW.format(name, f"{a.rows:,}", f"{b.rows:,}", a.sha256[:16], b.sha256[:16], ok))
    return all(a == b for _, a, b in results)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", required=True)
    parser.add_argument("--pg", required=True, help="PostgreSQL DSN of the build database")
    args = parser.parse_args()
    with (
        closing(sqlite3.connect(f"file:{args.sqlite}?mode=ro", uri=True)) as lite,
        psycopg.connect(args.pg) as pg,
    ):
        tables_ok = report("Tables (must match)", compare(lite, pg, "table"))
        views_ok = report("Views (information only)", compare(lite, pg, "view"))
    print(f"\nTables: {'PARITY OK' if tables_ok else 'MISMATCH'}")
    print(f"Views: {'match' if views_ok else 'differ (information only)'}")
    sys.exit(0 if tables_ok else 1)


if __name__ == "__main__":
    main()
