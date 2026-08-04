"""Registry for the representative triggered abilities supported by MTGNP."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TriggerEvent(StrEnum):
    ATTACKS = "ATTACKS"
    NONCREATURE_SPELL_CAST = "NONCREATURE_SPELL_CAST"
    BECOMES_TARGET = "BECOMES_TARGET"
    ENTERS_BATTLEFIELD = "ENTERS_BATTLEFIELD"


class TriggerEffect(StrEnum):
    REVEAL_DEFENDER_TOP_CARD = "REVEAL_DEFENDER_TOP_CARD"
    PROWESS = "PROWESS"
    SACRIFICE_SOURCE = "SACRIFICE_SOURCE"
    DEVOTION_DRAIN = "DEVOTION_DRAIN"
    RETURN_CREATURE_FROM_GRAVEYARD = "RETURN_CREATURE_FROM_GRAVEYARD"


@dataclass(frozen=True, slots=True)
class TriggerSpec:
    event: TriggerEvent
    effect: TriggerEffect
    summary: str
    requires_target: bool = False
    optional: bool = False


TRIGGERED_ABILITIES: dict[str, TriggerSpec] = {
    "goblin_guide": TriggerSpec(
        event=TriggerEvent.ATTACKS,
        effect=TriggerEffect.REVEAL_DEFENDER_TOP_CARD,
        summary=(
            "Defending player reveals their top card and puts it into their "
            "hand if it is a land."
        ),
    ),
    "monastery_swiftspear": TriggerSpec(
        event=TriggerEvent.NONCREATURE_SPELL_CAST,
        effect=TriggerEffect.PROWESS,
        summary="Monastery Swiftspear gets +1/+1 until end of turn.",
    ),
    "phantasmal_bear": TriggerSpec(
        event=TriggerEvent.BECOMES_TARGET,
        effect=TriggerEffect.SACRIFICE_SOURCE,
        summary="Sacrifice Phantasmal Bear.",
    ),
    "gray_merchant": TriggerSpec(
        event=TriggerEvent.ENTERS_BATTLEFIELD,
        effect=TriggerEffect.DEVOTION_DRAIN,
        summary=(
            "Each opponent loses life equal to your devotion to black; you gain "
            "that much life."
        ),
    ),
    "gravedigger": TriggerSpec(
        event=TriggerEvent.ENTERS_BATTLEFIELD,
        effect=TriggerEffect.RETURN_CREATURE_FROM_GRAVEYARD,
        summary="Return target creature card from your graveyard to your hand.",
        requires_target=True,
    ),
}


def triggered_ability(base_id: str, event: TriggerEvent) -> TriggerSpec | None:
    """Return the supported trigger when *base_id* listens for *event*."""

    trigger = TRIGGERED_ABILITIES.get(base_id)
    if trigger is None or trigger.event != event:
        return None
    return trigger
