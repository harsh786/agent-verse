"""LLM pricing table for accurate cost estimation.
Uses per-1k-token pricing from each provider's public pricing page.
"""
from __future__ import annotations

# model_name_fragment: (input_cost_per_1k, output_cost_per_1k)
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4":    (0.015,  0.075),
    "claude-sonnet-4":  (0.003,  0.015),
    "claude-haiku-3":   (0.00025, 0.00125),
    "claude-opus-3":    (0.015,  0.075),
    "claude-sonnet-3":  (0.003,  0.015),
    "claude-haiku":     (0.00025, 0.00125),
    "gpt-4o-mini":      (0.00015, 0.0006),
    "gpt-4o":           (0.005,  0.015),
    "gpt-4":            (0.03,   0.06),
    "gpt-3.5":          (0.0005, 0.0015),
    "llama-3.1-70b":    (0.0009, 0.0009),
    "llama-3.1-8b":     (0.0002, 0.0002),
    "mixtral":          (0.0007, 0.0007),
    "gemini-pro":       (0.0005, 0.0015),
    "gemini-flash":     (0.00015, 0.0006),
}

# Fallback rate when model is unknown
_DEFAULT_INPUT_RATE = 0.005  # per 1k tokens
_DEFAULT_OUTPUT_RATE = 0.015


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for a completion. Matches model name fragment."""
    model_lower = model.lower()
    for fragment, (inp_rate, out_rate) in _PRICING.items():
        if fragment in model_lower:
            return (input_tokens * inp_rate + output_tokens * out_rate) / 1000
    # Unknown model: use GPT-4o pricing as conservative estimate
    return (input_tokens * _DEFAULT_INPUT_RATE + output_tokens * _DEFAULT_OUTPUT_RATE) / 1000


def format_cost(usd: float) -> str:
    """Format cost for display: $0.0012 or $1.23."""
    if usd < 0.01:
        return f"${usd:.4f}"
    return f"${usd:.2f}"
