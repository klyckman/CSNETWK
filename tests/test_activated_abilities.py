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
from mtgnp.protocol import ErrorCode, TurnStep


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
        random_source=random.Random(4),
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


def put_permanent(
    game: GameSession,
    seat_id: str,
    card_id: str,
    *,
    summoning_sick: bool = False,
) -> dict[str, object]:
    player = game.players[seat_id]
    player.hand.remove(card_id)
    definition = CATALOG.definition_for_instance(card_id)
    permanent: dict[str, object] = {"id": card_id, "tapped": False}
    if definition.card_type in {"Creature", "Artifact Creature"}:
        permanent.update(
            {
                "damage": 0,
                "power": definition.power,
                "toughness": definition.toughness,
                "summoning_sick": summoning_sick,
            }
        )
    player.battlefield.append(permanent)
    return permanent


def resolve_after_two_passes(
    game: GameSession, controller_seat: str, *, first_seq: int
):
    game.record_priority_grant(controller_seat, first_seq)
    self_pass = game.process_priority_pass(
        controller_seat, seq_num=first_seq
    )
    opposing_seat = game.opposing_seat(controller_seat)
    game.record_priority_grant(opposing_seat, first_seq + 1)
    opposing_pass = game.process_priority_pass(
        opposing_seat, seq_num=first_seq + 1
    )
    if self_pass != PriorityPassOutcome.GRANT_OPPONENT:
        raise AssertionError("First pass did not transfer priority.")
    if opposing_pass != PriorityPassOutcome.RESOLVE_STACK:
        raise AssertionError("Second pass did not request Stack resolution.")
    return game.resolve_top_stack_item()


class ActivatedAbilityTests(unittest.TestCase):
    def test_tap_damage_ability_resolves_without_moving_its_source(self) -> None:
        game = ready_game(
            ("prodigal_sorcerer_001",),
            ("island_001",),
        )
        source = put_permanent(
            game, "seat_1", "prodigal_sorcerer_001"
        )
        game.record_priority_grant("seat_1", 10)
        item = game.process_activate_ability(
            "seat_1",
            seq_num=10,
            source_id="prodigal_sorcerer_001",
            ability_index=0,
            targets=("bob",),
            cost_payment={"tap": True, "mana": {}},
        )

        self.assertEqual(item.item_type, "ABILITY")
        self.assertEqual(item.ability_index, 0)
        self.assertTrue(source["tapped"])
        self.assertEqual(game.visible_stack_item(item)["source"], item.source_id)

        resolution = resolve_after_two_passes(
            game, "seat_1", first_seq=11
        )
        self.assertEqual(resolution.outcome, StackResolutionOutcome.RESOLVED)
        self.assertEqual(game.players["seat_2"].life, 19)
        self.assertIn(source, game.players["seat_1"].battlefield)
        self.assertNotIn(
            "prodigal_sorcerer_001", game.players["seat_1"].graveyard
        )

    def test_summoning_sickness_rejects_tap_cost_atomically(self) -> None:
        game = ready_game(
            ("prodigal_sorcerer_001",),
            ("island_001",),
        )
        source = put_permanent(
            game,
            "seat_1",
            "prodigal_sorcerer_001",
            summoning_sick=True,
        )
        game.record_priority_grant("seat_1", 10)
        before = game.visible_state("seat_1")
        with self.assertRaises(GameRuleError) as caught:
            game.process_activate_ability(
                "seat_1",
                seq_num=10,
                source_id="prodigal_sorcerer_001",
                ability_index=0,
                targets=("bob",),
                cost_payment={"tap": True, "mana": {}},
            )
        self.assertEqual(caught.exception.code, ErrorCode.ILLEGAL_ACTION)
        self.assertFalse(source["tapped"])
        self.assertEqual(game.visible_state("seat_1"), before)

    def test_rod_of_ruin_pays_three_mana_and_lethal_damage_runs_sbas(self) -> None:
        game = ready_game(
            (
                "rod_of_ruin_001",
                "mountain_001",
                "forest_001",
                "plains_001",
            ),
            ("prodigal_sorcerer_002",),
        )
        rod = put_permanent(game, "seat_1", "rod_of_ruin_001")
        lands = [
            put_permanent(game, "seat_1", "mountain_001"),
            put_permanent(game, "seat_1", "forest_001"),
            put_permanent(game, "seat_1", "plains_001"),
        ]
        put_permanent(game, "seat_2", "prodigal_sorcerer_002")

        game.record_priority_grant("seat_1", 10)
        item = game.process_activate_ability(
            "seat_1",
            seq_num=10,
            source_id="rod_of_ruin_001",
            ability_index=0,
            targets=("prodigal_sorcerer_002",),
            cost_payment={"tap": True, "mana": {"X": 3}},
        )
        self.assertTrue(rod["tapped"])
        self.assertTrue(all(land["tapped"] for land in lands))
        self.assertEqual(item.item_type, "ABILITY")

        resolution = resolve_after_two_passes(
            game, "seat_1", first_seq=11
        )
        self.assertEqual(resolution.outcome, StackResolutionOutcome.RESOLVED)
        self.assertIn(
            "prodigal_sorcerer_002", game.players["seat_2"].graveyard
        )
        self.assertTrue(
            any(
                change.get("reason") == "LETHAL_DAMAGE"
                for change in resolution.state_changes
            )
        )

    def test_incorrect_ability_payment_leaves_every_permanent_untapped(self) -> None:
        game = ready_game(
            ("millstone_001", "island_001", "island_002"),
            ("mountain_001",),
        )
        source = put_permanent(game, "seat_1", "millstone_001")
        lands = [
            put_permanent(game, "seat_1", "island_001"),
            put_permanent(game, "seat_1", "island_002"),
        ]
        game.record_priority_grant("seat_1", 10)
        with self.assertRaises(GameRuleError) as caught:
            game.process_activate_ability(
                "seat_1",
                seq_num=10,
                source_id="millstone_001",
                ability_index=0,
                targets=("bob",),
                cost_payment={"tap": True, "mana": {"X": 1}},
            )
        self.assertEqual(caught.exception.code, ErrorCode.INSUFFICIENT_MANA)
        self.assertFalse(source["tapped"])
        self.assertTrue(all(not land["tapped"] for land in lands))
        self.assertEqual(game.stack, [])

    def test_millstone_moves_up_to_two_library_cards_to_graveyard(self) -> None:
        game = ready_game(
            ("millstone_001", "island_004", "island_005"),
            ("mountain_001", "mountain_002", "mountain_003"),
        )
        put_permanent(game, "seat_1", "millstone_001")
        put_permanent(game, "seat_1", "island_004")
        put_permanent(game, "seat_1", "island_005")
        bob = game.players["seat_2"]
        bob.hand.clear()
        bob.library[:] = ["mountain_001", "mountain_002", "mountain_003"]

        game.record_priority_grant("seat_1", 10)
        game.process_activate_ability(
            "seat_1",
            seq_num=10,
            source_id="millstone_001",
            ability_index=0,
            targets=("bob",),
            cost_payment={"tap": True, "mana": {"X": 2}},
        )
        resolution = resolve_after_two_passes(
            game, "seat_1", first_seq=11
        )
        self.assertEqual(resolution.outcome, StackResolutionOutcome.RESOLVED)
        self.assertEqual(bob.library, ["mountain_003"])
        self.assertEqual(bob.graveyard, ["mountain_001", "mountain_002"])

    def test_royal_assassin_refizzles_if_target_becomes_untapped(self) -> None:
        game = ready_game(
            ("royal_assassin_001",),
            ("grizzly_bears_001",),
        )
        put_permanent(game, "seat_1", "royal_assassin_001")
        target = put_permanent(game, "seat_2", "grizzly_bears_001")
        target["tapped"] = True
        game.record_priority_grant("seat_1", 10)
        game.process_activate_ability(
            "seat_1",
            seq_num=10,
            source_id="royal_assassin_001",
            ability_index=0,
            targets=("grizzly_bears_001",),
            cost_payment={"tap": True, "mana": {}},
        )
        target["tapped"] = False
        resolution = resolve_after_two_passes(
            game, "seat_1", first_seq=11
        )
        self.assertEqual(resolution.outcome, StackResolutionOutcome.FIZZLE)
        self.assertIn(target, game.players["seat_2"].battlefield)

    def test_unsupported_ability_index_is_rejected_without_mutation(self) -> None:
        game = ready_game(
            ("prodigal_sorcerer_001",),
            ("island_001",),
        )
        source = put_permanent(
            game, "seat_1", "prodigal_sorcerer_001"
        )
        game.record_priority_grant("seat_1", 10)
        with self.assertRaises(GameRuleError) as caught:
            game.process_activate_ability(
                "seat_1",
                seq_num=10,
                source_id="prodigal_sorcerer_001",
                ability_index=1,
                targets=("bob",),
                cost_payment={"tap": True, "mana": {}},
            )
        self.assertEqual(caught.exception.code, ErrorCode.ILLEGAL_ACTION)
        self.assertFalse(source["tapped"])
        self.assertEqual(game.stack, [])


if __name__ == "__main__":
    unittest.main()
