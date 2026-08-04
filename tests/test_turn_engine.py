from __future__ import annotations

import random
import unittest

from mtgnp.game import (
    DiscardOutcome,
    DrawOutcome,
    GameRuleError,
    GameSession,
    MulliganOutcome,
    PriorityPassOutcome,
)
from mtgnp.lobby import ReadyPlayer
from mtgnp.protocol import ErrorCode, LifecycleState, TurnStep


DECK_1 = tuple(f"mountain_{number:03d}" for number in range(1, 9))
DECK_2 = tuple(f"island_{number:03d}" for number in range(1, 9))


def in_game(seed: int = 3) -> GameSession:
    game = GameSession(
        (
            ReadyPlayer("seat_1", "alice", DECK_1),
            ReadyPlayer("seat_2", "bob", DECK_2),
        ),
        random_source=random.Random(seed),
    )
    game.record_mulligan_request("seat_1", 1)
    game.record_mulligan_request("seat_2", 2)
    game.process_mulligan("seat_1", seq_num=1, keep=True, cards_to_bottom=())
    outcome = game.process_mulligan(
        "seat_2", seq_num=2, keep=True, cards_to_bottom=()
    )
    if outcome != MulliganOutcome.ALL_PLAYERS_KEPT:
        raise AssertionError("Test setup did not enter IN_GAME.")
    return game


class TurnEngineTests(unittest.TestCase):
    def test_priority_passes_move_to_opponent_then_close_window(self) -> None:
        game = in_game()
        game.perform_untap()
        game.enter_upkeep()
        active_seat = game.open_priority_window()
        opponent_seat = game.opposing_seat(active_seat)

        with self.assertRaises(GameRuleError) as wrong_player:
            game.process_priority_pass(opponent_seat, seq_num=10)
        self.assertEqual(wrong_player.exception.code, ErrorCode.NOT_YOUR_PRIORITY)

        game.record_priority_grant(active_seat, 10)
        with self.assertRaises(GameRuleError) as stale:
            game.process_priority_pass(active_seat, seq_num=9)
        self.assertEqual(stale.exception.code, ErrorCode.STALE_ACTION)

        first = game.process_priority_pass(active_seat, seq_num=10)
        self.assertEqual(first, PriorityPassOutcome.GRANT_OPPONENT)
        game.record_priority_grant(opponent_seat, 11)
        second = game.process_priority_pass(opponent_seat, seq_num=11)
        self.assertEqual(second, PriorityPassOutcome.WINDOW_CLOSED)
        self.assertIsNone(game.priority_holder_player_id)

        previous, next_step = game.advance_after_priority_window()
        self.assertEqual((previous, next_step), (TurnStep.UPKEEP, TurnStep.DRAW))

    def test_first_player_skips_turn_one_draw_then_later_turn_draws(self) -> None:
        game = in_game()
        game.current_step = TurnStep.DRAW
        before = len(game.players[game.active_seat_id].hand)
        self.assertEqual(game.perform_draw(), DrawOutcome.SKIPPED_FIRST_TURN)
        self.assertEqual(len(game.players[game.active_seat_id].hand), before)

        game.turn = 2
        game.active_seat_id = game.opposing_seat(game.first_player_seat_id)
        game.current_step = TurnStep.DRAW
        before = len(game.players[game.active_seat_id].hand)
        self.assertEqual(game.perform_draw(), DrawOutcome.CARD_DRAWN)
        self.assertEqual(len(game.players[game.active_seat_id].hand), before + 1)

    def test_empty_attackers_skip_to_end_of_combat(self) -> None:
        game = in_game()
        game.current_step = TurnStep.DECLARE_ATTACKERS
        active = game.active_seat_id
        game.record_attackers_request(active, 30)
        with self.assertRaises(GameRuleError):
            game.process_declare_attackers(
                active,
                seq_num=30,
                attackers=[{"creature_id": "example", "target": "bob"}],
            )
        game.process_declare_attackers(active, seq_num=30, attackers=[])
        self.assertEqual(game.current_step, TurnStep.END_OF_COMBAT)

    def test_cleanup_discard_moves_card_to_graveyard_and_switches_turn(self) -> None:
        game = in_game()
        active_seat = game.active_seat_id
        active = game.players[active_seat]
        active.hand.append(active.library.pop(0))
        game.current_step = TurnStep.CLEANUP
        self.assertEqual(game.cleanup_excess_cards(), 1)
        game.record_discard_request(active_seat, 40)
        discarded = active.hand[-1]
        outcome = game.process_discard(
            active_seat, seq_num=40, card_ids=(discarded,)
        )
        self.assertEqual(outcome, DiscardOutcome.COMPLETE)
        self.assertIn(discarded, active.graveyard)
        self.assertEqual(len(active.hand), 7)

        previous_active = game.active_player_id
        game.finish_cleanup()
        game.start_next_turn()
        self.assertEqual(game.turn, 2)
        self.assertEqual(game.current_step, TurnStep.UNTAP)
        self.assertNotEqual(game.active_player_id, previous_active)

    def test_empty_library_can_transition_to_game_over(self) -> None:
        game = in_game()
        game.turn = 2
        game.current_step = TurnStep.DRAW
        game.players[game.active_seat_id].library.clear()
        self.assertEqual(game.perform_draw(), DrawOutcome.EMPTY_LIBRARY)
        loser = game.active_seat_id
        game.declare_game_over(loser, "DECK_EMPTY")
        self.assertEqual(game.lifecycle_state, LifecycleState.GAME_OVER)
        self.assertEqual(game.game_over_reason, "DECK_EMPTY")
        self.assertEqual(game.loser_seat_id, loser)
        self.assertNotEqual(game.winner_seat_id, loser)


if __name__ == "__main__":
    unittest.main()

