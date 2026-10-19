"""The cost of one API call, from token counts and the prices in config/pricing.toml.

Demo:
    python -m assistant.pricing gpt-4.1-mini 1200 200 300   # model, input, cached input, output
"""

from __future__ import annotations

import argparse
from decimal import Decimal
from typing import Literal

from assistant.config import ModelPrice, Pricing, PricingError, load_pricing, load_settings

PER_TOKENS = Decimal(1_000_000)


def price_for(pricing: Pricing, model: str, kind: Literal["chat", "embedding"]) -> ModelPrice:
    """The model's price, checked before any call: unpriced or wrong kind raises PricingError."""
    price = pricing.price(model)
    if price.kind != kind:
        raise PricingError(f"model {model!r} is priced as {price.kind}, not {kind}")
    return price


def cost_usd(
    price: ModelPrice, *, input_tokens: int, cached_input_tokens: int = 0, output_tokens: int = 0
) -> Decimal:
    """USD for one call. Cached input tokens are part of input_tokens; reasoning tokens are part
    of output_tokens. Without a cached price, cached tokens are billed at the input price."""
    cached_rate = price.input if price.cached_input is None else price.cached_input
    total = (
        (input_tokens - cached_input_tokens) * price.input
        + cached_input_tokens * cached_rate
        + output_tokens * (price.output or Decimal(0))
    )
    return total / PER_TOKENS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("model")
    parser.add_argument("tokens", nargs="+", type=int, help="input [cached_input [output]]")
    args = parser.parse_args()
    price = load_pricing(load_settings().pricing_path).price(args.model)
    names = ("input_tokens", "cached_input_tokens", "output_tokens")
    print(f"{cost_usd(price, **dict(zip(names, args.tokens, strict=False)))} USD")


if __name__ == "__main__":
    main()
