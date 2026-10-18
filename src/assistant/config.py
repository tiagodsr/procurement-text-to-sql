"""Runtime settings from environment variables, and model prices from config/pricing.toml.

Demo:
    python -m assistant.config
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_DATABASE_URL = "postgresql://app_ro:app_ro_local@127.0.0.1:55432/procurement"
APP_ROLE = "app_ro"


class PricingError(LookupError):
    """A model has no price in config/pricing.toml."""


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    openai_api_key: SecretStr | None = None
    database_url: str = DEV_DATABASE_URL
    llm_model: str = "gpt-4.1-mini"
    embedding_model: str = "text-embedding-3-small"
    llm_timeout_s: float = Field(default=30.0, gt=0)
    trace_dir: Path = REPO_ROOT / "var" / "traces"
    pricing_path: Path = REPO_ROOT / "config" / "pricing.toml"

    @field_validator("database_url")
    @classmethod
    def _read_only_role(cls, url: str) -> str:
        user = urlsplit(url).username
        if user != APP_ROLE:
            raise ValueError(f"DATABASE_URL must connect as {APP_ROLE!r}, not {user!r}")
        return url

    @property
    def llm_configured(self) -> bool:
        return self.openai_api_key is not None


ENV_NAMES = {
    "openai_api_key": "OPENAI_API_KEY",
    "database_url": "DATABASE_URL",
    "llm_model": "LLM_MODEL",
    "embedding_model": "EMBEDDING_MODEL",
    "llm_timeout_s": "LLM_TIMEOUT_S",
    "trace_dir": "TRACE_DIR",
    "pricing_path": "PRICING_PATH",
}


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Settings from `env` (default: the process environment). Empty values count as unset."""
    env = os.environ if env is None else env
    values = {field: env[name] for field, name in ENV_NAMES.items() if env.get(name, "").strip()}
    return Settings(**values)


class ModelPrice(BaseModel):
    """USD per 1M tokens for one model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["chat", "embedding"]
    input: Decimal = Field(ge=0)
    cached_input: Decimal | None = Field(default=None, ge=0)
    output: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _output_matches_kind(self) -> Self:
        if self.kind == "chat" and self.output is None:
            raise ValueError("a chat model needs an output price")
        if self.kind == "embedding" and (self.output is not None or self.cached_input is not None):
            raise ValueError("an embedding model has only an input price")
        return self


class Pricing(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of: date | None = None
    source: str
    currency: Literal["USD"]
    unit: Literal["per 1M tokens"]
    models: dict[str, ModelPrice] = {}

    @model_validator(mode="after")
    def _dated_when_priced(self) -> Self:
        if self.models and self.as_of is None:
            raise ValueError("meta.as_of is required once any model has a price")
        return self

    def price(self, model: str) -> ModelPrice:
        try:
            return self.models[model]
        except KeyError:
            raise PricingError(f"no price for model {model!r} in config/pricing.toml") from None


def load_pricing(path: Path) -> Pricing:
    data = tomllib.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    meta = data.pop("meta", {})
    as_of = meta.pop("as_of", None) or None  # an empty string means "not filled in yet"
    return Pricing(as_of=as_of, **meta, **data)


def main() -> None:
    settings = load_settings()
    for field in Settings.model_fields:
        value = getattr(settings, field)
        if isinstance(value, SecretStr):
            value = "<set>"
        elif field == "database_url" and (password := urlsplit(value).password):
            value = value.replace(f":{password}@", ":***@")
        print(f"{field:16s} {value}")
    print(f"{'llm_configured':16s} {settings.llm_configured}")
    pricing = load_pricing(settings.pricing_path)
    print(
        f"\nprices as of {pricing.as_of or '(not filled in)'}, {pricing.unit}, {pricing.currency}"
    )
    for name, price in pricing.models.items():
        print(f"  {name:28s} {price.model_dump(exclude_none=True)}")


if __name__ == "__main__":
    main()
