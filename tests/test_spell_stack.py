from __future__ import annotations

import random
import unittest
from pathlib import Path

from mtgnp.catalog import load_catalog
from mtgnp.game import (
    GameRuleError,
    GameSession,
    MulliganOutcome,
    PriorityPassOutcome,
    StackResolutionOutcome,
)
from mtgnp.lobby import ReadyPlayer
from mtgnp.protocol import ErrorCode, LifecycleState, TurnStep


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG = load_catalog(PROJECT_ROOT / "data")


def ready_game(
    alice_deck: tuple[str, ...], bob_deck: tuple[str, ...]
) -> GameSession:
    game = GameSession(
        (
            ReadyPlayer("seat_1", "alice", alice_deck),
            ReadyPlayer("seat_2", "bob", bob_deck),
        ),
        catalog=CATALOG,
        random_source=random.Random(1),
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
    game.current_step = TurnStep.PRECOMBAT_MAIN
    game.open_priority_window()
    return game


def put_land_on_battlefield(game: GameSession, seat_id: str, card_id: str) -> None:
    player = game.players[seat_id]
    player.hand.remove(card_id)
    player.battlefield.append({"id": card_id, "tapped": False})


def put_creature_on_battlefield(
    game: GameSession, seat_id: str, card_id: str
) -> dict[str, object]:
    player = game.players[seat_id]
    player.hand.remove(card_id)
    definition = CATALOG.definition_for_instance(card_id)
    permanent: dict[str, object] = {
        "id": card_id,
        "tapped": False,
        "damage": 0,
        "power": definition.power,
        "toughness": definition.toughness,
        "summoning_sick": False,
    }
    player.battlefield.append(permanent)
    return permanent


def cast_then_pass_twice(
    game: GameSession,
    seat_id: str,
    card_id: str,
    targets: tuple[str, ...],
    mana_payment: dict[str, int],
    *,
    first_seq: int = 10,
):
    game.record_priority_grant(seat_id, first_seq)
    item = game.process_cast_spell(
        seat_id,
        seq_num=first_seq,
        card_id=card_id,
        targets=targets,
        mana_payment=mana_payment,
    )
    game.record_priority_grant(seat_id, first_seq + 1)
    first_pass = game.process_priority_pass(seat_id, seq_num=first_seq + 1)
    opponent = game.opposing_seat(seat_id)
    game.record_priority_grant(opponent, first_seq + 2)
    second_pass = game.process_priority_pass(opponent, seq_num=first_seq + 2)
    if first_pass != PriorityPassOutcome.GRANT_OPPONENT:
        raise AssertionError("First pass did not transfer priority.")
    if second_pass != PriorityPassOutcome.RESOLVE_STACK:
        raise AssertionError("Two passes did not request stack resolution.")
    return item, game.resolve_top_stack_item()


class SpellStackTests(unittest.TestCase):
    def test_land_play_is_atomic_and_limited_to_once_per_main_phase(self) -> None:
        game = ready_game(
            ("mountain_001", "mountain_002", "lightning_bolt_001"),
            ("island_001",),
        )
        game.record_priority_grant("seat_1", 10)
        permanent = game.process_play_land(
            "seat_1", seq_num=10, card_id="mountain_001"
        )
        self.assertEqual(permanent, {"id": "mountain_001", "tapped": False})
        self.assertNotIn("mountain_001", game.players["seat_1"].hand)
        self.assertTrue(game.land_played_this_turn)
        self.assertEqual(game.priority_holder_seat_id, "seat_1")

        game.record_priority_grant("seat_1", 11)
        before = game.visible_state("seat_1")
        with self.assertRaises(GameRuleError) as caught:
            game.process_play_land(
                "seat_1", seq_num=11, card_id="mountain_002"
            )
        self.assertEqual(caught.exception.code, ErrorCode.ILLEGAL_ACTION)
        self.assertEqual(game.visible_state("seat_1"), before)

    def test_insufficient_mana_does_not_tap_lands_or_move_the_card(self) -> None:
        game = ready_game(
            ("island_001", "lightning_bolt_001"),
            ("mountain_001",),
        )
        put_land_on_battlefield(game, "seat_1", "island_001")
        game.record_priority_grant("seat_1", 10)
        before = game.visible_state("seat_1")
        with self.assertRaises(GameRuleError) as caught:
            game.process_cast_spell(
                "seat_1",
                seq_num=10,
                card_id="lightning_bolt_001",
                targets=("bob",),
                mana_payment={"R": 1},
            )
        self.assertEqual(caught.exception.code, ErrorCode.INSUFFICIENT_MANA)
        self.assertEqual(game.visible_state("seat_1"), before)

    def test_lightning_bolt_resolves_after_two_passes_and_deals_damage(self) -> None:
        game = ready_game(
            ("mountain_001", "lightning_bolt_001"),
            ("island_001",),
        )
        put_land_on_battlefield(game, "seat_1", "mountain_001")
        self.assertEqual(
            game.visible_state("seat_1")["available_mana"]["alice"]["R"], 1
        )
        item, resolution = cast_then_pass_twice(
            game,
            "seat_1",
            "lightning_bolt_001",
            ("bob",),
            {"R": 1},
        )
        self.assertEqual(resolution.item, item)
        self.assertEqual(resolution.outcome, StackResolutionOutcome.RESOLVED)
        self.assertEqual(game.players["seat_2"].life, 17)
        self.assertEqual(game.stack, [])
        self.assertIn("lightning_bolt_001", game.players["seat_1"].graveyard)
        self.assertTrue(game.players["seat_1"].battlefield[0]["tapped"])
        self.assertEqual(
            game.visible_state("seat_1")["available_mana"]["alice"]["R"], 0
        )
        game.current_step = TurnStep.UNTAP
        game.perform_untap()
        self.assertEqual(
            game.visible_state("seat_1")["available_mana"]["alice"]["R"], 1
        )

    def test_counterspell_resolves_first_and_removes_the_target_spell(self) -> None:
        game = ready_game(
            ("mountain_001", "lightning_bolt_001"),
            ("island_001", "island_002", "counterspell_001"),
        )
        put_land_on_battlefield(game, "seat_1", "mountain_001")
        put_land_on_battlefield(game, "seat_2", "island_001")
        put_land_on_battlefield(game, "seat_2", "island_002")

        game.record_priority_grant("seat_1", 10)
        bolt = game.process_cast_spell(
            "seat_1",
            seq_num=10,
            card_id="lightning_bolt_001",
            targets=("bob",),
            mana_payment={"R": 1},
        )
        game.record_priority_grant("seat_1", 11)
        self.assertEqual(
            game.process_priority_pass("seat_1", seq_num=11),
            PriorityPassOutcome.GRANT_OPPONENT,
        )
        game.record_priority_grant("seat_2", 12)
        counter = game.process_cast_spell(
            "seat_2",
            seq_num=12,
            card_id="counterspell_001",
            targets=(bolt.stack_item_id,),
            mana_payment={"U": 2},
        )
        self.assertEqual(
            [item.source_id for item in game.stack],
            ["lightning_bolt_001", "counterspell_001"],
        )

        game.record_priority_grant("seat_2", 13)
        game.process_priority_pass("seat_2", seq_num=13)
        game.record_priority_grant("seat_1", 14)
        self.assertEqual(
            game.process_priority_pass("seat_1", seq_num=14),
            PriorityPassOutcome.RESOLVE_STACK,
        )
        resolution = game.resolve_top_stack_item()
        self.assertEqual(resolution.item, counter)
        self.assertEqual(resolution.outcome, StackResolutionOutcome.RESOLVED)
        self.assertEqual(game.stack, [])
        self.assertEqual(game.players["seat_2"].life, 20)
        self.assertIn("lightning_bolt_001", game.players["seat_1"].graveyard)
        self.assertIn("counterspell_001", game.players["seat_2"].graveyard)

    def test_creature_spell_resolves_as_a_battlefield_permanent(self) -> None:
        game = ready_game(
            ("mountain_001", "goblin_guide_001"),
            ("island_001",),
        )
        put_land_on_battlefield(game, "seat_1", "mountain_001")
        _, resolution = cast_then_pass_twice(
            game,
            "seat_1",
            "goblin_guide_001",
            (),
            {"R": 1},
        )
        self.assertEqual(resolution.outcome, StackResolutionOutcome.RESOLVED)
        creature = game.players["seat_1"].battlefield[-1]
        self.assertEqual(creature["id"], "goblin_guide_001")
        self.assertEqual((creature["power"], creature["toughness"]), (2, 2))
        self.assertFalse(creature["summoning_sick"])

    def test_unsummon_giant_growth_and_doom_blade_effects(self) -> None:
        cases = (
            ("unsummon", "island_001", "unsummon_001", {"U": 1}),
            ("giant_growth", "forest_001", "giant_growth_001", {"G": 1}),
            ("doom_blade", "swamp_001", "doom_blade_001", {"B": 1, "X": 1}),
        )
        for effect, land, spell, payment in cases:
            with self.subTest(effect=effect):
                alice_deck = [land, spell]
                if effect == "doom_blade":
                    alice_deck.append("swamp_002")
                game = ready_game(
                    tuple(alice_deck),
                    ("grizzly_bears_001",),
                )
                put_land_on_battlefield(game, "seat_1", land)
                if effect == "doom_blade":
                    put_land_on_battlefield(game, "seat_1", "swamp_002")
                creature = put_creature_on_battlefield(
                    game, "seat_2", "grizzly_bears_001"
                )
                _, resolution = cast_then_pass_twice(
                    game,
                    "seat_1",
                    spell,
                    ("grizzly_bears_001",),
                    payment,
                )
                self.assertEqual(
                    resolution.outcome, StackResolutionOutcome.RESOLVED
                )
                if effect == "unsummon":
                    self.assertIn("grizzly_bears_001", game.players["seat_2"].hand)
                    self.assertEqual(game.players["seat_2"].battlefield, [])
                elif effect == "giant_growth":
                    self.assertEqual((creature["power"], creature["toughness"]), (5, 5))
                    game.current_step = TurnStep.CLEANUP
                    game.finish_cleanup()
                    self.assertEqual((creature["power"], creature["toughness"]), (2, 2))
                else:
                    self.assertIn(
                        "grizzly_bears_001", game.players["seat_2"].graveyard
                    )
                    self.assertEqual(game.players["seat_2"].battlefield, [])

    def test_lethal_spell_damage_causes_life_zero_game_over(self) -> None:
        game = ready_game(
            ("mountain_001", "lightning_bolt_001"),
            ("island_001",),
        )
        put_land_on_battlefield(game, "seat_1", "mountain_001")
        game.players["seat_2"].life = 3
        cast_then_pass_twice(
            game,
            "seat_1",
            "lightning_bolt_001",
            ("bob",),
            {"R": 1},
        )
        self.assertEqual(game.lifecycle_state, LifecycleState.GAME_OVER)
        self.assertEqual(game.game_over_reason, "LIFE_ZERO")
        self.assertEqual(game.winner_seat_id, "seat_1")


if __name__ == "__main__":
    unittest.main()
