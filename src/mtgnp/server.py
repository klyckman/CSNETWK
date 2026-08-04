"""Runnable two-client TCP lobby server for MTGNP v1.0."""

from __future__ import annotations

import argparse
import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .catalog import load_catalog
from .framing import ConnectionClosed, FrameTooLarge, FramedConnection
from .game import (
    DiscardOutcome,
    DrawOutcome,
    GameRuleError,
    GameSession,
    MulliganOutcome,
    PriorityPassOutcome,
)
from .lobby import Lobby, LobbyError, LobbyFull
from .protocol import (
    ErrorCode,
    LifecycleState,
    MessageType,
    PDUValidationError,
    Sender,
    TurnStep,
)
from .tracing import PDUTracer


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 4444
DEFAULT_DATA_DIRECTORY = Path(__file__).resolve().parents[2] / "data"
DEFAULT_PRIORITY_TIME_LIMIT_MS = 60_000
DEFAULT_RECONNECT_GRACE_SECONDS = 30.0


@dataclass(slots=True)
class ClientSession:
    seat_id: str
    address: tuple[str, int]
    connection: FramedConnection


@dataclass(slots=True)
class ReconnectReservation:
    seat_id: str
    player_id: str
    deck_list: tuple[str, ...]
    game: GameSession
    timer: threading.Timer


class MTGNPServer:
    """Accept two clients and coordinate the MTGNP LOBBY state."""

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        data_directory: str | Path = DEFAULT_DATA_DIRECTORY,
        verbose: bool = False,
        priority_time_limit_ms: int = DEFAULT_PRIORITY_TIME_LIMIT_MS,
        reconnect_grace_seconds: float = DEFAULT_RECONNECT_GRACE_SECONDS,
    ) -> None:
        if priority_time_limit_ms <= 0:
            raise ValueError("priority_time_limit_ms must be positive.")
        if reconnect_grace_seconds <= 0:
            raise ValueError("reconnect_grace_seconds must be positive.")
        self.host = host
        self.port = port
        self.priority_time_limit_ms = priority_time_limit_ms
        self.reconnect_grace_seconds = reconnect_grace_seconds
        self.catalog = load_catalog(data_directory)
        self.lobby = Lobby(self.catalog)
        self.tracer = PDUTracer(enabled=verbose)
        self._sessions: dict[str, ClientSession] = {}
        self._sessions_lock = threading.RLock()
        self._sequence_lock = threading.Lock()
        self._game_lock = threading.RLock()
        self._game: GameSession | None = None
        self._trigger_resume: tuple[GameSession, str, bool] | None = None
        self._priority_timer_lock = threading.RLock()
        self._priority_timer: threading.Timer | None = None
        self._priority_deadline: tuple[GameSession, str, int] | None = None
        self._reconnect_lock = threading.RLock()
        self._reconnect_reservations: dict[str, ReconnectReservation] = {}
        self._next_sequence = 1
        self._listener: socket.socket | None = None
        self._stop = threading.Event()
        self._listening = threading.Event()
        self.bound_port: int | None = None

    @property
    def active_connections(self) -> int:
        with self._sessions_lock:
            return len(self._sessions)

    def wait_until_listening(self, timeout: float = 5.0) -> bool:
        return self._listening.wait(timeout) and self.bound_port is not None

    def set_verbose(self, enabled: bool) -> None:
        self.tracer.set_enabled(enabled)

    @property
    def game(self) -> GameSession | None:
        with self._game_lock:
            return self._game

    def _server_sequence(self) -> int:
        with self._sequence_lock:
            value = self._next_sequence
            self._next_sequence += 1
            return value

    def serve_forever(self) -> None:
        """Bind, listen, and dispatch one reader thread per accepted client."""

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((self.host, self.port))
            listener.listen(5)
            listener.settimeout(0.25)
            self._listener = listener
            self.bound_port = int(listener.getsockname()[1])
            self._listening.set()
            print(f"MTGNP server listening on {self.host}:{self.bound_port}")

            while not self._stop.is_set():
                try:
                    client_socket, raw_address = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    raise

                address = (str(raw_address[0]), int(raw_address[1]))
                session = self._accept_session(client_socket, address)
                if session is None:
                    print(f"Refused additional client {address[0]}:{address[1]}")
                    client_socket.close()
                    continue

                print(
                    f"Accepted {session.seat_id} from "
                    f"{address[0]}:{address[1]}"
                )
                thread = threading.Thread(
                    target=self._handle_session,
                    args=(session,),
                    name=f"mtgnp-{session.seat_id}",
                    daemon=True,
                )
                thread.start()
        finally:
            self._listening.set()
            self.stop()

    def _accept_session(
        self, client_socket: socket.socket, address: tuple[str, int]
    ) -> ClientSession | None:
        with self._sessions_lock:
            if len(self._sessions) >= 2:
                return None
            try:
                seat_id = self.lobby.connect()
            except LobbyFull:
                return None
            connection = FramedConnection(
                client_socket,
                local_sender=Sender.SERVER,
                tracer=self.tracer,
                peer_label=f"{seat_id}@{address[0]}:{address[1]}",
            )
            session = ClientSession(seat_id, address, connection)
            self._sessions[seat_id] = session
            return session

    def _handle_session(self, session: ClientSession) -> None:
        try:
            while not self._stop.is_set():
                try:
                    pdu = session.connection.receive()
                except PDUValidationError as exc:
                    self._send_error(session, exc.code, str(exc), {})
                    continue
                self._dispatch(session, pdu)
        except (ConnectionClosed, ConnectionResetError, BrokenPipeError):
            pass
        except FrameTooLarge as exc:
            print(f"Closing {session.seat_id}: {exc}")
        except OSError as exc:
            if not self._stop.is_set() and not session.connection.closed:
                print(f"Connection error for {session.seat_id}: {exc}")
        finally:
            self._drop_session(session)

    def _dispatch(self, session: ClientSession, pdu: Mapping[str, Any]) -> None:
        message_type = MessageType(pdu["type"])
        if message_type == MessageType.PING:
            session.connection.send(
                {
                    "type": MessageType.PONG.value,
                    "seq_num": pdu["seq_num"],
                    "timestamp": pdu["timestamp"],
                }
            )
            return
        game = self.game
        reservation = self._reconnect_reservation(session.seat_id)
        if reservation is not None:
            if message_type == MessageType.PLAYER_READY:
                self._handle_reconnect_ready(session, pdu, reservation)
            else:
                self._send_error(
                    session,
                    ErrorCode.ILLEGAL_ACTION,
                    "Reconnect with PLAYER_READY before sending game actions.",
                    pdu,
                )
            return
        if game is not None and self._game_has_reconnect_reservation(game):
            if message_type == MessageType.CONCEDE:
                self._handle_concede(session, pdu, game)
            else:
                self._send_error(
                    session,
                    ErrorCode.ILLEGAL_ACTION,
                    "The game is paused while the opponent reconnects.",
                    pdu,
                )
            return
        if (
            game is not None
            and game.lifecycle_state in {LifecycleState.MULLIGAN, LifecycleState.IN_GAME}
            and message_type == MessageType.CONCEDE
        ):
            self._handle_concede(session, pdu, game)
            return
        if game is None and message_type == MessageType.PLAYER_READY:
            self._handle_player_ready(session, pdu)
            return
        if (
            game is not None
            and game.lifecycle_state == LifecycleState.MULLIGAN
            and message_type == MessageType.MULLIGAN_CHOICE
        ):
            self._handle_mulligan_choice(session, pdu, game)
            return
        if game is not None and game.lifecycle_state == LifecycleState.IN_GAME:
            if message_type == MessageType.TRIGGER_ORDER_RESPONSE:
                self._handle_trigger_order_response(session, pdu, game)
                return
            if message_type == MessageType.TRIGGER_CHOICE_RESPONSE:
                self._handle_trigger_choice_response(session, pdu, game)
                return
            if message_type == MessageType.PRIORITY_PASS:
                self._handle_priority_pass(session, pdu, game)
                return
            if message_type == MessageType.PLAY_LAND:
                self._handle_play_land(session, pdu, game)
                return
            if message_type == MessageType.CAST_SPELL:
                self._handle_cast_spell(session, pdu, game)
                return
            if message_type == MessageType.ACTIVATE_ABILITY:
                self._handle_activate_ability(session, pdu, game)
                return
            if message_type == MessageType.DECLARE_ATTACKERS:
                self._handle_declare_attackers(session, pdu, game)
                return
            if message_type == MessageType.DECLARE_BLOCKERS:
                self._handle_declare_blockers(session, pdu, game)
                return
            if message_type == MessageType.ASSIGN_DAMAGE_ORDER:
                self._handle_damage_order(session, pdu, game)
                return
            if message_type == MessageType.DISCARD:
                self._handle_discard(session, pdu, game)
                return
        current_phase = (
            "LOBBY"
            if game is None
            else game.current_step.value
            if game.current_step is not None
            else game.lifecycle_state.value
        )
        self._send_error(
            session,
            ErrorCode.WRONG_PHASE,
            f"{message_type.value} is not accepted while the server is in {current_phase}.",
            pdu,
        )

    def _handle_player_ready(
        self, session: ClientSession, pdu: Mapping[str, Any]
    ) -> None:
        try:
            self.lobby.submit_ready(
                session.seat_id,
                pdu["player_id"],
                pdu["deck_list"],
            )
        except LobbyError as exc:
            self._send_error(session, exc.code, str(exc), pdu)
            return
        self._broadcast_lobby_state()
        self._start_game_if_ready()

    def _handle_reconnect_ready(
        self,
        session: ClientSession,
        pdu: Mapping[str, Any],
        reservation: ReconnectReservation,
    ) -> None:
        """Authenticate a reserved seat using the RFC's existing ready PDU."""

        if pdu["player_id"] != reservation.player_id:
            self._send_error(
                session,
                ErrorCode.DUPLICATE_ID,
                f"This seat is reserved for player_id {reservation.player_id!r}.",
                pdu,
            )
            session.connection.close()
            return
        if tuple(pdu["deck_list"]) != reservation.deck_list:
            self._send_error(
                session,
                ErrorCode.ILLEGAL_DECK,
                "Reconnect must use the same ordered deck list as the interrupted game.",
                pdu,
            )
            session.connection.close()
            return

        with self._reconnect_lock:
            current = self._reconnect_reservations.get(session.seat_id)
            if current is not reservation or self.game is not reservation.game:
                self._send_error(
                    session,
                    ErrorCode.WRONG_PHASE,
                    "The reconnect grace period has already ended.",
                    pdu,
                )
                session.connection.close()
                return
            reservation.timer.cancel()
            del self._reconnect_reservations[session.seat_id]

        print(f"Reconnected {session.seat_id} as {reservation.player_id}")
        if not self._game_has_reconnect_reservation(reservation.game):
            self._resume_game_after_reconnect(reservation.game)

    def _start_game_if_ready(self) -> None:
        with self._game_lock:
            if self._game is not None:
                return
            ready_players = self.lobby.ready_submissions()
            if len(ready_players) != 2:
                return
            self._game = GameSession(ready_players, catalog=self.catalog)
            game = self._game
        self._send_personalized_game_states(game, record_mulligan_tokens=True)

    def _handle_mulligan_choice(
        self,
        session: ClientSession,
        pdu: Mapping[str, Any],
        game: GameSession,
    ) -> None:
        try:
            outcome = game.process_mulligan(
                session.seat_id,
                seq_num=pdu["seq_num"],
                keep=pdu["keep"],
                cards_to_bottom=pdu["cards_to_bottom"],
            )
        except GameRuleError as exc:
            self._send_error(session, exc.code, str(exc), pdu)
            return

        if outcome == MulliganOutcome.REDRAW:
            self._send_personalized_game_state(
                session, game, record_mulligan_token=True
            )
        elif outcome == MulliganOutcome.ALL_PLAYERS_KEPT:
            self._begin_first_turn(game)

    def _send_personalized_game_states(
        self,
        game: GameSession,
        *,
        record_mulligan_tokens: bool,
    ) -> None:
        with self._sessions_lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            self._send_personalized_game_state(
                session,
                game,
                record_mulligan_token=record_mulligan_tokens,
            )

    def _send_personalized_game_state(
        self,
        session: ClientSession,
        game: GameSession,
        *,
        record_mulligan_token: bool,
        record_discard_token: bool = False,
    ) -> None:
        seq_num = self._server_sequence()
        if record_mulligan_token:
            game.record_mulligan_request(session.seat_id, seq_num)
        if record_discard_token:
            game.record_discard_request(session.seat_id, seq_num)
        try:
            session.connection.send(
                {
                    "type": MessageType.GAME_STATE_UPDATE.value,
                    "seq_num": seq_num,
                    "state": game.visible_state(session.seat_id),
                }
            )
        except (OSError, ConnectionClosed):
            session.connection.close()

    def _resume_game_after_reconnect(self, game: GameSession) -> None:
        """Refresh visible state and reissue whichever request was interrupted."""

        if self.game is not game:
            return
        if game.lifecycle_state == LifecycleState.MULLIGAN:
            self._send_personalized_game_states(
                game, record_mulligan_tokens=True
            )
            return
        if game.lifecycle_state != LifecycleState.IN_GAME:
            return

        self._send_personalized_game_states(
            game, record_mulligan_tokens=False
        )
        if game.has_pending_triggers():
            for seat_id in game.players:
                game.reset_trigger_request_for_seat(seat_id)
            self._process_pending_triggers(game)
            return
        if game.priority_holder_seat_id is not None:
            self._send_priority_grant(game, game.priority_holder_seat_id)
            return
        if game.current_step in {
            TurnStep.DECLARE_ATTACKERS,
            TurnStep.DECLARE_BLOCKERS,
            TurnStep.ASSIGN_DAMAGE_ORDER,
        }:
            self._reissue_combat_request(game, game.current_step)
            return
        if game.current_step == TurnStep.CLEANUP:
            self._enter_cleanup(game)

    def _begin_first_turn(self, game: GameSession) -> None:
        self._run_untap_and_open_upkeep(game, from_phase="MULLIGAN")

    def _run_untap_and_open_upkeep(
        self, game: GameSession, *, from_phase: str
    ) -> None:
        self._broadcast_phase_transition(
            game, from_phase=from_phase, to_phase="UNTAP"
        )
        game.perform_untap()
        self._send_personalized_game_states(
            game, record_mulligan_tokens=False
        )
        game.enter_upkeep()
        self._broadcast_phase_transition(
            game, from_phase="UNTAP", to_phase="UPKEEP"
        )
        self._open_priority_window(game)

    def _open_priority_window(self, game: GameSession) -> None:
        holder_seat = game.open_priority_window()
        self._send_priority_grant(game, holder_seat)

    def _send_priority_grant(self, game: GameSession, seat_id: str) -> None:
        session = self._session_for_seat(seat_id)
        if session is None:
            return
        seq_num = self._server_sequence()
        game.record_priority_grant(seat_id, seq_num)
        try:
            session.connection.send(
                {
                    "type": MessageType.PRIORITY_GRANT.value,
                    "seq_num": seq_num,
                    "player_id": game.player_id_for_seat(seat_id),
                    "time_limit_ms": self.priority_time_limit_ms,
                }
            )
            self._arm_priority_deadline(game, seat_id, seq_num)
        except (OSError, ConnectionClosed):
            session.connection.close()

    def _arm_priority_deadline(
        self, game: GameSession, seat_id: str, seq_num: int
    ) -> None:
        with self._priority_timer_lock:
            if self._priority_timer is not None:
                self._priority_timer.cancel()
            self._priority_deadline = (game, seat_id, seq_num)
            timer = threading.Timer(
                self.priority_time_limit_ms / 1000,
                self._priority_deadline_expired,
                args=(game, seat_id, seq_num),
            )
            timer.daemon = True
            self._priority_timer = timer
            timer.start()

    def _consume_priority_deadline(
        self, game: GameSession, seat_id: str, seq_num: int
    ) -> bool:
        with self._priority_timer_lock:
            if self._priority_deadline != (game, seat_id, seq_num):
                return False
            if self._priority_timer is not None:
                self._priority_timer.cancel()
            self._priority_timer = None
            self._priority_deadline = None
            return True

    def _cancel_priority_deadline(self) -> None:
        with self._priority_timer_lock:
            if self._priority_timer is not None:
                self._priority_timer.cancel()
            self._priority_timer = None
            self._priority_deadline = None

    def _priority_deadline_expired(
        self, game: GameSession, seat_id: str, seq_num: int
    ) -> None:
        with self._priority_timer_lock:
            if self._priority_deadline != (game, seat_id, seq_num):
                return
            self._priority_timer = None
            self._priority_deadline = None
            if (
                self.game is not game
                or game.lifecycle_state != LifecycleState.IN_GAME
                or game.priority_holder_seat_id != seat_id
            ):
                return
            game.declare_game_over(seat_id, "DISCONNECT")
            timed_out_session = self._session_for_seat(seat_id)
            self._finish_game(game)
            if timed_out_session is not None:
                timed_out_session.connection.close()

    def _handle_priority_pass(
        self,
        session: ClientSession,
        pdu: Mapping[str, Any],
        game: GameSession,
    ) -> None:
        self._consume_priority_deadline(
            game, session.seat_id, pdu["seq_num"]
        )
        try:
            outcome = game.process_priority_pass(
                session.seat_id, seq_num=pdu["seq_num"]
            )
        except GameRuleError as exc:
            self._send_error(session, exc.code, str(exc), pdu)
            if game.priority_holder_seat_id == session.seat_id:
                self._send_priority_grant(game, session.seat_id)
            return

        if outcome == PriorityPassOutcome.GRANT_OPPONENT:
            self._send_priority_grant(game, game.priority_holder_seat_id)
        elif outcome == PriorityPassOutcome.RESOLVE_STACK:
            self._resolve_top_stack_item(game)
        else:
            self._advance_after_priority_window(game)

    def _handle_play_land(
        self,
        session: ClientSession,
        pdu: Mapping[str, Any],
        game: GameSession,
    ) -> None:
        self._consume_priority_deadline(
            game, session.seat_id, pdu["seq_num"]
        )
        try:
            game.process_play_land(
                session.seat_id,
                seq_num=pdu["seq_num"],
                card_id=pdu["card_id"],
            )
            game.apply_state_based_actions()
        except GameRuleError as exc:
            self._send_error(session, exc.code, str(exc), pdu)
            if game.priority_holder_seat_id == session.seat_id:
                self._send_priority_grant(game, session.seat_id)
            return

        self._send_personalized_game_states(
            game, record_mulligan_tokens=False
        )
        if game.lifecycle_state == LifecycleState.GAME_OVER:
            self._finish_game(game)
            return
        self._send_priority_grant(game, session.seat_id)

    def _handle_cast_spell(
        self,
        session: ClientSession,
        pdu: Mapping[str, Any],
        game: GameSession,
    ) -> None:
        self._consume_priority_deadline(
            game, session.seat_id, pdu["seq_num"]
        )
        try:
            item = game.process_cast_spell(
                session.seat_id,
                seq_num=pdu["seq_num"],
                card_id=pdu["card_id"],
                targets=pdu["targets"],
                mana_payment=pdu["mana_payment"],
            )
        except GameRuleError as exc:
            self._send_error(session, exc.code, str(exc), pdu)
            if game.priority_holder_seat_id == session.seat_id:
                self._send_priority_grant(game, session.seat_id)
            return

        stack_item = game.visible_stack_item(item)
        self._broadcast_server_pdu(
            MessageType.STACK_PUSH,
            {
                "stack_item_id": stack_item["stack_item_id"],
                "item_type": stack_item["item_type"],
                "source": stack_item["source"],
                "targets": stack_item["targets"],
                "controller": stack_item["controller"],
            },
        )
        self._send_personalized_game_states(
            game, record_mulligan_tokens=False
        )
        self._continue_after_triggerable_event(
            game, resume_seat_id=session.seat_id, open_priority_window=False
        )

    def _handle_activate_ability(
        self,
        session: ClientSession,
        pdu: Mapping[str, Any],
        game: GameSession,
    ) -> None:
        self._consume_priority_deadline(
            game, session.seat_id, pdu["seq_num"]
        )
        try:
            item = game.process_activate_ability(
                session.seat_id,
                seq_num=pdu["seq_num"],
                source_id=pdu["source_id"],
                ability_index=pdu["ability_index"],
                targets=pdu["targets"],
                cost_payment=pdu["cost_payment"],
            )
        except GameRuleError as exc:
            self._send_error(session, exc.code, str(exc), pdu)
            if game.priority_holder_seat_id == session.seat_id:
                self._send_priority_grant(game, session.seat_id)
            return

        stack_item = game.visible_stack_item(item)
        self._broadcast_server_pdu(
            MessageType.STACK_PUSH,
            {
                "stack_item_id": stack_item["stack_item_id"],
                "item_type": stack_item["item_type"],
                "source": stack_item["source"],
                "targets": stack_item["targets"],
                "controller": stack_item["controller"],
            },
        )
        self._send_personalized_game_states(
            game, record_mulligan_tokens=False
        )
        self._continue_after_triggerable_event(
            game, resume_seat_id=session.seat_id, open_priority_window=False
        )

    def _handle_concede(
        self,
        session: ClientSession,
        pdu: Mapping[str, Any],
        game: GameSession,
    ) -> None:
        expected_player_id = game.player_id_for_seat(session.seat_id)
        if pdu["player_id"] != expected_player_id:
            self._send_error(
                session,
                ErrorCode.ILLEGAL_ACTION,
                "CONCEDE player_id must identify the sending player.",
                pdu,
            )
            return
        self._cancel_priority_deadline()
        game.declare_game_over(session.seat_id, "CONCEDE")
        self._finish_game(game)

    def _resolve_top_stack_item(self, game: GameSession) -> None:
        resolution = game.resolve_top_stack_item()
        self._broadcast_server_pdu(
            MessageType.STACK_RESOLVE,
            {
                "stack_item_id": resolution.item.stack_item_id,
                "result": resolution.outcome.value,
                "state_changes": list(resolution.state_changes),
            },
        )
        self._send_personalized_game_states(
            game, record_mulligan_tokens=False
        )
        if game.lifecycle_state == LifecycleState.GAME_OVER:
            self._finish_game(game)
            return
        self._continue_after_triggerable_event(
            game,
            resume_seat_id=game.active_seat_id,
            open_priority_window=True,
        )

    def _continue_after_triggerable_event(
        self,
        game: GameSession,
        *,
        resume_seat_id: str,
        open_priority_window: bool,
    ) -> None:
        if not game.has_pending_triggers():
            if open_priority_window:
                self._open_priority_window(game)
            else:
                self._send_priority_grant(game, resume_seat_id)
            return
        with self._game_lock:
            if self._trigger_resume is None:
                self._trigger_resume = (
                    game,
                    resume_seat_id,
                    open_priority_window,
                )
        self._process_pending_triggers(game)

    def _process_pending_triggers(self, game: GameSession) -> None:
        if not game.trigger_choices_complete():
            trigger = game.next_trigger_choice()
            if trigger is None:
                return
            session = self._session_for_seat(trigger.controller_seat_id)
            if session is None:
                return
            seq_num = self._server_sequence()
            game.record_trigger_choice_request(
                trigger.controller_seat_id, trigger.trigger_id, seq_num
            )
            try:
                session.connection.send(
                    {
                        "type": MessageType.TRIGGER_CHOICE.value,
                        "seq_num": seq_num,
                        "trigger_id": trigger.trigger_id,
                        "source_id": trigger.source_id,
                        "effect_summary": trigger.spec.summary,
                        "requires_target": trigger.spec.requires_target,
                        "legal_targets": list(trigger.legal_targets),
                        "optional": trigger.spec.optional,
                    }
                )
            except (OSError, ConnectionClosed):
                session.connection.close()
            return

        if not game.trigger_orders_complete():
            request = game.next_trigger_order_request()
            if request is None:
                return
            seat_id, trigger_ids = request
            session = self._session_for_seat(seat_id)
            if session is None:
                return
            seq_num = self._server_sequence()
            game.record_trigger_order_request(seat_id, seq_num)
            try:
                session.connection.send(
                    {
                        "type": MessageType.TRIGGER_ORDER.value,
                        "seq_num": seq_num,
                        "player_id": game.player_id_for_seat(seat_id),
                        "trigger_ids": list(trigger_ids),
                    }
                )
            except (OSError, ConnectionClosed):
                session.connection.close()
            return

        placed = game.place_pending_triggers()
        for item in placed:
            stack_item = game.visible_stack_item(item)
            self._broadcast_server_pdu(
                MessageType.STACK_PUSH,
                {
                    "stack_item_id": stack_item["stack_item_id"],
                    "item_type": stack_item["item_type"],
                    "source": stack_item["source"],
                    "targets": stack_item["targets"],
                    "controller": stack_item["controller"],
                },
            )
        self._send_personalized_game_states(
            game, record_mulligan_tokens=False
        )
        with self._game_lock:
            resume = self._trigger_resume
            self._trigger_resume = None
        if resume is None or resume[0] is not game:
            raise RuntimeError("Trigger processing lost its priority resume state.")
        _, resume_seat_id, open_priority_window = resume
        if open_priority_window:
            self._open_priority_window(game)
        else:
            self._send_priority_grant(game, resume_seat_id)

    def _handle_trigger_choice_response(
        self,
        session: ClientSession,
        pdu: Mapping[str, Any],
        game: GameSession,
    ) -> None:
        try:
            game.process_trigger_choice_response(
                session.seat_id,
                seq_num=pdu["seq_num"],
                trigger_id=pdu["trigger_id"],
                accept=pdu["accept"],
                chosen_target=pdu.get("chosen_target"),
            )
        except GameRuleError as exc:
            self._send_error(session, exc.code, str(exc), pdu)
            game.reset_trigger_request_for_seat(session.seat_id)
        self._process_pending_triggers(game)

    def _handle_trigger_order_response(
        self,
        session: ClientSession,
        pdu: Mapping[str, Any],
        game: GameSession,
    ) -> None:
        try:
            game.process_trigger_order_response(
                session.seat_id,
                seq_num=pdu["seq_num"],
                ordered_trigger_ids=pdu["ordered_trigger_ids"],
            )
        except GameRuleError as exc:
            self._send_error(session, exc.code, str(exc), pdu)
            game.reset_trigger_request_for_seat(session.seat_id)
        self._process_pending_triggers(game)

    def _advance_after_priority_window(self, game: GameSession) -> None:
        previous, next_step = game.advance_after_priority_window()
        cleared_combat = (
            previous == TurnStep.END_OF_COMBAT
            and next_step == TurnStep.POSTCOMBAT_MAIN
        )
        if cleared_combat:
            game.clear_combat_state()
        transition_tokens = self._broadcast_phase_transition(
            game,
            from_phase=previous.value,
            to_phase=next_step.value,
        )

        if next_step.value == "DRAW":
            draw_outcome = game.perform_draw()
            if draw_outcome == DrawOutcome.EMPTY_LIBRARY:
                self._finish_game_for_empty_library(game)
                return
            self._send_personalized_game_states(
                game, record_mulligan_tokens=False
            )
            self._open_priority_window(game)
            return

        if next_step.value == "DECLARE_ATTACKERS":
            token = transition_tokens.get(game.active_seat_id)
            if token is not None:
                game.record_attackers_request(game.active_seat_id, token)
            return

        if next_step.value == "DECLARE_BLOCKERS":
            defending_seat = game.opposing_seat(game.active_seat_id)
            token = transition_tokens.get(defending_seat)
            if token is not None:
                game.record_blockers_request(defending_seat, token)
            return

        if next_step.value == "ASSIGN_DAMAGE_ORDER":
            token = transition_tokens.get(game.active_seat_id)
            if token is not None:
                game.record_damage_order_request(game.active_seat_id, token)
            return

        if next_step.value == "FIRST_STRIKE_DAMAGE":
            self._resolve_combat_damage(game, first_strike=True)
            return

        if next_step.value == "COMBAT_DAMAGE":
            self._resolve_combat_damage(game, first_strike=False)
            return

        if next_step.value == "CLEANUP":
            self._enter_cleanup(game)
            return

        self._open_priority_window(game)

    def _handle_declare_attackers(
        self,
        session: ClientSession,
        pdu: Mapping[str, Any],
        game: GameSession,
    ) -> None:
        try:
            declared_any = game.process_declare_attackers(
                session.seat_id,
                seq_num=pdu["seq_num"],
                attackers=pdu["attackers"],
            )
        except GameRuleError as exc:
            self._send_error(session, exc.code, str(exc), pdu)
            self._reissue_combat_request(game, TurnStep.DECLARE_ATTACKERS)
            return
        if declared_any:
            self._send_personalized_game_states(
                game, record_mulligan_tokens=False
            )
            self._continue_after_triggerable_event(
                game,
                resume_seat_id=game.active_seat_id,
                open_priority_window=True,
            )
            return
        self._broadcast_phase_transition(
            game,
            from_phase="DECLARE_ATTACKERS",
            to_phase="END_OF_COMBAT",
        )
        self._open_priority_window(game)

    def _handle_declare_blockers(
        self,
        session: ClientSession,
        pdu: Mapping[str, Any],
        game: GameSession,
    ) -> None:
        try:
            game.process_declare_blockers(
                session.seat_id,
                seq_num=pdu["seq_num"],
                blockers=pdu["blockers"],
            )
        except GameRuleError as exc:
            self._send_error(session, exc.code, str(exc), pdu)
            self._reissue_combat_request(game, TurnStep.DECLARE_BLOCKERS)
            return
        self._send_personalized_game_states(
            game, record_mulligan_tokens=False
        )
        self._open_priority_window(game)

    def _handle_damage_order(
        self,
        session: ClientSession,
        pdu: Mapping[str, Any],
        game: GameSession,
    ) -> None:
        try:
            complete = game.process_damage_order(
                session.seat_id,
                seq_num=pdu["seq_num"],
                attacker_id=pdu["attacker_id"],
                blocker_order=pdu["blocker_order"],
            )
        except GameRuleError as exc:
            self._send_error(session, exc.code, str(exc), pdu)
            self._reissue_combat_request(game, TurnStep.ASSIGN_DAMAGE_ORDER)
            return
        if not complete:
            return
        self._send_personalized_game_states(
            game, record_mulligan_tokens=False
        )
        self._open_priority_window(game)

    def _resolve_combat_damage(
        self, game: GameSession, *, first_strike: bool
    ) -> None:
        result = game.resolve_combat_damage(first_strike=first_strike)
        self._broadcast_server_pdu(
            MessageType.COMBAT_DAMAGE_RESULT,
            {
                "damage_events": list(result.damage_events),
                "life_totals": result.life_totals,
                "creatures_died": list(result.creatures_died),
            },
        )
        self._send_personalized_game_states(
            game, record_mulligan_tokens=False
        )
        if game.lifecycle_state == LifecycleState.GAME_OVER:
            self._finish_game(game)
            return
        if first_strike:
            self._open_priority_window(game)
            return

        game.enter_end_of_combat()
        self._broadcast_phase_transition(
            game,
            from_phase=TurnStep.COMBAT_DAMAGE.value,
            to_phase=TurnStep.END_OF_COMBAT.value,
        )
        self._open_priority_window(game)

    def _reissue_combat_request(
        self, game: GameSession, step: TurnStep
    ) -> None:
        tokens = self._broadcast_phase_transition(
            game,
            from_phase=step.value,
            to_phase=step.value,
        )
        if step == TurnStep.DECLARE_ATTACKERS:
            actor = game.active_seat_id
            token = tokens.get(actor)
            if token is not None:
                game.record_attackers_request(actor, token)
        elif step == TurnStep.DECLARE_BLOCKERS:
            actor = game.opposing_seat(game.active_seat_id)
            token = tokens.get(actor)
            if token is not None:
                game.record_blockers_request(actor, token)
        else:
            actor = game.active_seat_id
            token = tokens.get(actor)
            if token is not None:
                game.record_damage_order_request(actor, token)

    def _enter_cleanup(self, game: GameSession) -> None:
        if game.cleanup_excess_cards() == 0:
            self._complete_cleanup(game)
            return
        session = self._session_for_seat(game.active_seat_id)
        if session is not None:
            self._send_personalized_game_state(
                session,
                game,
                record_mulligan_token=False,
                record_discard_token=True,
            )

    def _handle_discard(
        self,
        session: ClientSession,
        pdu: Mapping[str, Any],
        game: GameSession,
    ) -> None:
        try:
            outcome = game.process_discard(
                session.seat_id,
                seq_num=pdu["seq_num"],
                card_ids=pdu["card_ids"],
            )
        except GameRuleError as exc:
            self._send_error(session, exc.code, str(exc), pdu)
            if session.seat_id == game.active_seat_id:
                self._send_personalized_game_state(
                    session,
                    game,
                    record_mulligan_token=False,
                    record_discard_token=True,
                )
            return

        if outcome == DiscardOutcome.MORE_REQUIRED:
            self._send_personalized_game_state(
                session,
                game,
                record_mulligan_token=False,
                record_discard_token=True,
            )
        else:
            self._complete_cleanup(game)

    def _complete_cleanup(self, game: GameSession) -> None:
        game.finish_cleanup()
        self._send_personalized_game_states(
            game, record_mulligan_tokens=False
        )
        game.start_next_turn()
        self._run_untap_and_open_upkeep(game, from_phase="CLEANUP")

    def _broadcast_phase_transition(
        self,
        game: GameSession,
        *,
        from_phase: str,
        to_phase: str,
    ) -> dict[str, int]:
        tokens: dict[str, int] = {}
        with self._sessions_lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            seq_num = self._server_sequence()
            try:
                session.connection.send(
                    {
                        "type": MessageType.PHASE_TRANSITION.value,
                        "seq_num": seq_num,
                        "from_phase": from_phase,
                        "to_phase": to_phase,
                        "active_player": game.active_player_id,
                        "turn": game.turn,
                    }
                )
                tokens[session.seat_id] = seq_num
            except (OSError, ConnectionClosed):
                session.connection.close()
        return tokens

    def _finish_game_for_empty_library(self, game: GameSession) -> None:
        loser_seat = game.active_seat_id
        game.declare_game_over(loser_seat, "DECK_EMPTY")
        self._finish_game(game)

    def _finish_game(self, game: GameSession) -> None:
        self._cancel_priority_deadline()
        self._cancel_reconnect_reservations(game)
        if (
            game.winner_seat_id is None
            or game.loser_seat_id is None
            or game.game_over_reason is None
        ):
            raise RuntimeError("GAME_OVER requires winner, loser, and reason state.")
        winner_id = game.player_id_for_seat(game.winner_seat_id)
        loser_id = game.player_id_for_seat(game.loser_seat_id)
        with self._sessions_lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            try:
                session.connection.send(
                    {
                        "type": MessageType.GAME_OVER.value,
                        "seq_num": self._server_sequence(),
                        "winner_id": winner_id,
                        "loser_id": loser_id,
                        "reason": game.game_over_reason,
                    }
                )
            except (OSError, ConnectionClosed):
                session.connection.close()
        with self._game_lock:
            if self._game is game:
                self._game = None
            self._trigger_resume = None
        self.lobby.reset_for_new_game()
        self._broadcast_lobby_state()

    def _broadcast_server_pdu(
        self, message_type: MessageType, fields: Mapping[str, Any]
    ) -> None:
        with self._sessions_lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            try:
                session.connection.send(
                    {
                        "type": message_type.value,
                        "seq_num": self._server_sequence(),
                        **fields,
                    }
                )
            except (OSError, ConnectionClosed):
                session.connection.close()

    def _session_for_seat(self, seat_id: str | None) -> ClientSession | None:
        if seat_id is None:
            return None
        with self._sessions_lock:
            return self._sessions.get(seat_id)

    def _send_error(
        self,
        session: ClientSession,
        code: ErrorCode,
        message: str,
        rejected_action: Mapping[str, Any],
    ) -> None:
        rejected_seq = rejected_action.get("seq_num")
        seq_num = (
            rejected_seq
            if isinstance(rejected_seq, int) and not isinstance(rejected_seq, bool)
            else self._server_sequence()
        )
        try:
            session.connection.send(
                {
                    "type": MessageType.ERROR.value,
                    "seq_num": seq_num,
                    "code": code.value,
                    "message": message,
                    "rejected_action": dict(rejected_action),
                }
            )
        except (OSError, ConnectionClosed):
            session.connection.close()

    def _broadcast_lobby_state(self) -> None:
        with self._sessions_lock:
            sessions = list(self._sessions.values())
        state = self.lobby.snapshot()
        for session in sessions:
            try:
                session.connection.send(
                    {
                        "type": MessageType.GAME_STATE_UPDATE.value,
                        "seq_num": self._server_sequence(),
                        "state": state,
                    }
                )
            except (OSError, ConnectionClosed):
                session.connection.close()

    def _drop_session(self, session: ClientSession) -> None:
        removed = False
        disconnected_game: GameSession | None = None
        with self._sessions_lock:
            if self._sessions.get(session.seat_id) is session:
                del self._sessions[session.seat_id]
                removed = True
                with self._game_lock:
                    if (
                        self._game is not None
                        and self._game.lifecycle_state
                        in {LifecycleState.MULLIGAN, LifecycleState.IN_GAME}
                    ):
                        disconnected_game = self._game
                        self.lobby.disconnect(
                            session.seat_id, preserve_ready=True
                        )
                    else:
                        self.lobby.disconnect(session.seat_id)
                        self._game = None
                        self._trigger_resume = None
        session.connection.close()
        if removed:
            print(f"Disconnected {session.seat_id}")
            if disconnected_game is not None and not self._stop.is_set():
                self._begin_reconnect_grace(disconnected_game, session.seat_id)
            elif disconnected_game is not None:
                with self._game_lock:
                    if self._game is disconnected_game:
                        self._game = None
                    self._trigger_resume = None
            elif not self._stop.is_set():
                self._broadcast_lobby_state()

    def _begin_reconnect_grace(self, game: GameSession, seat_id: str) -> None:
        """Pause an active game and reserve its disconnected seat."""

        self._cancel_priority_deadline()
        with self._reconnect_lock:
            if seat_id in self._reconnect_reservations:
                return
            player = game.players[seat_id]
            timer = threading.Timer(
                self.reconnect_grace_seconds,
                self._reconnect_grace_expired,
                args=(game, seat_id),
            )
            timer.daemon = True
            self._reconnect_reservations[seat_id] = ReconnectReservation(
                seat_id=seat_id,
                player_id=player.player_id,
                deck_list=player.original_deck,
                game=game,
                timer=timer,
            )
            timer.start()
        print(
            f"Waiting {self.reconnect_grace_seconds:g}s for "
            f"{player.player_id} to reconnect"
        )

    def _reconnect_grace_expired(
        self, game: GameSession, seat_id: str
    ) -> None:
        with self._reconnect_lock:
            reservation = self._reconnect_reservations.get(seat_id)
            if reservation is None or reservation.game is not game:
                return
            del self._reconnect_reservations[seat_id]
        if self.game is not game or game.lifecycle_state not in {
            LifecycleState.MULLIGAN,
            LifecycleState.IN_GAME,
        }:
            return

        unauthenticated_session = self._session_for_seat(seat_id)
        if unauthenticated_session is not None:
            unauthenticated_session.connection.close()
        game.declare_game_over(seat_id, "DISCONNECT")
        self._finish_game(game)

    def _reconnect_reservation(
        self, seat_id: str
    ) -> ReconnectReservation | None:
        with self._reconnect_lock:
            return self._reconnect_reservations.get(seat_id)

    def _game_has_reconnect_reservation(self, game: GameSession) -> bool:
        with self._reconnect_lock:
            return any(
                reservation.game is game
                for reservation in self._reconnect_reservations.values()
            )

    def _cancel_reconnect_reservations(self, game: GameSession) -> None:
        with self._reconnect_lock:
            matching = [
                seat_id
                for seat_id, reservation in self._reconnect_reservations.items()
                if reservation.game is game
            ]
            for seat_id in matching:
                self._reconnect_reservations.pop(seat_id).timer.cancel()

    def stop(self) -> None:
        """Stop accepting clients and close all active connections."""

        self._stop.set()
        self._cancel_priority_deadline()
        with self._reconnect_lock:
            reservations = list(self._reconnect_reservations.values())
            self._reconnect_reservations.clear()
        for reservation in reservations:
            reservation.timer.cancel()
        listener = self._listener
        self._listener = None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        with self._sessions_lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            session.connection.close()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the MTGNP two-player server.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_DIRECTORY)
    parser.add_argument(
        "--verbose", action="store_true", help="Print every PDU sent and received."
    )
    parser.add_argument(
        "--reconnect-grace",
        type=float,
        default=DEFAULT_RECONNECT_GRACE_SECONDS,
        metavar="SECONDS",
        help="Seconds to reserve a disconnected in-game seat (default: 30).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    server = MTGNPServer(
        host=args.host,
        port=args.port,
        data_directory=args.data,
        verbose=args.verbose,
        reconnect_grace_seconds=args.reconnect_grace,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping MTGNP server.")
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
