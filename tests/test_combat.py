from __future__ import annotations

import random
import unittest
from pathlib import Path

from mtgnp.catalog import load_catalog
from mtgnp.game import GameRuleError, GameSession, MulliganOutcome
from mtgnp.lobby import ReadyPlayer
from mtgnp.protocol import ErrorCode, LifecycleState, TurnStep


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG = load_catalog(PROJECT_ROOT / "data")


def combat_game(
    alice_cards: tuple[str, ...], bob_cards: tuple[str, ...]
) -> GameSession:
    game = GameSession(
        (
            ReadyPlayer("seat_1", "alice", alice_cards),
            ReadyPlayer("seat_2", "bob", bob_cards),
        ),
        catalog=CATALOG,
        random_source=random.Random(2),
    )
    game.record_mulligan_request("seat_1", 1)
    game.record_mulligan_request("seat_2", 2)
    game.process_mulligan("seat_1", seq_num=1, keep=True, cards_to_bottom=())
    outcome = game.process_mulligan(
        "seat_2", seq_num=2, keep=True, cards_to_bottom=()
    )
    if outcome != MulliganOutcome.ALL_PLAYERS_KEPT:
        raise AssertionError("Test setup did not enter IN_GAME.")
    game.active_seat_id = "seat_1"
    game.current_step = TurnStep.DECLARE_ATTACKERS
    return game


def put_creature(
    game: GameSession,
    seat_id: str,
    card_id: str,
    *,
    tapped: bool = False,
    summoning_sick: bool = False,
) -> dict[str, object]:
    player = game.players[seat_id]
    player.hand.remove(card_id)
    definition = CATALOG.definition_for_instance(card_id)
    permanent: dict[str, object] = {
        "id": card_id,
        "tapped": tapped,
        "damage": 0,
        "power": definition.power,
        "toughness": definition.toughness,
        "summoning_sick": summoning_sick,
    }
    player.battlefield.append(permanent)
    return permanent


def declare_attack(
    game: GameSession, *attacker_ids: str, seq_num: int = 10
) -> bool:
    game.record_attackers_request("seat_1", seq_num)
    return game.process_declare_attackers(
        "seat_1",
        seq_num=seq_num,
        attackers=[
            {"creature_id": attacker_id, "target": "bob"}
            for attacker_id in attacker_ids
        ],
    )


def declare_blocks(
    game: GameSession,
    assignments: list[tuple[str, str]],
    *,
    seq_num: int = 20,
) -> None:
    game.current_step = TurnStep.DECLARE_BLOCKERS
    game.record_blockers_request("seat_2", seq_num)
    game.process_declare_blockers(
        "seat_2",
        seq_num=seq_num,
        blockers=[
            {"creature_id": blocker_id, "blocking_id": attacker_id}
            for blocker_id, attacker_id in assignments
        ],
    )


class CombatTests(unittest.TestCase):
    def test_attackers_tap_atomically_while_vigilance_does_not(self) -> None:
        game = combat_game(
            ("grizzly_bears_001", "serra_angel_001"),
            ("island_001",),
        )
        bear = put_creature(game, "seat_1", "grizzly_bears_001")
        angel = put_creature(game, "seat_1", "serra_angel_001")
        self.assertTrue(declare_attack(game, "grizzly_bears_001", "serra_angel_001"))
        self.assertTrue(bear["tapped"])
        self.assertFalse(angel["tapped"])
        self.assertTrue(bear["attacking"])
        self.assertEqual(
            game.visible_state("seat_1")["combat"]["attackers"],
            [
                {"creature_id": "grizzly_bears_001", "target": "bob"},
                {"creature_id": "serra_angel_001", "target": "bob"},
            ],
        )

    def test_illegal_attacker_keeps_every_creature_unchanged(self) -> None:
        game = combat_game(
            ("grizzly_bears_001", "wall_of_stone_001"),
            ("island_001",),
        )
        bear = put_creature(game, "seat_1", "grizzly_bears_001")
        wall = put_creature(game, "seat_1", "wall_of_stone_001")
        game.record_attackers_request("seat_1", 10)
        with self.assertRaises(GameRuleError) as caught:
            game.process_declare_attackers(
                "seat_1",
                seq_num=10,
                attackers=[
                    {"creature_id": "grizzly_bears_001", "target": "bob"},
                    {"creature_id": "wall_of_stone_001", "target": "bob"},
                ],
            )
        self.assertEqual(caught.exception.code, ErrorCode.ILLEGAL_ACTION)
        self.assertFalse(bear["tapped"])
        self.assertFalse(wall["tapped"])
        self.assertEqual(game.combat_attackers, {})

    def test_summoning_sick_and_tapped_creatures_cannot_attack(self) -> None:
        for tapped, sick in ((True, False), (False, True)):
            with self.subTest(tapped=tapped, summoning_sick=sick):
                game = combat_game(("grizzly_bears_001",), ("island_001",))
                put_creature(
                    game,
                    "seat_1",
                    "grizzly_bears_001",
                    tapped=tapped,
                    summoning_sick=sick,
                )
                game.record_attackers_request("seat_1", 10)
                with self.assertRaises(GameRuleError):
                    game.process_declare_attackers(
                        "seat_1",
                        seq_num=10,
                        attackers=[
                            {"creature_id": "grizzly_bears_001", "target": "bob"}
                        ],
                    )

    def test_flying_attacker_requires_a_flying_blocker(self) -> None:
        game = combat_game(
            ("air_elemental_001",),
            ("grizzly_bears_001", "ornithopter_001"),
        )
        put_creature(game, "seat_1", "air_elemental_001")
        put_creature(game, "seat_2", "grizzly_bears_001")
        put_creature(game, "seat_2", "ornithopter_001")
        declare_attack(game, "air_elemental_001")
        game.current_step = TurnStep.DECLARE_BLOCKERS
        game.record_blockers_request("seat_2", 20)
        with self.assertRaises(GameRuleError):
            game.process_declare_blockers(
                "seat_2",
                seq_num=20,
                blockers=[
                    {
                        "creature_id": "grizzly_bears_001",
                        "blocking_id": "air_elemental_001",
                    }
                ],
            )
        game.process_declare_blockers(
            "seat_2",
            seq_num=20,
            blockers=[
                {
                    "creature_id": "ornithopter_001",
                    "blocking_id": "air_elemental_001",
                }
            ],
        )
        self.assertEqual(
            game.combat_blockers["air_elemental_001"], ["ornithopter_001"]
        )

    def test_unblocked_creature_deals_damage_to_defending_player(self) -> None:
        game = combat_game(("grizzly_bears_001",), ("island_001",))
        put_creature(game, "seat_1", "grizzly_bears_001")
        declare_attack(game, "grizzly_bears_001")
        declare_blocks(game, [])
        game.current_step = TurnStep.COMBAT_DAMAGE
        result = game.resolve_combat_damage(first_strike=False)
        self.assertEqual(game.players["seat_2"].life, 18)
        self.assertEqual(
            result.damage_events,
            ({"source": "grizzly_bears_001", "target": "bob", "amount": 2},),
        )

    def test_blocked_creatures_deal_damage_simultaneously_and_trade(self) -> None:
        game = combat_game(("grizzly_bears_001",), ("grizzly_bears_002",))
        put_creature(game, "seat_1", "grizzly_bears_001")
        put_creature(game, "seat_2", "grizzly_bears_002")
        declare_attack(game, "grizzly_bears_001")
        declare_blocks(game, [("grizzly_bears_002", "grizzly_bears_001")])
        game.current_step = TurnStep.COMBAT_DAMAGE
        result = game.resolve_combat_damage(first_strike=False)
        self.assertEqual(
            set(result.creatures_died),
            {"grizzly_bears_001", "grizzly_bears_002"},
        )
        self.assertEqual(game.players["seat_1"].battlefield, [])
        self.assertEqual(game.players["seat_2"].battlefield, [])

    def test_first_strike_creature_kills_blocker_before_regular_damage(self) -> None:
        game = combat_game(("white_knight_001",), ("grizzly_bears_001",))
        knight = put_creature(game, "seat_1", "white_knight_001")
        put_creature(game, "seat_2", "grizzly_bears_001")
        declare_attack(game, "white_knight_001")
        declare_blocks(game, [("grizzly_bears_001", "white_knight_001")])
        self.assertEqual(game.next_combat_damage_step(), TurnStep.FIRST_STRIKE_DAMAGE)

        game.current_step = TurnStep.FIRST_STRIKE_DAMAGE
        first = game.resolve_combat_damage(first_strike=True)
        self.assertEqual(first.creatures_died, ("grizzly_bears_001",))
        self.assertEqual(knight["damage"], 0)

        game.current_step = TurnStep.COMBAT_DAMAGE
        regular = game.resolve_combat_damage(first_strike=False)
        self.assertEqual(regular.damage_events, ())
        self.assertIn(knight, game.players["seat_1"].battlefield)

    def test_multi_blocker_order_assigns_lethal_before_next_blocker(self) -> None:
        game = combat_game(
            ("reckless_wurm_001",),
            ("savannah_lions_001", "grizzly_bears_001"),
        )
        put_creature(game, "seat_1", "reckless_wurm_001")
        put_creature(game, "seat_2", "savannah_lions_001")
        put_creature(game, "seat_2", "grizzly_bears_001")
        declare_attack(game, "reckless_wurm_001")
        declare_blocks(
            game,
            [
                ("savannah_lions_001", "reckless_wurm_001"),
                ("grizzly_bears_001", "reckless_wurm_001"),
            ],
        )
        self.assertTrue(game.combat_requires_damage_order())
        game.current_step = TurnStep.ASSIGN_DAMAGE_ORDER
        game.record_damage_order_request("seat_1", 30)
        self.assertTrue(
            game.process_damage_order(
                "seat_1",
                seq_num=30,
                attacker_id="reckless_wurm_001",
                blocker_order=("savannah_lions_001", "grizzly_bears_001"),
            )
        )
        game.current_step = TurnStep.COMBAT_DAMAGE
        result = game.resolve_combat_damage(first_strike=False)
        attacker_events = [
            event for event in result.damage_events if event["source"] == "reckless_wurm_001"
        ]
        self.assertEqual(
            attacker_events,
            [
                {
                    "source": "reckless_wurm_001",
                    "target": "savannah_lions_001",
                    "amount": 1,
                },
                {
                    "source": "reckless_wurm_001",
                    "target": "grizzly_bears_001",
                    "amount": 3,
                },
            ],
        )
        self.assertEqual(game.players["seat_2"].life, 20)

    def test_lethal_unblocked_combat_damage_ends_the_game(self) -> None:
        game = combat_game(("leatherback_baloth_001",), ("island_001",))
        put_creature(game, "seat_1", "leatherback_baloth_001")
        declare_attack(game, "leatherback_baloth_001")
        declare_blocks(game, [])
        game.players["seat_2"].life = 4
        game.current_step = TurnStep.COMBAT_DAMAGE
        game.resolve_combat_damage(first_strike=False)
        self.assertEqual(game.lifecycle_state, LifecycleState.GAME_OVER)
        self.assertEqual(game.game_over_reason, "LIFE_ZERO")
        self.assertEqual(game.winner_seat_id, "seat_1")


if __name__ == "__main__":
    unittest.main()
