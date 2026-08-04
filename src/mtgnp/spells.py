"""Declarative specifications for implemented nonpermanent spell effects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


class SpellTarget(StrEnum):
    """Legal target family for a supported spell."""

    NONE = "NONE"
    ANY = "ANY"
    PLAYER = "PLAYER"
    CREATURE = "CREATURE"
    SPELL = "SPELL"
    NONCREATURE_SPELL = "NONCREATURE_SPELL"


class SpellEffect(StrEnum):
    """Resolution operation performed by a supported spell."""

    DAMAGE = "DAMAGE"
    RETURN_TO_HAND = "RETURN_TO_HAND"
    COUNTER = "COUNTER"
    MODIFY_STATS = "MODIFY_STATS"
    DESTROY = "DESTROY"


@dataclass(frozen=True, slots=True)
class SpellSpec:
    """Rules data shared by casting validation and Stack resolution."""

    target: SpellTarget
    effect: SpellEffect
    amount: int = 0
    power: int = 0
    toughness: int = 0
    excluded_color: str | None = None


SPELLS: Mapping[str, SpellSpec] = MappingProxyType(
    {
        # Direct damage family.
        "lightning_bolt": SpellSpec(
            SpellTarget.ANY, SpellEffect.DAMAGE, amount=3
        ),
        "shock": SpellSpec(SpellTarget.ANY, SpellEffect.DAMAGE, amount=2),
        "lava_spike": SpellSpec(
            SpellTarget.PLAYER, SpellEffect.DAMAGE, amount=3
        ),
        "flame_slash": SpellSpec(
            SpellTarget.CREATURE, SpellEffect.DAMAGE, amount=4
        ),
        "searing_spear": SpellSpec(
            SpellTarget.ANY, SpellEffect.DAMAGE, amount=3
        ),
        # Counterspell family.
        "counterspell": SpellSpec(SpellTarget.SPELL, SpellEffect.COUNTER),
        "cancel": SpellSpec(SpellTarget.SPELL, SpellEffect.COUNTER),
        "negate": SpellSpec(
            SpellTarget.NONCREATURE_SPELL, SpellEffect.COUNTER
        ),
        # Existing base-milestone spell effects.
        "unsummon": SpellSpec(
            SpellTarget.CREATURE, SpellEffect.RETURN_TO_HAND
        ),
        "giant_growth": SpellSpec(
            SpellTarget.CREATURE,
            SpellEffect.MODIFY_STATS,
            power=3,
            toughness=3,
        ),
        "doom_blade": SpellSpec(
            SpellTarget.CREATURE,
            SpellEffect.DESTROY,
            excluded_color="B",
        ),
    }
)


def spell_spec(base_id: str) -> SpellSpec | None:
    """Return an implemented spell specification, if one exists."""

    return SPELLS.get(base_id)
