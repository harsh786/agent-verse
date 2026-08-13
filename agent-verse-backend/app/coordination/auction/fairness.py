"""Bounded opportunity/exposure fairness adjustment."""


def bounded_fairness_adjustment(*, opportunities: int, exposures: int, cap: int) -> int:
    if min(opportunities, exposures, cap) < 0:
        raise ValueError("fairness inputs must be non-negative")
    if opportunities == exposures:
        return 0
    denominator = max(1, opportunities + exposures)
    raw = (opportunities - exposures) * 10_000 // denominator
    return max(-cap, min(cap, raw))


__all__ = ["bounded_fairness_adjustment"]
