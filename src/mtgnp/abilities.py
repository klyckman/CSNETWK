"""Shared definitions for the activated abilities supported by this milestone."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


class AbilityTarget(StrEnum):
    ANY = "ANY"
    PLAYER = "PLAYER"
    TAPPED_CREATURE = "TAPPED_CREATURE"


class AbilityEffect(StrEnum):
    DAMAGE = "DAMAGE"
    DESTROY = "DESTROY"
    MILL = "MILL"


@dataclass(frozen=True, slots=True)
class ActivatedAbilitySpec:
    """Rules needed to validate, display, and resolve one activated ability."""

    tap_cost: bool
    mana_cost: Mapping[str, int]
    target: AbilityTarget
    effect: AbilityEffect
    amount: int
    summary: str

    def cost_payment(self) -> dict[str, object]:
        return {"tap": self.tap_cost, "mana": dict(self.mana_cost)}


def _ability(
    *,
    mana_cost: Mapping[str, int] | None = None,
    target: AbilityTarget,
    effect: AbilityEffect,
    amount: int,
    summary: str,
) -> ActivatedAbilitySpec:
    return ActivatedAbilitySpec(
        tap_cost=True,
        mana_cost=MappingProxyType(dict(mana_cost or {})),
        target=target,
        effect=effect,
        amount=amount,
        summary=summary,
    )


# Tuple position is the RFC's zero-based ability_index. The first slice uses
# abilities whose choices fit ACTIVATE_ABILITY without an additional request PDU.
ACTIVATED_ABILITIES: Mapping[str, tuple[ActivatedAbilitySpec, ...]] = (
    MappingProxyType(
        {
            "prodigal_sorcerer": (
                _ability(
                    target=AbilityTarget.ANY,
                    effect=AbilityEffect.DAMAGE,
                    amount=1,
                    summary="Tap: deal 1 damage to a player or creature.",
                ),
            ),
            "royal_assassin": (
                _ability(
                    target=AbilityTarget.TAPPED_CREATURE,
                    effect=AbilityEffect.DESTROY,
                    amount=1,
                    summary="Tap: destroy target tapped creature.",
                ),
            ),
            "millstone": (
                _ability(
                    mana_cost={"X": 2},
                    target=AbilityTarget.PLAYER,
                    effect=AbilityEffect.MILL,
                    amount=2,
                    summary="Pay 2 and tap: target player mills two cards.",
                ),
            ),
            "rod_of_ruin": (
                _ability(
                    mana_cost={"X": 3},
                    target=AbilityTarget.ANY,
                    effect=AbilityEffect.DAMAGE,
                    amount=1,
                    summary="Pay 3 and tap: deal 1 damage to a player or creature.",
                ),
            ),
        }
    )
)


def activated_ability(
    base_id: str, ability_index: int
) -> ActivatedAbilitySpec | None:
    abilities = ACTIVATED_ABILITIES.get(base_id, ())
    if isinstance(ability_index, bool) or not isinstance(ability_index, int):
        return None
    if ability_index < 0 or ability_index >= len(abilities):
        return None
    return abilities[ability_index]
