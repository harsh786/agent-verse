"""PromptVariantSelector — deterministic A/B variant selection per goal_id."""
from __future__ import annotations
import hashlib
from dataclasses import dataclass


@dataclass
class PromptVariant:
    variant_id: str
    description: str = ""


class PromptVariantSelector:
    def select(self, goal_id: str, variant_pool: list[str]) -> PromptVariant:
        if not variant_pool:
            return PromptVariant("default")
        idx = int(hashlib.md5(goal_id.encode()).hexdigest(), 16) % len(variant_pool)
        return PromptVariant(variant_id=variant_pool[idx])
