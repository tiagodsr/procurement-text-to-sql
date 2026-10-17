"""Static checks on the Compose files and the init scripts (no Docker needed)."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def code_lines(path: Path) -> list[str]:
    """Lines without comments, so the checks look at configuration, not at explanations."""
    lines = (line.split("#", 1)[0].rstrip() for line in path.read_text().splitlines())
    return [line for line in lines if line.strip()]


def test_base_compose_publishes_no_port():
    assert not any("ports:" in line for line in code_lines(REPO_ROOT / "docker-compose.yml"))


def test_dev_compose_publishes_only_on_loopback():
    ports = re.findall(r'"([^"]+:\d+)"', (REPO_ROOT / "compose.dev.yml").read_text())
    assert ports == ["127.0.0.1:55432:5432"]


def test_build_and_compose_use_the_same_pinned_image():
    compose = re.search(r"image: (postgres:\S+)", (REPO_ROOT / "docker-compose.yml").read_text())
    make = re.search(r"PG_IMAGE\s*:=\s*(\S+)", (REPO_ROOT / "Makefile").read_text())
    assert compose[1] == make[1]
    assert re.fullmatch(r"postgres:16\.\d+-\w+", compose[1])


def test_init_scripts_are_mounted_read_only():
    assert (
        "./data/init:/docker-entrypoint-initdb.d:ro"
        in (REPO_ROOT / "docker-compose.yml").read_text()
    )


def test_init_scripts_run_dump_before_roles():
    names = sorted(p.name for p in (REPO_ROOT / "data" / "init").iterdir())
    assert names == ["01_dump.sql.gz", "02_roles.sql"]


def test_app_ro_gets_select_only():
    sql = " ".join(code_lines(REPO_ROOT / "data" / "init" / "02_roles.sql")).upper()
    grants = re.findall(r"GRANT (\w+(?:, \w+)*) ON", sql)
    assert sorted(grants) == ["CONNECT", "SELECT", "USAGE"]
    assert "DEFAULT_TRANSACTION_READ_ONLY = ON" in sql
