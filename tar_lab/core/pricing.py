"""API list prices, used to express subscription usage as an equivalent dollar estimate."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from .ledger import Event, OrderState
from .workorder import Usage

MAIN_AGENT_USAGE = frozenset({"pi.decision", "intake.answers", "project.charter"})

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


@dataclass(frozen=True)
class Spend:
    usd: float
    unpriced: tuple[str, ...]  # orders whose model has no known price, counted as $0


def order_cost(state: OrderState, model: str) -> float | None:
    if state.result is not None and state.result.cost_usd is not None:
        return state.result.cost_usd
    return estimate_usd(model, state.usage)


def project_spend(events: Iterable[Event], orders: Mapping[str, OrderState],
                  model_of: Callable[[str], str], main_model: str) -> Spend:
    """Estimated dollars spent: work orders plus the main agent's own calls."""
    usd, unpriced = 0.0, []
    for oid, state in orders.items():
        cost = order_cost(state, model_of(state.order.executor))
        if cost is None and state.usage.total_tokens:
            unpriced.append(oid)
        usd += cost or 0.0
    for e in events:
        if e.kind in MAIN_AGENT_USAGE and "usage" in e.payload:
            usd += estimate_usd(main_model, Usage(**e.payload["usage"])) or 0.0
    return Spend(usd, tuple(unpriced))
