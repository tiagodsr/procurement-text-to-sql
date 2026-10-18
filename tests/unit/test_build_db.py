"""The generator, run without a database through a recording connection."""

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict

import pytest

from assistant.fingerprint import fingerprint
from tests.build_recorder import REPO_ROOT, load_build_module, record

RECORDER = REPO_ROOT / "tests" / "build_recorder.py"


@pytest.fixture(scope="module")
def recorded():
    return record()


def fingerprint_in_subprocess(hashseed: str) -> dict:
    env = {**os.environ, "PYTHONHASHSEED": hashseed}
    out = subprocess.run(
        [sys.executable, str(RECORDER)], env=env, check=True, capture_output=True, text=True
    )
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def manifest():
    return json.loads((REPO_ROOT / "data" / "fingerprint.json").read_text())


def test_data_does_not_depend_on_python_hash_seed(manifest):
    first, second = fingerprint_in_subprocess("0"), fingerprint_in_subprocess("1")
    assert first == second
    assert first["overall"] == manifest["fingerprint"]


def test_generator_reproduces_the_frozen_data(recorded, manifest):
    """Any change to the generator, even the order of draws, changes the data. Then bump
    DATA_VERSION, run `make dump` and re-verify the golden set."""
    current = fingerprint(recorded.tables)
    assert {n: asdict(s) for n, s in current.tables.items()} == manifest["tables"]
    assert current.overall == manifest["fingerprint"]


def test_committed_dump_is_the_one_in_the_manifest(manifest):
    dump = REPO_ROOT / manifest["dump"]["file"]
    assert hashlib.sha256(dump.read_bytes()).hexdigest() == manifest["dump"]["sha256"]


def test_every_table_is_written(recorded):
    assert set(recorded.tables) == set(load_build_module().TABLES)


def test_booleans_are_real_booleans(recorded):
    is_preferred = {row[7] for row in recorded.tables["suppliers"]}
    active = {row[9] for row in recorded.tables["catalog_items"]}
    assert is_preferred == {True, False}
    assert active == {True, False}


def test_lead_time_column_equals_the_value_in_the_description(recorded):
    items = recorded.tables["catalog_items"]
    assert items
    for item in items:
        description, lead_time_days = item[6], item[11]
        assert description.endswith(f"| lead time {lead_time_days} days")
        assert 2 <= lead_time_days <= 21


def test_tables_are_dropped_before_the_schema_is_created(recorded):
    assert recorded.statements[0].startswith("DROP TABLE IF EXISTS meta, ")
    assert "CREATE TABLE" in recorded.statements[1]


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://postgres:x@127.0.0.1:55433/procurement",
        "postgresql://postgres:x@127.0.0.1:55433/postgres",
        "host=127.0.0.1 dbname=procurement_build_old",
        "postgresql://127.0.0.1:55433",
    ],
)
def test_only_allowlisted_databases_are_build_targets(dsn):
    with pytest.raises(ValueError, match="refusing to build"):
        load_build_module().target_database(dsn)


def test_build_database_is_accepted():
    build = load_build_module()
    assert build.target_database("postgresql://u:p@127.0.0.1:1/procurement_build") == (
        "procurement_build"
    )
