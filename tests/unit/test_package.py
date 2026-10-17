import tomllib
from pathlib import Path

import assistant

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_package_imports_with_a_version():
    assert assistant.__version__


def test_pricing_file_has_a_source_and_unit():
    pricing = tomllib.loads((REPO_ROOT / "config" / "pricing.toml").read_text())
    assert pricing["meta"]["source"].startswith("https://")
    assert pricing["meta"]["unit"] == "per 1M tokens"
