from __future__ import annotations

import unittest
from pathlib import Path

from mtgnp.catalog import load_catalog
from mtgnp.lobby import Lobby, LobbyError, LobbyFull
from mtgnp.protocol import ErrorCode


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class LobbyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lobby = Lobby(load_catalog(PROJECT_ROOT / "data"))
        self.seat_1 = self.lobby.connect()
        self.seat_2 = self.lobby.connect()

    def test_exactly_two_seats_are_available(self) -> None:
        self.assertEqual((self.seat_1, self.seat_2), ("seat_1", "seat_2"))
        with self.assertRaises(LobbyFull):
            self.lobby.connect()

    def test_ready_submission_updates_public_lobby_state(self) -> None:
        self.lobby.submit_ready(self.seat_1, "alice", ["mountain_001"])
        state = self.lobby.snapshot()
        self.assertEqual(state["players_connected"], 2)
        self.assertEqual(state["players_ready"], 1)
        self.assertEqual(state["ready_players"], ["alice"])
        self.assertEqual(state["waiting_for"], ["seat_2"])

    def test_duplicate_player_id_is_rejected_without_mutation(self) -> None:
        self.lobby.submit_ready(self.seat_1, "alice", ["mountain_001"])
        with self.assertRaises(LobbyError) as caught:
            self.lobby.submit_ready(self.seat_2, "alice", ["island_001"])
        self.assertEqual(caught.exception.code, ErrorCode.DUPLICATE_ID)
        self.assertEqual(self.lobby.ready_count, 1)

    def test_two_ready_decks_cannot_share_a_physical_instance(self) -> None:
        self.lobby.submit_ready(
            self.seat_1, "alice", ["mountain_001", "lightning_bolt_001"]
        )
        with self.assertRaises(LobbyError) as caught:
            self.lobby.submit_ready(
                self.seat_2, "bob", ["mountain_001", "island_001"]
            )
        self.assertEqual(caught.exception.code, ErrorCode.ILLEGAL_DECK)
        self.assertEqual(self.lobby.ready_count, 1)

    def test_illegal_deck_is_reported_as_illegal_deck(self) -> None:
        with self.assertRaises(LobbyError) as caught:
            self.lobby.submit_ready(self.seat_1, "alice", ["unknown_001"])
        self.assertEqual(caught.exception.code, ErrorCode.ILLEGAL_DECK)
        self.assertEqual(self.lobby.ready_count, 0)

    def test_subsequent_ready_replaces_the_same_seat_submission(self) -> None:
        self.lobby.submit_ready(self.seat_1, "alice", ["mountain_001"])
        self.lobby.submit_ready(self.seat_1, "alice_v2", ["forest_001"])
        state = self.lobby.snapshot()
        self.assertEqual(state["ready_players"], ["alice_v2"])

    def test_disconnect_releases_and_resets_the_seat(self) -> None:
        self.lobby.submit_ready(self.seat_1, "alice", ["mountain_001"])
        self.lobby.disconnect(self.seat_1)
        self.assertEqual(self.lobby.ready_count, 0)
        self.assertEqual(self.lobby.connect(), "seat_1")

    def test_reconnect_reservation_preserves_ready_identity(self) -> None:
        self.lobby.submit_ready(self.seat_1, "alice", ["mountain_001"])
        self.lobby.disconnect(self.seat_1, preserve_ready=True)
        self.assertEqual(self.lobby.ready_count, 0)

        self.assertEqual(self.lobby.connect(), "seat_1")
        self.assertEqual(self.lobby.snapshot()["ready_players"], ["alice"])

    def test_new_game_reset_preserves_connections_but_requires_fresh_ready(self) -> None:
        self.lobby.submit_ready(self.seat_1, "alice", ["mountain_001"])
        self.lobby.submit_ready(self.seat_2, "bob", ["island_001"])
        self.lobby.reset_for_new_game()
        state = self.lobby.snapshot()
        self.assertEqual(state["players_connected"], 2)
        self.assertEqual(state["players_ready"], 0)
        self.assertEqual(state["waiting_for"], ["seat_1", "seat_2"])


if __name__ == "__main__":
    unittest.main()
