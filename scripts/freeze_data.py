"""Write data/fingerprint.json, the manifest of the frozen data.

Usage (run by `make dump` after the dump is written):
    python scripts/freeze_data.py --pg <build DSN> --dump data/init/01_dump.sql.gz
"""

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import psycopg

from assistant.fingerprint import fingerprint, read_postgres

DATA_VERSION = "v1"
MANIFEST = Path(__file__).resolve().parents[1] / "data" / "fingerprint.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pg", required=True, help="PostgreSQL DSN of the build database")
    parser.add_argument("--dump", required=True, type=Path)
    args = parser.parse_args()
    with psycopg.connect(args.pg) as conn:
        tables = fingerprint(read_postgres(conn))
        views = fingerprint(read_postgres(conn, views=True))
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    manifest = {
        "data_version": DATA_VERSION,
        "seed": int(meta["seed"]),
        "as_of_date": meta["as_of_date"],
        "fingerprint": tables.overall,
        "tables": {name: asdict(s) for name, s in tables.tables.items()},
        "views": {name: asdict(s) for name, s in views.tables.items()},
        "dump": {
            "file": args.dump.as_posix(),
            "sha256": hashlib.sha256(args.dump.read_bytes()).hexdigest(),
        },
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote {MANIFEST.name}: data {DATA_VERSION}, fingerprint {tables.overall[:16]}")


if __name__ == "__main__":
    main()
