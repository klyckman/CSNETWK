from __future__ import annotations

import random
import unittest

from mtgnp.game import GameRuleError, GameSession, MulliganOutcome
from mtgnp.lobby import ReadyPlayer
from mtgnp.protocol import ErrorCode, LifecycleState, TurnStep


ALICE_DECK = (
    "mountain_001",
    "mountain_002",
    "mountain_003",
    "mountain_004",
    "lightning_bolt_001",
    "shock_001",
    "searing_spear_001",
    "goblin_guide_001",
)
BOB_DECK = (
    "island_001",
    "island_002",
    "island_003",
    "island_004",
    "counterspell_001",
    "cancel_001",
    "unsummon_001",
    "phantasmal_bear_001",
)


def create_game(seed: int = 7) -> GameSession:
    return GameSession(
        (
            ReadyPlayer("seat_1", "alice", ALICE_DECK),
            ReadyPlayer("seat_2", "bob", BOB_DECK),
        ),
        random_source=random.Random(seed),
    )


class GameSetupTests(unittest.TestCase):
    def test_setup_initializes_life_shuffles_and_draws_personalized_hands(self) -> None:
        game = create_game()
        alice = game.visible_state("seat_1")
        bob = game.visible_state("seat_2")

        self.assertEqual(alice["phase"], "MULLIGAN")
        self.assertEqual(alice["turn"], 0)
        self.assertEqual(alice["life_totals"], {"alice": 20, "bob": 20})
        self.assertEqual(len(alice["hand"]["alice"]), 7)
        self.assertEqual(len(bob["hand"]["bob"]), 7)
        self.assertNotIn("bob", alice["hand"])
        self.assertNotIn("alice", bob["hand"])
        self.assertEqual(alice["library_counts"], {"alice": 1, "bob": 1})
        self.assertIn(alice["active_player"], {"alice", "bob"})

    def test_short_legal_deck_draws_all_available_cards(self) -> None:
        game = GameSession(
            (
                ReadyPlayer("seat_1", "alice", ("mountain_001",)),
                ReadyPlayer("seat_2", "bob", ("island_001",)),
            ),
            random_source=random.Random(1),
        )
        self.assertEqual(game.visible_state("seat_1")["hand_counts"]["alice"], 1)
        self.assertEqual(game.visible_state("seat_2")["library_counts"]["bob"], 0)

    def test_stale_mulligan_token_does_not_change_state(self) -> None:
        game = create_game()
        game.record_mulligan_request("seat_1", 10)
        before = game.visible_state("seat_1")
        with self.assertRaises(GameRuleError) as caught:
            game.process_mulligan(
                "seat_1", seq_num=9, keep=True, cards_to_bottom=()
            )
        self.assertEqual(caught.exception.code, ErrorCode.STALE_ACTION)
        self.assertEqual(game.visible_state("seat_1"), before)

    def test_redraw_then_keep_bottoms_one_card(self) -> None:
        game = create_game()
        game.record_mulligan_request("seat_1", 10)
        outcome = game.process_mulligan(
            "seat_1", seq_num=10, keep=False, cards_to_bottom=()
        )
        self.assertEqual(outcome, MulliganOutcome.REDRAW)
        redrawn = game.visible_state("seat_1")
        self.assertEqual(redrawn["mulligans_taken"]["alice"], 1)
        self.assertEqual(redrawn["hand_counts"]["alice"], 7)

        game.record_mulligan_request("seat_1", 11)
        selected = redrawn["hand"]["alice"][0]
        outcome = game.process_mulligan(
            "seat_1", seq_num=11, keep=True, cards_to_bottom=(selected,)
        )
        self.assertEqual(outcome, MulliganOutcome.WAITING_FOR_OPPONENT)
        kept = game.visible_state("seat_1")
        self.assertEqual(kept["hand_counts"]["alice"], 6)
        self.assertEqual(kept["library_counts"]["alice"], 2)
        self.assertTrue(kept["mulligan_kept"]["alice"])

    def test_invalid_bottom_count_is_atomic_and_can_be_retried(self) -> None:
        game = create_game()
        game.record_mulligan_request("seat_1", 10)
        game.process_mulligan("seat_1", seq_num=10, keep=False, cards_to_bottom=())
        game.record_mulligan_request("seat_1", 11)
        before = game.visible_state("seat_1")

        with self.assertRaises(GameRuleError) as caught:
            game.process_mulligan(
                "seat_1", seq_num=11, keep=True, cards_to_bottom=()
            )
        self.assertEqual(caught.exception.code, ErrorCode.ILLEGAL_ACTION)
        self.assertEqual(game.visible_state("seat_1"), before)

        selected = before["hand"]["alice"][0]
        outcome = game.process_mulligan(
            "seat_1", seq_num=11, keep=True, cards_to_bottom=(selected,)
        )
        self.assertEqual(outcome, MulliganOutcome.WAITING_FOR_OPPONENT)

    def test_malformed_bottom_card_entry_is_rejected_without_crashing(self) -> None:
        game = create_game()
        game.record_mulligan_request("seat_1", 10)
        before = game.visible_state("seat_1")
        with self.assertRaises(GameRuleError) as caught:
            game.process_mulligan(
                "seat_1",
                seq_num=10,
                keep=True,
                cards_to_bottom=({},),
            )
        self.assertEqual(caught.exception.code, ErrorCode.ILLEGAL_ACTION)
        self.assertEqual(game.visible_state("seat_1"), before)

    def test_both_keeps_begin_turn_one_untap(self) -> None:
        game = create_game()
        game.record_mulligan_request("seat_1", 20)
        game.record_mulligan_request("seat_2", 21)
        first = game.process_mulligan(
            "seat_1", seq_num=20, keep=True, cards_to_bottom=()
        )
        second = game.process_mulligan(
            "seat_2", seq_num=21, keep=True, cards_to_bottom=()
        )
        self.assertEqual(first, MulliganOutcome.WAITING_FOR_OPPONENT)
        self.assertEqual(second, MulliganOutcome.ALL_PLAYERS_KEPT)
        self.assertEqual(game.lifecycle_state, LifecycleState.IN_GAME)
        self.assertEqual(game.turn, 1)
        self.assertEqual(game.current_step, TurnStep.UNTAP)
        self.assertEqual(game.visible_state("seat_1")["phase"], "UNTAP")


if __name__ == "__main__":
    unittest.main()
