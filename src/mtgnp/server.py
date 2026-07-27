"""Asyncio TCP server for the MTGNP milestone implementation."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from typing import Any

from .catalog import CardCatalog
from .framing import read_pdu, write_pdu
from .game import GameSession, SEATS, StackItem
from .protocol import (
    DEFAULT_PORT,
    ErrorCode,
    Lifecycle,
    MessageType,
    Phase,
    ProtocolError,
    TOKEN_ACTIONS,
    validate_pdu,
)
from .tracing import PduTrace


@dataclass(slots=True)
class Peer:
    seat: str
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    label: str
    last_server_seq: int = 0


class MTGNPServer:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        verbose: bool = False,
        priority_timeout_ms: int = 60_000,
    ) -> None:
        self.host = host
        self.port = port
        self.priority_timeout_ms = priority_timeout_ms
        self.trace = PduTrace(verbose)
        self.catalog = CardCatalog.load()
        self.session = GameSession(self.catalog)
        self.peers: dict[str, Peer] = {}
        self.sequence = 0
        self.lock = asyncio.Lock()
        self.priority_timeout_task: asyncio.Task[None] | None = None

    async def serve_forever(self) -> None:
        server = await asyncio.start_server(self._accept_connection, self.host, self.port)
        addresses = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
        print(
            f"MTGNP server listening on {addresses}; "
            f"catalog={len(self.catalog.definitions)} cards/"
            f"{len(self.catalog.instances)} instances; verbose={self.trace.enabled}",
            flush=True,
        )
        async with server:
            await server.serve_forever()

    async def _accept_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        address = writer.get_extra_info("peername")
        async with self.lock:
            available = next((seat for seat in SEATS if seat not in self.peers), None)
            if available is None:
                print(f"Refused additional client {address}: both seats are occupied.", flush=True)
                writer.close()
                await writer.wait_closed()
                return
            peer = Peer(available, reader, writer, str(address))
            self.peers[available] = peer
            print(f"Connected {peer.label} as {available}.", flush=True)

        try:
            while True:
                try:
                    pdu = await read_pdu(
                        reader,
                        direction="client",
                        trace=lambda message: self.trace.log("CLIENT -> SERVER", peer.label, message),
                    )
                except ProtocolError as exc:
                    async with self.lock:
                        await self._send_error(peer, exc)
                    if "exceeds" in exc.message:
                        break
                    continue
                await self._dispatch(peer, pdu)
        except (asyncio.IncompleteReadError, ConnectionError, BrokenPipeError):
            pass
        finally:
            await self._disconnect(peer)

    async def _dispatch(self, peer: Peer, pdu: dict[str, Any]) -> None:
        async with self.lock:
            try:
                message_type = validate_pdu(pdu, direction="client")
                if message_type is MessageType.PING:
                    await self._send(
                        peer,
                        MessageType.PONG,
                        {"timestamp": pdu["timestamp"]},
                        seq_num=pdu["seq_num"],
                    )
                elif message_type is MessageType.PLAYER_READY:
                    await self._handle_ready(peer, pdu)
                elif message_type is MessageType.MULLIGAN_CHOICE:
                    await self._handle_mulligan(peer, pdu)
                elif message_type is MessageType.PRIORITY_PASS:
                    await self._handle_priority_pass(peer, pdu)
                elif message_type is MessageType.PLAY_LAND:
                    await self._handle_play_land(peer, pdu)
                elif message_type is MessageType.CAST_SPELL:
                    await self._handle_cast_spell(peer, pdu)
                elif message_type is MessageType.DECLARE_ATTACKERS:
                    await self._handle_declare_attackers(peer, pdu)
                elif message_type is MessageType.DISCARD:
                    await self._handle_discard(peer, pdu)
                elif message_type is MessageType.CONCEDE:
                    await self._handle_concede(peer, pdu)
                else:
                    raise ProtocolError(
                        ErrorCode.ILLEGAL_ACTION,
                        f"{message_type} is defined but not implemented in this milestone.",
                        pdu,
                    )
            except ProtocolError as exc:
                if exc.rejected_action is None:
                    exc.rejected_action = dict(pdu)
                await self._send_error(peer, exc)
                try:
                    rejected_type = MessageType(pdu.get("type"))
                except (TypeError, ValueError):
                    rejected_type = None
                if (
                    self.session.priority_slot == peer.seat
                    and rejected_type in TOKEN_ACTIONS
                ):
                    await self._send_priority_grant(peer.seat)

    async def _handle_ready(self, peer: Peer, pdu: dict[str, Any]) -> None:
        all_ready = self.session.register_ready(
            peer.seat,
            pdu["player_id"],
            pdu["deck_list"],
        )
        await self._send(
            peer,
            MessageType.GAME_STATE_UPDATE,
            {"state": self._lobby_state()},
        )
        if not all_ready:
            return

        self.session.lifecycle = Lifecycle.GAME_SETUP
        await self._broadcast(
            MessageType.GAME_STATE_UPDATE,
            {
                "state": {
                    "phase": Lifecycle.GAME_SETUP.value,
                    "players_ready": 2,
                    "waiting_for": [],
                }
            },
        )
        self.session.setup()
        await self._broadcast_states(token_slots=set(SEATS))

    async def _handle_mulligan(self, peer: Peer, pdu: dict[str, Any]) -> None:
        self._require_token(peer, pdu)
        all_kept = self.session.mulligan_choice(
            peer.seat,
            keep=pdu["keep"],
            cards_to_bottom=pdu["cards_to_bottom"],
        )
        if not pdu["keep"]:
            await self._send_state(peer.seat, token=True)
            return
        if all_kept:
            await self._begin_game()

    async def _begin_game(self) -> None:
        old_phase = self.session.phase
        self.session.begin_game()
        await self._broadcast_transition(old_phase)
        self.session.untap_active_player()
        await self._broadcast_states()
        old_phase = self.session.phase
        self.session.phase = Phase.UPKEEP
        await self._broadcast_transition(old_phase)
        await self._open_priority()

    async def _handle_priority_pass(self, peer: Peer, pdu: dict[str, Any]) -> None:
        self._require_priority_token(peer, pdu)
        self._cancel_priority_timeout()
        outcome = self.session.pass_priority(peer.seat)
        if outcome == "switch":
            assert self.session.priority_slot is not None
            await self._send_priority_grant(self.session.priority_slot)
        elif outcome == "resolve":
            await self._resolve_top()
        else:
            await self._advance_phase()

    async def _handle_play_land(self, peer: Peer, pdu: dict[str, Any]) -> None:
        self._require_priority_token(peer, pdu)
        self._cancel_priority_timeout()
        self.session.play_land(peer.seat, pdu["card_id"])
        await self._broadcast_states()
        await self._send_priority_grant(peer.seat)

    async def _handle_cast_spell(self, peer: Peer, pdu: dict[str, Any]) -> None:
        self._require_priority_token(peer, pdu)
        self._cancel_priority_timeout()
        item = self.session.cast_spell(
            peer.seat,
            pdu["card_id"],
            pdu["targets"],
            pdu["mana_payment"],
        )
        await self._broadcast_stack_push(item)
        await self._broadcast_states()
        await self._send_priority_grant(peer.seat)

    async def _handle_declare_attackers(
        self,
        peer: Peer,
        pdu: dict[str, Any],
    ) -> None:
        self._require_token(peer, pdu)
        old_phase = self.session.phase
        self.session.declare_no_attackers(peer.seat, pdu["attackers"])
        await self._broadcast_transition(old_phase)
        await self._open_priority()

    async def _handle_discard(self, peer: Peer, pdu: dict[str, Any]) -> None:
        self._require_token(peer, pdu)
        finished = self.session.discard(peer.seat, pdu["card_ids"])
        if finished:
            await self._finish_cleanup()
        else:
            await self._broadcast_states(token_slots={peer.seat})

    async def _handle_concede(self, peer: Peer, pdu: dict[str, Any]) -> None:
        if self.session.lifecycle not in {Lifecycle.MULLIGAN, Lifecycle.IN_GAME}:
            raise ProtocolError(ErrorCode.WRONG_PHASE, "There is no game to concede.")
        if pdu["player_id"] != self.session.player_id(peer.seat):
            raise ProtocolError(
                ErrorCode.ILLEGAL_ACTION,
                "CONCEDE player_id does not match this connection.",
            )
        self.session.end_game(self.session.opponent(peer.seat), peer.seat, "CONCEDE")
        await self._finish_game()

    async def _advance_phase(self) -> None:
        old_phase = self.session.phase
        next_phase = self.session.advance_after_empty_stack()
        token_slot = self.session.active_slot if next_phase is Phase.DECLARE_ATTACKERS else None
        await self._broadcast_transition(old_phase, token_slot=token_slot)

        if next_phase is Phase.DRAW:
            drew = self.session.perform_draw_step()
            if self.session.lifecycle is Lifecycle.GAME_OVER:
                await self._finish_game()
                return
            if drew:
                await self._broadcast_states()
            await self._open_priority()
        elif next_phase in {
            Phase.PRECOMBAT_MAIN,
            Phase.BEGIN_COMBAT,
            Phase.END_OF_COMBAT,
            Phase.POSTCOMBAT_MAIN,
            Phase.END_STEP,
        }:
            await self._open_priority()
        elif next_phase is Phase.CLEANUP:
            active = self.session.players[self.session.active_slot]
            if len(active.hand) > 7:
                await self._broadcast_states(token_slots={active.seat})
            else:
                await self._finish_cleanup()

    async def _finish_cleanup(self) -> None:
        self.session.perform_cleanup()
        await self._broadcast_states()
        old_phase = self.session.phase
        self.session.start_next_turn()
        await self._broadcast_transition(old_phase)
        self.session.untap_active_player()
        await self._broadcast_states()
        old_phase = self.session.phase
        self.session.phase = Phase.UPKEEP
        await self._broadcast_transition(old_phase)
        await self._open_priority()

    async def _resolve_top(self) -> None:
        item, result, changes = self.session.resolve_top()
        await self._broadcast(
            MessageType.STACK_RESOLVE,
            {
                "stack_item_id": item.stack_item_id,
                "result": result,
                "state_changes": changes,
            },
        )
        await self._broadcast_states()
        if self.session.lifecycle is Lifecycle.GAME_OVER:
            await self._finish_game()
        else:
            await self._open_priority()

    async def _broadcast_stack_push(self, item: StackItem) -> None:
        await self._broadcast(
            MessageType.STACK_PUSH,
            {
                "stack_item_id": item.stack_item_id,
                "item_type": item.item_type,
                "source": item.source_id,
                "targets": list(item.targets),
                "controller": self.session.player_id(item.controller_slot),
            },
        )

    async def _open_priority(self) -> None:
        await self._send_priority_grant(self.session.start_priority())

    async def _send_priority_grant(self, seat: str) -> None:
        peer = self.peers.get(seat)
        if not peer:
            return
        seq_num = await self._send(
            peer,
            MessageType.PRIORITY_GRANT,
            {
                "player_id": self.session.player_id(seat),
                "time_limit_ms": self.priority_timeout_ms,
            },
        )
        self.session.players[seat].expected_token = seq_num
        self._schedule_priority_timeout(seat, seq_num)

    async def _broadcast_transition(
        self,
        old_phase: Phase,
        *,
        token_slot: str | None = None,
    ) -> None:
        seq_by_seat = await self._broadcast(
            MessageType.PHASE_TRANSITION,
            {
                "from_phase": old_phase.value,
                "to_phase": self.session.phase.value,
                "active_player": self.session.player_id(self.session.active_slot),
                "turn": self.session.turn,
            },
        )
        if token_slot and token_slot in seq_by_seat:
            self.session.players[token_slot].expected_token = seq_by_seat[token_slot]

    async def _send_state(self, seat: str, *, token: bool = False) -> None:
        peer = self.peers.get(seat)
        if not peer:
            return
        seq_num = await self._send(
            peer,
            MessageType.GAME_STATE_UPDATE,
            {"state": self.session.visible_state(seat)},
        )
        if token:
            self.session.players[seat].expected_token = seq_num

    async def _broadcast_states(self, *, token_slots: set[str] | None = None) -> None:
        for seat in list(self.peers):
            await self._send_state(seat, token=bool(token_slots and seat in token_slots))

    async def _finish_game(self) -> None:
        self._cancel_priority_timeout()
        await self._broadcast(MessageType.GAME_OVER, self.session.game_over_payload())
        self.session.reset_to_lobby()

    async def _send_error(self, peer: Peer, error: ProtocolError) -> None:
        payload: dict[str, Any] = {
            "code": error.code.value,
            "message": error.message,
        }
        if error.rejected_action is not None:
            payload["rejected_action"] = error.rejected_action
        await self._send(peer, MessageType.ERROR, payload)

    async def _send(
        self,
        peer: Peer,
        message_type: MessageType,
        payload: dict[str, Any],
        *,
        seq_num: int | None = None,
    ) -> int:
        if seq_num is None:
            seq_num = self._next_seq()
        pdu = {"type": message_type.value, "seq_num": seq_num, **payload}
        await write_pdu(
            peer.writer,
            pdu,
            direction="server",
            trace=lambda message: self.trace.log("SERVER -> CLIENT", peer.label, message),
        )
        peer.last_server_seq = seq_num
        return seq_num

    async def _broadcast(
        self,
        message_type: MessageType,
        payload: dict[str, Any],
    ) -> dict[str, int]:
        seq_by_seat: dict[str, int] = {}
        for seat, peer in list(self.peers.items()):
            seq_by_seat[seat] = await self._send(peer, message_type, payload)
        return seq_by_seat

    def _next_seq(self) -> int:
        self.sequence += 1
        return self.sequence

    def _require_token(self, peer: Peer, pdu: dict[str, Any]) -> None:
        expected = self.session.players[peer.seat].expected_token
        if pdu["seq_num"] != expected:
            raise ProtocolError(
                ErrorCode.STALE_ACTION,
                f"Priority/request token mismatch. Expected {expected}, got {pdu['seq_num']}.",
                pdu,
            )

    def _require_priority_token(self, peer: Peer, pdu: dict[str, Any]) -> None:
        if self.session.priority_slot != peer.seat:
            raise ProtocolError(
                ErrorCode.NOT_YOUR_PRIORITY,
                "This connection does not currently hold priority.",
                pdu,
            )
        self._require_token(peer, pdu)

    def _lobby_state(self) -> dict[str, Any]:
        waiting = [
            seat for seat, player in self.session.players.items() if not player.ready
        ]
        return {
            "phase": Lifecycle.LOBBY.value,
            "players_ready": sum(player.ready for player in self.session.players.values()),
            "waiting_for": waiting,
        }

    def _schedule_priority_timeout(self, seat: str, token: int) -> None:
        self._cancel_priority_timeout()
        self.priority_timeout_task = asyncio.create_task(
            self._priority_timeout(seat, token)
        )

    def _cancel_priority_timeout(self) -> None:
        task = self.priority_timeout_task
        self.priority_timeout_task = None
        if task and task is not asyncio.current_task():
            task.cancel()

    async def _priority_timeout(self, seat: str, token: int) -> None:
        try:
            await asyncio.sleep(self.priority_timeout_ms / 1000)
            async with self.lock:
                player = self.session.players[seat]
                if (
                    self.session.lifecycle is Lifecycle.IN_GAME
                    and self.session.priority_slot == seat
                    and player.expected_token == token
                ):
                    self.session.end_game(self.session.opponent(seat), seat, "DISCONNECT")
                    await self._finish_game()
                    peer = self.peers.get(seat)
                    if peer:
                        peer.writer.close()
        except asyncio.CancelledError:
            return

    async def _disconnect(self, peer: Peer) -> None:
        async with self.lock:
            current = self.peers.get(peer.seat)
            if current is not peer:
                return
            disconnected_id = self.session.player_id(peer.seat)
            del self.peers[peer.seat]
            if (
                disconnected_id
                and self.session.lifecycle
                in {Lifecycle.GAME_SETUP, Lifecycle.MULLIGAN, Lifecycle.IN_GAME}
                and self.session.opponent(peer.seat) in self.peers
            ):
                self.session.end_game(
                    self.session.opponent(peer.seat),
                    peer.seat,
                    "DISCONNECT",
                )
                await self._finish_game()
            else:
                self.session.players[peer.seat].reset_for_lobby()
                if not self.peers:
                    self.session.reset_to_lobby()
            peer.writer.close()
            try:
                await peer.writer.wait_closed()
            except (ConnectionError, BrokenPipeError):
                pass
            print(f"Disconnected {peer.label} from {peer.seat}.", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the MTGNP authoritative server.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every sent and received PDU (demo prerequisite).",
    )
    parser.add_argument("--priority-timeout-ms", type=int, default=60_000)
    return parser


async def _run(args: argparse.Namespace) -> None:
    server = MTGNPServer(
        args.host,
        args.port,
        verbose=args.verbose,
        priority_timeout_ms=args.priority_timeout_ms,
    )
    await server.serve_forever()


def main() -> None:
    args = build_parser().parse_args()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()
