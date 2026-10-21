"""The golden set agrees with the database: each reference query passes the guard and returns its
expected rows, and each trap case gets the same rows a second way, from the raw tables."""

import pytest

from assistant.evaluate import CASES_PATH, load_cases, matches
from assistant.executor import execute
from assistant.guard import GuardedSQL, check
from assistant.schemas import SqlResult

pytestmark = pytest.mark.integration

CASES = [c for split in ("dev", "holdout") for c in load_cases(split, CASES_PATH)]
WITH_ROWS = [c for c in CASES if "reference_sql" in c]
TRAPS = [c for c in CASES if "check_sql" in c]


def run(sql: str) -> SqlResult:
    guarded = check(sql)
    assert isinstance(guarded, GuardedSQL), guarded
    result = execute(guarded)
    assert isinstance(result, SqlResult), result
    return result


@pytest.mark.parametrize("case", WITH_ROWS, ids=lambda c: c["id"])
def test_the_reference_query_returns_the_expected_rows(case):
    assert run(case["reference_sql"]).rows == case["expected_rows"]


@pytest.mark.parametrize("case", TRAPS, ids=lambda c: c["id"])
def test_a_trap_gives_the_same_rows_from_the_raw_tables(case):
    """The view rounds each line to cents, so the raw sum can differ by a few cents."""
    assert matches(run(case["check_sql"]).rows, case["expected_rows"])
