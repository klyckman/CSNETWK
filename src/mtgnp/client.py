"""Minimal command-line MTGNP client for the LOBBY milestone."""

from __future__ import annotations

import argparse
import json
import queue
import shlex
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from .abilities import ACTIVATED_ABILITIES, activated_ability
from .catalog import CardCatalog, CatalogError, MANA_COLORS, load_catalog
from .framing import ConnectionClosed, FramedConnection
from .protocol import MessageType, Sender
from .tracing import PDUTracer


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4444
DEFAULT_DATA_DIRECTORY = Path(__file__).resolve().parents[2] / "data"
DISPLAY_WIDTH = 78
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0
DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 10.0


class _PromptTracker:
    """Remember the active interactive prompt so background output can restore it."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active_prompt: str | None = None

    def read(self, prompt: str) -> str:
        with self._lock:
            self._active_prompt = prompt
        try:
            return input(prompt)
        finally:
            with self._lock:
                if self._active_prompt == prompt:
                    self._active_prompt = None

    def redraw(self) -> None:
        with self._lock:
            prompt = self._active_prompt
        if prompt is None:
            return
        label = prompt.rstrip()
        if label.endswith(":"):
            label = label[:-1]
        print(
            f"{label} (continue; existing input is preserved): ",
            end="",
            flush=True,
        )


_PROMPT_TRACKER = _PromptTracker()


def _read_input(prompt: str) -> str:
    return _PROMPT_TRACKER.read(prompt)


def load_deck_file(path: str | Path, catalog: CardCatalog) -> tuple[str, ...]:
    """Load either a JSON array or an object containing a deck_list array."""

    deck_path = Path(path)
    try:
        document = json.loads(deck_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"Cannot load deck file {deck_path}: {exc}") from exc
    deck_list = document.get("deck_list") if isinstance(document, dict) else document
    if not isinstance(deck_list, list):
        raise CatalogError("Deck JSON must be an array or contain a deck_list array.")
    return catalog.validate_deck(deck_list)


class MTGNPClient:
    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        verbose: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.tracer = PDUTracer(
            enabled=verbose,
            leading_newline=True,
            heartbeat_border="+" * DISPLAY_WIDTH,
        )
        self.connection: FramedConnection | None = None
        self._sequence = 1
        self._sequence_lock = threading.Lock()
        self._heartbeat_sequence = 1
        self._heartbeat_lock = threading.RLock()
        self._pending_ping: tuple[int, int] | None = None
        self._pong_received = threading.Event()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_failed = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._receiver_thread: threading.Thread | None = None
        self._incoming: queue.Queue[dict[str, Any] | Exception] = queue.Queue()
        self._heartbeat_resume_renderer: Callable[[], None] | None = None

    def connect(self) -> None:
        stream = socket.create_connection((self.host, self.port))
        self.connection = FramedConnection(
            stream,
            local_sender=Sender.CLIENT,
            tracer=self.tracer,
            peer_label=f"server@{self.host}:{self.port}",
        )

    def set_verbose(self, enabled: bool) -> None:
        self.tracer.set_enabled(enabled)

    def set_heartbeat_resume_renderer(
        self, renderer: Callable[[], None] | None
    ) -> None:
        """Set the UI callback run after a verbose, matching PONG is received."""

        self._heartbeat_resume_renderer = renderer

    def _next_client_sequence(self) -> int:
        with self._sequence_lock:
            value = self._sequence
            self._sequence += 1
            return value

    def send_ready(self, player_id: str, deck_list: Sequence[str]) -> None:
        self._connection().send(
            {
                "type": MessageType.PLAYER_READY.value,
                "seq_num": self._next_client_sequence(),
                "player_id": player_id,
                "deck_list": list(deck_list),
            }
        )

    def send_ping(self) -> tuple[int, int]:
        with self._heartbeat_lock:
            seq_num = self._heartbeat_sequence
            self._heartbeat_sequence += 1
            timestamp = int(time.time() * 1000)
            self._pending_ping = (seq_num, timestamp)
            self._pong_received.clear()
        self._connection().send(
            {
                "type": MessageType.PING.value,
                "seq_num": seq_num,
                "timestamp": timestamp,
            }
        )
        return seq_num, timestamp

    def start_heartbeat(
        self,
        *,
        interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        timeout_seconds: float = DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
    ) -> None:
        if interval_seconds <= 0 or timeout_seconds <= 0:
            raise ValueError("Heartbeat interval and timeout must be positive.")
        if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
            return
        self._heartbeat_stop.clear()
        self._heartbeat_failed.clear()
        self._start_receiver()
        thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(interval_seconds, timeout_seconds),
            name="mtgnp-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread = thread
        thread.start()

    @property
    def heartbeat_failed(self) -> bool:
        return self._heartbeat_failed.is_set()

    def _start_receiver(self) -> None:
        if self._receiver_thread is not None and self._receiver_thread.is_alive():
            return
        thread = threading.Thread(
            target=self._receiver_loop,
            name="mtgnp-receiver",
            daemon=True,
        )
        self._receiver_thread = thread
        thread.start()

    def _receiver_loop(self) -> None:
        try:
            while not self._heartbeat_stop.is_set():
                pdu = self._connection().receive()
                if pdu["type"] == MessageType.PONG.value:
                    self._record_pong(pdu)
                self._incoming.put(pdu)
        except Exception as exc:
            if not self._heartbeat_stop.is_set():
                self._incoming.put(exc)

    def _heartbeat_loop(
        self, interval_seconds: float, timeout_seconds: float
    ) -> None:
        while not self._heartbeat_stop.wait(interval_seconds):
            try:
                self.send_ping()
            except (ConnectionError, ConnectionClosed, OSError):
                self._fail_heartbeat()
                return
            if not self._pong_received.wait(timeout_seconds):
                self._fail_heartbeat()
                return

    def _record_pong(self, pdu: dict[str, Any]) -> None:
        matched = False
        with self._heartbeat_lock:
            if self._pending_ping == (pdu["seq_num"], pdu["timestamp"]):
                self._pending_ping = None
                self._pong_received.set()
                matched = True
        if matched and self.tracer.enabled:
            renderer = self._heartbeat_resume_renderer
            if renderer is not None:
                renderer()

    def _fail_heartbeat(self) -> None:
        self._heartbeat_failed.set()
        if self.connection is not None:
            self.connection.close()

    def send_mulligan_choice(
        self,
        *,
        request_seq_num: int,
        keep: bool,
        cards_to_bottom: Sequence[str] = (),
    ) -> None:
        self._connection().send(
            {
                "type": MessageType.MULLIGAN_CHOICE.value,
                "seq_num": request_seq_num,
                "keep": keep,
                "cards_to_bottom": list(cards_to_bottom),
            }
        )

    def send_priority_pass(self, *, request_seq_num: int) -> None:
        self._connection().send(
            {
                "type": MessageType.PRIORITY_PASS.value,
                "seq_num": request_seq_num,
            }
        )

    def send_play_land(self, *, request_seq_num: int, card_id: str) -> None:
        self._connection().send(
            {
                "type": MessageType.PLAY_LAND.value,
                "seq_num": request_seq_num,
                "card_id": card_id,
            }
        )

    def send_cast_spell(
        self,
        *,
        request_seq_num: int,
        card_id: str,
        targets: Sequence[str],
        mana_payment: dict[str, int],
    ) -> None:
        self._connection().send(
            {
                "type": MessageType.CAST_SPELL.value,
                "seq_num": request_seq_num,
                "card_id": card_id,
                "targets": list(targets),
                "mana_payment": dict(mana_payment),
            }
        )

    def send_activate_ability(
        self,
        *,
        request_seq_num: int,
        source_id: str,
        ability_index: int,
        targets: Sequence[str],
        cost_payment: dict[str, Any],
    ) -> None:
        self._connection().send(
            {
                "type": MessageType.ACTIVATE_ABILITY.value,
                "seq_num": request_seq_num,
                "source_id": source_id,
                "ability_index": ability_index,
                "targets": list(targets),
                "cost_payment": dict(cost_payment),
            }
        )

    def send_trigger_order_response(
        self,
        *,
        request_seq_num: int,
        ordered_trigger_ids: Sequence[str],
    ) -> None:
        self._connection().send(
            {
                "type": MessageType.TRIGGER_ORDER_RESPONSE.value,
                "seq_num": request_seq_num,
                "ordered_trigger_ids": list(ordered_trigger_ids),
            }
        )

    def send_trigger_choice_response(
        self,
        *,
        request_seq_num: int,
        trigger_id: str,
        accept: bool,
        chosen_target: str | None = None,
    ) -> None:
        pdu: dict[str, Any] = {
            "type": MessageType.TRIGGER_CHOICE_RESPONSE.value,
            "seq_num": request_seq_num,
            "trigger_id": trigger_id,
            "accept": accept,
        }
        if chosen_target is not None:
            pdu["chosen_target"] = chosen_target
        self._connection().send(pdu)

    def send_attackers(
        self, *, request_seq_num: int, attackers: Sequence[dict[str, str]]
    ) -> None:
        self._connection().send(
            {
                "type": MessageType.DECLARE_ATTACKERS.value,
                "seq_num": request_seq_num,
                "attackers": list(attackers),
            }
        )

    def send_empty_attackers(self, *, request_seq_num: int) -> None:
        self.send_attackers(request_seq_num=request_seq_num, attackers=())

    def send_blockers(
        self, *, request_seq_num: int, blockers: Sequence[dict[str, str]]
    ) -> None:
        self._connection().send(
            {
                "type": MessageType.DECLARE_BLOCKERS.value,
                "seq_num": request_seq_num,
                "blockers": list(blockers),
            }
        )

    def send_damage_order(
        self,
        *,
        request_seq_num: int,
        attacker_id: str,
        blocker_order: Sequence[str],
    ) -> None:
        self._connection().send(
            {
                "type": MessageType.ASSIGN_DAMAGE_ORDER.value,
                "seq_num": request_seq_num,
                "attacker_id": attacker_id,
                "blocker_order": list(blocker_order),
            }
        )

    def send_discard(
        self, *, request_seq_num: int, card_ids: Sequence[str]
    ) -> None:
        self._connection().send(
            {
                "type": MessageType.DISCARD.value,
                "seq_num": request_seq_num,
                "card_ids": list(card_ids),
            }
        )

    def send_concede(self, *, request_seq_num: int, player_id: str) -> None:
        self._connection().send(
            {
                "type": MessageType.CONCEDE.value,
                "seq_num": request_seq_num,
                "player_id": player_id,
            }
        )

    def receive(self) -> dict[str, Any]:
        if self._receiver_thread is None:
            return self._connection().receive()
        received = self._incoming.get()
        if isinstance(received, Exception):
            raise received
        return received

    def close(self) -> None:
        self._heartbeat_stop.set()
        self._pong_received.set()
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        current = threading.current_thread()
        for thread in (self._heartbeat_thread, self._receiver_thread):
            if thread is not None and thread is not current:
                thread.join(timeout=1)

    def _connection(self) -> FramedConnection:
        if self.connection is None:
            raise ConnectionError("Client is not connected.")
        return self.connection


def _format_permanent(permanent: dict[str, Any]) -> str:
    status = "tapped" if permanent.get("tapped") else "untapped"
    details = [status]
    if "power" in permanent and "toughness" in permanent:
        details.append(f"{permanent['power']}/{permanent['toughness']}")
    if permanent.get("damage"):
        details.append(f"damage={permanent['damage']}")
    if permanent.get("summoning_sick"):
        details.append("summoning sick")
    if permanent.get("attacking"):
        details.append("attacking")
    if permanent.get("blocking"):
        details.append(f"blocking {permanent['blocking']}")
    return f"{permanent.get('id', '?')} [{', '.join(details)}]"


def _format_available_mana(mana: dict[str, Any]) -> str:
    ready = [
        f"{color}={mana.get(color, 0)}"
        for color in MANA_COLORS
        if mana.get(color, 0)
    ]
    return " | ".join(ready) if ready else "none"


def _render_game_state(state: dict[str, Any]) -> None:
    own_hand = next(iter(state.get("hand", {}).values()), [])
    life_text = " | ".join(
        f"{player_id}={life}" for player_id, life in state["life_totals"].items()
    )
    print()
    print(
        f"[STATE] Turn {state['turn']} | {state['phase']} | "
        f"Active: {state['active_player']}"
    )
    print(f"  Life: {life_text}")
    available_mana = state.get("available_mana", {})
    if available_mana:
        print("  Available mana (untapped basic lands):")
        for player_id, mana in available_mana.items():
            print(f"    {player_id}: {_format_available_mana(mana)}")
    print(f"  Your hand ({len(own_hand)}): {own_hand}")
    print("  Battlefield:")
    for player_id, permanents in state.get("battlefield", {}).items():
        rendered = (
            ", ".join(_format_permanent(card) for card in permanents)
            if permanents
            else "(empty)"
        )
        print(f"    {player_id}: {rendered}")

    stack = state.get("stack", [])
    print("  Stack (bottom -> top):")
    if stack:
        for item in stack:
            print(
                f"    {item['stack_item_id']} ({item['item_type']}): {item['source']} "
                f"by {item['controller']} -> {item['targets']}"
            )
    else:
        print("    (empty)")

    print("  Graveyards:")
    for player_id, cards in state.get("graveyard", {}).items():
        print(f"    {player_id}: {cards or '(empty)'}")
    combat = state.get("combat", {})
    if combat.get("attackers") or combat.get("blockers"):
        print("  Combat:")
        print(f"    Attackers: {combat.get('attackers', [])}")
        print(f"    Blockers: {combat.get('blockers', [])}")
        if combat.get("damage_order"):
            print(f"    Damage order: {combat['damage_order']}")
    print()


def _state_after_phase_transition(
    state: dict[str, Any], pdu: dict[str, Any]
) -> dict[str, Any]:
    """Update the client's display context until the next authoritative state PDU."""

    updated = dict(state)
    updated["phase"] = pdu["to_phase"]
    updated["turn"] = pdu["turn"]
    updated["active_player"] = pdu["active_player"]
    return updated


def _render_heartbeat_resume(
    state: dict[str, Any] | None, player_id: str
) -> None:
    """Restore concise gameplay context after verbose heartbeat output."""

    if state is None:
        print("[GAME] Waiting for the first authoritative game-state update.")
    elif state.get("phase") == "LOBBY":
        print(
            "[GAME] Phase: LOBBY | "
            f"{state.get('players_connected', 0)}/2 connected | "
            f"{state.get('players_ready', 0)}/2 ready"
        )
    else:
        parts = [
            f"Turn {state.get('turn', '?')}",
            f"Phase: {state.get('phase', 'UNKNOWN')}",
            f"Owner: {state.get('active_player', 'unknown')}",
        ]
        own_mana = state.get("available_mana", {}).get(player_id)
        if own_mana is not None:
            parts.append(f"Mana: {_format_available_mana(own_mana)}")
        stack = state.get("stack", [])
        stack_summary = "empty" if not stack else f"{len(stack)} item(s)"
        parts.append(f"Stack: {stack_summary}")
        print("[GAME] " + " | ".join(parts))
    _PROMPT_TRACKER.redraw()


def _render_pdu(pdu: dict[str, Any]) -> None:
    message_type = MessageType(pdu["type"])
    if message_type == MessageType.GAME_STATE_UPDATE:
        state = pdu["state"]
        if state.get("phase") != "LOBBY":
            _render_game_state(state)
            return
        print(
            "Lobby: "
            f"{state['players_connected']}/2 connected, "
            f"{state['players_ready']}/2 ready, "
            f"waiting for {state['waiting_for']}"
        )
        if state["players_ready"] == 2:
            print("Both players are ready. Starting GAME_SETUP.")
        return
    if message_type == MessageType.PHASE_TRANSITION:
        if pdu["to_phase"] == "UNTAP":
            print("\n\n")
            print("=" * DISPLAY_WIDTH)
            print(
                f"TURN {pdu['turn']}  |  ACTIVE PLAYER: "
                f"{pdu['active_player'].upper()}"
            )
            print("=" * DISPLAY_WIDTH)
            print()
        else:
            print()
            print(
                f"--- ENTERING PHASE: {pdu['to_phase']} "
                f"| from: {pdu['from_phase']} "
                f"| active: {pdu['active_player']} ---"
            )
            print()
        return
    if message_type == MessageType.PRIORITY_GRANT:
        print()
        print(
            f"[PRIORITY] Granted to {pdu['player_id']} "
            f"for up to {pdu['time_limit_ms']} ms."
        )
        print()
        return
    if message_type == MessageType.STACK_PUSH:
        if pdu["item_type"] == "ABILITY":
            action = "activated an ability of"
        elif pdu["item_type"] == "TRIGGER_ABILITY":
            action = "put a triggered ability from"
        else:
            action = "cast"
        print()
        print(
            f"[STACK PUSH] {pdu['controller']} {action} {pdu['source']} as "
            f"{pdu['stack_item_id']} targeting {pdu['targets']}."
        )
        print()
        return
    if message_type == MessageType.TRIGGER_CHOICE:
        print()
        print(
            f"[TRIGGER CHOICE] {pdu['trigger_id']} from {pdu['source_id']}: "
            f"{pdu['effect_summary']}"
        )
        if pdu["requires_target"]:
            print(f"  Legal targets: {pdu['legal_targets']}")
        print()
        return
    if message_type == MessageType.TRIGGER_ORDER:
        print()
        print(
            f"[TRIGGER ORDER] {pdu['player_id']} must order simultaneous "
            f"triggers {pdu['trigger_ids']} (first is Stack bottom)."
        )
        print()
        return
    if message_type == MessageType.STACK_RESOLVE:
        print()
        print(
            f"[STACK RESOLVE] {pdu['stack_item_id']} {pdu['result']}; "
            f"changes={pdu['state_changes']}"
        )
        print()
        return
    if message_type == MessageType.COMBAT_DAMAGE_RESULT:
        print()
        print("[COMBAT DAMAGE]")
        if pdu["damage_events"]:
            for event in pdu["damage_events"]:
                print(
                    f"  {event['source']} -> {event['target']}: "
                    f"{event['amount']} damage"
                )
        else:
            print("  No combat damage was dealt.")
        print(f"  Life totals: {pdu['life_totals']}")
        print(f"  Creatures died: {pdu['creatures_died'] or '(none)'}")
        print()
        return
    if message_type == MessageType.ERROR:
        print(
            f"\n[SERVER ERROR: {pdu['code']}] {pdu['message']}\n",
            file=sys.stderr,
        )
        return
    if message_type == MessageType.PONG:
        # Verbose tracing already displays the complete PONG. In normal mode a
        # successful heartbeat is intentionally silent.
        return
    if message_type == MessageType.GAME_OVER:
        print("\n" + "=" * DISPLAY_WIDTH)
        print(
            f"GAME OVER: {pdu['winner_id']} defeated {pdu['loser_id']} "
            f"({pdu['reason']})."
        )
        print("=" * DISPLAY_WIDTH + "\n")
        return
    print(json.dumps(pdu, ensure_ascii=False, indent=2))


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Join an MTGNP lobby.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--player-id", required=True)
    parser.add_argument("--deck", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_DIRECTORY)
    parser.add_argument(
        "--verbose", action="store_true", help="Print every PDU sent and received."
    )
    parser.add_argument(
        "--auto-keep",
        action="store_true",
        help="Automatically keep the opening hand without taking a mulligan.",
    )
    parser.add_argument(
        "--auto-pass",
        action="store_true",
        help=(
            "Automatically pass priority, declare no attackers or blockers, "
            "make trigger choices, order damage automatically, and discard to seven."
        ),
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=_positive_seconds,
        default=DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        help="Seconds between PING heartbeats (default: 30).",
    )
    parser.add_argument(
        "--heartbeat-timeout",
        type=_positive_seconds,
        default=DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
        help="Seconds to wait for the matching PONG (default: 10).",
    )
    return parser


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number of seconds") from exc
    if seconds <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return seconds


def _choose_mulligan(
    state: dict[str, Any], player_id: str, *, auto_keep: bool
) -> tuple[bool, tuple[str, ...]]:
    hand = tuple(state["hand"].get(player_id, []))
    mulligans_taken = int(state["mulligans_taken"][player_id])
    if auto_keep:
        return True, hand[:mulligans_taken]

    while True:
        choice = _read_input("Keep this hand or mulligan? [k/m]: ").strip().lower()
        if choice in {"m", "mulligan"}:
            return False, ()
        if choice not in {"k", "keep"}:
            print("Enter 'k' to keep or 'm' to mulligan.")
            continue
        if mulligans_taken == 0:
            return True, ()

        print(
            f"Choose exactly {mulligans_taken} card(s) from your hand to put "
            "on the bottom of the library."
        )
        selected = tuple(_read_input("Card IDs, separated by spaces: ").split())
        if len(selected) != mulligans_taken:
            print(f"Exactly {mulligans_taken} card ID(s) are required.")
            continue
        if len(set(selected)) != len(selected) or any(card not in hand for card in selected):
            print("Every selected ID must be a distinct card in your current hand.")
            continue
        return True, selected


def _choose_cleanup_discard(
    state: dict[str, Any], player_id: str, *, automatic: bool
) -> tuple[str, ...]:
    hand = tuple(state["hand"].get(player_id, []))
    excess = max(0, len(hand) - 7)
    if excess == 0:
        return ()
    if automatic:
        return hand[-excess:]

    while True:
        print(f"Cleanup requires discarding exactly {excess} card(s): {list(hand)}")
        selected = tuple(_read_input("Card IDs, separated by spaces: ").split())
        if len(selected) != excess:
            print(f"Exactly {excess} card ID(s) are required.")
            continue
        if len(set(selected)) != len(selected) or any(card not in hand for card in selected):
            print("Every selected ID must be a distinct card in your current hand.")
            continue
        return selected


def _default_mana_payment(card_id: str, catalog: CardCatalog) -> dict[str, int]:
    definition = catalog.definition_for_instance(card_id)
    payment = {
        color: amount
        for color, amount in definition.colored_cost.items()
        if amount
    }
    if definition.generic_cost:
        payment["X"] = definition.generic_cost
    return payment


def _parse_mana_override(
    action_tokens: Sequence[str], default: dict[str, int]
) -> tuple[tuple[str, ...], dict[str, int]]:
    if "--mana" not in action_tokens:
        return tuple(action_tokens), dict(default)
    if action_tokens.count("--mana") != 1:
        raise ValueError("--mana may be supplied only once.")
    marker = action_tokens.index("--mana")
    targets = tuple(action_tokens[:marker])
    payment_tokens = action_tokens[marker + 1 :]
    if not payment_tokens:
        raise ValueError("--mana must be followed by entries such as R=1,X=1.")
    entries = [
        entry
        for token in payment_tokens
        for entry in token.split(",")
        if entry
    ]
    payment: dict[str, int] = {}
    for entry in entries:
        try:
            color, raw_amount = entry.split("=", 1)
            amount = int(raw_amount)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"Invalid mana entry {entry!r}; use forms such as U=2 or B=1,X=1."
            ) from exc
        color = color.upper()
        if color not in {"W", "U", "B", "R", "G", "X"} or amount < 0:
            raise ValueError(f"Invalid mana entry {entry!r}.")
        if color in payment:
            raise ValueError(f"Mana key {color} was supplied more than once.")
        payment[color] = amount
    return targets, payment


def _parse_priority_action(
    command: str, catalog: CardCatalog
) -> tuple[str, dict[str, Any]]:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"Cannot parse action: {exc}") from exc
    if not tokens:
        raise ValueError(
            "Enter pass, concede, land CARD_ID, cast CARD_ID, or activate SOURCE_ID."
        )

    action = tokens[0].lower()
    if action in {"pass", "p"} and len(tokens) == 1:
        return "pass", {}
    if action in {"concede", "surrender"} and len(tokens) == 1:
        return "concede", {}
    if action in {"land", "play"} and len(tokens) == 2:
        definition = catalog.definition_for_instance(tokens[1])
        if definition.card_type != "Land":
            raise ValueError(f"{definition.name} is not a land card.")
        return "land", {"card_id": tokens[1]}

    if action in {"activate", "ability"}:
        if len(tokens) < 4:
            raise ValueError(
                "Use activate SOURCE_ID ABILITY_INDEX TARGET [--mana ...]."
            )
        source_id = tokens[1]
        definition = catalog.definition_for_instance(source_id)
        try:
            ability_index = int(tokens[2])
        except ValueError as exc:
            raise ValueError("ABILITY_INDEX must be a non-negative integer.") from exc
        ability = activated_ability(definition.base_id, ability_index)
        if ability is None:
            raise ValueError(
                f"{definition.name} ability {ability_index!r} is not supported."
            )
        targets, mana = _parse_mana_override(
            tokens[3:], dict(ability.mana_cost)
        )
        return "activate", {
            "source_id": source_id,
            "ability_index": ability_index,
            "targets": targets,
            "cost_payment": {"tap": ability.tap_cost, "mana": mana},
        }

    if action != "cast" or len(tokens) < 2:
        raise ValueError(
            "Use pass, concede, land CARD_ID, cast CARD_ID, or activate SOURCE_ID."
        )

    card_id = tokens[1]
    catalog.definition_for_instance(card_id)
    targets, payment = _parse_mana_override(
        tokens[2:], _default_mana_payment(card_id, catalog)
    )
    return "cast", {
        "card_id": card_id,
        "targets": targets,
        "mana_payment": payment,
    }


def _land_action_error(
    state: dict[str, Any] | None, player_id: str
) -> str | None:
    if state is None:
        return None
    active_player = state.get("active_player")
    if active_player != player_id:
        return (
            f"It is {active_player}'s turn. Only the active player can play a land. "
            "You may pass or cast an Instant if you have enough untapped mana."
        )
    phase = state.get("phase")
    if phase not in {"PRECOMBAT_MAIN", "POSTCOMBAT_MAIN"}:
        return (
            "Lands can only be played during your Precombat or Postcombat Main Phase. "
            "You may pass or cast an Instant if you have enough untapped mana."
        )
    if state.get("land_played_this_turn"):
        return "You have already played a land this turn."
    return None


def _priority_role_message(state: dict[str, Any], player_id: str) -> str:
    active_player = state.get("active_player")
    phase = state.get("phase", "UNKNOWN")
    if active_player == player_id:
        return f"Turn owner: {player_id} | Phase: {phase} | This is your turn."
    return (
        f"Turn owner: {active_player} | Phase: {phase} | You have response priority; "
        "this is not the start of your turn."
    )


def _render_action_submission(
    action: str, fields: dict[str, Any], *, automatic: bool = False
) -> None:
    if action == "land":
        description = f"PLAY LAND {fields['card_id']}"
    elif action == "cast":
        targets = list(fields.get("targets", ()))
        description = (
            f"CAST {fields['card_id']} -> {targets} "
            f"using {fields['mana_payment']}"
        )
    elif action == "activate":
        description = (
            f"ACTIVATE {fields['source_id']} ability {fields['ability_index']} "
            f"-> {list(fields.get('targets', ()))} using "
            f"{fields['cost_payment']}"
        )
    elif action == "attack":
        description = f"DECLARE ATTACKERS {fields.get('attackers', [])}"
    elif action == "block":
        description = f"DECLARE BLOCKERS {fields.get('blockers', [])}"
    elif action == "order":
        description = f"ASSIGN DAMAGE ORDER {fields.get('orders', [])}"
    elif action == "concede":
        description = "CONCEDE GAME"
    else:
        description = "PASS"
    prefix = "AUTO ACTION" if automatic else "ACTION SUBMITTED"
    print()
    print(f">>> {prefix}: {description}")
    print("-" * DISPLAY_WIDTH)
    print()


def _choose_priority_action(
    state: dict[str, Any] | None,
    player_id: str,
    catalog: CardCatalog,
) -> tuple[str, dict[str, Any]]:
    if state is not None:
        hand = state.get("hand", {}).get(player_id, [])
        print(_priority_role_message(state, player_id))
        own_mana = state.get("available_mana", {}).get(player_id)
        if own_mana is not None:
            print(
                "Your available mana (untapped basic lands): "
                f"{_format_available_mana(own_mana)}"
            )
        print(f"Your current hand: {hand}")
        print(f"Stack (bottom -> top): {state.get('stack', [])}")
        land_error = _land_action_error(state, player_id)
        if land_error is not None:
            print(f"Land play unavailable: {land_error}")
        ability_lines: list[str] = []
        for permanent in state.get("battlefield", {}).get(player_id, []):
            card_id = permanent.get("id")
            if not isinstance(card_id, str):
                continue
            definition = catalog.definition_for_instance(card_id)
            for index, ability in enumerate(
                ACTIVATED_ABILITIES.get(definition.base_id, ())
            ):
                ability_lines.append(
                    f"  {card_id} ability {index}: {ability.summary}"
                )
        if ability_lines:
            print("Your supported activated abilities:")
            print("\n".join(ability_lines))
    print(
        "Actions: pass | concede | land CARD_ID | cast CARD_ID [TARGET ...] | "
        "activate SOURCE_ID ABILITY_INDEX TARGET"
    )
    print("Mana is inferred from the catalog; add --mana R=1,X=1 to override.")
    while True:
        command = _read_input("Action: ")
        try:
            action, fields = _parse_priority_action(command, catalog)
        except (ValueError, CatalogError) as exc:
            print(f"\nInvalid action: {exc}\n")
            continue
        if action == "land":
            land_error = _land_action_error(state, player_id)
            if land_error is not None:
                print(f"\nLand play unavailable: {land_error}\n")
                continue
        if state is not None and action in {"land", "cast"}:
            hand = state.get("hand", {}).get(player_id, [])
            if fields["card_id"] not in hand:
                print(f"\n{fields['card_id']} is not in your current hand.\n")
                continue
        if state is not None and action == "activate":
            controlled = {
                permanent.get("id")
                for permanent in state.get("battlefield", {}).get(player_id, [])
            }
            if fields["source_id"] not in controlled:
                print(
                    f"\n{fields['source_id']} is not on your battlefield.\n"
                )
                continue
        return action, fields


def _opposing_player_id(state: dict[str, Any], player_id: str) -> str:
    return next(
        candidate for candidate in state.get("life_totals", {}) if candidate != player_id
    )


def _eligible_combat_creatures(
    state: dict[str, Any],
    player_id: str,
    catalog: CardCatalog,
    *,
    attacking: bool,
) -> tuple[str, ...]:
    eligible: list[str] = []
    for permanent in state.get("battlefield", {}).get(player_id, []):
        card_id = permanent.get("id")
        if not isinstance(card_id, str) or "power" not in permanent:
            continue
        if permanent.get("tapped"):
            continue
        if attacking and permanent.get("summoning_sick"):
            continue
        if attacking and "defender" in catalog.definition_for_instance(
            card_id
        ).simplified_effect.casefold():
            continue
        eligible.append(card_id)
    return tuple(eligible)


def _parse_attacker_declaration(
    command: str, eligible: Sequence[str], target_player: str
) -> list[dict[str, str]]:
    selected = tuple(shlex.split(command))
    if len(selected) == 1 and selected[0].lower() in {"none", "pass", "no"}:
        return []
    if len(set(selected)) != len(selected) or any(
        card_id not in eligible for card_id in selected
    ):
        raise ValueError(f"Choose distinct eligible attackers from {list(eligible)}.")
    return [
        {"creature_id": card_id, "target": target_player}
        for card_id in selected
    ]


def _parse_blocker_declaration(
    command: str,
    eligible: Sequence[str],
    attacker_ids: Sequence[str],
) -> list[dict[str, str]]:
    entries = tuple(shlex.split(command))
    if len(entries) == 1 and entries[0].lower() in {"none", "pass", "no"}:
        return []
    assignments: list[dict[str, str]] = []
    used_blockers: set[str] = set()
    for entry in entries:
        if "=" not in entry:
            raise ValueError("Use BLOCKER_ID=ATTACKER_ID pairs, or 'none'.")
        blocker_id, attacker_id = entry.split("=", 1)
        if (
            blocker_id not in eligible
            or attacker_id not in attacker_ids
            or blocker_id in used_blockers
        ):
            raise ValueError(
                f"Blockers must be distinct members of {list(eligible)} and target "
                f"attackers from {list(attacker_ids)}."
            )
        used_blockers.add(blocker_id)
        assignments.append(
            {"creature_id": blocker_id, "blocking_id": attacker_id}
        )
    return assignments


def _parse_damage_order(command: str, required: Sequence[str]) -> tuple[str, ...]:
    selected = tuple(shlex.split(command))
    if len(set(selected)) != len(selected) or set(selected) != set(required):
        raise ValueError(f"List every blocker exactly once: {list(required)}.")
    return selected


def _choose_attackers(
    state: dict[str, Any],
    player_id: str,
    catalog: CardCatalog,
    *,
    automatic: bool,
) -> list[dict[str, str]]:
    eligible = _eligible_combat_creatures(
        state, player_id, catalog, attacking=True
    )
    if automatic or not eligible:
        return []
    target = _opposing_player_id(state, player_id)
    print(f"Eligible attackers: {list(eligible)}")
    while True:
        command = _read_input("Attacker IDs separated by spaces, or 'none': ")
        try:
            return _parse_attacker_declaration(command, eligible, target)
        except ValueError as exc:
            print(f"\nInvalid attacker declaration: {exc}\n")


def _choose_blockers(
    state: dict[str, Any],
    player_id: str,
    catalog: CardCatalog,
    *,
    automatic: bool,
) -> list[dict[str, str]]:
    eligible = _eligible_combat_creatures(
        state, player_id, catalog, attacking=False
    )
    attacker_ids = tuple(
        entry["creature_id"] for entry in state.get("combat", {}).get("attackers", [])
    )
    if automatic or not eligible or not attacker_ids:
        return []
    print(f"Attackers: {list(attacker_ids)}")
    print(f"Eligible blockers: {list(eligible)}")
    while True:
        command = _read_input(
            "Blocks as BLOCKER_ID=ATTACKER_ID pairs, or 'none': "
        )
        try:
            return _parse_blocker_declaration(command, eligible, attacker_ids)
        except ValueError as exc:
            print(f"\nInvalid blocker declaration: {exc}\n")


def _choose_damage_orders(
    state: dict[str, Any], *, automatic: bool
) -> list[tuple[str, tuple[str, ...]]]:
    blockers_by_attacker: dict[str, list[str]] = {}
    for assignment in state.get("combat", {}).get("blockers", []):
        blockers_by_attacker.setdefault(assignment["blocking_id"], []).append(
            assignment["creature_id"]
        )
    orders: list[tuple[str, tuple[str, ...]]] = []
    for attacker_id, blockers in blockers_by_attacker.items():
        if len(blockers) <= 1:
            continue
        if automatic:
            order = tuple(blockers)
        else:
            print(f"Choose damage order for {attacker_id}: {blockers}")
            while True:
                command = _read_input("Blocker IDs from first damaged to last: ")
                try:
                    order = _parse_damage_order(command, blockers)
                    break
                except ValueError as exc:
                    print(f"\nInvalid damage order: {exc}\n")
        orders.append((attacker_id, order))
    return orders


def _choose_trigger_order(
    pdu: dict[str, Any], *, automatic: bool
) -> tuple[str, ...]:
    trigger_ids = tuple(pdu["trigger_ids"])
    if automatic:
        return trigger_ids
    print("Enter every trigger ID from Stack bottom to Stack top.")
    while True:
        selected = tuple(shlex.split(_read_input("Trigger order: ")))
        if len(set(selected)) == len(selected) and set(selected) == set(trigger_ids):
            return selected
        print(f"\nList every trigger exactly once: {list(trigger_ids)}.\n")


def _choose_trigger_choice(
    pdu: dict[str, Any], *, automatic: bool
) -> tuple[bool, str | None]:
    optional = bool(pdu.get("optional", False))
    legal_targets = tuple(pdu["legal_targets"])
    if optional and not automatic:
        while True:
            answer = _read_input(
                "Use this optional triggered ability? [y/n]: "
            ).strip().lower()
            if answer in {"n", "no"}:
                return False, None
            if answer in {"y", "yes"}:
                break
            print("Enter 'y' or 'n'.")
    if pdu["requires_target"]:
        if automatic:
            return True, legal_targets[0]
        while True:
            target = _read_input("Choose trigger target: ").strip()
            if target in legal_targets:
                return True, target
            print(f"\nChoose one legal target from {list(legal_targets)}.\n")
    return True, None


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        catalog = load_catalog(args.data)
        deck_list = load_deck_file(args.deck, catalog)
    except CatalogError as exc:
        print(f"Deck error: {exc}", file=sys.stderr)
        return 2

    client = MTGNPClient(host=args.host, port=args.port, verbose=args.verbose)
    latest_state: dict[str, Any] | None = None
    ready_after_game_over = False
    client.set_heartbeat_resume_renderer(
        lambda: _render_heartbeat_resume(latest_state, args.player_id)
    )
    try:
        client.connect()
        client.start_heartbeat(
            interval_seconds=args.heartbeat_interval,
            timeout_seconds=args.heartbeat_timeout,
        )
        client.send_ready(args.player_id, deck_list)
        print(f"Connected to {args.host}:{args.port} as {args.player_id}.")
        while True:
            pdu = client.receive()
            _render_pdu(pdu)
            if pdu["type"] == MessageType.GAME_OVER.value:
                ready_after_game_over = True
            if (
                pdu["type"] == MessageType.PHASE_TRANSITION.value
                and latest_state is not None
            ):
                latest_state = _state_after_phase_transition(latest_state, pdu)
            if pdu["type"] == MessageType.GAME_STATE_UPDATE.value:
                state = pdu["state"]
                latest_state = state
                if state.get("phase") == "LOBBY" and ready_after_game_over:
                    client.send_ready(args.player_id, deck_list)
                    ready_after_game_over = False
                    print("Re-entered the Lobby and resubmitted the same deck.")
                elif (
                    state.get("phase") == "MULLIGAN"
                    and not state["mulligan_kept"].get(args.player_id, False)
                ):
                    keep, cards_to_bottom = _choose_mulligan(
                        state, args.player_id, auto_keep=args.auto_keep
                    )
                    client.send_mulligan_choice(
                        request_seq_num=pdu["seq_num"],
                        keep=keep,
                        cards_to_bottom=cards_to_bottom,
                    )
                elif (
                    state.get("phase") == "CLEANUP"
                    and state.get("active_player") == args.player_id
                    and state["hand_counts"].get(args.player_id, 0) > 7
                ):
                    selected = _choose_cleanup_discard(
                        state, args.player_id, automatic=args.auto_pass
                    )
                    client.send_discard(
                        request_seq_num=pdu["seq_num"], card_ids=selected
                    )
            elif pdu["type"] == MessageType.TRIGGER_CHOICE.value:
                accept, chosen_target = _choose_trigger_choice(
                    pdu, automatic=args.auto_pass
                )
                client.send_trigger_choice_response(
                    request_seq_num=pdu["seq_num"],
                    trigger_id=pdu["trigger_id"],
                    accept=accept,
                    chosen_target=chosen_target,
                )
                _render_action_submission(
                    "trigger choice",
                    {
                        "trigger_id": pdu["trigger_id"],
                        "accept": accept,
                        "chosen_target": chosen_target,
                    },
                    automatic=args.auto_pass,
                )
            elif pdu["type"] == MessageType.TRIGGER_ORDER.value:
                trigger_order = _choose_trigger_order(
                    pdu, automatic=args.auto_pass
                )
                client.send_trigger_order_response(
                    request_seq_num=pdu["seq_num"],
                    ordered_trigger_ids=trigger_order,
                )
                _render_action_submission(
                    "trigger order",
                    {"ordered_trigger_ids": trigger_order},
                    automatic=args.auto_pass,
                )
            elif pdu["type"] == MessageType.PRIORITY_GRANT.value:
                if args.auto_pass:
                    client.send_priority_pass(request_seq_num=pdu["seq_num"])
                    _render_action_submission("pass", {}, automatic=True)
                else:
                    action, fields = _choose_priority_action(
                        latest_state, args.player_id, catalog
                    )
                    if action == "land":
                        client.send_play_land(
                            request_seq_num=pdu["seq_num"],
                            card_id=fields["card_id"],
                        )
                    elif action == "cast":
                        client.send_cast_spell(
                            request_seq_num=pdu["seq_num"],
                            card_id=fields["card_id"],
                            targets=fields["targets"],
                            mana_payment=fields["mana_payment"],
                        )
                    elif action == "activate":
                        client.send_activate_ability(
                            request_seq_num=pdu["seq_num"],
                            source_id=fields["source_id"],
                            ability_index=fields["ability_index"],
                            targets=fields["targets"],
                            cost_payment=fields["cost_payment"],
                        )
                    elif action == "concede":
                        client.send_concede(
                            request_seq_num=pdu["seq_num"],
                            player_id=args.player_id,
                        )
                    else:
                        client.send_priority_pass(request_seq_num=pdu["seq_num"])
                    _render_action_submission(action, fields)
            elif (
                pdu["type"] == MessageType.PHASE_TRANSITION.value
                and pdu["to_phase"] == "DECLARE_ATTACKERS"
                and pdu["active_player"] == args.player_id
            ):
                attackers = _choose_attackers(
                    latest_state or {},
                    args.player_id,
                    catalog,
                    automatic=args.auto_pass,
                )
                client.send_attackers(
                    request_seq_num=pdu["seq_num"], attackers=attackers
                )
                _render_action_submission(
                    "attack",
                    {"attackers": attackers},
                    automatic=args.auto_pass,
                )
            elif (
                pdu["type"] == MessageType.PHASE_TRANSITION.value
                and pdu["to_phase"] == "DECLARE_BLOCKERS"
                and pdu["active_player"] != args.player_id
            ):
                blockers = _choose_blockers(
                    latest_state or {},
                    args.player_id,
                    catalog,
                    automatic=args.auto_pass,
                )
                client.send_blockers(
                    request_seq_num=pdu["seq_num"], blockers=blockers
                )
                _render_action_submission(
                    "block",
                    {"blockers": blockers},
                    automatic=args.auto_pass,
                )
            elif (
                pdu["type"] == MessageType.PHASE_TRANSITION.value
                and pdu["to_phase"] == "ASSIGN_DAMAGE_ORDER"
                and pdu["active_player"] == args.player_id
            ):
                orders = _choose_damage_orders(
                    latest_state or {}, automatic=args.auto_pass
                )
                for attacker_id, blocker_order in orders:
                    client.send_damage_order(
                        request_seq_num=pdu["seq_num"],
                        attacker_id=attacker_id,
                        blocker_order=blocker_order,
                    )
                _render_action_submission(
                    "order",
                    {"orders": orders},
                    automatic=args.auto_pass,
                )
    except KeyboardInterrupt:
        print("Disconnecting client.")
    except (
        ConnectionClosed,
        ConnectionRefusedError,
        ConnectionResetError,
        OSError,
    ) as exc:
        if client.heartbeat_failed:
            print("Connection ended: the server did not answer the heartbeat.", file=sys.stderr)
        else:
            print(f"Connection ended: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
