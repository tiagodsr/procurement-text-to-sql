"""The database role app_ro can read and cannot write, by itself (no application guard involved)."""

import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.integration

WRITES = [
    "INSERT INTO plants VALUES (99, 'Site 99', 'DE', 'Berlin')",
    "UPDATE suppliers SET status = 'active'",
    "DELETE FROM shopping_cart_items",
    "TRUNCATE meta",
    "CREATE TABLE notes (id int)",
    "CREATE TEMP TABLE scratch (id int)",
    "CREATE VIEW v_all AS SELECT 1",
    "DROP TABLE contracts",
    "ALTER TABLE suppliers ADD COLUMN x int",
    "COMMENT ON TABLE suppliers IS 'changed'",
]


def test_can_read_tables_and_views(ro):
    assert ro.execute("SELECT count(*) FROM shopping_cart_items").fetchone()[0] > 0
    assert ro.execute("SELECT count(*) FROM v_completed_lines").fetchone()[0] > 0


def test_is_app_ro_without_special_attributes(ro):
    row = ro.execute(
        "SELECT current_user, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
        "FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    assert row == ("app_ro", False, False, False, False)


def test_role_defaults(ro):
    assert ro.execute("SHOW default_transaction_read_only").fetchone()[0] == "on"
    assert ro.execute("SHOW statement_timeout").fetchone()[0] == "5s"


@pytest.mark.parametrize("statement", WRITES)
def test_writes_fail_in_the_default_read_only_transaction(ro, statement):
    with pytest.raises(errors.ReadOnlySqlTransaction):
        ro.execute(statement)


@pytest.mark.parametrize("statement", WRITES)
def test_writes_fail_even_after_switching_read_only_off(ro, statement):
    # The session default is not the barrier; the missing privileges are.
    ro.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE")
    ro.commit()
    assert ro.execute("SHOW transaction_read_only").fetchone()[0] == "off"
    with pytest.raises(errors.InsufficientPrivilege):
        ro.execute(statement)


def test_grant_by_app_ro_grants_nothing(ro):
    # A non-owner's GRANT is not an error in PostgreSQL, only a warning: check the effect instead.
    ro.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE")
    ro.commit()
    ro.execute("GRANT SELECT ON suppliers TO PUBLIC")
    ro.commit()
    granted = "SELECT has_table_privilege('public', 'suppliers', 'SELECT')"
    assert ro.execute(granted).fetchone()[0] is False


def test_cannot_become_another_role(ro):
    with pytest.raises(errors.InsufficientPrivilege):
        ro.execute("SET ROLE postgres")


def test_long_queries_are_cancelled_by_the_role_timeout(ro):
    with pytest.raises(errors.QueryCanceled):
        ro.execute("SELECT pg_sleep(6)")


def test_wrong_password_is_rejected(database_url):
    bad = database_url.replace("app_ro_local", "wrong")
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(bad, connect_timeout=5)
