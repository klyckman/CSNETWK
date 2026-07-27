"""PDU names, structural validation, and protocol-level exceptions."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping


MAX_PDU_BYTES = 65_535
DEFAULT_PORT = 4444


class MessageType(StrEnum):
    PLAYER_READY = "PLAYER_READY"
    GAME_STATE_UPDATE = "GAME_STATE_UPDATE"
    MULLIGAN_CHOICE = "MULLIGAN_CHOICE"
    PHASE_TRANSITION = "PHASE_TRANSITION"
    PRIORITY_GRANT = "PRIORITY_GRANT"
    PRIORITY_PASS = "PRIORITY_PASS"
    CAST_SPELL = "CAST_SPELL"
    ACTIVATE_ABILITY = "ACTIVATE_ABILITY"
    STACK_PUSH = "STACK_PUSH"
    TRIGGER_ORDER = "TRIGGER_ORDER"
    TRIGGER_ORDER_RESPONSE = "TRIGGER_ORDER_RESPONSE"
    TRIGGER_CHOICE = "TRIGGER_CHOICE"
    TRIGGER_CHOICE_RESPONSE = "TRIGGER_CHOICE_RESPONSE"
    STACK_RESOLVE = "STACK_RESOLVE"
    DECLARE_ATTACKERS = "DECLARE_ATTACKERS"
    DECLARE_BLOCKERS = "DECLARE_BLOCKERS"
    ASSIGN_DAMAGE_ORDER = "ASSIGN_DAMAGE_ORDER"
    COMBAT_DAMAGE_RESULT = "COMBAT_DAMAGE_RESULT"
    PLAY_LAND = "PLAY_LAND"
    DISCARD = "DISCARD"
    CONCEDE = "CONCEDE"
    GAME_OVER = "GAME_OVER"
    ERROR = "ERROR"
    PING = "PING"
    PONG = "PONG"


class ErrorCode(StrEnum):
    INVALID_JSON = "INVALID_JSON"
    ILLEGAL_DECK = "ILLEGAL_DECK"
    UNKNOWN_TYPE = "UNKNOWN_TYPE"
    STALE_ACTION = "STALE_ACTION"
    NOT_YOUR_PRIORITY = "NOT_YOUR_PRIORITY"
    ILLEGAL_ACTION = "ILLEGAL_ACTION"
    ILLEGAL_TARGET = "ILLEGAL_TARGET"
    TRIGGER_ORDER_INVALID = "TRIGGER_ORDER_INVALID"
    TRIGGER_CHOICE_INVALID = "TRIGGER_CHOICE_INVALID"
    INSUFFICIENT_MANA = "INSUFFICIENT_MANA"
    WRONG_PHASE = "WRONG_PHASE"
    DUPLICATE_ID = "DUPLICATE_ID"


class Lifecycle(StrEnum):
    LOBBY = "LOBBY"
    GAME_SETUP = "GAME_SETUP"
    MULLIGAN = "MULLIGAN"
    IN_GAME = "IN_GAME"
    GAME_OVER = "GAME_OVER"


class Phase(StrEnum):
    LOBBY = "LOBBY"
    MULLIGAN = "MULLIGAN"
    UNTAP = "UNTAP"
    UPKEEP = "UPKEEP"
    DRAW = "DRAW"
    PRECOMBAT_MAIN = "PRECOMBAT_MAIN"
    BEGIN_COMBAT = "BEGIN_COMBAT"
    DECLARE_ATTACKERS = "DECLARE_ATTACKERS"
    DECLARE_BLOCKERS = "DECLARE_BLOCKERS"
    ASSIGN_DAMAGE_ORDER = "ASSIGN_DAMAGE_ORDER"
    FIRST_STRIKE_DAMAGE = "FIRST_STRIKE_DAMAGE"
    COMBAT_DAMAGE = "COMBAT_DAMAGE"
    END_OF_COMBAT = "END_OF_COMBAT"
    POSTCOMBAT_MAIN = "POSTCOMBAT_MAIN"
    END_STEP = "END_STEP"
    CLEANUP = "CLEANUP"


CLIENT_MESSAGE_TYPES = {
    MessageType.PLAYER_READY,
    MessageType.MULLIGAN_CHOICE,
    MessageType.PRIORITY_PASS,
    MessageType.CAST_SPELL,
    MessageType.ACTIVATE_ABILITY,
    MessageType.TRIGGER_ORDER_RESPONSE,
    MessageType.TRIGGER_CHOICE_RESPONSE,
    MessageType.DECLARE_ATTACKERS,
    MessageType.DECLARE_BLOCKERS,
    MessageType.ASSIGN_DAMAGE_ORDER,
    MessageType.PLAY_LAND,
    MessageType.DISCARD,
    MessageType.CONCEDE,
    MessageType.PING,
}

SERVER_MESSAGE_TYPES = set(MessageType) - CLIENT_MESSAGE_TYPES

TOKEN_ACTIONS = {
    MessageType.MULLIGAN_CHOICE,
    MessageType.PRIORITY_PASS,
    MessageType.CAST_SPELL,
    MessageType.ACTIVATE_ABILITY,
    MessageType.TRIGGER_ORDER_RESPONSE,
    MessageType.TRIGGER_CHOICE_RESPONSE,
    MessageType.DECLARE_ATTACKERS,
    MessageType.DECLARE_BLOCKERS,
    MessageType.ASSIGN_DAMAGE_ORDER,
    MessageType.PLAY_LAND,
    MessageType.DISCARD,
}

REQUIRED_FIELDS: dict[MessageType, tuple[str, ...]] = {
    MessageType.PLAYER_READY: ("player_id", "deck_list"),
    MessageType.GAME_STATE_UPDATE: ("state",),
    MessageType.MULLIGAN_CHOICE: ("keep", "cards_to_bottom"),
    MessageType.PHASE_TRANSITION: ("from_phase", "to_phase", "active_player", "turn"),
    MessageType.PRIORITY_GRANT: ("player_id", "time_limit_ms"),
    MessageType.PRIORITY_PASS: (),
    MessageType.CAST_SPELL: ("card_id", "targets", "mana_payment"),
    MessageType.ACTIVATE_ABILITY: (
        "source_id",
        "ability_index",
        "targets",
        "cost_payment",
    ),
    MessageType.STACK_PUSH: (
        "stack_item_id",
        "item_type",
        "source",
        "targets",
        "controller",
    ),
    MessageType.TRIGGER_ORDER: ("player_id", "trigger_ids"),
    MessageType.TRIGGER_ORDER_RESPONSE: ("ordered_trigger_ids",),
    MessageType.TRIGGER_CHOICE: (
        "trigger_id",
        "source_id",
        "effect_summary",
        "requires_target",
        "legal_targets",
    ),
    MessageType.TRIGGER_CHOICE_RESPONSE: ("trigger_id", "accept"),
    MessageType.STACK_RESOLVE: (
        "stack_item_id",
        "result",
        "state_changes",
    ),
    MessageType.DECLARE_ATTACKERS: ("attackers",),
    MessageType.DECLARE_BLOCKERS: ("blockers",),
    MessageType.ASSIGN_DAMAGE_ORDER: ("attacker_id", "blocker_order"),
    MessageType.COMBAT_DAMAGE_RESULT: (
        "damage_events",
        "life_totals",
        "creatures_died",
    ),
    MessageType.PLAY_LAND: ("card_id",),
    MessageType.DISCARD: ("card_ids",),
    MessageType.CONCEDE: ("player_id",),
    MessageType.GAME_OVER: ("winner_id", "loser_id", "reason"),
    MessageType.ERROR: ("code", "message"),
    MessageType.PING: ("timestamp",),
    MessageType.PONG: ("timestamp",),
}


class ProtocolError(Exception):
    """A recoverable protocol violation that should become an ERROR PDU."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        rejected_action: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.rejected_action = dict(rejected_action) if rejected_action else None


def validate_pdu(pdu: Any, *, direction: str | None = None) -> MessageType:
    """Validate common PDU structure and required fields.

    Semantic game-rule validation intentionally belongs to the authoritative
    server engine.
    """

    if not isinstance(pdu, dict):
        raise ProtocolError(ErrorCode.INVALID_JSON, "PDU payload must be a JSON object.")

    raw_type = pdu.get("type")
    if not isinstance(raw_type, str):
        raise ProtocolError(
            ErrorCode.UNKNOWN_TYPE,
            "Every PDU must contain a string 'type' field.",
            pdu,
        )
    try:
        message_type = MessageType(raw_type)
    except ValueError as exc:
        raise ProtocolError(
            ErrorCode.UNKNOWN_TYPE,
            f"Unknown PDU type: {raw_type!r}.",
            pdu,
        ) from exc

    seq_num = pdu.get("seq_num")
    if isinstance(seq_num, bool) or not isinstance(seq_num, int) or seq_num < 0:
        raise ProtocolError(
            ErrorCode.ILLEGAL_ACTION,
            "Every PDU must contain a non-negative integer 'seq_num'.",
            pdu,
        )

    if direction == "client" and message_type not in CLIENT_MESSAGE_TYPES:
        raise ProtocolError(
            ErrorCode.ILLEGAL_ACTION,
            f"{message_type} is not a client-to-server PDU.",
            pdu,
        )
    if direction == "server" and message_type not in SERVER_MESSAGE_TYPES:
        raise ProtocolError(
            ErrorCode.ILLEGAL_ACTION,
            f"{message_type} is not a server-to-client PDU.",
            pdu,
        )

    missing = [field for field in REQUIRED_FIELDS[message_type] if field not in pdu]
    if missing:
        raise ProtocolError(
            ErrorCode.ILLEGAL_ACTION,
            f"{message_type} is missing required field(s): {', '.join(missing)}.",
            pdu,
        )
    return message_type

