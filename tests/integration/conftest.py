"""Integration tests run against the compose database, always as the read-only role app_ro.

Start it with `docker compose up -d --wait db` (published on 127.0.0.1:55432).
"""

import os

import psycopg
import pytest

DEFAULT_URL = "postgresql://app_ro:app_ro_local@127.0.0.1:55432/procurement"
DATABASE_URL = os.environ.get("DATABASE_URL", DEFAULT_URL)


@pytest.fixture(scope="session")
def database_url() -> str:
    return DATABASE_URL


@pytest.fixture
def ro():
    """A fresh app_ro connection per test (a failed statement aborts its transaction)."""
    try:
        conn = psycopg.connect(DATABASE_URL, connect_timeout=5)
    except psycopg.OperationalError as exc:
        start = "docker compose up -d --wait db"
        pytest.fail(f"database not reachable at {DATABASE_URL}; run `{start}` first ({exc})")
    yield conn
    conn.close()
