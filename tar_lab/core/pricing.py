"""API list prices, used to express subscription usage as an equivalent dollar estimate."""

from __future__ import annotations

from .workorder import Usage

# US dollars per million tokens: (uncached input, cached input, output).
# Source: https://developers.openai.com/api/docs/models/gpt-6.1-sol (checked 2026-11-17).
PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-6.1-sol": (2.00, 0.10, 10.00),
}


def estimate_usd(model: str, usage: Usage) -> float | None:
    """Dollar estimate of ``usage``; ``input_tokens`` includes the cached ones."""
    price = PRICES.get(model)
    if price is None:
        return None
    fresh = max(0, usage.input_tokens - usage.cached_input_tokens)
    return (fresh * price[0] + usage.cached_input_tokens * price[1]
            + usage.output_tokens * price[2]) / 1e6
