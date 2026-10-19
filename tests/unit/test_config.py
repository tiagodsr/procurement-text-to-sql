"""Spec for assistant.config: settings from the environment and prices from TOML."""

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from assistant.config import (
    REPO_ROOT,
    PricingError,
    Settings,
    load_pricing,
    load_settings,
)

META = """
[meta]
as_of = "2026-10-18"
source = "https://example.test/pricing"
currency = "USD"
unit = "per 1M tokens"
"""

# Round, obviously fictitious prices: tests never depend on real ones.
TEST_MODELS = """
[models."test-chat"]
kind = "chat"
input = 1.00
cached_input = 0.50
output = 4.00

[models."test-embed"]
kind = "embedding"
input = 0.10
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "pricing.toml"
    path.write_text(text, encoding="utf-8")
    return path


# --- settings ---------------------------------------------------------------------------------


def test_defaults_with_an_empty_environment():
    s = load_settings({})
    assert s.llm_model == "gpt-4.1-mini"
    assert s.embedding_model == "text-embedding-3-small"
    assert s.trace_dir == REPO_ROOT / "var" / "traces"
    assert s.pricing_path == REPO_ROOT / "config" / "pricing.toml"
    assert s.database_url.startswith("postgresql://app_ro:")


def test_environment_overrides_and_empty_values_count_as_unset(tmp_path):
    s = load_settings(
        {
            "LLM_MODEL": "other-model",
            "LLM_TIMEOUT_S": "5",
            "TRACE_DIR": str(tmp_path),
            "EMBEDDING_MODEL": " ",
        }
    )
    assert s.llm_model == "other-model"
    assert s.llm_timeout_s == 5.0
    assert s.trace_dir == tmp_path
    assert s.embedding_model == "text-embedding-3-small"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://postgres:secret@db:5432/procurement",
        "postgresql://127.0.0.1:55432/procurement",
    ],
)
def test_database_url_must_use_the_read_only_role(url):
    with pytest.raises(ValidationError, match="app_ro"):
        load_settings({"DATABASE_URL": url})


def test_missing_key_means_llm_not_configured():
    assert load_settings({}).llm_configured is False


def test_key_is_never_shown():
    s = load_settings({"OPENAI_API_KEY": "sk-test-not-a-real-key"})
    assert s.llm_configured is True
    assert "sk-test" not in repr(s)
    assert "sk-test" not in str(s.model_dump())


def test_timeout_must_be_positive():
    with pytest.raises(ValidationError):
        Settings(llm_timeout_s=0)


# --- pricing ----------------------------------------------------------------------------------


def test_prices_are_read_as_decimal(tmp_path):
    pricing = load_pricing(write(tmp_path, META + TEST_MODELS))
    chat = pricing.price("test-chat")
    assert chat.input == Decimal("1.00")
    assert chat.cached_input == Decimal("0.50")
    assert pricing.price("test-embed").output is None


def test_unpriced_model_raises(tmp_path):
    pricing = load_pricing(write(tmp_path, META + TEST_MODELS))
    with pytest.raises(PricingError, match="nope"):
        pricing.price("nope")


@pytest.mark.parametrize(
    "models",
    [
        '[models.m]\nkind = "chat"\ninput = -1.0\noutput = 1.0\n',  # negative price
        '[models.m]\nkind = "chat"\ninput = 1.0\n',  # chat without output
        '[models.m]\nkind = "embedding"\ninput = 1.0\noutput = 1.0\n',  # embedding with output
        '[models.m]\nkind = "chat"\ninput = 1.0\noutput = 1.0\nbatch = 0.5\n',  # unknown key
        '[models.m]\nkind = "audio"\ninput = 1.0\n',  # unknown kind
    ],
)
def test_invalid_price_tables_are_rejected(tmp_path, models):
    with pytest.raises(ValidationError):
        load_pricing(write(tmp_path, META + models))


def test_prices_need_an_as_of_date(tmp_path):
    undated = META.replace('as_of = "2026-10-18"', 'as_of = ""')
    with pytest.raises(ValidationError, match="as_of"):
        load_pricing(write(tmp_path, undated + TEST_MODELS))


def test_committed_pricing_file_loads():
    pricing = load_pricing(REPO_ROOT / "config" / "pricing.toml")
    assert pricing.source.startswith("https://")
    assert pricing.unit == "per 1M tokens"
