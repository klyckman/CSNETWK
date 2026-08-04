"""Authoritative GAME_SETUP and London mulligan state for MTGNP."""

from __future__ import annotations

import random
import threading
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from collections.abc import Mapping
from typing import Any, Sequence

from .catalog import CardCatalog, CardDefinition, CatalogError, MANA_COLORS
from .lobby import ReadyPlayer
from .protocol import ErrorCode, LifecycleState, TurnStep


OPENING_HAND_SIZE = 7
STARTING_LIFE = 20


class GameRuleError(ValueError):
    """A game request failed validation without changing authoritative state."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class MulliganOutcome(StrEnum):
    REDRAW = "REDRAW"
    WAITING_FOR_OPPONENT = "WAITING_FOR_OPPONENT"
    ALL_PLAYERS_KEPT = "ALL_PLAYERS_KEPT"


class PriorityPassOutcome(StrEnum):
    GRANT_OPPONENT = "GRANT_OPPONENT"
    RESOLVE_STACK = "RESOLVE_STACK"
    WINDOW_CLOSED = "WINDOW_CLOSED"


class DrawOutcome(StrEnum):
    SKIPPED_FIRST_TURN = "SKIPPED_FIRST_TURN"
    CARD_DRAWN = "CARD_DRAWN"
    EMPTY_LIBRARY = "EMPTY_LIBRARY"


class DiscardOutcome(StrEnum):
    MORE_REQUIRED = "MORE_REQUIRED"
    COMPLETE = "COMPLETE"


class StackResolutionOutcome(StrEnum):
    RESOLVED = "RESOLVED"
    FIZZLE = "FIZZLE"


@dataclass(frozen=True, slots=True)
class StackItem:
    stack_item_id: str
    item_type: str
    source_id: str
    controller_seat_id: str
    targets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StackResolution:
    item: StackItem
    outcome: StackResolutionOutcome
    state_changes: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class CombatDamageResult:
    step: TurnStep
    damage_events: tuple[dict[str, Any], ...]
    life_totals: dict[str, int]
    creatures_died: tuple[str, ...]


SUPPORTED_EFFECTS = frozenset(
    {"lightning_bolt", "unsummon", "counterspell", "giant_growth", "doom_blade"}
)
PERMANENT_CARD_TYPES = frozenset({"Creature", "Artifact Creature", "Artifact"})
SORCERY_SPEED_CARD_TYPES = frozenset(
    {"Sorcery", "Creature", "Artifact Creature", "Artifact", "Enchantment"}
)
MAIN_PHASES = frozenset({TurnStep.PRECOMBAT_MAIN, TurnStep.POSTCOMBAT_MAIN})
BASIC_LAND_MANA = {
    "plains": "W",
    "island": "U",
    "swamp": "B",
    "mountain": "R",
    "forest": "G",
}
COLOR_NAMES = {"W": "white", "U": "blue", "B": "black", "R": "red", "G": "green"}


PRIORITY_STEPS = frozenset(
    {
        TurnStep.UPKEEP,
        TurnStep.DRAW,
        TurnStep.PRECOMBAT_MAIN,
        TurnStep.BEGIN_COMBAT,
        TurnStep.DECLARE_ATTACKERS,
        TurnStep.DECLARE_BLOCKERS,
        TurnStep.ASSIGN_DAMAGE_ORDER,
        TurnStep.FIRST_STRIKE_DAMAGE,
        TurnStep.END_OF_COMBAT,
        TurnStep.POSTCOMBAT_MAIN,
        TurnStep.END_STEP,
    }
)

NEXT_STEP_AFTER_PRIORITY = {
    TurnStep.UPKEEP: TurnStep.DRAW,
    TurnStep.DRAW: TurnStep.PRECOMBAT_MAIN,
    TurnStep.PRECOMBAT_MAIN: TurnStep.BEGIN_COMBAT,
    TurnStep.BEGIN_COMBAT: TurnStep.DECLARE_ATTACKERS,
    TurnStep.DECLARE_ATTACKERS: TurnStep.DECLARE_BLOCKERS,
    TurnStep.FIRST_STRIKE_DAMAGE: TurnStep.COMBAT_DAMAGE,
    TurnStep.END_OF_COMBAT: TurnStep.POSTCOMBAT_MAIN,
    TurnStep.POSTCOMBAT_MAIN: TurnStep.END_STEP,
    TurnStep.END_STEP: TurnStep.CLEANUP,
}


@dataclass(slots=True)
class PlayerGameState:
    seat_id: str
    player_id: str
    original_deck: tuple[str, ...]
    life: int = STARTING_LIFE
    library: list[str] = field(default_factory=list)
    hand: list[str] = field(default_factory=list)
    battlefield: list[dict[str, Any]] = field(default_factory=list)
    graveyard: list[str] = field(default_factory=list)
    mulligans_taken: int = 0
    kept: bool = False


class GameSession:
    """Single authoritative state for setup and mulligan decisions."""

    def __init__(
        self,
        ready_players: Sequence[ReadyPlayer],
        *,
        catalog: CardCatalog | None = None,
        random_source: random.Random | None = None,
    ) -> None:
        if len(ready_players) != 2:
            raise ValueError("GAME_SETUP requires exactly two ready players.")
        seat_ids = [player.seat_id for player in ready_players]
        player_ids = [player.player_id for player in ready_players]
        if len(set(seat_ids)) != 2 or len(set(player_ids)) != 2:
            raise ValueError("Ready players must have distinct seats and player IDs.")
        all_card_ids = [
            card_id for ready in ready_players for card_id in ready.deck_list
        ]
        if len(set(all_card_ids)) != len(all_card_ids):
            raise ValueError("Both decks must use distinct physical card instance IDs.")

        self._random = random_source if random_source is not None else random.SystemRandom()
        self.catalog = catalog
        self._lock = threading.RLock()
        self.players: dict[str, PlayerGameState] = {}
        self._expected_mulligan_sequence: dict[str, int] = {}
        self.lifecycle_state = LifecycleState.MULLIGAN
        self.turn = 0
        self.current_step: TurnStep | None = None
        self.priority_holder_seat_id: str | None = None
        self._expected_priority_sequence: int | None = None
        self._priority_passes = 0
        self._expected_attackers_sequence: int | None = None
        self._expected_blockers_sequence: int | None = None
        self._expected_damage_order_sequence: int | None = None
        self._expected_discard_sequence: int | None = None
        self.combat_attackers: dict[str, str] = {}
        self.combat_blockers: dict[str, list[str]] = {}
        self.combat_damage_order: dict[str, list[str]] = {}
        self.stack: list[StackItem] = []
        self._next_stack_item_number = 1
        self.land_played_this_turn = False
        self.winner_seat_id: str | None = None
        self.loser_seat_id: str | None = None
        self.game_over_reason: str | None = None

        for ready in ready_players:
            library = list(ready.deck_list)
            self._random.shuffle(library)
            player = PlayerGameState(
                seat_id=ready.seat_id,
                player_id=ready.player_id,
                original_deck=ready.deck_list,
                library=library,
            )
            self._draw_opening_hand(player)
            self.players[ready.seat_id] = player

        self.active_seat_id = self._random.choice(seat_ids)
        self.first_player_seat_id = self.active_seat_id

    @property
    def active_player_id(self) -> str:
        with self._lock:
            return self.players[self.active_seat_id].player_id

    @property
    def priority_holder_player_id(self) -> str | None:
        with self._lock:
            if self.priority_holder_seat_id is None:
                return None
            return self.players[self.priority_holder_seat_id].player_id

    def player_id_for_seat(self, seat_id: str) -> str:
        with self._lock:
            return self._player(seat_id).player_id

    def opposing_seat(self, seat_id: str) -> str:
        with self._lock:
            self._player(seat_id)
            return next(candidate for candidate in self.players if candidate != seat_id)

    def record_mulligan_request(self, seat_id: str, seq_num: int) -> None:
        with self._lock:
            self._player(seat_id)
            self._expected_mulligan_sequence[seat_id] = seq_num

    def process_mulligan(
        self,
        seat_id: str,
        *,
        seq_num: int,
        keep: bool,
        cards_to_bottom: Sequence[str],
    ) -> MulliganOutcome:
        """Validate and apply one MULLIGAN_CHOICE atomically."""

        with self._lock:
            if self.lifecycle_state != LifecycleState.MULLIGAN:
                raise GameRuleError(
                    ErrorCode.WRONG_PHASE,
                    "MULLIGAN_CHOICE is only legal during MULLIGAN.",
                )
            player = self._player(seat_id)
            expected = self._expected_mulligan_sequence.get(seat_id)
            if expected is None or seq_num != expected:
                raise GameRuleError(
                    ErrorCode.STALE_ACTION,
                    f"Mulligan token mismatch. Expected {expected}, received {seq_num}.",
                )
            if player.kept:
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    f"{player.player_id} has already kept a hand.",
                )

            bottom_cards = tuple(cards_to_bottom)
            if any(not isinstance(card_id, str) for card_id in bottom_cards):
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    "Every cards_to_bottom entry must be a card instance ID string.",
                )
            if not keep:
                if bottom_cards:
                    raise GameRuleError(
                        ErrorCode.ILLEGAL_ACTION,
                        "cards_to_bottom must be empty when keep is false.",
                    )
                combined_deck = [*player.library, *player.hand]
                self._random.shuffle(combined_deck)
                player.library = combined_deck
                player.hand = []
                player.mulligans_taken += 1
                self._draw_opening_hand(player)
                del self._expected_mulligan_sequence[seat_id]
                return MulliganOutcome.REDRAW

            required_bottom_count = player.mulligans_taken
            if len(bottom_cards) != required_bottom_count:
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    f"Keep after {required_bottom_count} mulligan(s) requires exactly "
                    f"{required_bottom_count} cards_to_bottom.",
                )
            if len(set(bottom_cards)) != len(bottom_cards):
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    "cards_to_bottom cannot repeat a card instance ID.",
                )
            hand_counts = Counter(player.hand)
            requested_counts = Counter(bottom_cards)
            if any(requested_counts[card_id] > hand_counts[card_id] for card_id in bottom_cards):
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    "cards_to_bottom contains a card that is not in the current hand.",
                )

            for card_id in bottom_cards:
                player.hand.remove(card_id)
                player.library.append(card_id)
            player.kept = True
            del self._expected_mulligan_sequence[seat_id]

            if all(candidate.kept for candidate in self.players.values()):
                self.lifecycle_state = LifecycleState.IN_GAME
                self.turn = 1
                self.current_step = TurnStep.UNTAP
                return MulliganOutcome.ALL_PLAYERS_KEPT
            return MulliganOutcome.WAITING_FOR_OPPONENT

    def perform_untap(self) -> None:
        with self._lock:
            self._require_in_game_step(TurnStep.UNTAP)
            active = self.players[self.active_seat_id]
            for permanent in active.battlefield:
                permanent["tapped"] = False
                if "summoning_sick" in permanent:
                    permanent["summoning_sick"] = False
                if "summoning_sickness" in permanent:
                    permanent["summoning_sickness"] = False
            self.land_played_this_turn = False

    def enter_upkeep(self) -> None:
        with self._lock:
            self._require_in_game_step(TurnStep.UNTAP)
            self.current_step = TurnStep.UPKEEP

    def open_priority_window(self) -> str:
        with self._lock:
            if self.lifecycle_state != LifecycleState.IN_GAME:
                raise GameRuleError(ErrorCode.WRONG_PHASE, "The game is not IN_GAME.")
            if self.current_step not in PRIORITY_STEPS:
                raise GameRuleError(
                    ErrorCode.WRONG_PHASE,
                    f"{self.current_step} does not open a priority window.",
                )
            self.priority_holder_seat_id = self.active_seat_id
            self._expected_priority_sequence = None
            self._priority_passes = 0
            return self.priority_holder_seat_id

    def record_priority_grant(self, seat_id: str, seq_num: int) -> None:
        with self._lock:
            if seat_id != self.priority_holder_seat_id:
                raise GameRuleError(
                    ErrorCode.NOT_YOUR_PRIORITY,
                    f"{seat_id} is not the current priority holder.",
                )
            self._expected_priority_sequence = seq_num

    def process_priority_pass(
        self, seat_id: str, *, seq_num: int
    ) -> PriorityPassOutcome:
        with self._lock:
            if self.current_step not in PRIORITY_STEPS or self.priority_holder_seat_id is None:
                raise GameRuleError(
                    ErrorCode.WRONG_PHASE,
                    "No priority window is currently open.",
                )
            if seat_id != self.priority_holder_seat_id:
                raise GameRuleError(
                    ErrorCode.NOT_YOUR_PRIORITY,
                    f"Priority belongs to {self.priority_holder_player_id}.",
                )
            if self._expected_priority_sequence != seq_num:
                raise GameRuleError(
                    ErrorCode.STALE_ACTION,
                    f"Priority token mismatch. Expected {self._expected_priority_sequence}, "
                    f"received {seq_num}.",
                )

            self._priority_passes += 1
            self._expected_priority_sequence = None
            if self._priority_passes == 1:
                self.priority_holder_seat_id = self.opposing_seat(seat_id)
                return PriorityPassOutcome.GRANT_OPPONENT

            self.priority_holder_seat_id = None
            if self.stack:
                return PriorityPassOutcome.RESOLVE_STACK
            return PriorityPassOutcome.WINDOW_CLOSED

    def process_play_land(
        self, seat_id: str, *, seq_num: int, card_id: str
    ) -> dict[str, Any]:
        """Move one land from the active player's hand to the battlefield."""

        with self._lock:
            self._require_priority_action(seat_id, seq_num)
            if seat_id != self.active_seat_id or self.current_step not in MAIN_PHASES:
                raise GameRuleError(
                    ErrorCode.WRONG_PHASE,
                    "A land may be played only by the active player during a Main Phase.",
                )
            if self.stack:
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    "A land cannot be played while the stack is not empty.",
                )
            if self.land_played_this_turn:
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    "The active player has already played a land this turn.",
                )
            player = self._player(seat_id)
            if card_id not in player.hand:
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    f"{card_id} is not in the active player's hand.",
                )
            definition = self._definition(card_id)
            if definition.card_type != "Land":
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    f"{definition.name} is not a land.",
                )

            permanent = {"id": card_id, "tapped": False}
            player.hand.remove(card_id)
            player.battlefield.append(permanent)
            self.land_played_this_turn = True
            self._retain_priority_after_action()
            return dict(permanent)

    def process_cast_spell(
        self,
        seat_id: str,
        *,
        seq_num: int,
        card_id: str,
        targets: Sequence[str],
        mana_payment: Mapping[str, Any],
    ) -> StackItem:
        """Validate a spell, pay mana atomically, and push it onto the stack."""

        with self._lock:
            self._require_priority_action(seat_id, seq_num)
            player = self._player(seat_id)
            if card_id not in player.hand:
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    f"{card_id} is not in {player.player_id}'s hand.",
                )
            definition = self._definition(card_id)
            if definition.card_type == "Land":
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    "Lands must be submitted with PLAY_LAND, not CAST_SPELL.",
                )
            if definition.card_type in SORCERY_SPEED_CARD_TYPES and (
                seat_id != self.active_seat_id
                or self.current_step not in MAIN_PHASES
                or self.stack
            ):
                raise GameRuleError(
                    ErrorCode.WRONG_PHASE,
                    f"{definition.name} may be cast only at sorcery speed.",
                )
            if (
                definition.card_type not in PERMANENT_CARD_TYPES
                and definition.base_id not in SUPPORTED_EFFECTS
            ):
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    f"{definition.name}'s effect is not implemented in this milestone.",
                )

            normalized_targets = self._validate_cast_targets(definition, targets)
            mana_sources = self._validate_mana_payment(
                player, definition, mana_payment
            )

            for permanent in mana_sources:
                permanent["tapped"] = True
            player.hand.remove(card_id)
            item = StackItem(
                stack_item_id=f"stk_{self._next_stack_item_number:04d}",
                item_type="SPELL",
                source_id=card_id,
                controller_seat_id=seat_id,
                targets=normalized_targets,
            )
            self._next_stack_item_number += 1
            self.stack.append(item)
            self._retain_priority_after_action()
            return item

    def visible_stack_item(self, item: StackItem) -> dict[str, Any]:
        with self._lock:
            return {
                "stack_item_id": item.stack_item_id,
                "item_type": item.item_type,
                "source": item.source_id,
                "targets": list(item.targets),
                "controller": self.player_id_for_seat(item.controller_seat_id),
            }

    def resolve_top_stack_item(self) -> StackResolution:
        """Pop and resolve exactly one stack item using LIFO ordering."""

        with self._lock:
            if self.lifecycle_state != LifecycleState.IN_GAME or not self.stack:
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    "There is no stack item to resolve.",
                )
            if self.priority_holder_seat_id is not None:
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    "A stack item resolves only after both players pass.",
                )

            item = self.stack.pop()
            definition = self._definition(item.source_id)
            changes: list[dict[str, Any]] = []
            resolved = self._apply_spell_effect(item, definition, changes)
            if definition.card_type not in PERMANENT_CARD_TYPES:
                self.players[item.controller_seat_id].graveyard.append(item.source_id)
            changes.extend(self.apply_state_based_actions())
            self._priority_passes = 0
            return StackResolution(
                item=item,
                outcome=(
                    StackResolutionOutcome.RESOLVED
                    if resolved
                    else StackResolutionOutcome.FIZZLE
                ),
                state_changes=tuple(changes if resolved else ()),
            )

    def advance_after_priority_window(self) -> tuple[TurnStep, TurnStep]:
        with self._lock:
            if self.priority_holder_seat_id is not None:
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    "Cannot advance while a player still holds priority.",
                )
            if self.current_step == TurnStep.DECLARE_BLOCKERS:
                next_step = (
                    TurnStep.ASSIGN_DAMAGE_ORDER
                    if self.combat_requires_damage_order()
                    else self.next_combat_damage_step()
                )
            elif self.current_step == TurnStep.ASSIGN_DAMAGE_ORDER:
                if not self.damage_orders_complete():
                    raise GameRuleError(
                        ErrorCode.ILLEGAL_ACTION,
                        "All multi-blocker damage orders are required before combat damage.",
                    )
                next_step = self.next_combat_damage_step()
            else:
                try:
                    next_step = NEXT_STEP_AFTER_PRIORITY[self.current_step]
                except KeyError as exc:
                    raise GameRuleError(
                        ErrorCode.WRONG_PHASE,
                        f"No turn transition is defined after {self.current_step}.",
                    ) from exc
            previous = self.current_step
            self.current_step = next_step
            self._priority_passes = 0
            return previous, next_step

    def perform_draw(self) -> DrawOutcome:
        with self._lock:
            self._require_in_game_step(TurnStep.DRAW)
            if self.turn == 1 and self.active_seat_id == self.first_player_seat_id:
                return DrawOutcome.SKIPPED_FIRST_TURN
            active = self.players[self.active_seat_id]
            if not active.library:
                return DrawOutcome.EMPTY_LIBRARY
            active.hand.append(active.library.pop(0))
            return DrawOutcome.CARD_DRAWN

    def record_attackers_request(self, seat_id: str, seq_num: int) -> None:
        with self._lock:
            self._require_in_game_step(TurnStep.DECLARE_ATTACKERS)
            if seat_id != self.active_seat_id:
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    "Only the active player declares attackers.",
                )
            self._expected_attackers_sequence = seq_num

    def process_declare_attackers(
        self,
        seat_id: str,
        *,
        seq_num: int,
        attackers: Sequence[dict[str, Any]],
    ) -> bool:
        """Validate and tap attackers atomically; return whether any attacked."""

        with self._lock:
            self._require_in_game_step(TurnStep.DECLARE_ATTACKERS)
            if seat_id != self.active_seat_id:
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    "Only the active player declares attackers.",
                )
            if seq_num != self._expected_attackers_sequence:
                raise GameRuleError(
                    ErrorCode.STALE_ACTION,
                    f"Attacker token mismatch. Expected {self._expected_attackers_sequence}, "
                    f"received {seq_num}.",
                )
            declarations = tuple(attackers)
            normalized: list[tuple[str, str, dict[str, Any]]] = []
            seen: set[str] = set()
            defending_player = self.players[self.opposing_seat(seat_id)].player_id
            for declaration in declarations:
                if not isinstance(declaration, Mapping):
                    raise GameRuleError(
                        ErrorCode.ILLEGAL_ACTION,
                        "Every attacker entry must contain creature_id and target.",
                    )
                creature_id = declaration.get("creature_id")
                target = declaration.get("target")
                if not isinstance(creature_id, str) or not isinstance(target, str):
                    raise GameRuleError(
                        ErrorCode.ILLEGAL_ACTION,
                        "Every attacker requires string creature_id and target fields.",
                    )
                if creature_id in seen:
                    raise GameRuleError(
                        ErrorCode.ILLEGAL_ACTION,
                        f"{creature_id} cannot be declared as an attacker twice.",
                    )
                if target != defending_player:
                    raise GameRuleError(
                        ErrorCode.ILLEGAL_TARGET,
                        f"Attackers must target opposing player {defending_player}.",
                    )
                permanent = self._controlled_permanent(seat_id, creature_id)
                if not self._is_creature_permanent(permanent):
                    raise GameRuleError(
                        ErrorCode.ILLEGAL_ACTION,
                        f"{creature_id} is not a creature.",
                    )
                if permanent.get("tapped"):
                    raise GameRuleError(
                        ErrorCode.ILLEGAL_ACTION,
                        f"Tapped creature {creature_id} cannot attack.",
                    )
                if permanent.get("summoning_sick"):
                    raise GameRuleError(
                        ErrorCode.ILLEGAL_ACTION,
                        f"Summoning-sick creature {creature_id} cannot attack.",
                    )
                if self._has_keyword(creature_id, "Defender"):
                    raise GameRuleError(
                        ErrorCode.ILLEGAL_ACTION,
                        f"Creature {creature_id} has Defender and cannot attack.",
                    )
                seen.add(creature_id)
                normalized.append((creature_id, target, permanent))

            self.clear_combat_state()
            for creature_id, target, permanent in normalized:
                permanent["attacking"] = True
                if not self._has_keyword(creature_id, "Vigilance"):
                    permanent["tapped"] = True
                self.combat_attackers[creature_id] = target
                self.combat_blockers[creature_id] = []
            self._expected_attackers_sequence = None
            if not normalized:
                self.current_step = TurnStep.END_OF_COMBAT
                return False
            self._priority_passes = 0
            return True

    def record_blockers_request(self, seat_id: str, seq_num: int) -> None:
        with self._lock:
            self._require_in_game_step(TurnStep.DECLARE_BLOCKERS)
            if seat_id != self.opposing_seat(self.active_seat_id):
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    "Only the defending player declares blockers.",
                )
            self._expected_blockers_sequence = seq_num

    def process_declare_blockers(
        self,
        seat_id: str,
        *,
        seq_num: int,
        blockers: Sequence[dict[str, Any]],
    ) -> None:
        """Validate all blocker assignments before marking any creature."""

        with self._lock:
            self._require_in_game_step(TurnStep.DECLARE_BLOCKERS)
            defending_seat = self.opposing_seat(self.active_seat_id)
            if seat_id != defending_seat:
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    "Only the defending player declares blockers.",
                )
            if seq_num != self._expected_blockers_sequence:
                raise GameRuleError(
                    ErrorCode.STALE_ACTION,
                    f"Blocker token mismatch. Expected {self._expected_blockers_sequence}, "
                    f"received {seq_num}.",
                )

            assignments = tuple(blockers)
            normalized: list[tuple[str, str, dict[str, Any]]] = []
            seen_blockers: set[str] = set()
            for assignment in assignments:
                if not isinstance(assignment, Mapping):
                    raise GameRuleError(
                        ErrorCode.ILLEGAL_ACTION,
                        "Every blocker entry must contain creature_id and blocking_id.",
                    )
                creature_id = assignment.get("creature_id")
                blocking_id = assignment.get("blocking_id")
                if not isinstance(creature_id, str) or not isinstance(blocking_id, str):
                    raise GameRuleError(
                        ErrorCode.ILLEGAL_ACTION,
                        "Every blocker requires string creature_id and blocking_id fields.",
                    )
                if creature_id in seen_blockers:
                    raise GameRuleError(
                        ErrorCode.ILLEGAL_ACTION,
                        f"{creature_id} cannot block more than one attacker.",
                    )
                if blocking_id not in self.combat_attackers:
                    raise GameRuleError(
                        ErrorCode.ILLEGAL_TARGET,
                        f"{blocking_id} is not an attacking creature.",
                    )
                if self._find_permanent(blocking_id) is None:
                    raise GameRuleError(
                        ErrorCode.ILLEGAL_TARGET,
                        f"Attacking creature {blocking_id} is no longer on the battlefield.",
                    )
                permanent = self._controlled_permanent(defending_seat, creature_id)
                if not self._is_creature_permanent(permanent):
                    raise GameRuleError(
                        ErrorCode.ILLEGAL_ACTION,
                        f"{creature_id} is not a creature.",
                    )
                if permanent.get("tapped"):
                    raise GameRuleError(
                        ErrorCode.ILLEGAL_ACTION,
                        f"Tapped creature {creature_id} cannot block.",
                    )
                if self._has_keyword(blocking_id, "Flying") and not self._has_keyword(
                    creature_id, "Flying"
                ):
                    raise GameRuleError(
                        ErrorCode.ILLEGAL_ACTION,
                        f"{creature_id} cannot block flying creature {blocking_id}.",
                    )
                if self._attacker_has_protection_from(blocking_id, creature_id):
                    raise GameRuleError(
                        ErrorCode.ILLEGAL_ACTION,
                        f"{blocking_id}'s protection prevents {creature_id} from blocking it.",
                    )
                seen_blockers.add(creature_id)
                normalized.append((creature_id, blocking_id, permanent))

            for player in self.players.values():
                for permanent in player.battlefield:
                    permanent.pop("blocking", None)
            for attacker_id in self.combat_blockers:
                self.combat_blockers[attacker_id] = []
            self.combat_damage_order.clear()
            for creature_id, blocking_id, permanent in normalized:
                permanent["blocking"] = blocking_id
                self.combat_blockers[blocking_id].append(creature_id)
            self._expected_blockers_sequence = None
            self._priority_passes = 0

    def combat_requires_damage_order(self) -> bool:
        with self._lock:
            return any(
                self._find_permanent(attacker_id) is not None
                and len(self._living_blockers(attacker_id)) > 1
                for attacker_id in self.combat_attackers
            )

    def record_damage_order_request(self, seat_id: str, seq_num: int) -> None:
        with self._lock:
            self._require_in_game_step(TurnStep.ASSIGN_DAMAGE_ORDER)
            if seat_id != self.active_seat_id:
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    "Only the attacking player assigns combat damage order.",
                )
            self._expected_damage_order_sequence = seq_num

    def process_damage_order(
        self,
        seat_id: str,
        *,
        seq_num: int,
        attacker_id: str,
        blocker_order: Sequence[str],
    ) -> bool:
        with self._lock:
            self._require_in_game_step(TurnStep.ASSIGN_DAMAGE_ORDER)
            if seat_id != self.active_seat_id:
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    "Only the attacking player assigns combat damage order.",
                )
            if seq_num != self._expected_damage_order_sequence:
                raise GameRuleError(
                    ErrorCode.STALE_ACTION,
                    f"Damage-order token mismatch. Expected "
                    f"{self._expected_damage_order_sequence}, received {seq_num}.",
                )
            required = self._living_blockers(attacker_id)
            if len(required) <= 1:
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    f"{attacker_id} does not require a multi-blocker damage order.",
                )
            normalized = tuple(blocker_order)
            if (
                any(not isinstance(blocker_id, str) for blocker_id in normalized)
                or len(set(normalized)) != len(normalized)
                or set(normalized) != set(required)
            ):
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    f"Damage order for {attacker_id} must list exactly {required}.",
                )
            self.combat_damage_order[attacker_id] = list(normalized)
            return self.damage_orders_complete()

    def damage_orders_complete(self) -> bool:
        with self._lock:
            required_attackers = {
                attacker_id
                for attacker_id in self.combat_attackers
                if self._find_permanent(attacker_id) is not None
                and len(self._living_blockers(attacker_id)) > 1
            }
            return required_attackers.issubset(self.combat_damage_order)

    def next_combat_damage_step(self) -> TurnStep:
        with self._lock:
            living_attackers = [
                attacker_id
                for attacker_id in self.combat_attackers
                if self._find_permanent(attacker_id) is not None
            ]
            participants = [
                *living_attackers,
                *(
                    blocker_id
                    for attacker_id in living_attackers
                    for blocker_id in self._living_blockers(attacker_id)
                ),
            ]
            return (
                TurnStep.FIRST_STRIKE_DAMAGE
                if any(
                    self._find_permanent(card_id) is not None
                    and (
                        self._has_keyword(card_id, "First strike")
                        or self._has_keyword(card_id, "Double strike")
                    )
                    for card_id in participants
                )
                else TurnStep.COMBAT_DAMAGE
            )

    def resolve_combat_damage(self, *, first_strike: bool) -> CombatDamageResult:
        with self._lock:
            expected_step = (
                TurnStep.FIRST_STRIKE_DAMAGE if first_strike else TurnStep.COMBAT_DAMAGE
            )
            self._require_in_game_step(expected_step)
            events: list[dict[str, Any]] = []
            for attacker_id, defending_player in self.combat_attackers.items():
                attacker_location = self._find_permanent(attacker_id)
                if attacker_location is None:
                    continue
                attacker = attacker_location[1]
                living_blockers = self._living_blockers(attacker_id)
                originally_blocked = bool(self.combat_blockers.get(attacker_id))

                if self._deals_damage_in_step(attacker_id, first_strike):
                    power = max(0, int(attacker.get("power", 0)))
                    if not originally_blocked:
                        if power:
                            events.append(
                                {
                                    "source": attacker_id,
                                    "target": defending_player,
                                    "amount": power,
                                }
                            )
                    elif living_blockers and power:
                        order = self.combat_damage_order.get(
                            attacker_id, living_blockers
                        )
                        ordered_living = [
                            blocker_id
                            for blocker_id in order
                            if blocker_id in living_blockers
                        ]
                        remaining = power
                        for index, blocker_id in enumerate(ordered_living):
                            blocker = self._find_permanent(blocker_id)[1]
                            lethal = max(
                                0,
                                int(blocker.get("toughness", 0))
                                - int(blocker.get("damage", 0)),
                            )
                            amount = (
                                remaining
                                if index == len(ordered_living) - 1
                                else min(remaining, lethal)
                            )
                            if amount and not self._damage_is_prevented(
                                attacker_id, blocker_id
                            ):
                                events.append(
                                    {
                                        "source": attacker_id,
                                        "target": blocker_id,
                                        "amount": amount,
                                    }
                                )
                            remaining -= amount
                            if remaining <= 0:
                                break

                if attacker_location is None:
                    continue
                for blocker_id in living_blockers:
                    blocker_location = self._find_permanent(blocker_id)
                    if blocker_location is None or not self._deals_damage_in_step(
                        blocker_id, first_strike
                    ):
                        continue
                    blocker_power = max(0, int(blocker_location[1].get("power", 0)))
                    if blocker_power and not self._damage_is_prevented(
                        blocker_id, attacker_id
                    ):
                        events.append(
                            {
                                "source": blocker_id,
                                "target": attacker_id,
                                "amount": blocker_power,
                            }
                        )

            for event in events:
                target_seat = self._seat_for_player_id(event["target"])
                if target_seat is not None:
                    self.players[target_seat].life -= event["amount"]
                    continue
                target = self._find_permanent(event["target"])
                if target is not None:
                    target[1]["damage"] = int(target[1].get("damage", 0)) + event[
                        "amount"
                    ]

            changes = self.apply_state_based_actions()
            creatures_died = tuple(
                change["target"]
                for change in changes
                if change.get("change_type") == "DESTROY"
            )
            return CombatDamageResult(
                step=expected_step,
                damage_events=tuple(events),
                life_totals={
                    player.player_id: player.life for player in self.players.values()
                },
                creatures_died=creatures_died,
            )

    def enter_end_of_combat(self) -> None:
        with self._lock:
            self._require_in_game_step(TurnStep.COMBAT_DAMAGE)
            self.current_step = TurnStep.END_OF_COMBAT
            self._priority_passes = 0

    def clear_combat_state(self) -> None:
        with self._lock:
            for player in self.players.values():
                for permanent in player.battlefield:
                    permanent.pop("attacking", None)
                    permanent.pop("blocking", None)
            self.combat_attackers.clear()
            self.combat_blockers.clear()
            self.combat_damage_order.clear()
            self._expected_blockers_sequence = None
            self._expected_damage_order_sequence = None

    def cleanup_excess_cards(self) -> int:
        with self._lock:
            self._require_in_game_step(TurnStep.CLEANUP)
            return max(0, len(self.players[self.active_seat_id].hand) - 7)

    def record_discard_request(self, seat_id: str, seq_num: int) -> None:
        with self._lock:
            self._require_in_game_step(TurnStep.CLEANUP)
            if seat_id != self.active_seat_id:
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    "Only the active player discards during Cleanup.",
                )
            self._expected_discard_sequence = seq_num

    def process_discard(
        self, seat_id: str, *, seq_num: int, card_ids: Sequence[str]
    ) -> DiscardOutcome:
        with self._lock:
            self._require_in_game_step(TurnStep.CLEANUP)
            if seat_id != self.active_seat_id:
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    "Only the active player discards during Cleanup.",
                )
            if seq_num != self._expected_discard_sequence:
                raise GameRuleError(
                    ErrorCode.STALE_ACTION,
                    f"Discard token mismatch. Expected {self._expected_discard_sequence}, "
                    f"received {seq_num}.",
                )
            selected = tuple(card_ids)
            if not selected or any(not isinstance(card_id, str) for card_id in selected):
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    "DISCARD requires at least one card instance ID string.",
                )
            if len(set(selected)) != len(selected):
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    "DISCARD cannot repeat a card instance ID.",
                )
            excess = self.cleanup_excess_cards()
            if excess == 0 or len(selected) > excess:
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    f"Cleanup requires discarding at most {excess} card(s).",
                )
            active = self.players[self.active_seat_id]
            if any(card_id not in active.hand for card_id in selected):
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    "DISCARD contains a card that is not in the active player's hand.",
                )
            for card_id in selected:
                active.hand.remove(card_id)
                active.graveyard.append(card_id)
            self._expected_discard_sequence = None
            return (
                DiscardOutcome.MORE_REQUIRED
                if self.cleanup_excess_cards() > 0
                else DiscardOutcome.COMPLETE
            )

    def finish_cleanup(self) -> None:
        with self._lock:
            self._require_in_game_step(TurnStep.CLEANUP)
            if self.cleanup_excess_cards() > 0:
                raise GameRuleError(
                    ErrorCode.ILLEGAL_ACTION,
                    "Cleanup cannot finish while the active hand exceeds seven cards.",
                )
            for player in self.players.values():
                for permanent in player.battlefield:
                    if "damage" in permanent:
                        permanent["damage"] = 0
                    if "power" in permanent and self.catalog is not None:
                        definition = self._definition(permanent["id"])
                        permanent["power"] = definition.power
                        permanent["toughness"] = definition.toughness

    def apply_state_based_actions(self) -> list[dict[str, Any]]:
        """Apply required life and creature checks until the state is stable."""

        with self._lock:
            changes: list[dict[str, Any]] = []
            while True:
                doomed: list[tuple[str, dict[str, Any]]] = []
                for seat_id, player in self.players.items():
                    for permanent in player.battlefield:
                        if not self._is_creature_permanent(permanent):
                            continue
                        toughness = int(permanent.get("toughness", 0))
                        damage = int(permanent.get("damage", 0))
                        if toughness <= 0 or damage >= toughness:
                            doomed.append((seat_id, permanent))
                if not doomed:
                    break
                for seat_id, permanent in doomed:
                    if permanent in self.players[seat_id].battlefield:
                        self._move_permanent_to_graveyard(seat_id, permanent)
                        changes.append(
                            {
                                "change_type": "DESTROY",
                                "target": permanent["id"],
                                "reason": "ZERO_TOUGHNESS"
                                if int(permanent.get("toughness", 0)) <= 0
                                else "LETHAL_DAMAGE",
                            }
                        )

            losing_seats = [
                seat_id for seat_id, player in self.players.items() if player.life <= 0
            ]
            if losing_seats and self.lifecycle_state == LifecycleState.IN_GAME:
                loser = (
                    self.active_seat_id
                    if self.active_seat_id in losing_seats
                    else losing_seats[0]
                )
                self.declare_game_over(loser, "LIFE_ZERO")
            return changes

    def start_next_turn(self) -> tuple[str, str]:
        with self._lock:
            self._require_in_game_step(TurnStep.CLEANUP)
            previous_player = self.players[self.active_seat_id].player_id
            self.active_seat_id = self.opposing_seat(self.active_seat_id)
            self.turn += 1
            self.current_step = TurnStep.UNTAP
            self.land_played_this_turn = False
            return previous_player, self.players[self.active_seat_id].player_id

    def declare_game_over(self, loser_seat_id: str, reason: str) -> None:
        with self._lock:
            self._player(loser_seat_id)
            self.loser_seat_id = loser_seat_id
            self.winner_seat_id = self.opposing_seat(loser_seat_id)
            self.game_over_reason = reason
            self.lifecycle_state = LifecycleState.GAME_OVER
            self.current_step = None
            self.priority_holder_seat_id = None
            self._expected_priority_sequence = None

    def visible_state(self, seat_id: str) -> dict[str, Any]:
        """Return a personalized view that never reveals the opponent's hand."""

        with self._lock:
            viewer = self._player(seat_id)
            if self.lifecycle_state == LifecycleState.MULLIGAN:
                phase = LifecycleState.MULLIGAN.value
            elif self.lifecycle_state == LifecycleState.GAME_OVER:
                phase = LifecycleState.GAME_OVER.value
            else:
                phase = self.current_step.value
            return {
                "lifecycle_state": self.lifecycle_state.value,
                "turn": self.turn,
                "phase": phase,
                "active_player": self.players[self.active_seat_id].player_id,
                "priority_holder": self.priority_holder_player_id,
                "life_totals": {
                    player.player_id: player.life for player in self.players.values()
                },
                "available_mana": {
                    player.player_id: self._available_mana_for_player(player)
                    for player in self.players.values()
                },
                "hand": {viewer.player_id: list(viewer.hand)},
                "hand_counts": {
                    player.player_id: len(player.hand)
                    for player in self.players.values()
                },
                "library_counts": {
                    player.player_id: len(player.library)
                    for player in self.players.values()
                },
                "battlefield": {
                    player.player_id: list(player.battlefield)
                    for player in self.players.values()
                },
                "graveyard": {
                    player.player_id: list(player.graveyard)
                    for player in self.players.values()
                },
                "stack": [self.visible_stack_item(item) for item in self.stack],
                "combat": {
                    "attackers": [
                        {"creature_id": attacker_id, "target": target}
                        for attacker_id, target in self.combat_attackers.items()
                        if self._find_permanent(attacker_id) is not None
                    ],
                    "blockers": [
                        {"creature_id": blocker_id, "blocking_id": attacker_id}
                        for attacker_id, blocker_ids in self.combat_blockers.items()
                        for blocker_id in blocker_ids
                        if self._find_permanent(attacker_id) is not None
                        and self._find_permanent(blocker_id) is not None
                    ],
                    "damage_order": {
                        attacker_id: list(order)
                        for attacker_id, order in self.combat_damage_order.items()
                    },
                },
                "land_played_this_turn": self.land_played_this_turn,
                "mulligans_taken": {
                    player.player_id: player.mulligans_taken
                    for player in self.players.values()
                },
                "mulligan_kept": {
                    player.player_id: player.kept for player in self.players.values()
                },
            }

    def _draw_opening_hand(self, player: PlayerGameState) -> None:
        draw_count = min(OPENING_HAND_SIZE, len(player.library))
        player.hand.extend(player.library[:draw_count])
        del player.library[:draw_count]

    def _available_mana_for_player(
        self, player: PlayerGameState
    ) -> dict[str, int]:
        """Count mana currently producible by untapped basic lands."""

        available = {color: 0 for color in MANA_COLORS}
        for permanent in player.battlefield:
            if permanent.get("tapped"):
                continue
            card_id = str(permanent.get("id", ""))
            if self.catalog is not None and card_id in self.catalog.instances:
                base_id = self.catalog.instances[card_id].base_id
            else:
                base_id = card_id.rsplit("_", 1)[0]
            mana_color = BASIC_LAND_MANA.get(base_id)
            if mana_color is not None:
                available[mana_color] += 1
        return available

    def _require_priority_action(self, seat_id: str, seq_num: int) -> None:
        if self.current_step not in PRIORITY_STEPS or self.priority_holder_seat_id is None:
            raise GameRuleError(
                ErrorCode.WRONG_PHASE,
                "No priority window is currently open.",
            )
        if seat_id != self.priority_holder_seat_id:
            raise GameRuleError(
                ErrorCode.NOT_YOUR_PRIORITY,
                f"Priority belongs to {self.priority_holder_player_id}.",
            )
        if self._expected_priority_sequence != seq_num:
            raise GameRuleError(
                ErrorCode.STALE_ACTION,
                f"Priority token mismatch. Expected {self._expected_priority_sequence}, "
                f"received {seq_num}.",
            )

    def _retain_priority_after_action(self) -> None:
        self._expected_priority_sequence = None
        self._priority_passes = 0

    def _definition(self, card_id: str) -> CardDefinition:
        if self.catalog is None:
            raise GameRuleError(
                ErrorCode.ILLEGAL_ACTION,
                "Card rules require a loaded card catalog.",
            )
        try:
            return self.catalog.definition_for_instance(card_id)
        except CatalogError as exc:
            raise GameRuleError(ErrorCode.ILLEGAL_ACTION, str(exc)) from exc

    def _validate_cast_targets(
        self, definition: CardDefinition, targets: Sequence[str]
    ) -> tuple[str, ...]:
        normalized = tuple(targets)
        if any(not isinstance(target, str) for target in normalized):
            raise GameRuleError(
                ErrorCode.ILLEGAL_TARGET,
                "Every spell target must be a player, permanent, or stack-item ID.",
            )

        requires_one = definition.base_id in SUPPORTED_EFFECTS
        if requires_one and len(normalized) != 1:
            raise GameRuleError(
                ErrorCode.ILLEGAL_TARGET,
                f"{definition.name} requires exactly one target.",
            )
        if not requires_one and normalized:
            raise GameRuleError(
                ErrorCode.ILLEGAL_TARGET,
                f"{definition.name} does not accept targets in this milestone.",
            )
        if not requires_one:
            return normalized

        target = normalized[0]
        if definition.base_id == "lightning_bolt":
            if self._seat_for_player_id(target) is None and not self._target_is_creature(target):
                raise GameRuleError(
                    ErrorCode.ILLEGAL_TARGET,
                    "Lightning Bolt must target a player or creature.",
                )
        elif definition.base_id in {"unsummon", "giant_growth"}:
            if not self._target_is_creature(target):
                raise GameRuleError(
                    ErrorCode.ILLEGAL_TARGET,
                    f"{definition.name} must target a creature.",
                )
        elif definition.base_id == "doom_blade":
            located = self._find_permanent(target)
            if located is None or not self._is_creature_permanent(located[1]):
                raise GameRuleError(
                    ErrorCode.ILLEGAL_TARGET,
                    "Doom Blade must target a creature.",
                )
            if self._definition(target).color == "B":
                raise GameRuleError(
                    ErrorCode.ILLEGAL_TARGET,
                    "Doom Blade cannot target a black creature.",
                )
        elif definition.base_id == "counterspell":
            target_item = next(
                (item for item in self.stack if item.stack_item_id == target), None
            )
            if target_item is None or target_item.item_type != "SPELL":
                raise GameRuleError(
                    ErrorCode.ILLEGAL_TARGET,
                    "Counterspell must target a spell currently on the stack.",
                )
        return normalized

    def _validate_mana_payment(
        self,
        player: PlayerGameState,
        definition: CardDefinition,
        mana_payment: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        if not isinstance(mana_payment, Mapping):
            raise GameRuleError(
                ErrorCode.INSUFFICIENT_MANA,
                "mana_payment must be an object using W, U, B, R, G, and X keys.",
            )
        allowed_keys = {*MANA_COLORS, "X"}
        if any(key not in allowed_keys for key in mana_payment):
            raise GameRuleError(
                ErrorCode.INSUFFICIENT_MANA,
                "mana_payment contains an unsupported mana key.",
            )
        if any(
            not isinstance(amount, int) or isinstance(amount, bool) or amount < 0
            for amount in mana_payment.values()
        ):
            raise GameRuleError(
                ErrorCode.INSUFFICIENT_MANA,
                "Every mana_payment amount must be a non-negative integer.",
            )

        declared = {key: amount for key, amount in mana_payment.items() if amount}
        expected = {
            color: amount
            for color, amount in definition.colored_cost.items()
            if amount
        }
        if definition.generic_cost:
            expected["X"] = definition.generic_cost
        if declared != expected:
            raise GameRuleError(
                ErrorCode.INSUFFICIENT_MANA,
                f"{definition.name} requires mana payment {expected}; received {declared}.",
            )

        available: list[tuple[dict[str, Any], str]] = []
        for permanent in player.battlefield:
            if permanent.get("tapped"):
                continue
            mana_color = BASIC_LAND_MANA.get(self._definition(permanent["id"]).base_id)
            if mana_color is not None:
                available.append((permanent, mana_color))

        selected: list[dict[str, Any]] = []
        used_ids: set[str] = set()
        for color in MANA_COLORS:
            required = expected.get(color, 0)
            matching = [
                permanent
                for permanent, produced in available
                if produced == color and permanent["id"] not in used_ids
            ]
            if len(matching) < required:
                raise GameRuleError(
                    ErrorCode.INSUFFICIENT_MANA,
                    f"Not enough untapped {color} mana sources for {definition.name}.",
                )
            for permanent in matching[:required]:
                selected.append(permanent)
                used_ids.add(permanent["id"])

        generic_required = expected.get("X", 0)
        generic_sources = [
            permanent
            for permanent, _ in available
            if permanent["id"] not in used_ids
        ]
        if len(generic_sources) < generic_required:
            raise GameRuleError(
                ErrorCode.INSUFFICIENT_MANA,
                f"Not enough untapped mana sources for {definition.name}'s generic cost.",
            )
        selected.extend(generic_sources[:generic_required])
        return selected

    def _apply_spell_effect(
        self,
        item: StackItem,
        definition: CardDefinition,
        changes: list[dict[str, Any]],
    ) -> bool:
        if definition.card_type in PERMANENT_CARD_TYPES:
            permanent: dict[str, Any] = {"id": item.source_id, "tapped": False}
            if definition.card_type in {"Creature", "Artifact Creature"}:
                permanent.update(
                    {
                        "damage": 0,
                        "power": definition.power,
                        "toughness": definition.toughness,
                        "summoning_sick": "Haste" not in definition.simplified_effect,
                    }
                )
            self.players[item.controller_seat_id].battlefield.append(permanent)
            changes.append(
                {
                    "change_type": "PERMANENT_ENTERS",
                    "card_id": item.source_id,
                    "controller": self.player_id_for_seat(item.controller_seat_id),
                    "tapped": False,
                }
            )
            return True

        target = item.targets[0]
        if definition.base_id == "lightning_bolt":
            player_seat = self._seat_for_player_id(target)
            if player_seat is not None:
                self.players[player_seat].life -= 3
            else:
                located = self._find_permanent(target)
                if located is None or not self._is_creature_permanent(located[1]):
                    return False
                located[1]["damage"] = int(located[1].get("damage", 0)) + 3
            changes.append({"change_type": "DAMAGE", "target": target, "amount": 3})
            return True

        if definition.base_id == "unsummon":
            located = self._find_permanent(target)
            if located is None or not self._is_creature_permanent(located[1]):
                return False
            owner_seat, permanent = located
            self.players[owner_seat].battlefield.remove(permanent)
            self.players[owner_seat].hand.append(target)
            changes.append({"change_type": "RETURN_TO_HAND", "target": target})
            return True

        if definition.base_id == "counterspell":
            target_item = next(
                (candidate for candidate in self.stack if candidate.stack_item_id == target),
                None,
            )
            if target_item is None or target_item.item_type != "SPELL":
                return False
            self.stack.remove(target_item)
            self.players[target_item.controller_seat_id].graveyard.append(
                target_item.source_id
            )
            changes.append(
                {
                    "change_type": "COUNTER",
                    "target": target,
                    "card_id": target_item.source_id,
                }
            )
            return True

        located = self._find_permanent(target)
        if located is None or not self._is_creature_permanent(located[1]):
            return False
        owner_seat, permanent = located
        if definition.base_id == "giant_growth":
            permanent["power"] = int(permanent["power"]) + 3
            permanent["toughness"] = int(permanent["toughness"]) + 3
            changes.append(
                {
                    "change_type": "MODIFY_STATS",
                    "target": target,
                    "power": 3,
                    "toughness": 3,
                    "duration": "END_OF_TURN",
                }
            )
            return True
        if definition.base_id == "doom_blade":
            if self._definition(target).color == "B":
                return False
            self._move_permanent_to_graveyard(owner_seat, permanent)
            changes.append({"change_type": "DESTROY", "target": target})
            return True
        return False

    def _seat_for_player_id(self, player_id: str) -> str | None:
        return next(
            (
                seat_id
                for seat_id, player in self.players.items()
                if player.player_id == player_id
            ),
            None,
        )

    def _find_permanent(
        self, card_id: str
    ) -> tuple[str, dict[str, Any]] | None:
        for seat_id, player in self.players.items():
            for permanent in player.battlefield:
                if permanent.get("id") == card_id:
                    return seat_id, permanent
        return None

    def _controlled_permanent(
        self, seat_id: str, card_id: str
    ) -> dict[str, Any]:
        player = self._player(seat_id)
        permanent = next(
            (
                candidate
                for candidate in player.battlefield
                if candidate.get("id") == card_id
            ),
            None,
        )
        if permanent is None:
            raise GameRuleError(
                ErrorCode.ILLEGAL_ACTION,
                f"{card_id} is not controlled by {player.player_id}.",
            )
        return permanent

    def _has_keyword(self, card_id: str, keyword: str) -> bool:
        return keyword.casefold() in self._definition(card_id).simplified_effect.casefold()

    def _living_blockers(self, attacker_id: str) -> list[str]:
        return [
            blocker_id
            for blocker_id in self.combat_blockers.get(attacker_id, [])
            if self._find_permanent(blocker_id) is not None
        ]

    def _deals_damage_in_step(self, card_id: str, first_strike: bool) -> bool:
        has_first_strike = self._has_keyword(card_id, "First strike")
        has_double_strike = self._has_keyword(card_id, "Double strike")
        if first_strike:
            return has_first_strike or has_double_strike
        return not has_first_strike or has_double_strike

    def _attacker_has_protection_from(
        self, attacker_id: str, blocker_id: str
    ) -> bool:
        blocker_color = self._definition(blocker_id).color
        color_name = COLOR_NAMES.get(blocker_color)
        return color_name is not None and self._has_keyword(
            attacker_id, f"Protection from {color_name}"
        )

    def _damage_is_prevented(self, source_id: str, target_id: str) -> bool:
        source_color = self._definition(source_id).color
        color_name = COLOR_NAMES.get(source_color)
        return color_name is not None and self._has_keyword(
            target_id, f"Protection from {color_name}"
        )

    def _target_is_creature(self, card_id: str) -> bool:
        located = self._find_permanent(card_id)
        return located is not None and self._is_creature_permanent(located[1])

    def _is_creature_permanent(self, permanent: Mapping[str, Any]) -> bool:
        return self._definition(str(permanent.get("id"))).card_type in {
            "Creature",
            "Artifact Creature",
        }

    def _move_permanent_to_graveyard(
        self, owner_seat_id: str, permanent: dict[str, Any]
    ) -> None:
        player = self.players[owner_seat_id]
        player.battlefield.remove(permanent)
        player.graveyard.append(permanent["id"])

    def _require_in_game_step(self, step: TurnStep) -> None:
        if self.lifecycle_state != LifecycleState.IN_GAME or self.current_step != step:
            raise GameRuleError(
                ErrorCode.WRONG_PHASE,
                f"Expected IN_GAME/{step.value}; current state is "
                f"{self.lifecycle_state.value}/{self.current_step}.",
            )

    def _player(self, seat_id: str) -> PlayerGameState:
        try:
            return self.players[seat_id]
        except KeyError as exc:
            raise GameRuleError(ErrorCode.ILLEGAL_ACTION, f"Unknown seat: {seat_id}") from exc
