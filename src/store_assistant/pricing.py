"""Pricing constants and per-turn cost computation.

Single source of truth for model pricing. The UI does not compute cost; it
reads the persisted cost_usd from turn_metrics. Verify the constants below
against console.anthropic.com/pricing on every model swap.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

# Pricing per 1M tokens for claude-sonnet-4-5. Decimal — never float — to
# avoid representation drift when summed across turns for billing-style
# aggregates in the session totals panel.
CLAUDE_SONNET_4_5_INPUT_PER_MTOK = Decimal("3.00")
CLAUDE_SONNET_4_5_OUTPUT_PER_MTOK = Decimal("15.00")
CLAUDE_SONNET_4_5_CACHE_READ_PER_MTOK = Decimal("0.30")
CLAUDE_SONNET_4_5_CACHE_WRITE_PER_MTOK = Decimal("3.75")

_ONE_MILLION = Decimal("1000000")
# Six-decimal rounding matches the NUMERIC(10,6) cost_usd column. Doing the
# rounding here means the value persisted is exactly the value the UI would
# format — no half-cent drift between display and storage.
_QUANTUM = Decimal("0.000001")


def compute_cost(
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> Decimal:
    """Compute USD cost for a single turn given token counts.

    Returns a Decimal quantized to 6 decimal places (matches the schema).
    """
    cost = (
        Decimal(input_tokens) * CLAUDE_SONNET_4_5_INPUT_PER_MTOK
        + Decimal(output_tokens) * CLAUDE_SONNET_4_5_OUTPUT_PER_MTOK
        + Decimal(cache_read_tokens) * CLAUDE_SONNET_4_5_CACHE_READ_PER_MTOK
        + Decimal(cache_creation_tokens) * CLAUDE_SONNET_4_5_CACHE_WRITE_PER_MTOK
    ) / _ONE_MILLION
    return cost.quantize(_QUANTUM, rounding=ROUND_HALF_UP)
