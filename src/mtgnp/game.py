"""Authoritative, transport-independent MTGNP game state.

This milestone intentionally implements the complete lobby/setup/mulligan
foundation, a pass-driven turn loop, land play, stack mechanics, mana payment,
and a small effect registry. Full combat and triggered abilities remain later
milestones and are called out in the README.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from .catalog import CardCatalog, CardDefinition
from .protocol import ErrorCode, Lifecycle, Phase, ProtocolError

SEATS = ("seat_1", "seat_2")

EFFECT_KIND = {
    "lightning_bolt": "damage_any:3",
    "shock": "damage_any:2",
    "searing_spear": "damage_any:3",
    "lava_spike": "damage_player:3",
    "flame_slash": "damage_creature:4",
    "counterspell": "counter",
    "cancel": "counter",
    "unsummon": "bounce_creature",
    "giant_growth": "buff_creature:3:3",
    "doom_blade": "destroy_nonblack_creature",
}


@dataclass(slots=True)
class Permanent:
    instance_id: str
    owner_slot: str
    controller_slot: str
    tapped: bool = False
    damage: int = 0
    summoning_sick: bool = False
    temporary_power: int = 0
    temporary_toughness: int = 0


@dataclass(slots=True)
class StackItem:
    stack_item_id: str
    source_id: str
    controller_slot: str
    targets: list[str]
    item_type: str = "SPELL"


@dataclass(slots=True)
class PlayerState:
    seat: str
    player_id: str | None = None
    submitted_deck: list[str] = field(default_factory=list)
    library: list[str] = field(default_factory=list)
    hand: list[str] = field(default_factory=list)
    battlefield: list[Permanent] = field(default_factory=list)
    graveyard: list[str] = field(default_factory=list)
    life: int = 20
    mulligans: int = 0
    kept: bool = False
    expected_token: int | None = None

    @property
    def ready(self) -> bool:
        return bool(self.player_id and self.submitted_deck)

    def reset_runtime_state(self) -> None:
        self.library = []
        self.hand = []
        self.battlefield = []
        self.graveyard = []
        self.life = 20
        self.mulligans = 0
        self.kept = False
        self.expected_token = None

    def reset_for_lobby(self) -> None:
        self.player_id = None
        self.submitted_deck = []
        self.reset_runtime_state()


class GameSession:
    def __init__(
        self,
        catalog: CardCatalog,
        *,
        rng: random.Random | None = None,
    ) -> None:
        self.catalog = catalog
        self.rng = rng or random.SystemRandom()
        self.players = {seat: PlayerState(seat) for seat in SEATS}
        self.lifecycle = Lifecycle.LOBBY
        self.phase = Phase.LOBBY
        self.turn = 0
        self.active_slot: str | None = None
        self.first_player_slot: str | None = None
        self.priority_slot: str | None = None
        self.pass_count = 0
        self.land_played_this_turn = False
        self.stack: list[StackItem] = []
        self.next_stack_id = 1
        self.winner_slot: str | None = None
        self.loser_slot: str | None = None
        self.game_over_reason: str | None = None

    def opponent(self, seat: str) -> str:
        if seat not in SEATS:
            raise ValueError(f"Unknown seat {seat}")
        return SEATS[1] if seat == SEATS[0] else SEATS[0]

    def player_id(self, seat: str | None) -> str | None:
        return self.players[seat].player_id if seat else None

    def register_ready(self, seat: str, player_id: Any, deck: Any) -> bool:
        if self.lifecycle is not Lifecycle.LOBBY:
            raise ProtocolError(ErrorCode.WRONG_PHASE, "PLAYER_READY is only valid in LOBBY.")
        if not isinstance(player_id, str) or not player_id.strip():
            raise ProtocolError(ErrorCode.ILLEGAL_ACTION, "player_id must be a non-empty string.")
        normalized_id = player_id.strip()
        other = self.players[self.opponent(seat)]
        if other.player_id == normalized_id:
            raise ProtocolError(
                ErrorCode.DUPLICATE_ID,
                f"player_id {normalized_id!r} is already claimed.",
            )
        if not isinstance(deck, list) or not all(isinstance(item, str) for item in deck):
            raise ProtocolError(ErrorCode.ILLEGAL_DECK, "deck_list must be an array of card IDs.")

        player = self.players[seat]
        player.player_id = normalized_id
        player.submitted_deck = self.catalog.validate_deck(deck)
        return all(candidate.ready for candidate in self.players.values())

    def setup(self) -> None:
        if not all(player.ready for player in self.players.values()):
            raise RuntimeError("Both players must be ready before setup.")
        self.lifecycle = Lifecycle.GAME_SETUP
        self.turn = 0
        self.active_slot = self.rng.choice(SEATS)
        self.first_player_slot = self.active_slot
        self.stack = []
        self.next_stack_id = 1
        self.winner_slot = None
        self.loser_slot = None
        self.game_over_reason = None

        for player in self.players.values():
            player.reset_runtime_state()
            player.library = list(player.submitted_deck)
            self.rng.shuffle(player.library)
            self._draw_many(player, 7)

        self.lifecycle = Lifecycle.MULLIGAN
        self.phase = Phase.MULLIGAN

    def mulligan_choice(
        self,
        seat: str,
        *,
        keep: Any,
        cards_to_bottom: Any,
    ) -> bool:
        if self.lifecycle is not Lifecycle.MULLIGAN:
            raise ProtocolError(ErrorCode.WRONG_PHASE, "MULLIGAN_CHOICE is only valid in MULLIGAN.")
        if not isinstance(keep, bool) or not isinstance(cards_to_bottom, list):
            raise ProtocolError(
                ErrorCode.ILLEGAL_ACTION,
                "keep must be boolean and cards_to_bottom must be an array.",
            )
        if not all(isinstance(card_id, str) for card_id in cards_to_bottom):
            raise ProtocolError(ErrorCode.ILLEGAL_ACTION, "cards_to_bottom must contain card IDs.")

        player = self.players[seat]
        if player.kept:
            raise ProtocolError(ErrorCode.ILLEGAL_ACTION, "This player has already kept.")

        if not keep:
            if cards_to_bottom:
                raise ProtocolError(
                    ErrorCode.ILLEGAL_ACTION,
                    "cards_to_bottom must be empty when keep is false.",
                )
            player.library.extend(player.hand)
            player.hand.clear()
            self.rng.shuffle(player.library)
            player.mulligans += 1
            self._draw_many(player, 7)
            return False

        if len(cards_to_bottom) != player.mulligans:
            raise ProtocolError(
                ErrorCode.ILLEGAL_ACTION,
                f"Expected {player.mulligans} card(s) to bottom after "
                f"{player.mulligans} mulligan(s).",
            )
        if len(cards_to_bottom) != len(set(cards_to_bottom)):
            raise ProtocolError(ErrorCode.ILLEGAL_ACTION, "cards_to_bottom contains duplicates.")
        if any(card_id not in player.hand for card_id in cards_to_bottom):
            raise ProtocolError(
                ErrorCode.ILLEGAL_ACTION,
                "cards_to_bottom contains a card not present in the current hand.",
            )
        for card_id in cards_to_bottom:
            player.hand.remove(card_id)
        player.library[0:0] = cards_to_bottom
        player.kept = True
        return all(candidate.kept for candidate in self.players.values())

    def begin_game(self) -> None:
        if not all(player.kept for player in self.players.values()):
            raise RuntimeError("Both players must keep before IN_GAME.")
        self.lifecycle = Lifecycle.IN_GAME
        self.turn = 1
        self.phase = Phase.UNTAP
        self.priority_slot = None
        self.pass_count = 0
        self.land_played_this_turn = False

    def untap_active_player(self) -> None:
        active = self._active_player()
        for permanent in active.battlefield:
            permanent.tapped = False
            if self.catalog.definition_for_instance(permanent.instance_id).is_creature:
                permanent.summoning_sick = False
        self.land_played_this_turn = False

    def start_priority(self) -> str:
        if self.active_slot is None:
            raise RuntimeError("No active player.")
        self.priority_slot = self.active_slot
        self.pass_count = 0
        return self.priority_slot

    def pass_priority(self, seat: str) -> str:
        self.require_priority(seat)
        if self.pass_count == 0:
            self.pass_count = 1
            self.priority_slot = self.opponent(seat)
            return "switch"
        self.pass_count = 0
        self.priority_slot = None
        return "resolve" if self.stack else "advance"

    def advance_after_empty_stack(self) -> Phase:
        next_phase = {
            Phase.UPKEEP: Phase.DRAW,
            Phase.DRAW: Phase.PRECOMBAT_MAIN,
            Phase.PRECOMBAT_MAIN: Phase.BEGIN_COMBAT,
            Phase.BEGIN_COMBAT: Phase.DECLARE_ATTACKERS,
            Phase.END_OF_COMBAT: Phase.POSTCOMBAT_MAIN,
            Phase.POSTCOMBAT_MAIN: Phase.END_STEP,
            Phase.END_STEP: Phase.CLEANUP,
        }.get(self.phase)
        if next_phase is None:
            raise RuntimeError(f"No pass-driven transition is defined from {self.phase}.")
        self.phase = next_phase
        self.priority_slot = None
        self.pass_count = 0
        return next_phase

    def perform_draw_step(self) -> bool:
        if self.turn == 1 and self.active_slot == self.first_player_slot:
            return False
        active = self._active_player()
        if not active.library:
            self.end_game(self.opponent(active.seat), active.seat, "DECK_EMPTY")
            return False
        active.hand.append(active.library.pop())
        return True

    def play_land(self, seat: str, card_id: Any) -> None:
        self.require_priority(seat)
        if seat != self.active_slot:
            raise ProtocolError(ErrorCode.ILLEGAL_ACTION, "Only the active player may play a land.")
        if self.phase not in {Phase.PRECOMBAT_MAIN, Phase.POSTCOMBAT_MAIN}:
            raise ProtocolError(ErrorCode.WRONG_PHASE, "Lands may be played only during a Main Phase.")
        if self.land_played_this_turn:
            raise ProtocolError(ErrorCode.ILLEGAL_ACTION, "A land has already been played this turn.")
        if not isinstance(card_id, str) or card_id not in self.players[seat].hand:
            raise ProtocolError(ErrorCode.ILLEGAL_ACTION, "The selected card is not in your hand.")
        definition = self.catalog.definition_for_instance(card_id)
        if definition.card_type != "Land":
            raise ProtocolError(ErrorCode.ILLEGAL_ACTION, f"{definition.name} is not a land.")

        self.players[seat].hand.remove(card_id)
        self.players[seat].battlefield.append(
            Permanent(card_id, owner_slot=seat, controller_slot=seat)
        )
        self.land_played_this_turn = True
        self.pass_count = 0

    def cast_spell(
        self,
        seat: str,
        card_id: Any,
        targets: Any,
        mana_payment: Any,
    ) -> StackItem:
        self.require_priority(seat)
        player = self.players[seat]
        if not isinstance(card_id, str) or card_id not in player.hand:
            raise ProtocolError(ErrorCode.ILLEGAL_ACTION, "The selected card is not in your hand.")
        if not isinstance(targets, list) or not all(isinstance(target, str) for target in targets):
            raise ProtocolError(ErrorCode.ILLEGAL_TARGET, "targets must be an array of IDs.")
        definition = self.catalog.definition_for_instance(card_id)
        if definition.card_type == "Land":
            raise ProtocolError(ErrorCode.ILLEGAL_ACTION, "Use PLAY_LAND for land cards.")
        if not definition.is_instant and (
            seat != self.active_slot
            or self.phase not in {Phase.PRECOMBAT_MAIN, Phase.POSTCOMBAT_MAIN}
            or self.stack
        ):
            raise ProtocolError(
                ErrorCode.WRONG_PHASE,
                f"{definition.name} can be cast only at sorcery speed.",
            )
        if definition.card_type in {"Instant", "Sorcery"} and definition.base_id not in EFFECT_KIND:
            raise ProtocolError(
                ErrorCode.ILLEGAL_ACTION,
                f"{definition.name}'s effect is not implemented in this milestone.",
            )
        if definition.card_type == "Enchantment":
            raise ProtocolError(
                ErrorCode.ILLEGAL_ACTION,
                "Aura/enchantment resolution is scheduled for a later milestone.",
            )

        self._validate_cast_targets(definition, targets)
        self._consume_mana(seat, definition, mana_payment)
        player.hand.remove(card_id)
        item = StackItem(
            stack_item_id=f"stk_{self.next_stack_id:04d}",
            source_id=card_id,
            controller_slot=seat,
            targets=list(targets),
        )
        self.next_stack_id += 1
        self.stack.append(item)
        self.pass_count = 0
        return item

    def resolve_top(self) -> tuple[StackItem, str, list[dict[str, Any]]]:
        if not self.stack:
            raise RuntimeError("Cannot resolve an empty stack.")
        item = self.stack.pop()
        definition = self.catalog.definition_for_instance(item.source_id)
        changes: list[dict[str, Any]] = []
        result = "RESOLVED"

        if definition.is_permanent and definition.card_type != "Enchantment":
            haste = "Haste." in definition.effect
            permanent = Permanent(
                item.source_id,
                owner_slot=item.controller_slot,
                controller_slot=item.controller_slot,
                summoning_sick=definition.is_creature and not haste,
            )
            self.players[item.controller_slot].battlefield.append(permanent)
            changes.append(
                {
                    "change_type": "PERMANENT_ENTERS",
                    "card_id": item.source_id,
                    "controller": self.player_id(item.controller_slot),
                }
            )
        else:
            effect = EFFECT_KIND.get(definition.base_id)
            if not effect or not self._targets_are_legal(effect, item.targets):
                result = "FIZZLE"
            else:
                changes.extend(self._apply_effect(effect, item))
            self.players[item.controller_slot].graveyard.append(item.source_id)

        changes.extend(self.check_state_based_actions())
        return item, result, changes

    def discard(self, seat: str, card_ids: Any) -> bool:
        if self.phase is not Phase.CLEANUP or seat != self.active_slot:
            raise ProtocolError(ErrorCode.WRONG_PHASE, "DISCARD is valid only for the active player at Cleanup.")
        if not isinstance(card_ids, list) or not card_ids:
            raise ProtocolError(ErrorCode.ILLEGAL_ACTION, "card_ids must be a non-empty array.")
        if len(card_ids) != len(set(card_ids)) or any(
            not isinstance(card_id, str) or card_id not in self.players[seat].hand
            for card_id in card_ids
        ):
            raise ProtocolError(ErrorCode.ILLEGAL_ACTION, "DISCARD contains an invalid hand card.")
        for card_id in card_ids:
            self.players[seat].hand.remove(card_id)
            self.players[seat].graveyard.append(card_id)
        return len(self.players[seat].hand) <= 7

    def perform_cleanup(self) -> None:
        for player in self.players.values():
            for permanent in player.battlefield:
                permanent.damage = 0
                permanent.temporary_power = 0
                permanent.temporary_toughness = 0

    def start_next_turn(self) -> tuple[str, str]:
        previous_slot = self._active_player().seat
        self.turn += 1
        self.active_slot = self.opponent(previous_slot)
        self.phase = Phase.UNTAP
        self.priority_slot = None
        self.pass_count = 0
        return previous_slot, self.active_slot

    def declare_no_attackers(self, seat: str, attackers: Any) -> None:
        if self.phase is not Phase.DECLARE_ATTACKERS or seat != self.active_slot:
            raise ProtocolError(
                ErrorCode.WRONG_PHASE,
                "DECLARE_ATTACKERS is valid only for the active player in that step.",
            )
        if not isinstance(attackers, list):
            raise ProtocolError(ErrorCode.ILLEGAL_ACTION, "attackers must be an array.")
        if attackers:
            raise ProtocolError(
                ErrorCode.ILLEGAL_ACTION,
                "Non-empty combat declarations are scheduled for the combat milestone.",
            )
        self.phase = Phase.END_OF_COMBAT

    def require_priority(self, seat: str) -> None:
        if self.lifecycle is not Lifecycle.IN_GAME:
            raise ProtocolError(ErrorCode.WRONG_PHASE, "This action requires an active game.")
        if self.priority_slot != seat:
            raise ProtocolError(ErrorCode.NOT_YOUR_PRIORITY, "This player does not hold priority.")

    def check_state_based_actions(self) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        for controller_slot, player in self.players.items():
            for permanent in list(player.battlefield):
                definition = self.catalog.definition_for_instance(permanent.instance_id)
                if not definition.is_creature:
                    continue
                toughness = (definition.toughness or 0) + permanent.temporary_toughness
                if toughness <= 0 or permanent.damage >= toughness:
                    self._move_permanent_to_graveyard(permanent)
                    changes.append(
                        {
                            "change_type": "DESTROY",
                            "target": permanent.instance_id,
                        }
                    )

        losing_slots = [seat for seat, player in self.players.items() if player.life <= 0]
        if len(losing_slots) == 2:
            loser = self.active_slot or losing_slots[0]
            self.end_game(self.opponent(loser), loser, "LIFE_ZERO")
        elif len(losing_slots) == 1:
            loser = losing_slots[0]
            self.end_game(self.opponent(loser), loser, "LIFE_ZERO")
        return changes

    def end_game(self, winner_slot: str, loser_slot: str, reason: str) -> None:
        self.lifecycle = Lifecycle.GAME_OVER
        self.priority_slot = None
        self.winner_slot = winner_slot
        self.loser_slot = loser_slot
        self.game_over_reason = reason

    def game_over_payload(self) -> dict[str, Any]:
        if not self.winner_slot or not self.loser_slot or not self.game_over_reason:
            raise RuntimeError("No completed game is available.")
        return {
            "winner_id": self.player_id(self.winner_slot),
            "loser_id": self.player_id(self.loser_slot),
            "reason": self.game_over_reason,
        }

    def reset_to_lobby(self) -> None:
        for player in self.players.values():
            player.reset_for_lobby()
        self.lifecycle = Lifecycle.LOBBY
        self.phase = Phase.LOBBY
        self.turn = 0
        self.active_slot = None
        self.first_player_slot = None
        self.priority_slot = None
        self.pass_count = 0
        self.land_played_this_turn = False
        self.stack = []
        self.winner_slot = None
        self.loser_slot = None
        self.game_over_reason = None

    def visible_state(self, viewer_slot: str) -> dict[str, Any]:
        viewer = self.players[viewer_slot]
        if not viewer.player_id:
            raise RuntimeError("Viewer has no player_id.")
        opponent = self.players[self.opponent(viewer_slot)]
        player_ids = [player.player_id for player in self.players.values()]
        if any(player_id is None for player_id in player_ids):
            raise RuntimeError("Both player IDs are required for a game-state update.")

        return {
            "turn": self.turn,
            "active_player": self.player_id(self.active_slot),
            "phase": self.phase.value,
            "priority_holder": self.player_id(self.priority_slot),
            "life_totals": {
                player.player_id: player.life for player in self.players.values()
            },
            "stack": [
                {
                    "stack_item_id": item.stack_item_id,
                    "item_type": item.item_type,
                    "source": item.source_id,
                    "targets": list(item.targets),
                    "controller": self.player_id(item.controller_slot),
                }
                for item in self.stack
            ],
            "battlefield": {
                player.player_id: [
                    self._public_permanent(permanent) for permanent in player.battlefield
                ]
                for player in self.players.values()
            },
            "graveyard": {
                player.player_id: list(player.graveyard)
                for player in self.players.values()
            },
            "hand": {viewer.player_id: list(viewer.hand)},
            "hand_counts": {opponent.player_id: len(opponent.hand)},
            "library_counts": {
                player.player_id: len(player.library)
                for player in self.players.values()
            },
            "land_played_this_turn": self.land_played_this_turn,
        }

    def _draw_many(self, player: PlayerState, count: int) -> None:
        # RFC 1-6 card decks conflict with the mandatory seven-card opening draw.
        # This documented interpretation accepts the legal deck and draws all
        # available cards; a later required draw from empty causes DECK_EMPTY.
        for _ in range(min(count, len(player.library))):
            player.hand.append(player.library.pop())

    def _active_player(self) -> PlayerState:
        if self.active_slot is None:
            raise RuntimeError("No active player.")
        return self.players[self.active_slot]

    def _validate_cast_targets(
        self,
        definition: CardDefinition,
        targets: list[str],
    ) -> None:
        if definition.is_permanent and definition.card_type != "Enchantment":
            expected = 0
        else:
            expected = 1
        if len(targets) != expected:
            raise ProtocolError(
                ErrorCode.ILLEGAL_TARGET,
                f"{definition.name} requires exactly {expected} target(s).",
            )
        effect = EFFECT_KIND.get(definition.base_id)
        if effect and not self._targets_are_legal(effect, targets):
            raise ProtocolError(ErrorCode.ILLEGAL_TARGET, "The selected target is not legal.")

    def _targets_are_legal(self, effect: str, targets: list[str]) -> bool:
        if len(targets) != 1:
            return False
        target = targets[0]
        if effect.startswith("damage_any"):
            return self._slot_for_player_id(target) is not None or self._creature(target) is not None
        if effect.startswith("damage_player"):
            return self._slot_for_player_id(target) is not None
        if effect.startswith("damage_creature") or effect.startswith("buff_creature"):
            return self._creature(target) is not None
        if effect == "counter":
            return any(item.stack_item_id == target for item in self.stack)
        if effect == "bounce_creature":
            return self._creature(target) is not None
        if effect == "destroy_nonblack_creature":
            permanent = self._creature(target)
            return bool(
                permanent
                and self.catalog.definition_for_instance(permanent.instance_id).color != "B"
            )
        return False

    def _apply_effect(self, effect: str, item: StackItem) -> list[dict[str, Any]]:
        target = item.targets[0]
        if effect.startswith("damage_"):
            amount = int(effect.rsplit(":", 1)[1])
            target_slot = self._slot_for_player_id(target)
            if target_slot:
                self.players[target_slot].life -= amount
            else:
                permanent = self._creature(target)
                assert permanent is not None
                permanent.damage += amount
            return [{"change_type": "DAMAGE", "target": target, "amount": amount}]

        if effect == "counter":
            target_index = next(
                index
                for index, candidate in enumerate(self.stack)
                if candidate.stack_item_id == target
            )
            countered = self.stack.pop(target_index)
            self.players[countered.controller_slot].graveyard.append(countered.source_id)
            return [
                {
                    "change_type": "COUNTER",
                    "target": target,
                    "card_id": countered.source_id,
                }
            ]

        permanent = self._creature(target)
        assert permanent is not None
        if effect == "bounce_creature":
            self.players[permanent.controller_slot].battlefield.remove(permanent)
            self.players[permanent.owner_slot].hand.append(permanent.instance_id)
            return [{"change_type": "RETURN_TO_HAND", "target": target}]
        if effect.startswith("buff_creature"):
            _, power, toughness = effect.split(":")
            permanent.temporary_power += int(power)
            permanent.temporary_toughness += int(toughness)
            return [
                {
                    "change_type": "MODIFY_POWER_TOUGHNESS",
                    "target": target,
                    "power": int(power),
                    "toughness": int(toughness),
                }
            ]
        if effect == "destroy_nonblack_creature":
            self._move_permanent_to_graveyard(permanent)
            return [{"change_type": "DESTROY", "target": target}]
        raise RuntimeError(f"Unhandled effect {effect}")

    def _consume_mana(
        self,
        seat: str,
        definition: CardDefinition,
        payment: Any,
    ) -> None:
        if not isinstance(payment, dict):
            raise ProtocolError(ErrorCode.INSUFFICIENT_MANA, "mana_payment must be an object.")
        allowed_keys = set("WUBRG") | {"X"}
        if any(key not in allowed_keys for key in payment):
            raise ProtocolError(
                ErrorCode.INSUFFICIENT_MANA,
                "mana_payment keys must be W, U, B, R, G, or X (generic).",
            )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in payment.values()
        ):
            raise ProtocolError(
                ErrorCode.INSUFFICIENT_MANA,
                "mana_payment values must be non-negative integers.",
            )
        expected = {
            color: amount
            for color, amount in definition.colored_cost.items()
            if amount
        }
        if definition.generic_cost:
            expected["X"] = definition.generic_cost
        normalized = {key: value for key, value in payment.items() if value}
        if normalized != expected:
            raise ProtocolError(
                ErrorCode.INSUFFICIENT_MANA,
                f"Expected mana payment {expected}, received {normalized}.",
            )

        sources = []
        for permanent in self.players[seat].battlefield:
            produced = self._mana_produced(permanent)
            if produced and not permanent.tapped:
                color, amount = produced
                sources.append([permanent, color, amount])

        selected: list[Permanent] = []
        for color in "WUBRG":
            for _ in range(expected.get(color, 0)):
                source = next(
                    (entry for entry in sources if entry[1] == color and entry[2] > 0),
                    None,
                )
                if source is None:
                    raise ProtocolError(
                        ErrorCode.INSUFFICIENT_MANA,
                        f"No untapped source can produce required {color} mana.",
                    )
                source[2] -= 1
                if source[0] not in selected:
                    selected.append(source[0])

        generic_remaining = expected.get("X", 0)
        for source in sources:
            if generic_remaining <= 0:
                break
            if source[2] > 0:
                generic_remaining -= source[2]
                if source[0] not in selected:
                    selected.append(source[0])
        if generic_remaining > 0:
            raise ProtocolError(
                ErrorCode.INSUFFICIENT_MANA,
                "Not enough untapped mana sources for the generic cost.",
            )
        for permanent in selected:
            permanent.tapped = True

    def _mana_produced(self, permanent: Permanent) -> tuple[str, int] | None:
        definition = self.catalog.definition_for_instance(permanent.instance_id)
        basics = {
            "plains": "W",
            "island": "U",
            "swamp": "B",
            "mountain": "R",
            "forest": "G",
        }
        if definition.base_id in basics:
            return basics[definition.base_id], 1
        if definition.base_id in {"llanowar_elves", "elvish_mystic"}:
            if permanent.summoning_sick:
                return None
            return "G", 1
        if definition.base_id == "sol_ring":
            return "C", 2
        return None

    def _slot_for_player_id(self, player_id: str) -> str | None:
        return next(
            (seat for seat, player in self.players.items() if player.player_id == player_id),
            None,
        )

    def _find_permanent(self, instance_id: str) -> Permanent | None:
        return next(
            (
                permanent
                for player in self.players.values()
                for permanent in player.battlefield
                if permanent.instance_id == instance_id
            ),
            None,
        )

    def _creature(self, instance_id: str) -> Permanent | None:
        permanent = self._find_permanent(instance_id)
        if not permanent:
            return None
        return (
            permanent
            if self.catalog.definition_for_instance(permanent.instance_id).is_creature
            else None
        )

    def _move_permanent_to_graveyard(self, permanent: Permanent) -> None:
        self.players[permanent.controller_slot].battlefield.remove(permanent)
        self.players[permanent.owner_slot].graveyard.append(permanent.instance_id)

    def _public_permanent(self, permanent: Permanent) -> dict[str, Any]:
        definition = self.catalog.definition_for_instance(permanent.instance_id)
        public: dict[str, Any] = {
            "id": permanent.instance_id,
            "tapped": permanent.tapped,
        }
        if definition.is_creature:
            public.update(
                {
                    "damage": permanent.damage,
                    "power": (definition.power or 0) + permanent.temporary_power,
                    "toughness": (definition.toughness or 0)
                    + permanent.temporary_toughness,
                    "summoning_sick": permanent.summoning_sick,
                }
            )
        return public
