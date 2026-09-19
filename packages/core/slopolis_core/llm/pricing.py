"""Static USD price table for chat models (spec 10.2).

Budgets and full cost accounting are deferred, but every completion still
reports a best-effort cost. Prices are **USD per 1,000 tokens** as
``(prompt, completion)``. LiteLLM-reported cost wins when present; this table
is the fallback when the gateway omits it.
"""

__all__ = ["DEFAULT_PRICE", "PRICE_TABLE", "compute_cost"]

#: (prompt_usd_per_1k, completion_usd_per_1k) used when a model is unknown.
DEFAULT_PRICE: tuple[float, float] = (0.005, 0.015)

#: Per-1k-token prices for common models. Keys are matched case-sensitively on
#: the exact model id, then by a normalized suffix (see :func:`_lookup_price`).
PRICE_TABLE: dict[str, tuple[float, float]] = {
    "gpt-4o": (0.0025, 0.010),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4.1": (0.002, 0.008),
    "gpt-4.1-mini": (0.0004, 0.0016),
    "o3-mini": (0.0011, 0.0044),
    "claude-3-5-sonnet": (0.003, 0.015),
    "claude-3-7-sonnet": (0.003, 0.015),
    "claude-sonnet-4": (0.003, 0.015),
    "claude-3-5-haiku": (0.0008, 0.004),
    "claude-3-haiku": (0.00025, 0.00125),
    "gemini-1.5-pro": (0.00125, 0.005),
    "gemini-1.5-flash": (0.000075, 0.0003),
    "gemini-2.0-flash": (0.0001, 0.0004),
    "deepseek-chat": (0.00027, 0.0011),
    "deepseek-reasoner": (0.00055, 0.00219),
}


def _lookup_price(model: str) -> tuple[float, float]:
    """Resolve a price entry, tolerating provider prefixes and date suffixes."""
    if model in PRICE_TABLE:
        return PRICE_TABLE[model]

    normalized = model.split("/", 1)[-1].lower()
    if normalized in PRICE_TABLE:
        return PRICE_TABLE[normalized]

    # Providers append variants, e.g. "claude-3-5-sonnet-20241022".
    for key, price in PRICE_TABLE.items():
        if normalized.startswith(key):
            return price

    return DEFAULT_PRICE


def compute_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Return the estimated USD cost for a completion.

    ``prompt_tokens`` and ``completion_tokens`` are charged at their respective
    per-1k rates. Unknown models fall back to :data:`DEFAULT_PRICE`.
    """
    prompt_price, completion_price = _lookup_price(model)
    return (prompt_tokens / 1000.0) * prompt_price + (
        completion_tokens / 1000.0
    ) * completion_price
