"""Persona version and authority validation."""

from app.coordination.generative.models import Persona


def validate_persona(persona: Persona, *, platform_authority: frozenset[str]) -> Persona:
    if not persona.authority_ceiling <= platform_authority:
        raise PermissionError("persona cannot expand execution authority")
    if any("secret" in trait.casefold() for trait in persona.public_traits):
        raise ValueError("public persona traits cannot contain secret material")
    return persona


__all__ = ["validate_persona"]
