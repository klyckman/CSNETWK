"""Declarative specifications for implemented nonpermanent spell effects."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


class SpellTarget(StrEnum):
    """Legal target family for a supported spell."""

    NONE = "NONE"
    ANY = "ANY"
    PLAYER = "PLAYER"
    CREATURE = "CREATURE"
    PERMANENT = "PERMANENT"
    GRAVEYARD_CREATURE = "GRAVEYARD_CREATURE"
    SPELL = "SPELL"
    NONCREATURE_SPELL = "NONCREATURE_SPELL"


class SpellEffect(StrEnum):
    """Resolution operation performed by a supported spell."""

    DAMAGE = "DAMAGE"
    RETURN_TO_HAND = "RETURN_TO_HAND"
    COUNTER = "COUNTER"
    MODIFY_STATS = "MODIFY_STATS"
    DESTROY = "DESTROY"
    SEARCH_LIBRARY = "SEARCH_LIBRARY"
    ADD_MANA = "ADD_MANA"
    RETURN_FROM_GRAVEYARD = "RETURN_FROM_GRAVEYARD"


@dataclass(frozen=True, slots=True)
class SpellSpec:
    """Rules data shared by casting validation and Stack resolution."""

    target: SpellTarget
    effect: SpellEffect
    amount: int = 0
    power: int = 0
    toughness: int = 0
    excluded_color: str | None = None
    target_types: frozenset[str] = frozenset()
    excluded_types: frozenset[str] = frozenset()
    mana_addition: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )
    prevent_regeneration: bool = False


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
        # O1B zone and resource spells.
        "naturalize": SpellSpec(
            SpellTarget.PERMANENT,
            SpellEffect.DESTROY,
            target_types=frozenset({"Artifact", "Enchantment"}),
        ),
        "terror": SpellSpec(
            SpellTarget.PERMANENT,
            SpellEffect.DESTROY,
            excluded_color="B",
            target_types=frozenset({"Creature"}),
            excluded_types=frozenset({"Artifact"}),
        ),
        "raise_dead": SpellSpec(
            SpellTarget.GRAVEYARD_CREATURE,
            SpellEffect.RETURN_FROM_GRAVEYARD,
        ),
        "rampant_growth": SpellSpec(
            SpellTarget.NONE,
            SpellEffect.SEARCH_LIBRARY,
        ),
        "dark_ritual": SpellSpec(
            SpellTarget.NONE,
            SpellEffect.ADD_MANA,
            mana_addition=MappingProxyType({"B": 3}),
        ),
        "incinerate": SpellSpec(
            SpellTarget.ANY,
            SpellEffect.DAMAGE,
            amount=3,
            prevent_regeneration=True,
        ),
    }
)


def spell_spec(base_id: str) -> SpellSpec | None:
    """Return an implemented spell specification, if one exists."""

    return SPELLS.get(base_id)
