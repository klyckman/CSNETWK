"""Transport-independent two-seat lobby state and PLAYER_READY validation."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Sequence

from .catalog import CardCatalog, CatalogError
from .protocol import ErrorCode, LifecycleState


class LobbyError(ValueError):
    """A lobby request failed without changing lobby state."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class LobbyFull(LobbyError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.ILLEGAL_ACTION, "Both player seats are occupied.")


@dataclass(slots=True)
class LobbySeat:
    seat_id: str
    connected: bool = False
    player_id: str | None = None
    deck_list: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.connected and self.player_id is not None and bool(self.deck_list)

    def reset(self) -> None:
        self.connected = False
        self.player_id = None
        self.deck_list = ()


@dataclass(frozen=True, slots=True)
class ReadyPlayer:
    seat_id: str
    player_id: str
    deck_list: tuple[str, ...]


class Lobby:
    """Own the two lobby seats while the server owns network connections."""

    def __init__(self, catalog: CardCatalog, *, capacity: int = 2) -> None:
        if capacity != 2:
            raise ValueError("MTGNP v1.0 requires exactly two lobby seats.")
        self.catalog = catalog
        self._seats = {
            f"seat_{number}": LobbySeat(f"seat_{number}")
            for number in range(1, capacity + 1)
        }
        self._lock = threading.RLock()

    def connect(self) -> str:
        """Reserve and return the first available seat."""

        with self._lock:
            for seat in self._seats.values():
                if not seat.connected:
                    seat.reset()
                    seat.connected = True
                    return seat.seat_id
        raise LobbyFull()

    def disconnect(self, seat_id: str) -> None:
        with self._lock:
            seat = self._seat(seat_id)
            seat.reset()

    def reset_for_new_game(self) -> None:
        """Clear ready identities/decks while preserving live TCP seats."""

        with self._lock:
            for seat in self._seats.values():
                was_connected = seat.connected
                seat.reset()
                seat.connected = was_connected

    def submit_ready(
        self, seat_id: str, player_id: str, deck_list: Sequence[str]
    ) -> None:
        """Validate and atomically replace one seat's PLAYER_READY submission."""

        normalized_player_id = player_id.strip() if isinstance(player_id, str) else ""
        if not normalized_player_id:
            raise LobbyError(ErrorCode.ILLEGAL_ACTION, "player_id cannot be empty.")

        try:
            normalized_deck = self.catalog.validate_deck(deck_list)
        except CatalogError as exc:
            raise LobbyError(ErrorCode.ILLEGAL_DECK, str(exc)) from exc

        with self._lock:
            seat = self._seat(seat_id)
            if not seat.connected:
                raise LobbyError(ErrorCode.ILLEGAL_ACTION, f"{seat_id} is disconnected.")
            duplicate = next(
                (
                    other
                    for other in self._seats.values()
                    if other.seat_id != seat_id
                    and other.connected
                    and other.player_id == normalized_player_id
                ),
                None,
            )
            if duplicate is not None:
                raise LobbyError(
                    ErrorCode.DUPLICATE_ID,
                    f"player_id {normalized_player_id!r} is already claimed.",
                )

            overlapping_cards = sorted(
                {
                    card_id
                    for other in self._seats.values()
                    if other.seat_id != seat_id and other.connected
                    for card_id in set(normalized_deck).intersection(other.deck_list)
                }
            )
            if overlapping_cards:
                raise LobbyError(
                    ErrorCode.ILLEGAL_DECK,
                    "Decks cannot share physical card instance IDs: "
                    + ", ".join(overlapping_cards),
                )

            seat.player_id = normalized_player_id
            seat.deck_list = normalized_deck

    def snapshot(self) -> dict[str, Any]:
        """Return the public lobby state used in GAME_STATE_UPDATE."""

        with self._lock:
            ready_seats = [seat for seat in self._seats.values() if seat.ready]
            return {
                "phase": LifecycleState.LOBBY.value,
                "players_connected": sum(
                    1 for seat in self._seats.values() if seat.connected
                ),
                "players_ready": len(ready_seats),
                "waiting_for": [
                    seat.player_id or seat.seat_id
                    for seat in self._seats.values()
                    if not seat.ready
                ],
                "ready_players": [seat.player_id for seat in ready_seats],
            }

    def ready_submissions(self) -> tuple[ReadyPlayer, ...]:
        """Return immutable private inputs needed to begin GAME_SETUP."""

        with self._lock:
            return tuple(
                ReadyPlayer(seat.seat_id, seat.player_id, seat.deck_list)
                for seat in self._seats.values()
                if seat.ready and seat.player_id is not None
            )

    @property
    def connected_count(self) -> int:
        with self._lock:
            return sum(1 for seat in self._seats.values() if seat.connected)

    @property
    def ready_count(self) -> int:
        with self._lock:
            return sum(1 for seat in self._seats.values() if seat.ready)

    def _seat(self, seat_id: str) -> LobbySeat:
        try:
            return self._seats[seat_id]
        except KeyError as exc:
            raise LobbyError(ErrorCode.ILLEGAL_ACTION, f"Unknown seat: {seat_id}") from exc
