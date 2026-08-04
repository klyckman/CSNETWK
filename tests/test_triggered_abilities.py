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
        random_source=random.Random(3),
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
    game: GameSession, seat_id: str, card_id: str
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
                "summoning_sick": False,
            }
        )
    player.battlefield.append(permanent)
    return permanent


def cast(
    game: GameSession,
    card_id: str,
    targets: tuple[str, ...],
    mana_payment: dict[str, int],
    *,
    seq_num: int = 10,
) -> None:
    game.record_priority_grant("seat_1", seq_num)
    game.process_cast_spell(
        "seat_1",
        seq_num=seq_num,
        card_id=card_id,
        targets=targets,
        mana_payment=mana_payment,
    )


def resolve_top(game: GameSession, *, seq_num: int) -> object:
    if game.priority_holder_seat_id is None:
        game.open_priority_window()
    first = game.priority_holder_seat_id
    if first is None:
        raise AssertionError("Test expected a priority holder.")
    game.record_priority_grant(first, seq_num)
    first_outcome = game.process_priority_pass(first, seq_num=seq_num)
    second = game.priority_holder_seat_id
    if second is None:
        raise AssertionError("First pass did not transfer priority.")
    game.record_priority_grant(second, seq_num + 1)
    second_outcome = game.process_priority_pass(second, seq_num=seq_num + 1)
    if first_outcome != PriorityPassOutcome.GRANT_OPPONENT:
        raise AssertionError("First pass did not transfer priority.")
    if second_outcome != PriorityPassOutcome.RESOLVE_STACK:
        raise AssertionError("Second pass did not request Stack resolution.")
    return game.resolve_top_stack_item()


class TriggeredAbilityTests(unittest.TestCase):
    def test_prowess_is_above_spell_and_expires_during_cleanup(self) -> None:
        game = ready_game(
            (
                "monastery_swiftspear_001",
                "mountain_001",
                "lightning_bolt_001",
            ),
            ("island_001",),
        )
        swiftspear = put_permanent(game, "seat_1", "monastery_swiftspear_001")
        put_permanent(game, "seat_1", "mountain_001")
        cast(game, "lightning_bolt_001", ("bob",), {"R": 1})

        self.assertTrue(game.has_pending_triggers())
        placed = game.place_pending_triggers()
        self.assertEqual(placed[0].item_type, "TRIGGER_ABILITY")
        self.assertEqual(game.stack[-1].source_id, "monastery_swiftspear_001")
        trigger_resolution = resolve_top(game, seq_num=20)
        self.assertEqual(trigger_resolution.outcome, StackResolutionOutcome.RESOLVED)
        self.assertEqual((swiftspear["power"], swiftspear["toughness"]), (2, 3))

        game.current_step = TurnStep.CLEANUP
        game.finish_cleanup()
        self.assertEqual((swiftspear["power"], swiftspear["toughness"]), (1, 2))

    def test_phantasmal_bear_sacrifices_before_targeting_spell_resolves(self) -> None:
        game = ready_game(
            ("mountain_001", "lightning_bolt_001"),
            ("phantasmal_bear_001",),
        )
        put_permanent(game, "seat_1", "mountain_001")
        put_permanent(game, "seat_2", "phantasmal_bear_001")
        cast(
            game,
            "lightning_bolt_001",
            ("phantasmal_bear_001",),
            {"R": 1},
        )
        game.place_pending_triggers()

        trigger_resolution = resolve_top(game, seq_num=20)
        self.assertEqual(trigger_resolution.outcome, StackResolutionOutcome.RESOLVED)
        self.assertIn("phantasmal_bear_001", game.players["seat_2"].graveyard)
        spell_resolution = resolve_top(game, seq_num=30)
        self.assertEqual(spell_resolution.outcome, StackResolutionOutcome.FIZZLE)

    def test_gray_merchant_uses_black_devotion_when_etb_trigger_resolves(self) -> None:
        game = ready_game(
            (
                "swamp_001",
                "swamp_002",
                "swamp_003",
                "swamp_004",
                "swamp_005",
                "gray_merchant_001",
            ),
            ("island_001",),
        )
        for index in range(1, 6):
            put_permanent(game, "seat_1", f"swamp_{index:03d}")
        cast(game, "gray_merchant_001", (), {"B": 2, "X": 3})
        resolve_top(game, seq_num=20)

        self.assertTrue(game.has_pending_triggers())
        game.place_pending_triggers()
        resolution = resolve_top(game, seq_num=30)
        self.assertEqual(resolution.outcome, StackResolutionOutcome.RESOLVED)
        self.assertEqual(game.players["seat_1"].life, 22)
        self.assertEqual(game.players["seat_2"].life, 18)

    def test_gravedigger_requires_a_legal_graveyard_target_choice(self) -> None:
        game = ready_game(
            (
                "swamp_001",
                "swamp_002",
                "swamp_003",
                "swamp_004",
                "gravedigger_001",
                "black_knight_001",
            ),
            ("island_001",),
        )
        for index in range(1, 5):
            put_permanent(game, "seat_1", f"swamp_{index:03d}")
        game.players["seat_1"].hand.remove("black_knight_001")
        game.players["seat_1"].graveyard.append("black_knight_001")
        cast(game, "gravedigger_001", (), {"B": 1, "X": 3})
        resolve_top(game, seq_num=20)

        trigger = game.next_trigger_choice()
        if trigger is None:
            self.fail("Gravedigger did not request a target.")
        self.assertEqual(trigger.legal_targets, ("black_knight_001",))
        game.record_trigger_choice_request("seat_1", trigger.trigger_id, 30)
        with self.assertRaises(GameRuleError) as caught:
            game.process_trigger_choice_response(
                "seat_1",
                seq_num=30,
                trigger_id=trigger.trigger_id,
                accept=False,
                chosen_target=None,
            )
        self.assertEqual(caught.exception.code, ErrorCode.TRIGGER_CHOICE_INVALID)
        game.process_trigger_choice_response(
            "seat_1",
            seq_num=30,
            trigger_id=trigger.trigger_id,
            accept=True,
            chosen_target="black_knight_001",
        )
        game.place_pending_triggers()
        resolve_top(game, seq_num=40)
        self.assertIn("black_knight_001", game.players["seat_1"].hand)
        self.assertNotIn("black_knight_001", game.players["seat_1"].graveyard)

    def test_goblin_guide_reveals_and_moves_a_land_to_defender_hand(self) -> None:
        game = ready_game(
            ("goblin_guide_001",),
            (
                "island_001",
                "island_002",
                "island_003",
                "island_004",
                "island_005",
                "island_006",
                "island_007",
                "island_008",
            ),
        )
        put_permanent(game, "seat_1", "goblin_guide_001")
        game.players["seat_2"].library = ["island_008"]
        if "island_008" in game.players["seat_2"].hand:
            game.players["seat_2"].hand.remove("island_008")
        game.current_step = TurnStep.DECLARE_ATTACKERS
        game.priority_holder_seat_id = None
        game.record_attackers_request("seat_1", 10)
        game.process_declare_attackers(
            "seat_1",
            seq_num=10,
            attackers=[{"creature_id": "goblin_guide_001", "target": "bob"}],
        )
        game.place_pending_triggers()
        resolution = resolve_top(game, seq_num=20)

        self.assertIn("island_008", game.players["seat_2"].hand)
        self.assertEqual(game.players["seat_2"].library, [])
        self.assertEqual(resolution.state_changes[0]["change_type"], "REVEAL")

    def test_same_player_order_and_apnap_put_nonactive_trigger_on_top(self) -> None:
        game = ready_game(
            (
                "monastery_swiftspear_001",
                "monastery_swiftspear_002",
                "mountain_001",
                "lightning_bolt_001",
            ),
            ("phantasmal_bear_001",),
        )
        put_permanent(game, "seat_1", "monastery_swiftspear_001")
        put_permanent(game, "seat_1", "monastery_swiftspear_002")
        put_permanent(game, "seat_1", "mountain_001")
        put_permanent(game, "seat_2", "phantasmal_bear_001")
        cast(
            game,
            "lightning_bolt_001",
            ("phantasmal_bear_001",),
            {"R": 1},
        )

        request = game.next_trigger_order_request()
        if request is None:
            self.fail("Two Prowess triggers did not request an order.")
        seat_id, trigger_ids = request
        self.assertEqual(seat_id, "seat_1")
        game.record_trigger_order_request(seat_id, 20)
        with self.assertRaises(GameRuleError) as caught:
            game.process_trigger_order_response(
                seat_id,
                seq_num=20,
                ordered_trigger_ids=(trigger_ids[0], trigger_ids[0]),
            )
        self.assertEqual(caught.exception.code, ErrorCode.TRIGGER_ORDER_INVALID)
        game.process_trigger_order_response(
            seat_id,
            seq_num=20,
            ordered_trigger_ids=tuple(reversed(trigger_ids)),
        )
        placed = game.place_pending_triggers()

        self.assertEqual([item.trigger_id for item in placed[:2]], list(reversed(trigger_ids)))
        self.assertEqual(placed[-1].controller_seat_id, "seat_2")
        self.assertEqual(placed[-1].source_id, "phantasmal_bear_001")


if __name__ == "__main__":
    unittest.main()
