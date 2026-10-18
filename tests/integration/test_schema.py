"""The loaded database: structure, the frozen data and the facts the assistant relies on."""

import pytest

from assistant.fingerprint import fingerprint, read_postgres

pytestmark = pytest.mark.integration

TABLES = {
    "meta",
    "exchange_rates",
    "plants",
    "categories",
    "suppliers",
    "supplier_aliases",
    "contracts",
    "catalog_items",
    "shopping_carts",
    "shopping_cart_items",
}
VIEWS = {"v_completed_lines", "v_supplier_summary"}

FOREIGN_KEYS = {
    ("catalog_items", "category_id", "categories"),
    ("catalog_items", "contract_id", "contracts"),
    ("catalog_items", "currency", "exchange_rates"),
    ("catalog_items", "supplier_id", "suppliers"),
    ("categories", "parent_id", "categories"),
    ("contracts", "category_id", "categories"),
    ("contracts", "supplier_id", "suppliers"),
    ("shopping_cart_items", "cart_id", "shopping_carts"),
    ("shopping_cart_items", "category_id", "categories"),
    ("shopping_cart_items", "contract_id", "contracts"),
    ("shopping_cart_items", "currency", "exchange_rates"),
    ("shopping_cart_items", "supplier_id", "suppliers"),
    ("shopping_carts", "plant_id", "plants"),
    ("supplier_aliases", "supplier_id", "suppliers"),
    ("suppliers", "currency", "exchange_rates"),
}


def scalar(conn, query, params=None):
    return conn.execute(query, params).fetchone()[0]


def test_expected_tables_and_views(ro):
    rows = ro.execute(
        "SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()
    assert {n for n, t in rows if t == "BASE TABLE"} == TABLES
    assert {n for n, t in rows if t == "VIEW"} == VIEWS


@pytest.mark.parametrize(
    "table, column, expected",
    [
        ("exchange_rates", "rate_to_eur", ("numeric", 10, 4)),
        ("suppliers", "is_preferred", ("boolean", None, None)),
        ("suppliers", "onboarded_at", ("date", None, None)),
        ("contracts", "start_date", ("date", None, None)),
        ("contracts", "end_date", ("date", None, None)),
        ("contracts", "discount_pct", ("numeric", 4, 1)),
        ("catalog_items", "unit_price", ("numeric", 14, 2)),
        ("catalog_items", "active", ("boolean", None, None)),
        ("catalog_items", "created_at", ("date", None, None)),
        ("catalog_items", "lead_time_days", ("integer", 32, 0)),
        ("shopping_carts", "cart_id", ("bigint", 64, 0)),
        ("shopping_carts", "created_at", ("date", None, None)),
        ("shopping_carts", "completed_at", ("date", None, None)),
        ("shopping_cart_items", "cart_id", ("bigint", 64, 0)),
        ("shopping_cart_items", "unit_price", ("numeric", 14, 2)),
        ("shopping_cart_items", "total_value", ("numeric", 14, 2)),
        ("shopping_cart_items", "created_at", ("date", None, None)),
    ],
)
def test_column_types(ro, table, column, expected):
    row = ro.execute(
        "SELECT data_type, numeric_precision, numeric_scale FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s AND column_name = %s",
        (table, column),
    ).fetchone()
    assert row == expected


def test_foreign_keys(ro):
    # pg_constraint, not information_schema: the latter hides constraints from a SELECT-only role.
    rows = ro.execute(
        "SELECT c.conrelid::regclass::text, a.attname, c.confrelid::regclass::text "
        "FROM pg_constraint c "
        "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1] "
        "WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace"
    ).fetchall()
    assert set(rows) == FOREIGN_KEYS


def test_loaded_data_matches_the_frozen_manifest(ro, manifest):
    tables = fingerprint(read_postgres(ro))
    assert {n: s.rows for n, s in tables.tables.items()} == {
        n: s["rows"] for n, s in manifest["tables"].items()
    }
    assert tables.overall == manifest["fingerprint"]


def test_views_match_the_frozen_manifest(ro, manifest):
    views = fingerprint(read_postgres(ro, views=True))
    assert {n: s.sha256 for n, s in views.tables.items()} == {
        n: s["sha256"] for n, s in manifest["views"].items()
    }


def test_meta_dates(ro):
    meta = dict(ro.execute("SELECT key, value FROM meta").fetchall())
    assert meta["as_of_date"] == "2026-10-03"
    assert (meta["data_from"], meta["data_to"]) == ("2023-01-02", "2026-09-30")
    assert meta["seed"] == "42"


def test_blocked_suppliers_are_high_risk_and_never_preferred(ro):
    assert (
        scalar(
            ro,
            "SELECT count(*) FROM suppliers WHERE status = 'blocked' "
            "AND (risk_rating <> 'high' OR is_preferred)",
        )
        == 0
    )


def test_completed_at_is_set_exactly_for_completed_carts(ro):
    assert (
        scalar(
            ro,
            "SELECT count(*) FROM shopping_carts "
            "WHERE (status = 'completed') <> (completed_at IS NOT NULL)",
        )
        == 0
    )


def test_in_approval_carts_are_from_the_last_ten_days(ro):
    assert (
        scalar(
            ro,
            "SELECT count(*) FROM shopping_carts "
            "WHERE status = 'in_approval' AND created_at < DATE '2026-10-03' - 10",
        )
        == 0
    )


def test_line_amounts_and_currency_follow_the_rules(ro):
    assert (
        scalar(
            ro,
            "SELECT count(*) FROM shopping_cart_items l JOIN suppliers s USING (supplier_id) "
            "WHERE l.currency <> s.currency "
            "OR l.total_value <> round(l.unit_price * l.quantity, 2)",
        )
        == 0
    )


def test_six_brand_aliases_map_to_two_suppliers(ro):
    rows = ro.execute(
        "SELECT alias FROM supplier_aliases GROUP BY alias "
        "HAVING count(DISTINCT supplier_id) > 1 ORDER BY alias"
    ).fetchall()
    assert [r[0] for r in rows] == ["Brabon", "Mernova", "Nornova", "Velria", "Venlon", "Zengen"]


def test_lead_time_column_matches_the_description(ro):
    assert (
        scalar(
            ro,
            "SELECT count(*) FROM catalog_items "
            "WHERE item_description NOT LIKE '%| lead time ' || lead_time_days || ' days'",
        )
        == 0
    )
