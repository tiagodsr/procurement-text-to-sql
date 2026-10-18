"""Run scripts/build_db.py against an in-memory recording connection (no database).

Used by the unit tests, in-process or as a subprocess with a chosen PYTHONHASHSEED:
    python tests/build_recorder.py      # prints the generator's fingerprint as JSON
"""

import importlib.util
import json
import re
from dataclasses import asdict
from pathlib import Path

from assistant.fingerprint import fingerprint

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_db.py"
SEED = 42


def load_build_module():
    spec = importlib.util.spec_from_file_location("build_db", BUILD_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingCursor:
    """Accepts the calls build() makes and keeps the inserted rows per table."""

    def __init__(self):
        self.tables: dict[str, list[tuple]] = {}
        self.statements: list[str] = []
        self._count = None

    def execute(self, query, params=None):
        self.statements.append(query)
        match = re.match(r"SELECT COUNT\(\*\) FROM (\w+)", query)
        self._count = len(self.tables.get(match[1], [])) if match else None
        return self

    def executemany(self, query, rows):
        table = re.match(r"INSERT INTO (\w+) VALUES", query)[1]
        placeholders = query.count("%s")
        for row in rows:
            row = tuple(row)
            assert len(row) == placeholders, f"{table}: {len(row)} values, {placeholders} %s"
            self.tables.setdefault(table, []).append(row)

    def fetchone(self):
        return (self._count,)


class RecordingConnection:
    def __init__(self):
        self.cur = RecordingCursor()

    def cursor(self):
        return self.cur

    def commit(self):
        pass


def record(seed=SEED) -> RecordingCursor:
    conn = RecordingConnection()
    load_build_module().build(conn, seed)
    return conn.cur


if __name__ == "__main__":
    print(json.dumps(asdict(fingerprint(record().tables)), sort_keys=True))
