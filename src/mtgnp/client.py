"""Thin interactive MTGNP client with heartbeat and runtime verbose toggle."""

from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import time
from pathlib import Path
from typing import Any

from .framing import read_pdu, write_pdu
from .protocol import DEFAULT_PORT, MessageType, Phase, ProtocolError
from .tracing import PduTrace

HELP = """Commands:
  keep [CARD_ID ...]                 Keep; bottom listed cards after mulligans
  mulligan                           Redraw a seven-card hand
  pass                               Pass priority
  land CARD_ID                       Play a land
  cast CARD_ID [TARGET ...] --mana R=1,X=1
  attack none                        Declare no attackers (current milestone)
  discard CARD_ID [CARD_ID ...]      Discard during Cleanup
  concede                            Concede the current game
  ready                              Resubmit this client/deck in Lobby
  verbose on|off                     Toggle readable PDU logging
  raw JSON_OBJECT                    Send another defined client PDU
  state                              Print the last visible state
  help
  quit
"""


class MTGNPClient:
    def __init__(
        self,
        host: str,
        port: int,
        player_id: str,
        deck: list[str],
        *,
        verbose: bool = False,
        heartbeat_interval: float = 30.0,
        pong_timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.player_id = player_id
        self.deck = deck
        self.trace = PduTrace(verbose)
        self.heartbeat_interval = heartbeat_interval
        self.pong_timeout = pong_timeout
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.ready_seq = 0
        self.ping_seq = 0
        self.last_server_seq = 0
        self.action_token: int | None = None
        self.last_state: dict[str, Any] | None = None
        self.last_pong_at = time.monotonic()

    async def run(self) -> None:
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        print(
            f"Connected to {self.host}:{self.port} as {self.player_id}; "
            f"verbose={self.trace.enabled}"
        )
        print(HELP)
        await self.send_ready()

        tasks = {
            asyncio.create_task(self._receive_loop()),
            asyncio.create_task(self._input_loop()),
            asyncio.create_task(self._heartbeat_loop()),
        }
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            error = task.exception()
            if error and not isinstance(error, (EOFError, asyncio.CancelledError)):
                print(f"Client stopped: {error}")
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()

    async def send_ready(self) -> None:
        self.ready_seq += 1
        await self._send(
            {
                "type": MessageType.PLAYER_READY.value,
                "seq_num": self.ready_seq,
                "player_id": self.player_id,
                "deck_list": self.deck,
            }
        )

    async def _receive_loop(self) -> None:
        assert self.reader is not None
        while True:
            pdu = await read_pdu(
                self.reader,
                direction="server",
                trace=lambda message: self.trace.log("SERVER -> CLIENT", self.player_id, message),
            )
            self.last_server_seq = pdu["seq_num"]
            message_type = MessageType(pdu["type"])

            if message_type is MessageType.PONG:
                self.last_pong_at = time.monotonic()
            elif message_type is MessageType.PRIORITY_GRANT:
                self.action_token = pdu["seq_num"]
            elif message_type is MessageType.GAME_STATE_UPDATE:
                self.last_state = pdu["state"]
                phase = pdu["state"].get("phase")
                if phase == Phase.MULLIGAN.value or (
                    phase == Phase.CLEANUP.value
                    and pdu["state"].get("active_player") == self.player_id
                    and len(pdu["state"].get("hand", {}).get(self.player_id, [])) > 7
                ):
                    self.action_token = pdu["seq_num"]
            elif (
                message_type is MessageType.PHASE_TRANSITION
                and pdu["to_phase"] is not None
                and pdu["to_phase"] == Phase.DECLARE_ATTACKERS.value
                and pdu["active_player"] == self.player_id
            ):
                self.action_token = pdu["seq_num"]
            elif message_type is MessageType.GAME_OVER:
                self.action_token = None
            self._render(pdu)

    async def _input_loop(self) -> None:
        while True:
            raw = await asyncio.to_thread(input, "mtgnp> ")
            if not raw.strip():
                continue
            try:
                should_quit = await self._handle_command(raw)
                if should_quit:
                    return
            except (ValueError, json.JSONDecodeError, ProtocolError) as exc:
                print(f"Command error: {exc}")

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            self.ping_seq += 1
            sent_at = time.monotonic()
            await self._send(
                {
                    "type": MessageType.PING.value,
                    "seq_num": self.ping_seq,
                    "timestamp": int(time.time() * 1000),
                }
            )
            await asyncio.sleep(self.pong_timeout)
            if self.last_pong_at < sent_at:
                raise TimeoutError(f"No PONG received within {self.pong_timeout:g} seconds.")

    async def _handle_command(self, raw: str) -> bool:
        command, *parts = shlex.split(raw)
        command = command.lower()
        if command == "help":
            print(HELP)
        elif command == "quit":
            return True
        elif command == "verbose":
            if len(parts) != 1 or parts[0].lower() not in {"on", "off"}:
                raise ValueError("Usage: verbose on|off")
            self.trace.set_enabled(parts[0].lower() == "on")
        elif command == "state":
            print(json.dumps(self.last_state, indent=2, ensure_ascii=False))
        elif command == "ready":
            await self.send_ready()
        elif command == "keep":
            await self._send_action(
                MessageType.MULLIGAN_CHOICE,
                {"keep": True, "cards_to_bottom": parts},
            )
        elif command == "mulligan":
            await self._send_action(
                MessageType.MULLIGAN_CHOICE,
                {"keep": False, "cards_to_bottom": []},
            )
        elif command == "pass":
            await self._send_action(MessageType.PRIORITY_PASS, {})
        elif command == "land":
            if len(parts) != 1:
                raise ValueError("Usage: land CARD_ID")
            await self._send_action(MessageType.PLAY_LAND, {"card_id": parts[0]})
        elif command == "cast":
            await self._cast(parts)
        elif command == "attack":
            if parts != ["none"]:
                raise ValueError("Current milestone supports only: attack none")
            await self._send_action(MessageType.DECLARE_ATTACKERS, {"attackers": []})
        elif command == "discard":
            if not parts:
                raise ValueError("Usage: discard CARD_ID [CARD_ID ...]")
            await self._send_action(MessageType.DISCARD, {"card_ids": parts})
        elif command == "concede":
            await self._send(
                {
                    "type": MessageType.CONCEDE.value,
                    "seq_num": self.last_server_seq,
                    "player_id": self.player_id,
                }
            )
        elif command == "raw":
            payload = json.loads(raw.partition(" ")[2])
            if not isinstance(payload, dict):
                raise ValueError("raw requires a JSON object")
            payload.setdefault("seq_num", self._token())
            await self._send(payload)
        else:
            raise ValueError(f"Unknown command {command!r}. Type 'help'.")
        return False

    async def _cast(self, parts: list[str]) -> None:
        if "--mana" not in parts:
            raise ValueError("Usage: cast CARD_ID [TARGET ...] --mana R=1,X=1")
        separator = parts.index("--mana")
        before = parts[:separator]
        after = parts[separator + 1 :]
        if not before or len(after) != 1:
            raise ValueError("Usage: cast CARD_ID [TARGET ...] --mana R=1,X=1")
        card_id, *targets = before
        targets = [] if targets == ["-"] else targets
        payment: dict[str, int] = {}
        if after[0] not in {"-", ""}:
            for entry in after[0].split(","):
                key, value = entry.split("=", 1)
                payment[key.upper()] = int(value)
        await self._send_action(
            MessageType.CAST_SPELL,
            {
                "card_id": card_id,
                "targets": targets,
                "mana_payment": payment,
            },
        )

    async def _send_action(
        self,
        message_type: MessageType,
        fields: dict[str, Any],
    ) -> None:
        await self._send(
            {
                "type": message_type.value,
                "seq_num": self._token(),
                **fields,
            }
        )

    def _token(self) -> int:
        if self.action_token is None:
            raise ValueError("No current priority/request token.")
        return self.action_token

    async def _send(self, pdu: dict[str, Any]) -> None:
        if self.writer is None:
            raise ConnectionError("Client is not connected.")
        await write_pdu(
            self.writer,
            pdu,
            direction="client",
            trace=lambda message: self.trace.log("CLIENT -> SERVER", self.player_id, message),
        )

    def _render(self, pdu: dict[str, Any]) -> None:
        if self.trace.enabled or pdu["type"] == MessageType.PONG.value:
            return
        message_type = MessageType(pdu["type"])
        if message_type is MessageType.GAME_STATE_UPDATE:
            state = pdu["state"]
            if state.get("phase") in {LifecycleName.LOBBY, LifecycleName.GAME_SETUP}:
                print(
                    f"Lobby: {state.get('players_ready', 0)}/2 ready; "
                    f"waiting={state.get('waiting_for', [])}"
                )
                return
            own_hand = state.get("hand", {}).get(self.player_id, [])
            print(
                f"State: turn={state.get('turn')} phase={state.get('phase')} "
                f"active={state.get('active_player')} priority={state.get('priority_holder')} "
                f"life={state.get('life_totals')} hand={own_hand} "
                f"stack={len(state.get('stack', []))}"
            )
        elif message_type is MessageType.PHASE_TRANSITION:
            print(
                f"Phase: {pdu['from_phase']} -> {pdu['to_phase']} "
                f"(turn {pdu['turn']}, active {pdu['active_player']})"
            )
        elif message_type is MessageType.PRIORITY_GRANT:
            print(f"Priority granted to you (token {pdu['seq_num']}).")
        elif message_type is MessageType.ERROR:
            print(f"ERROR {pdu['code']}: {pdu['message']}")
        elif message_type is MessageType.GAME_OVER:
            print(
                f"GAME OVER: winner={pdu['winner_id']} loser={pdu['loser_id']} "
                f"reason={pdu['reason']}"
            )
        elif message_type in {
            MessageType.STACK_PUSH,
            MessageType.STACK_RESOLVE,
            MessageType.COMBAT_DAMAGE_RESULT,
        }:
            print(json.dumps(pdu, indent=2, ensure_ascii=False))


class LifecycleName:
    LOBBY = "LOBBY"
    GAME_SETUP = "GAME_SETUP"


def _load_deck(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict):
        payload = payload.get("deck_list")
    if not isinstance(payload, list) or not all(isinstance(card_id, str) for card_id in payload):
        raise ValueError("Deck file must be a JSON array or an object containing deck_list.")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an interactive MTGNP client.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--player-id", required=True)
    parser.add_argument("--deck", type=Path, required=True)
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every sent and received PDU (demo prerequisite).",
    )
    parser.add_argument("--heartbeat-interval", type=float, default=30.0)
    parser.add_argument("--pong-timeout", type=float, default=10.0)
    return parser


async def _run(args: argparse.Namespace) -> None:
    client = MTGNPClient(
        args.host,
        args.port,
        args.player_id,
        _load_deck(args.deck),
        verbose=args.verbose,
        heartbeat_interval=args.heartbeat_interval,
        pong_timeout=args.pong_timeout,
    )
    await client.run()


def main() -> None:
    args = build_parser().parse_args()
    try:
        asyncio.run(_run(args))
    except (KeyboardInterrupt, ConnectionRefusedError):
        print("\nClient stopped.")


if __name__ == "__main__":
    main()
