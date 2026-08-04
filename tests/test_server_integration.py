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
