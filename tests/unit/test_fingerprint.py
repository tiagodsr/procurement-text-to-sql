from datetime import date
from decimal import Decimal

import pytest

from assistant.fingerprint import canonical, fingerprint, table_sum


@pytest.mark.parametrize(
    "a, b",
    [
        (0.92, Decimal("0.9200")),  # SQLite REAL versus PostgreSQL NUMERIC(10,4)
        (1017863.2, Decimal("1017863.20")),
        (12.0, Decimal("12.00")),
        (0.0, Decimal("0.00")),
        (True, 1),  # BOOLEAN versus 0/1 INTEGER
        (False, 0),
        (date(2026, 10, 3), "2026-10-03"),  # DATE versus ISO TEXT
        (2000000001, 2000000001),
    ],
)
def test_same_logical_value_same_canonical_string(a, b):
    assert canonical(a) == canonical(b)


@pytest.mark.parametrize(
    "a, b",
    [
        (None, ""),
        (None, r"\N"),
        (1.5, 1.05),
        ("a\tb", "a b"),
        ("line\nbreak", "line\\nbreak"),
    ],
)
def test_different_values_stay_different(a, b):
    assert canonical(a) != canonical(b)


def test_large_numbers_are_not_written_in_exponent_form():
    assert canonical(Decimal("1E+2")) == "100"
    assert canonical(100.0) == "100"


def test_row_order_does_not_change_the_checksum():
    rows = [(1, "a", 0.5), (2, "b", None)]
    assert table_sum(rows) == table_sum(list(reversed(rows)))


def test_any_changed_value_changes_the_checksum():
    assert table_sum([(1, "a", 0.5)]) != table_sum([(1, "a", 0.51)])


def test_overall_fingerprint_covers_table_names_and_contents():
    base = fingerprint({"t1": [(1,)], "t2": [(2,)]})
    assert base.tables["t1"].rows == 1
    assert fingerprint({"t2": [(2,)], "t1": [(1,)]}) == base
    assert fingerprint({"t1": [(1,)], "t3": [(2,)]}).overall != base.overall
    assert fingerprint({"t1": [(1,)], "t2": [(3,)]}).overall != base.overall
