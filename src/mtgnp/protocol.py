"""Shared MTGNP PDU names, directions, and base structural validation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any


MAX_PDU_SIZE = 65_535


class Sender(StrEnum):
    CLIENT = "CLIENT"
    SERVER = "SERVER"


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


class LifecycleState(StrEnum):
    LOBBY = "LOBBY"
    GAME_SETUP = "GAME_SETUP"
    MULLIGAN = "MULLIGAN"
    IN_GAME = "IN_GAME"
    GAME_OVER = "GAME_OVER"


class TurnStep(StrEnum):
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


@dataclass(frozen=True, slots=True)
class PDUSpec:
    sender: Sender
    required_fields: frozenset[str]


def _spec(sender: Sender, *required_fields: str) -> PDUSpec:
    return PDUSpec(sender, frozenset({"type", "seq_num", *required_fields}))


PDU_SPECS: Mapping[MessageType, PDUSpec] = MappingProxyType(
    {
        MessageType.PLAYER_READY: _spec(Sender.CLIENT, "player_id", "deck_list"),
        MessageType.GAME_STATE_UPDATE: _spec(Sender.SERVER, "state"),
        MessageType.MULLIGAN_CHOICE: _spec(
            Sender.CLIENT, "keep", "cards_to_bottom"
        ),
        MessageType.PHASE_TRANSITION: _spec(
            Sender.SERVER, "from_phase", "to_phase", "active_player", "turn"
        ),
        MessageType.PRIORITY_GRANT: _spec(
            Sender.SERVER, "player_id", "time_limit_ms"
        ),
        MessageType.PRIORITY_PASS: _spec(Sender.CLIENT),
        MessageType.CAST_SPELL: _spec(
            Sender.CLIENT, "card_id", "targets", "mana_payment"
        ),
        MessageType.ACTIVATE_ABILITY: _spec(
            Sender.CLIENT,
            "source_id",
            "ability_index",
            "targets",
            "cost_payment",
        ),
        MessageType.STACK_PUSH: _spec(
            Sender.SERVER,
            "stack_item_id",
            "item_type",
            "source",
            "targets",
            "controller",
        ),
        MessageType.TRIGGER_ORDER: _spec(
            Sender.SERVER, "player_id", "trigger_ids"
        ),
        MessageType.TRIGGER_ORDER_RESPONSE: _spec(
            Sender.CLIENT, "ordered_trigger_ids"
        ),
        MessageType.TRIGGER_CHOICE: _spec(
            Sender.SERVER,
            "trigger_id",
            "source_id",
            "effect_summary",
            "requires_target",
            "legal_targets",
        ),
        MessageType.TRIGGER_CHOICE_RESPONSE: _spec(
            Sender.CLIENT, "trigger_id", "accept"
        ),
        MessageType.STACK_RESOLVE: _spec(
            Sender.SERVER, "stack_item_id", "result", "state_changes"
        ),
        MessageType.DECLARE_ATTACKERS: _spec(Sender.CLIENT, "attackers"),
        MessageType.DECLARE_BLOCKERS: _spec(Sender.CLIENT, "blockers"),
        MessageType.ASSIGN_DAMAGE_ORDER: _spec(
            Sender.CLIENT, "attacker_id", "blocker_order"
        ),
        MessageType.COMBAT_DAMAGE_RESULT: _spec(
            Sender.SERVER, "damage_events", "life_totals", "creatures_died"
        ),
        MessageType.PLAY_LAND: _spec(Sender.CLIENT, "card_id"),
        MessageType.DISCARD: _spec(Sender.CLIENT, "card_ids"),
        MessageType.CONCEDE: _spec(Sender.CLIENT, "player_id"),
        MessageType.GAME_OVER: _spec(
            Sender.SERVER, "winner_id", "loser_id", "reason"
        ),
        MessageType.ERROR: _spec(
            Sender.SERVER, "code", "message", "rejected_action"
        ),
        MessageType.PING: _spec(Sender.CLIENT, "timestamp"),
        MessageType.PONG: _spec(Sender.SERVER, "timestamp"),
    }
)


LIST_FIELDS = frozenset(
    {
        "deck_list",
        "cards_to_bottom",
        "targets",
        "trigger_ids",
        "ordered_trigger_ids",
        "legal_targets",
        "state_changes",
        "attackers",
        "blockers",
        "blocker_order",
        "damage_events",
        "creatures_died",
        "card_ids",
    }
)
OBJECT_FIELDS = frozenset(
    {"state", "mana_payment", "cost_payment", "life_totals", "rejected_action"}
)
STRING_FIELDS = frozenset(
    {
        "player_id",
        "card_id",
        "from_phase",
        "to_phase",
        "active_player",
        "source_id",
        "stack_item_id",
        "item_type",
        "source",
        "controller",
        "trigger_id",
        "chosen_target",
        "effect_summary",
        "result",
        "attacker_id",
        "winner_id",
        "loser_id",
        "reason",
        "code",
        "message",
    }
)
BOOLEAN_FIELDS = frozenset({"keep", "requires_target", "accept", "optional"})
INTEGER_FIELDS = frozenset({"seq_num", "turn", "time_limit_ms", "ability_index", "timestamp"})


class PDUValidationError(ValueError):
    """A PDU could not be parsed or failed base structural validation."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def _require_field_shapes(pdu: Mapping[str, Any], fields: frozenset[str]) -> None:
    for field in fields:
        if field not in pdu:
            continue
        value = pdu[field]
        if field in LIST_FIELDS and not isinstance(value, list):
            raise PDUValidationError(ErrorCode.ILLEGAL_ACTION, f"{field} must be an array.")
        if field in OBJECT_FIELDS and not isinstance(value, Mapping):
            raise PDUValidationError(ErrorCode.ILLEGAL_ACTION, f"{field} must be an object.")
        if field in STRING_FIELDS and (not isinstance(value, str) or not value):
            raise PDUValidationError(
                ErrorCode.ILLEGAL_ACTION, f"{field} must be a non-empty string."
            )
        if field in BOOLEAN_FIELDS and not isinstance(value, bool):
            raise PDUValidationError(ErrorCode.ILLEGAL_ACTION, f"{field} must be a boolean.")
        if field in INTEGER_FIELDS and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise PDUValidationError(
                ErrorCode.ILLEGAL_ACTION,
                f"{field} must be a non-negative integer.",
            )


def validate_pdu(
    pdu: Mapping[str, Any], *, sender: Sender | None = None
) -> MessageType:
    """Validate the common envelope, sender, fields, and basic JSON shapes."""

    if not isinstance(pdu, Mapping):
        raise PDUValidationError(ErrorCode.INVALID_JSON, "A PDU must be a JSON object.")

    raw_type = pdu.get("type")
    try:
        message_type = MessageType(raw_type)
    except (TypeError, ValueError) as exc:
        raise PDUValidationError(
            ErrorCode.UNKNOWN_TYPE, f"Unknown or missing PDU type: {raw_type!r}"
        ) from exc

    spec = PDU_SPECS[message_type]
    if sender is not None and sender != spec.sender:
        raise PDUValidationError(
            ErrorCode.ILLEGAL_ACTION,
            f"{message_type.value} must be sent by {spec.sender.value}, not {sender.value}.",
        )

    missing = sorted(spec.required_fields.difference(pdu))
    if missing:
        raise PDUValidationError(
            ErrorCode.ILLEGAL_ACTION,
            f"{message_type.value} is missing fields: {', '.join(missing)}",
        )

    _require_field_shapes(pdu, frozenset(pdu))

    if message_type == MessageType.ERROR:
        try:
            ErrorCode(pdu["code"])
        except ValueError as exc:
            raise PDUValidationError(
                ErrorCode.ILLEGAL_ACTION, f"Unknown ERROR code: {pdu['code']!r}"
            ) from exc

    return message_type


def encode_pdu(pdu: Mapping[str, Any], *, sender: Sender | None = None) -> bytes:
    """Validate a PDU and encode its JSON payload (without TCP framing)."""

    validate_pdu(pdu, sender=sender)
    try:
        payload = json.dumps(
            pdu, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PDUValidationError(ErrorCode.INVALID_JSON, f"PDU is not JSON-safe: {exc}") from exc
    if len(payload) > MAX_PDU_SIZE:
        raise PDUValidationError(
            ErrorCode.ILLEGAL_ACTION,
            f"PDU payload is {len(payload)} bytes; maximum is {MAX_PDU_SIZE}.",
        )
    return payload


def decode_pdu(payload: bytes, *, sender: Sender | None = None) -> dict[str, Any]:
    """Decode and validate one unframed UTF-8 JSON PDU payload."""

    if len(payload) > MAX_PDU_SIZE:
        raise PDUValidationError(
            ErrorCode.ILLEGAL_ACTION,
            f"PDU payload is {len(payload)} bytes; maximum is {MAX_PDU_SIZE}.",
        )
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PDUValidationError(ErrorCode.INVALID_JSON, f"Invalid UTF-8 JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise PDUValidationError(ErrorCode.INVALID_JSON, "A PDU must be a JSON object.")
    validate_pdu(decoded, sender=sender)
    return decoded
