"""The guard's attacks (tests/security/test_guard_attacks.py) sent straight to app_ro, with no guard
and no executor: the database role must refuse every one that needs a privilege, by itself, with
the read-only default on and after a session switches it off. Every attack is classified, so a new
guard attack fails test_every_guard_attack_is_classified until someone decides what the role does
with it."""

import pytest
from psycopg import errors

from tests.security.test_guard_attacks import ATTACKS

pytestmark = pytest.mark.integration

SQL = {name: sql for name, sql, _ in ATTACKS}

# Writes and DDL: the read-only default refuses them; without it, the missing privileges do.
WRITES = {
    "drop", "delete", "truncate", "create table as", "cte wrapping insert", "cte wrapping update",
    "stacked", "stacked after line comment", "stacked after block comment",
    "stacked after backslash", "explain analyze", "select into", "for update",
    "for share in a subquery",
}  # fmt: skip
# Refused for lack of a privilege whatever the transaction mode.
PRIVILEGED = {
    "copy to program", "copy to file", "set role", "set_config", "pg_catalog",
    "unqualified catalog", "cte shadowing a catalog", "unicode escape", "read file", "large object",
}  # fmt: skip
# Bounded by statement_timeout (the role's 5 s default is tested in test_app_ro.py).
SLOW = {"do block", "pg_sleep", "qualified pg_sleep", "quoted pg_sleep", "row bomb"}
# Nothing to refuse: the object is not installed.
MISSING = {"call", "dblink"}
# The database runs these as app_ro: harmless for the data, but the guard (and the executor's own
# SET LOCAL timeout and LIMIT) are what stop them. Listed so the gap is explicit.
NOT_THE_ROLES_JOB = {
    "set timeout": "a session may change its own statement_timeout",
    "reset": "a session may reset its own settings",
    "begin": "a transaction statement, harmless alone",
    "information_schema": "shows only objects app_ro may already see",
    "comments as spaces": "pg_roles is public; passwords are masked",
    "deep nesting": "valid SQL, only costly to analyse",
    "long expression chain": "valid SQL, only costly to analyse",
    "parenthesis bomb": "valid SQL for PostgreSQL",
    "too long": "valid SQL, only long",
    "limit from a subquery": "valid SQL; the guard wants a literal LIMIT",
    "quoted helper": "valid SQL; the guard refuses it on the round trip",
}
GRANT = "grant"  # a non-owner's GRANT is only a warning: the effect is checked instead


def test_every_guard_attack_is_classified() -> None:
    groups = [WRITES, PRIVILEGED, SLOW, MISSING, set(NOT_THE_ROLES_JOB), {GRANT}]
    names = [name for group in groups for name in group]
    assert len(names) == len(set(names)), "an attack is in two groups"
    assert set(names) == set(SQL)


@pytest.fixture(params=["read_only_default", "read_only_off"])
def session(request, ro):
    """An app_ro connection, as the role defaults leave it or with the read-only default off."""
    ro.execute("SET statement_timeout = '300ms'")  # keeps the SLOW attacks fast
    if request.param == "read_only_off":
        ro.execute("SET default_transaction_read_only = off")
    ro.commit()
    expected = "on" if request.param == "read_only_default" else "off"
    assert ro.execute("SHOW transaction_read_only").fetchone()[0] == expected
    return ro, request.param


@pytest.mark.parametrize("name", sorted(WRITES))
def test_writes_are_refused(session, name) -> None:
    conn, mode = session
    refusal = (
        errors.ReadOnlySqlTransaction
        if mode == "read_only_default"
        else errors.InsufficientPrivilege
    )
    with pytest.raises(refusal):
        conn.execute(SQL[name])


@pytest.mark.parametrize("name", sorted(PRIVILEGED))
def test_privileged_operations_are_refused(session, name) -> None:
    conn, _ = session
    with pytest.raises(errors.InsufficientPrivilege):
        conn.execute(SQL[name])


@pytest.mark.parametrize("name", sorted(SLOW))
def test_slow_statements_are_cancelled(session, name) -> None:
    conn, _ = session
    with pytest.raises(errors.QueryCanceled):
        conn.execute(SQL[name])


@pytest.mark.parametrize("name", sorted(MISSING))
def test_missing_objects_do_not_exist(session, name) -> None:
    conn, _ = session
    with pytest.raises(errors.UndefinedFunction):
        conn.execute(SQL[name])


@pytest.mark.parametrize("name", sorted(NOT_THE_ROLES_JOB))
def test_what_the_role_lets_through_is_documented(session, name) -> None:
    conn, _ = session
    conn.execute(SQL[name])  # runs: the guard is the layer that blocks it


def test_grant_grants_nothing(session) -> None:
    conn, mode = session
    if mode == "read_only_default":
        with pytest.raises(errors.ReadOnlySqlTransaction):
            conn.execute(SQL[GRANT])
        return
    conn.execute(SQL[GRANT])  # accepted with a warning, not an error
    conn.commit()
    public_can_read = "SELECT has_table_privilege('public', 'suppliers', 'SELECT')"
    assert conn.execute(public_can_read).fetchone()[0] is False
    grants_by_app_ro = (
        "SELECT count(*) FROM pg_class AS c, aclexplode(c.relacl) AS acl"
        " WHERE c.relname = 'suppliers' AND acl.grantor = 'app_ro'::regrole"
    )
    assert conn.execute(grants_by_app_ro).fetchone()[0] == 0
