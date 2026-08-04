from __future__ import annotations

import socket
import threading
import time
import unittest
from pathlib import Path

from mtgnp.framing import FramedConnection
from mtgnp.protocol import Sender
from mtgnp.server import MTGNPServer


PROJECT_ROOT = Path(__file__).resolve().parents[1]

ALICE_DECK = [
    "mountain_001",
    "mountain_002",
    "mountain_003",
    "mountain_004",
    "mountain_005",
    "mountain_006",
    "mountain_007",
    "mountain_008",
]
BOB_DECK = [
    "island_001",
    "island_002",
    "island_003",
    "island_004",
    "island_005",
    "island_006",
    "island_007",
    "island_008",
]


def wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class ServerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = MTGNPServer(
            host="127.0.0.1",
            port=0,
            data_directory=PROJECT_ROOT / "data",
        )
        self.server_thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.server_thread.start()
        self.assertTrue(self.server.wait_until_listening())
        self.clients: list[FramedConnection] = []

    def tearDown(self) -> None:
        for client in self.clients:
            client.close()
        self.server.stop()
        self.server_thread.join(timeout=2)

    def connect_client(self, label: str) -> FramedConnection:
        stream = socket.create_connection(("127.0.0.1", self.server.bound_port))
        stream.settimeout(2)
        client = FramedConnection(
            stream, local_sender=Sender.CLIENT, peer_label=label
        )
        self.clients.append(client)
        return client

    def connect_kept_game(
        self,
        alice_deck: list[str],
        bob_deck: list[str],
    ) -> tuple[
        dict[str, FramedConnection],
        str,
        str,
        dict[str, object],
    ]:
        """Connect two players and return the first Upkeep priority grant."""

        alice = self.connect_client("alice-resilience")
        bob = self.connect_client("bob-resilience")
        self.assertTrue(wait_for(lambda: self.server.active_connections == 2))
        clients = {"alice": alice, "bob": bob}
        alice.send(
            {
                "type": "PLAYER_READY",
                "seq_num": 1,
                "player_id": "alice",
                "deck_list": alice_deck,
            }
        )
        alice.receive()
        bob.receive()
        bob.send(
            {
                "type": "PLAYER_READY",
                "seq_num": 1,
                "player_id": "bob",
                "deck_list": bob_deck,
            }
        )
        alice.receive()
        bob.receive()
        setup = {"alice": alice.receive(), "bob": bob.receive()}
        for player_id, client in clients.items():
            client.send(
                {
                    "type": "MULLIGAN_CHOICE",
                    "seq_num": setup[player_id]["seq_num"],
                    "keep": True,
                    "cards_to_bottom": [],
                }
            )
        untap = {"alice": alice.receive(), "bob": bob.receive()}
        active = untap["alice"]["active_player"]
        other = "bob" if active == "alice" else "alice"
        self.assertEqual(untap["bob"]["active_player"], active)
        self.assertEqual(alice.receive()["type"], "GAME_STATE_UPDATE")
        self.assertEqual(bob.receive()["type"], "GAME_STATE_UPDATE")
        self.assertEqual(alice.receive()["to_phase"], "UPKEEP")
        self.assertEqual(bob.receive()["to_phase"], "UPKEEP")
        grant = clients[active].receive()
        self.assertEqual(grant["type"], "PRIORITY_GRANT")
        return clients, active, other, grant

    def test_two_player_lobby_third_refusal_errors_updates_and_ping(self) -> None:
        player_1 = self.connect_client("server-for-alice")
        player_2 = self.connect_client("server-for-bob")
        self.assertTrue(wait_for(lambda: self.server.active_connections == 2))

        third = socket.create_connection(("127.0.0.1", self.server.bound_port))
        third.settimeout(2)
        self.assertEqual(third.recv(1), b"")
        third.close()
        self.assertEqual(self.server.active_connections, 2)

        player_1.send(
            {
                "type": "PLAYER_READY",
                "seq_num": 1,
                "player_id": "alice",
                "deck_list": ["mountain_001", "lightning_bolt_001"],
            }
        )
        update_for_alice = player_1.receive()
        update_for_bob = player_2.receive()
        self.assertEqual(update_for_alice["type"], "GAME_STATE_UPDATE")
        self.assertEqual(update_for_bob["state"]["players_ready"], 1)
        self.assertEqual(update_for_bob["state"]["waiting_for"], ["seat_2"])

        player_2.send(
            {
                "type": "PLAYER_READY",
                "seq_num": 1,
                "player_id": "alice",
                "deck_list": ["island_001"],
            }
        )
        duplicate_error = player_2.receive()
        self.assertEqual(duplicate_error["type"], "ERROR")
        self.assertEqual(duplicate_error["code"], "DUPLICATE_ID")
        self.assertEqual(duplicate_error["seq_num"], 1)

        player_2.send(
            {
                "type": "PLAYER_READY",
                "seq_num": 2,
                "player_id": "bob",
                "deck_list": ["island_001", "counterspell_001"],
            }
        )
        final_for_alice = player_1.receive()
        final_for_bob = player_2.receive()
        self.assertEqual(final_for_alice["state"]["players_ready"], 2)
        self.assertEqual(final_for_bob["state"]["waiting_for"], [])
        self.assertEqual(final_for_bob["state"]["ready_players"], ["alice", "bob"])

        setup_for_alice = player_1.receive()
        setup_for_bob = player_2.receive()
        self.assertEqual(setup_for_alice["state"]["phase"], "MULLIGAN")
        self.assertEqual(setup_for_bob["state"]["phase"], "MULLIGAN")
        self.assertEqual(set(setup_for_alice["state"]["hand"]), {"alice"})
        self.assertEqual(set(setup_for_bob["state"]["hand"]), {"bob"})
        self.assertEqual(setup_for_alice["state"]["life_totals"], {"alice": 20, "bob": 20})

        player_1.send({"type": "PING", "seq_num": 2, "timestamp": 987654})
        pong = player_1.receive()
        self.assertEqual(
            pong,
            {"type": "PONG", "seq_num": 2, "timestamp": 987654},
        )

        player_1.send({"type": "PRIORITY_PASS", "seq_num": 3})
        wrong_phase = player_1.receive()
        self.assertEqual(wrong_phase["type"], "ERROR")
        self.assertEqual(wrong_phase["code"], "WRONG_PHASE")

        player_1.send(
            {
                "type": "MULLIGAN_CHOICE",
                "seq_num": 0,
                "keep": True,
                "cards_to_bottom": [],
            }
        )
        stale = player_1.receive()
        self.assertEqual(stale["type"], "ERROR")
        self.assertEqual(stale["code"], "STALE_ACTION")

        player_1.send(
            {
                "type": "MULLIGAN_CHOICE",
                "seq_num": setup_for_alice["seq_num"],
                "keep": True,
                "cards_to_bottom": [],
            }
        )
        player_2.send(
            {
                "type": "MULLIGAN_CHOICE",
                "seq_num": setup_for_bob["seq_num"],
                "keep": True,
                "cards_to_bottom": [],
            }
        )

        transition_for_alice = player_1.receive()
        transition_for_bob = player_2.receive()
        self.assertEqual(transition_for_alice["type"], "PHASE_TRANSITION")
        self.assertEqual(transition_for_alice["from_phase"], "MULLIGAN")
        self.assertEqual(transition_for_bob["to_phase"], "UNTAP")
        self.assertEqual(transition_for_bob["turn"], 1)

        in_game_for_alice = player_1.receive()
        in_game_for_bob = player_2.receive()
        self.assertEqual(in_game_for_alice["state"]["phase"], "UNTAP")
        self.assertEqual(in_game_for_bob["state"]["lifecycle_state"], "IN_GAME")
        self.assertEqual(set(in_game_for_alice["state"]["hand"]), {"alice"})
        self.assertEqual(set(in_game_for_bob["state"]["hand"]), {"bob"})

    def test_concede_rejects_spoofing_then_restarts_on_same_connections(self) -> None:
        clients, active, other, grant = self.connect_kept_game(
            ["mountain_001", "lightning_bolt_001"],
            ["island_001", "counterspell_001"],
        )
        clients[active].send(
            {
                "type": "CONCEDE",
                "seq_num": grant["seq_num"],
                "player_id": other,
            }
        )
        spoof_error = clients[active].receive()
        self.assertEqual(spoof_error["type"], "ERROR")
        self.assertEqual(spoof_error["code"], "ILLEGAL_ACTION")
        retry_grant = clients[active].receive()
        self.assertEqual(retry_grant["type"], "PRIORITY_GRANT")
        self.assertEqual(retry_grant["seq_num"], grant["seq_num"])

        clients[active].send(
            {
                "type": "CONCEDE",
                "seq_num": retry_grant["seq_num"],
                "player_id": active,
            }
        )
        game_over = {
            "alice": clients["alice"].receive(),
            "bob": clients["bob"].receive(),
        }
        for message in game_over.values():
            self.assertEqual(message["type"], "GAME_OVER")
            self.assertEqual(message["winner_id"], other)
            self.assertEqual(message["loser_id"], active)
            self.assertEqual(message["reason"], "CONCEDE")
        lobby = {
            "alice": clients["alice"].receive(),
            "bob": clients["bob"].receive(),
        }
        self.assertEqual(lobby["alice"]["state"]["phase"], "LOBBY")
        self.assertEqual(lobby["bob"]["state"]["players_ready"], 0)
        self.assertEqual(self.server.active_connections, 2)

        clients["alice"].send(
            {
                "type": "PLAYER_READY",
                "seq_num": 2,
                "player_id": "alice",
                "deck_list": ["mountain_001", "lightning_bolt_001"],
            }
        )
        clients["alice"].receive()
        clients["bob"].receive()
        clients["bob"].send(
            {
                "type": "PLAYER_READY",
                "seq_num": 2,
                "player_id": "bob",
                "deck_list": ["island_001", "counterspell_001"],
            }
        )
        clients["alice"].receive()
        clients["bob"].receive()
        restarted = {
            "alice": clients["alice"].receive(),
            "bob": clients["bob"].receive(),
        }
        self.assertEqual(restarted["alice"]["state"]["phase"], "MULLIGAN")
        self.assertEqual(restarted["bob"]["state"]["phase"], "MULLIGAN")

    def test_priority_timeout_declares_disconnect_and_keeps_winner_connected(
        self,
    ) -> None:
        self.server.priority_time_limit_ms = 75
        clients, active, other, grant = self.connect_kept_game(
            ["mountain_001"],
            ["island_001"],
        )
        self.assertEqual(grant["time_limit_ms"], 75)

        game_over = {
            "alice": clients["alice"].receive(),
            "bob": clients["bob"].receive(),
        }
        for message in game_over.values():
            self.assertEqual(message["type"], "GAME_OVER")
            self.assertEqual(message["winner_id"], other)
            self.assertEqual(message["loser_id"], active)
            self.assertEqual(message["reason"], "DISCONNECT")
        self.assertTrue(wait_for(lambda: self.server.active_connections == 1))

        clients[other].send(
            {"type": "PING", "seq_num": 99, "timestamp": 123456}
        )
        received_types: list[str] = []
        while "PONG" not in received_types:
            received_types.append(clients[other].receive()["type"])
        self.assertIn("PONG", received_types)

    def test_rejected_priority_action_reuses_current_token(self) -> None:
        decks = {
            "alice": ["mountain_001", "lightning_bolt_001"],
            "bob": ["island_001", "counterspell_001"],
        }
        clients, active, other, grant = self.connect_kept_game(
            decks["alice"], decks["bob"]
        )
        land_id = decks[active][0]
        clients[active].send(
            {
                "type": "PLAY_LAND",
                "seq_num": grant["seq_num"],
                "card_id": land_id,
            }
        )

        error = clients[active].receive()
        retry = clients[active].receive()
        self.assertEqual(error["type"], "ERROR")
        self.assertEqual(error["code"], "WRONG_PHASE")
        self.assertEqual(retry["type"], "PRIORITY_GRANT")
        self.assertEqual(retry["seq_num"], grant["seq_num"])
        self.assertEqual(
            self.server.game.priority_token_for_seat(
                self.server.game.priority_holder_seat_id
            ),
            grant["seq_num"],
        )

        clients[active].send(
            {"type": "PRIORITY_PASS", "seq_num": retry["seq_num"] - 1}
        )
        stale_error = clients[active].receive()
        stale_retry = clients[active].receive()
        self.assertEqual(stale_error["code"], "STALE_ACTION")
        self.assertEqual(stale_retry["type"], "PRIORITY_GRANT")
        self.assertEqual(stale_retry["seq_num"], retry["seq_num"])

        clients[active].send(
            {"type": "PRIORITY_PASS", "seq_num": stale_retry["seq_num"]}
        )
        next_grant = clients[other].receive()
        self.assertEqual(next_grant["type"], "PRIORITY_GRANT")
        self.assertGreater(next_grant["seq_num"], stale_retry["seq_num"])

    def test_unexpected_tcp_loss_awards_game_to_connected_player(self) -> None:
        self.server.reconnect_grace_seconds = 0.075
        clients, active, other, _ = self.connect_kept_game(
            ["mountain_001"],
            ["island_001"],
        )
        clients[active].close()

        game_over = clients[other].receive()
        self.assertEqual(game_over["type"], "GAME_OVER")
        self.assertEqual(game_over["winner_id"], other)
        self.assertEqual(game_over["loser_id"], active)
        self.assertEqual(game_over["reason"], "DISCONNECT")
        lobby = clients[other].receive()
        self.assertEqual(lobby["state"]["phase"], "LOBBY")
        self.assertEqual(lobby["state"]["players_connected"], 1)
        self.assertTrue(wait_for(lambda: self.server.active_connections == 1))

    def test_player_can_reconnect_during_game_and_resume_priority(self) -> None:
        decks = {
            "alice": ["mountain_001", "lightning_bolt_001"],
            "bob": ["island_001", "counterspell_001"],
        }
        clients, active, other, _ = self.connect_kept_game(
            decks["alice"], decks["bob"]
        )
        interrupted_game = self.server.game
        clients[active].close()
        self.assertTrue(wait_for(lambda: self.server.active_connections == 1))
        self.assertIs(self.server.game, interrupted_game)

        replacement = self.connect_client(f"{active}-reconnected")
        self.assertTrue(wait_for(lambda: self.server.active_connections == 2))
        replacement.send(
            {
                "type": "PLAYER_READY",
                "seq_num": 2,
                "player_id": active,
                "deck_list": decks[active],
            }
        )

        replacement_state = replacement.receive()
        remaining_state = clients[other].receive()
        self.assertEqual(replacement_state["type"], "GAME_STATE_UPDATE")
        self.assertEqual(remaining_state["type"], "GAME_STATE_UPDATE")
        self.assertEqual(
            replacement_state["state"]["lifecycle_state"], "IN_GAME"
        )
        self.assertIs(self.server.game, interrupted_game)

        resumed_grant = replacement.receive()
        self.assertEqual(resumed_grant["type"], "PRIORITY_GRANT")
        self.assertEqual(resumed_grant["player_id"], active)
        replacement.send(
            {"type": "PRIORITY_PASS", "seq_num": resumed_grant["seq_num"]}
        )
        opponent_grant = clients[other].receive()
        self.assertEqual(opponent_grant["type"], "PRIORITY_GRANT")
        self.assertEqual(opponent_grant["player_id"], other)

    def test_player_can_reconnect_during_mulligan_with_fresh_tokens(self) -> None:
        alice_deck = ["mountain_001", "lightning_bolt_001"]
        bob_deck = ["island_001", "counterspell_001"]
        alice = self.connect_client("alice-before-mulligan-reconnect")
        bob = self.connect_client("bob-mulligan-reconnect")
        self.assertTrue(wait_for(lambda: self.server.active_connections == 2))

        alice.send(
            {
                "type": "PLAYER_READY",
                "seq_num": 1,
                "player_id": "alice",
                "deck_list": alice_deck,
            }
        )
        alice.receive()
        bob.receive()
        bob.send(
            {
                "type": "PLAYER_READY",
                "seq_num": 1,
                "player_id": "bob",
                "deck_list": bob_deck,
            }
        )
        alice.receive()
        bob.receive()
        alice.receive()
        bob.receive()

        alice.close()
        self.assertTrue(wait_for(lambda: self.server.active_connections == 1))
        replacement = self.connect_client("alice-after-mulligan-reconnect")
        replacement.send(
            {
                "type": "PLAYER_READY",
                "seq_num": 2,
                "player_id": "alice",
                "deck_list": alice_deck,
            }
        )

        refreshed = {
            "alice": replacement.receive(),
            "bob": bob.receive(),
        }
        for player_id, client in {"alice": replacement, "bob": bob}.items():
            self.assertEqual(refreshed[player_id]["state"]["phase"], "MULLIGAN")
            client.send(
                {
                    "type": "MULLIGAN_CHOICE",
                    "seq_num": refreshed[player_id]["seq_num"],
                    "keep": True,
                    "cards_to_bottom": [],
                }
            )
        self.assertEqual(replacement.receive()["type"], "PHASE_TRANSITION")
        self.assertEqual(bob.receive()["type"], "PHASE_TRANSITION")

    def test_reconnect_rejects_changed_identity_without_ending_grace(self) -> None:
        decks = {
            "alice": ["mountain_001"],
            "bob": ["island_001"],
        }
        clients, active, _, _ = self.connect_kept_game(
            decks["alice"], decks["bob"]
        )
        clients[active].close()
        self.assertTrue(wait_for(lambda: self.server.active_connections == 1))

        impostor = self.connect_client("impostor")
        impostor.send(
            {
                "type": "PLAYER_READY",
                "seq_num": 2,
                "player_id": "not-the-original-player",
                "deck_list": decks[active],
            }
        )
        error = impostor.receive()
        self.assertEqual(error["type"], "ERROR")
        self.assertEqual(error["code"], "DUPLICATE_ID")
        self.assertTrue(wait_for(lambda: self.server.active_connections == 1))
        self.assertIsNotNone(self.server.game)

    def test_disconnected_seat_can_be_reused_by_a_new_connection(self) -> None:
        original = self.connect_client("original")
        remaining = self.connect_client("remaining")
        self.assertTrue(wait_for(lambda: self.server.active_connections == 2))

        original.close()
        self.assertTrue(wait_for(lambda: self.server.active_connections == 1))
        disconnect_update = remaining.receive()
        self.assertEqual(disconnect_update["state"]["players_connected"], 1)

        replacement = self.connect_client("replacement")
        self.assertTrue(wait_for(lambda: self.server.active_connections == 2))
        replacement.send(
            {
                "type": "PLAYER_READY",
                "seq_num": 1,
                "player_id": "replacement_player",
                "deck_list": ["forest_001"],
            }
        )
        update_for_remaining = remaining.receive()
        update_for_replacement = replacement.receive()
        self.assertEqual(update_for_remaining["state"]["players_ready"], 1)
        self.assertEqual(
            update_for_replacement["state"]["ready_players"],
            ["replacement_player"],
        )

    def test_pass_only_engine_completes_turn_one_and_opens_turn_two_upkeep(self) -> None:
        alice = self.connect_client("alice")
        bob = self.connect_client("bob")
        self.assertTrue(wait_for(lambda: self.server.active_connections == 2))
        clients = {"alice": alice, "bob": bob}

        alice.send(
            {
                "type": "PLAYER_READY",
                "seq_num": 1,
                "player_id": "alice",
                "deck_list": ALICE_DECK,
            }
        )
        alice.receive()
        bob.receive()
        bob.send(
            {
                "type": "PLAYER_READY",
                "seq_num": 1,
                "player_id": "bob",
                "deck_list": BOB_DECK,
            }
        )
        alice.receive()
        bob.receive()
        setup_alice = alice.receive()
        setup_bob = bob.receive()
        alice.send(
            {
                "type": "MULLIGAN_CHOICE",
                "seq_num": setup_alice["seq_num"],
                "keep": True,
                "cards_to_bottom": [],
            }
        )
        bob.send(
            {
                "type": "MULLIGAN_CHOICE",
                "seq_num": setup_bob["seq_num"],
                "keep": True,
                "cards_to_bottom": [],
            }
        )

        def broadcast(expected_type: str, *, to_phase: str | None = None):
            messages = {"alice": alice.receive(), "bob": bob.receive()}
            for message in messages.values():
                self.assertEqual(message["type"], expected_type)
                if to_phase is not None:
                    self.assertEqual(message["to_phase"], to_phase)
            return messages

        def pass_window(active_player: str) -> None:
            other = "bob" if active_player == "alice" else "alice"
            active_grant = clients[active_player].receive()
            self.assertEqual(active_grant["type"], "PRIORITY_GRANT")
            self.assertEqual(active_grant["player_id"], active_player)
            clients[active_player].send(
                {"type": "PRIORITY_PASS", "seq_num": active_grant["seq_num"]}
            )
            other_grant = clients[other].receive()
            self.assertEqual(other_grant["player_id"], other)
            clients[other].send(
                {"type": "PRIORITY_PASS", "seq_num": other_grant["seq_num"]}
            )

        untap = broadcast("PHASE_TRANSITION", to_phase="UNTAP")
        active_player = untap["alice"]["active_player"]
        broadcast("GAME_STATE_UPDATE")
        broadcast("PHASE_TRANSITION", to_phase="UPKEEP")
        pass_window(active_player)

        broadcast("PHASE_TRANSITION", to_phase="DRAW")
        draw_states = broadcast("GAME_STATE_UPDATE")
        self.assertEqual(draw_states[active_player]["state"]["hand_counts"][active_player], 7)
        pass_window(active_player)

        broadcast("PHASE_TRANSITION", to_phase="PRECOMBAT_MAIN")
        pass_window(active_player)
        broadcast("PHASE_TRANSITION", to_phase="BEGIN_COMBAT")
        pass_window(active_player)

        attacker_requests = broadcast(
            "PHASE_TRANSITION", to_phase="DECLARE_ATTACKERS"
        )
        clients[active_player].send(
            {
                "type": "DECLARE_ATTACKERS",
                "seq_num": attacker_requests[active_player]["seq_num"],
                "attackers": [],
            }
        )
        broadcast("PHASE_TRANSITION", to_phase="END_OF_COMBAT")
        pass_window(active_player)

        broadcast("PHASE_TRANSITION", to_phase="POSTCOMBAT_MAIN")
        pass_window(active_player)
        broadcast("PHASE_TRANSITION", to_phase="END_STEP")
        pass_window(active_player)

        broadcast("PHASE_TRANSITION", to_phase="CLEANUP")
        broadcast("GAME_STATE_UPDATE")
        turn_two_untap = broadcast("PHASE_TRANSITION", to_phase="UNTAP")
        next_active = turn_two_untap["alice"]["active_player"]
        self.assertNotEqual(next_active, active_player)
        self.assertEqual(turn_two_untap["alice"]["turn"], 2)
        broadcast("GAME_STATE_UPDATE")
        broadcast("PHASE_TRANSITION", to_phase="UPKEEP")
        turn_two_grant = clients[next_active].receive()
        self.assertEqual(turn_two_grant["type"], "PRIORITY_GRANT")
        self.assertEqual(turn_two_grant["player_id"], next_active)

    def test_land_and_lightning_bolt_flow_through_the_network_stack(self) -> None:
        alice = self.connect_client("alice-actions")
        bob = self.connect_client("bob-actions")
        self.assertTrue(wait_for(lambda: self.server.active_connections == 2))
        clients = {"alice": alice, "bob": bob}
        decks = {
            "alice": ["mountain_001", "lightning_bolt_001"],
            "bob": ["mountain_002", "lightning_bolt_002"],
        }

        alice.send(
            {
                "type": "PLAYER_READY",
                "seq_num": 1,
                "player_id": "alice",
                "deck_list": decks["alice"],
            }
        )
        alice.receive()
        bob.receive()
        bob.send(
            {
                "type": "PLAYER_READY",
                "seq_num": 1,
                "player_id": "bob",
                "deck_list": decks["bob"],
            }
        )
        alice.receive()
        bob.receive()
        setup = {"alice": alice.receive(), "bob": bob.receive()}
        for player_id, client in clients.items():
            client.send(
                {
                    "type": "MULLIGAN_CHOICE",
                    "seq_num": setup[player_id]["seq_num"],
                    "keep": True,
                    "cards_to_bottom": [],
                }
            )

        def receive_broadcast(expected_type: str):
            messages = {"alice": alice.receive(), "bob": bob.receive()}
            for message in messages.values():
                self.assertEqual(message["type"], expected_type)
            return messages

        untap = receive_broadcast("PHASE_TRANSITION")
        active = untap["alice"]["active_player"]
        other = "bob" if active == "alice" else "alice"
        receive_broadcast("GAME_STATE_UPDATE")
        receive_broadcast("PHASE_TRANSITION")

        upkeep_grant = clients[active].receive()
        clients[active].send(
            {"type": "PRIORITY_PASS", "seq_num": upkeep_grant["seq_num"]}
        )
        response_grant = clients[other].receive()
        clients[other].send(
            {"type": "PRIORITY_PASS", "seq_num": response_grant["seq_num"]}
        )

        draw_transition = receive_broadcast("PHASE_TRANSITION")
        self.assertEqual(draw_transition["alice"]["to_phase"], "DRAW")
        receive_broadcast("GAME_STATE_UPDATE")
        draw_grant = clients[active].receive()
        clients[active].send(
            {"type": "PRIORITY_PASS", "seq_num": draw_grant["seq_num"]}
        )
        response_grant = clients[other].receive()
        clients[other].send(
            {"type": "PRIORITY_PASS", "seq_num": response_grant["seq_num"]}
        )

        main_transition = receive_broadcast("PHASE_TRANSITION")
        self.assertEqual(main_transition["alice"]["to_phase"], "PRECOMBAT_MAIN")
        main_grant = clients[active].receive()
        land_id = decks[active][0]
        bolt_id = decks[active][1]
        clients[active].send(
            {
                "type": "PLAY_LAND",
                "seq_num": main_grant["seq_num"],
                "card_id": land_id,
            }
        )
        land_states = receive_broadcast("GAME_STATE_UPDATE")
        self.assertTrue(land_states[active]["state"]["land_played_this_turn"])
        self.assertEqual(
            land_states[active]["state"]["battlefield"][active],
            [{"id": land_id, "tapped": False}],
        )

        retained_grant = clients[active].receive()
        clients[active].send(
            {
                "type": "CAST_SPELL",
                "seq_num": retained_grant["seq_num"],
                "card_id": bolt_id,
                "targets": [other],
                "mana_payment": {"R": 1},
            }
        )
        pushed = receive_broadcast("STACK_PUSH")
        stack_id = pushed[active]["stack_item_id"]
        self.assertEqual(pushed[active]["source"], bolt_id)
        cast_states = receive_broadcast("GAME_STATE_UPDATE")
        self.assertTrue(
            cast_states[active]["state"]["battlefield"][active][0]["tapped"]
        )
        self.assertEqual(
            cast_states[active]["state"]["stack"][-1]["stack_item_id"],
            stack_id,
        )

        cast_grant = clients[active].receive()
        clients[active].send(
            {"type": "PRIORITY_PASS", "seq_num": cast_grant["seq_num"]}
        )
        response_grant = clients[other].receive()
        clients[other].send(
            {"type": "PRIORITY_PASS", "seq_num": response_grant["seq_num"]}
        )
        resolved = receive_broadcast("STACK_RESOLVE")
        self.assertEqual(resolved[active]["stack_item_id"], stack_id)
        self.assertEqual(resolved[active]["result"], "RESOLVED")
        final_states = receive_broadcast("GAME_STATE_UPDATE")
        self.assertEqual(final_states[active]["state"]["life_totals"][other], 17)
        self.assertEqual(final_states[active]["state"]["stack"], [])
        post_resolution_grant = clients[active].receive()
        self.assertEqual(post_resolution_grant["player_id"], active)

    def test_activated_ability_flows_through_server_and_stack(self) -> None:
        alice = self.connect_client("alice-ability")
        bob = self.connect_client("bob-ability")
        self.assertTrue(wait_for(lambda: self.server.active_connections == 2))
        clients = {"alice": alice, "bob": bob}
        decks = {
            "alice": [
                "prodigal_sorcerer_001",
                *[f"island_{number:03d}" for number in range(1, 8)],
            ],
            "bob": [
                "prodigal_sorcerer_002",
                *[f"swamp_{number:03d}" for number in range(1, 8)],
            ],
        }

        alice.send(
            {
                "type": "PLAYER_READY",
                "seq_num": 1,
                "player_id": "alice",
                "deck_list": decks["alice"],
            }
        )
        alice.receive()
        bob.receive()
        bob.send(
            {
                "type": "PLAYER_READY",
                "seq_num": 1,
                "player_id": "bob",
                "deck_list": decks["bob"],
            }
        )
        alice.receive()
        bob.receive()
        setup = {"alice": alice.receive(), "bob": bob.receive()}
        for player_id, client in clients.items():
            client.send(
                {
                    "type": "MULLIGAN_CHOICE",
                    "seq_num": setup[player_id]["seq_num"],
                    "keep": True,
                    "cards_to_bottom": [],
                }
            )

        def receive_broadcast(expected_type: str):
            messages = {"alice": alice.receive(), "bob": bob.receive()}
            for message in messages.values():
                self.assertEqual(message["type"], expected_type)
            return messages

        untap = receive_broadcast("PHASE_TRANSITION")
        active = untap["alice"]["active_player"]
        other = "bob" if active == "alice" else "alice"
        source_id = (
            "prodigal_sorcerer_001"
            if active == "alice"
            else "prodigal_sorcerer_002"
        )
        receive_broadcast("GAME_STATE_UPDATE")
        receive_broadcast("PHASE_TRANSITION")
        upkeep_grant = clients[active].receive()

        active_seat = next(
            seat_id
            for seat_id, player in self.server.game.players.items()
            if player.player_id == active
        )
        active_state = self.server.game.players[active_seat]
        if source_id in active_state.hand:
            active_state.hand.remove(source_id)
        else:
            active_state.library.remove(source_id)
        definition = self.server.catalog.definition_for_instance(source_id)
        active_state.battlefield.append(
            {
                "id": source_id,
                "tapped": False,
                "damage": 0,
                "power": definition.power,
                "toughness": definition.toughness,
                "summoning_sick": False,
            }
        )

        clients[active].send(
            {
                "type": "ACTIVATE_ABILITY",
                "seq_num": upkeep_grant["seq_num"],
                "source_id": source_id,
                "ability_index": 0,
                "targets": [other],
                "cost_payment": {"tap": True, "mana": {}},
            }
        )
        pushes = receive_broadcast("STACK_PUSH")
        stack_id = pushes[active]["stack_item_id"]
        self.assertEqual(pushes[active]["item_type"], "ABILITY")
        states = receive_broadcast("GAME_STATE_UPDATE")
        source_state = next(
            permanent
            for permanent in states[active]["state"]["battlefield"][active]
            if permanent["id"] == source_id
        )
        self.assertTrue(source_state["tapped"])

        retained_grant = clients[active].receive()
        clients[active].send(
            {"type": "PRIORITY_PASS", "seq_num": retained_grant["seq_num"]}
        )
        response_grant = clients[other].receive()
        clients[other].send(
            {"type": "PRIORITY_PASS", "seq_num": response_grant["seq_num"]}
        )
        resolves = receive_broadcast("STACK_RESOLVE")
        self.assertEqual(resolves[active]["stack_item_id"], stack_id)
        self.assertEqual(resolves[active]["result"], "RESOLVED")
        final_states = receive_broadcast("GAME_STATE_UPDATE")
        self.assertEqual(final_states[active]["state"]["life_totals"][other], 19)
        self.assertEqual(final_states[active]["state"]["stack"], [])
        self.assertEqual(clients[active].receive()["type"], "PRIORITY_GRANT")

    def test_simultaneous_triggers_request_order_then_join_the_network_stack(
        self,
    ) -> None:
        alice = self.connect_client("alice-triggers")
        bob = self.connect_client("bob-triggers")
        self.assertTrue(wait_for(lambda: self.server.active_connections == 2))
        clients = {"alice": alice, "bob": bob}
        decks = {
            "alice": [
                "monastery_swiftspear_001",
                "monastery_swiftspear_002",
                "mountain_001",
                "lightning_bolt_001",
            ],
            "bob": [
                "monastery_swiftspear_003",
                "monastery_swiftspear_004",
                "mountain_002",
                "lightning_bolt_002",
            ],
        }
        alice.send(
            {
                "type": "PLAYER_READY",
                "seq_num": 1,
                "player_id": "alice",
                "deck_list": decks["alice"],
            }
        )
        alice.receive()
        bob.receive()
        bob.send(
            {
                "type": "PLAYER_READY",
                "seq_num": 1,
                "player_id": "bob",
                "deck_list": decks["bob"],
            }
        )
        alice.receive()
        bob.receive()
        setup = {"alice": alice.receive(), "bob": bob.receive()}
        for player_id, client in clients.items():
            client.send(
                {
                    "type": "MULLIGAN_CHOICE",
                    "seq_num": setup[player_id]["seq_num"],
                    "keep": True,
                    "cards_to_bottom": [],
                }
            )

        def receive_broadcast(expected_type: str):
            messages = {"alice": alice.receive(), "bob": bob.receive()}
            for message in messages.values():
                self.assertEqual(message["type"], expected_type)
            return messages

        untap = receive_broadcast("PHASE_TRANSITION")
        active = untap["alice"]["active_player"]
        other = "bob" if active == "alice" else "alice"
        active_seat = next(
            seat_id
            for seat_id, player in self.server.game.players.items()
            if player.player_id == active
        )
        swiftspears = decks[active][:2]
        land_id = decks[active][2]
        bolt_id = decks[active][3]
        active_state = self.server.game.players[active_seat]
        for card_id in (*swiftspears, land_id):
            active_state.hand.remove(card_id)
            definition = self.server.catalog.definition_for_instance(card_id)
            permanent = {"id": card_id, "tapped": False}
            if definition.card_type == "Creature":
                permanent.update(
                    {
                        "damage": 0,
                        "power": definition.power,
                        "toughness": definition.toughness,
                        "summoning_sick": False,
                    }
                )
            active_state.battlefield.append(permanent)

        receive_broadcast("GAME_STATE_UPDATE")
        receive_broadcast("PHASE_TRANSITION")
        upkeep_grant = clients[active].receive()
        clients[active].send(
            {
                "type": "CAST_SPELL",
                "seq_num": upkeep_grant["seq_num"],
                "card_id": bolt_id,
                "targets": [other],
                "mana_payment": {"R": 1},
            }
        )
        original_push = receive_broadcast("STACK_PUSH")
        self.assertEqual(original_push[active]["item_type"], "SPELL")
        receive_broadcast("GAME_STATE_UPDATE")

        order_request = clients[active].receive()
        self.assertEqual(order_request["type"], "TRIGGER_ORDER")
        self.assertEqual(len(order_request["trigger_ids"]), 2)
        requested_order = list(reversed(order_request["trigger_ids"]))
        clients[active].send(
            {
                "type": "TRIGGER_ORDER_RESPONSE",
                "seq_num": order_request["seq_num"],
                "ordered_trigger_ids": requested_order,
            }
        )
        first_trigger_push = receive_broadcast("STACK_PUSH")
        second_trigger_push = receive_broadcast("STACK_PUSH")
        self.assertEqual(first_trigger_push[active]["item_type"], "TRIGGER_ABILITY")
        self.assertEqual(second_trigger_push[active]["item_type"], "TRIGGER_ABILITY")
        trigger_state = receive_broadcast("GAME_STATE_UPDATE")
        self.assertEqual(
            [item["item_type"] for item in trigger_state[active]["state"]["stack"]],
            ["SPELL", "TRIGGER_ABILITY", "TRIGGER_ABILITY"],
        )
        self.assertEqual(clients[active].receive()["type"], "PRIORITY_GRANT")

    def test_attack_block_and_combat_damage_flow_across_both_clients(self) -> None:
        alice = self.connect_client("alice-combat")
        bob = self.connect_client("bob-combat")
        self.assertTrue(wait_for(lambda: self.server.active_connections == 2))
        clients = {"alice": alice, "bob": bob}
        decks = {
            "alice": [
                "grizzly_bears_001",
                "savannah_lions_001",
                *[f"mountain_{number:03d}" for number in range(1, 7)],
            ],
            "bob": [
                "grizzly_bears_002",
                "savannah_lions_002",
                *[f"island_{number:03d}" for number in range(1, 7)],
            ],
        }
        alice.send(
            {
                "type": "PLAYER_READY",
                "seq_num": 1,
                "player_id": "alice",
                "deck_list": decks["alice"],
            }
        )
        alice.receive()
        bob.receive()
        bob.send(
            {
                "type": "PLAYER_READY",
                "seq_num": 1,
                "player_id": "bob",
                "deck_list": decks["bob"],
            }
        )
        alice.receive()
        bob.receive()
        setup = {"alice": alice.receive(), "bob": bob.receive()}
        for player_id, client in clients.items():
            client.send(
                {
                    "type": "MULLIGAN_CHOICE",
                    "seq_num": setup[player_id]["seq_num"],
                    "keep": True,
                    "cards_to_bottom": [],
                }
            )

        def receive_broadcast(expected_type: str):
            messages = {"alice": alice.receive(), "bob": bob.receive()}
            for message in messages.values():
                self.assertEqual(message["type"], expected_type)
            return messages

        def pass_window(active_player: str) -> None:
            other_player = "bob" if active_player == "alice" else "alice"
            active_grant = clients[active_player].receive()
            self.assertEqual(active_grant["type"], "PRIORITY_GRANT")
            clients[active_player].send(
                {"type": "PRIORITY_PASS", "seq_num": active_grant["seq_num"]}
            )
            other_grant = clients[other_player].receive()
            self.assertEqual(other_grant["type"], "PRIORITY_GRANT")
            clients[other_player].send(
                {"type": "PRIORITY_PASS", "seq_num": other_grant["seq_num"]}
            )

        untap = receive_broadcast("PHASE_TRANSITION")
        active = untap["alice"]["active_player"]
        defender = "bob" if active == "alice" else "alice"
        active_seat = next(
            seat_id
            for seat_id, player in self.server.game.players.items()
            if player.player_id == active
        )
        defending_seat = self.server.game.opposing_seat(active_seat)
        active_grizzly = (
            "grizzly_bears_001" if active == "alice" else "grizzly_bears_002"
        )
        active_lion = (
            "savannah_lions_001" if active == "alice" else "savannah_lions_002"
        )
        defending_grizzly = (
            "grizzly_bears_002" if active == "alice" else "grizzly_bears_001"
        )

        def move_creature_to_battlefield(seat_id: str, card_id: str) -> None:
            player = self.server.game.players[seat_id]
            if card_id in player.hand:
                player.hand.remove(card_id)
            else:
                player.library.remove(card_id)
            definition = self.server.catalog.definition_for_instance(card_id)
            player.battlefield.append(
                {
                    "id": card_id,
                    "tapped": False,
                    "damage": 0,
                    "power": definition.power,
                    "toughness": definition.toughness,
                    "summoning_sick": False,
                }
            )

        move_creature_to_battlefield(active_seat, active_grizzly)
        move_creature_to_battlefield(active_seat, active_lion)
        move_creature_to_battlefield(defending_seat, defending_grizzly)

        receive_broadcast("GAME_STATE_UPDATE")
        receive_broadcast("PHASE_TRANSITION")
        pass_window(active)
        receive_broadcast("PHASE_TRANSITION")
        receive_broadcast("GAME_STATE_UPDATE")
        pass_window(active)
        receive_broadcast("PHASE_TRANSITION")
        pass_window(active)
        receive_broadcast("PHASE_TRANSITION")
        pass_window(active)

        attacker_requests = receive_broadcast("PHASE_TRANSITION")
        clients[active].send(
            {
                "type": "DECLARE_ATTACKERS",
                "seq_num": attacker_requests[active]["seq_num"],
                "attackers": [
                    {"creature_id": active_grizzly, "target": defender},
                    {"creature_id": active_lion, "target": defender},
                ],
            }
        )
        attacker_states = receive_broadcast("GAME_STATE_UPDATE")
        active_battlefield = attacker_states[active]["state"]["battlefield"][active]
        self.assertTrue(
            next(card for card in active_battlefield if card["id"] == active_grizzly)[
                "tapped"
            ]
        )
        pass_window(active)

        blocker_requests = receive_broadcast("PHASE_TRANSITION")
        clients[defender].send(
            {
                "type": "DECLARE_BLOCKERS",
                "seq_num": blocker_requests[defender]["seq_num"],
                "blockers": [
                    {
                        "creature_id": defending_grizzly,
                        "blocking_id": active_grizzly,
                    }
                ],
            }
        )
        blocker_states = receive_broadcast("GAME_STATE_UPDATE")
        self.assertEqual(
            blocker_states[defender]["state"]["combat"]["blockers"][0],
            {
                "creature_id": defending_grizzly,
                "blocking_id": active_grizzly,
            },
        )
        pass_window(active)

        damage_transition = receive_broadcast("PHASE_TRANSITION")
        self.assertEqual(damage_transition[active]["to_phase"], "COMBAT_DAMAGE")
        damage_result = receive_broadcast("COMBAT_DAMAGE_RESULT")
        self.assertEqual(damage_result[active]["life_totals"][defender], 18)
        self.assertEqual(
            set(damage_result[active]["creatures_died"]),
            {active_grizzly, defending_grizzly},
        )
        receive_broadcast("GAME_STATE_UPDATE")
        end_transition = receive_broadcast("PHASE_TRANSITION")
        self.assertEqual(end_transition[active]["to_phase"], "END_OF_COMBAT")
        end_grant = clients[active].receive()
        self.assertEqual(end_grant["type"], "PRIORITY_GRANT")


if __name__ == "__main__":
    unittest.main()
