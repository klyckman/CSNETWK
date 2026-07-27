import random
import unittest

from mtgnp.catalog import CardCatalog
from mtgnp.game import GameSession, Permanent
from mtgnp.protocol import Lifecycle, Phase


P1_DECK = [
    "mountain_001",
    "mountain_002",
    "mountain_003",
    "mountain_004",
    "lightning_bolt_001",
    "lightning_bolt_002",
    "shock_001",
    "goblin_guide_001",
]
P2_DECK = [
    "island_001",
    "island_002",
    "island_003",
    "island_004",
    "counterspell_001",
    "unsummon_001",
    "phantasmal_bear_001",
    "air_elemental_001",
]


class GameSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = CardCatalog.load()

    def new_setup_session(self) -> GameSession:
        session = GameSession(self.catalog, rng=random.Random(7))
        self.assertFalse(session.register_ready("seat_1", "alice", P1_DECK))
        self.assertTrue(session.register_ready("seat_2", "bob", P2_DECK))
        session.setup()
        return session

    def test_personalized_state_hides_opponent_hand(self) -> None:
        session = self.new_setup_session()
        alice_view = session.visible_state("seat_1")
        self.assertEqual({"alice"}, set(alice_view["hand"]))
        self.assertNotIn("bob", alice_view["hand"])
        self.assertEqual(len(session.players["seat_2"].hand), alice_view["hand_counts"]["bob"])

    def test_london_mulligan_requires_one_bottom_after_one_redraw(self) -> None:
        session = self.new_setup_session()
        self.assertFalse(
            session.mulligan_choice(
                "seat_1",
                keep=False,
                cards_to_bottom=[],
            )
        )
        bottom = session.players["seat_1"].hand[0]
        self.assertFalse(
            session.mulligan_choice(
                "seat_1",
                keep=True,
                cards_to_bottom=[bottom],
            )
        )
        self.assertTrue(
            session.mulligan_choice(
                "seat_2",
                keep=True,
                cards_to_bottom=[],
            )
        )
        self.assertEqual(6, len(session.players["seat_1"].hand))
        self.assertEqual(bottom, session.players["seat_1"].library[0])

    def test_lightning_bolt_uses_mana_and_resolves_server_side(self) -> None:
        session = self.new_setup_session()
        session.lifecycle = Lifecycle.IN_GAME
        session.phase = Phase.PRECOMBAT_MAIN
        session.active_slot = "seat_1"
        session.priority_slot = "seat_1"
        alice = session.players["seat_1"]
        alice.hand = ["lightning_bolt_001"]
        mountain = Permanent("mountain_001", "seat_1", "seat_1")
        alice.battlefield = [mountain]

        item = session.cast_spell(
            "seat_1",
            "lightning_bolt_001",
            ["bob"],
            {"R": 1},
        )
        self.assertTrue(mountain.tapped)
        self.assertEqual(item, session.stack[-1])
        resolved, result, changes = session.resolve_top()
        self.assertEqual(item, resolved)
        self.assertEqual("RESOLVED", result)
        self.assertEqual(17, session.players["seat_2"].life)
        self.assertEqual("DAMAGE", changes[0]["change_type"])
        self.assertIn("lightning_bolt_001", alice.graveyard)

    def test_counterspell_resolves_lifo_and_counters_target_spell(self) -> None:
        session = self.new_setup_session()
        session.lifecycle = Lifecycle.IN_GAME
        session.phase = Phase.PRECOMBAT_MAIN
        session.active_slot = "seat_1"
        session.priority_slot = "seat_1"
        alice = session.players["seat_1"]
        bob = session.players["seat_2"]
        alice.hand = ["lightning_bolt_001"]
        alice.battlefield = [Permanent("mountain_001", "seat_1", "seat_1")]
        bob.hand = ["counterspell_001"]
        bob.battlefield = [
            Permanent("island_001", "seat_2", "seat_2"),
            Permanent("island_002", "seat_2", "seat_2"),
        ]

        bolt = session.cast_spell(
            "seat_1",
            "lightning_bolt_001",
            ["bob"],
            {"R": 1},
        )
        session.priority_slot = "seat_2"
        counter = session.cast_spell(
            "seat_2",
            "counterspell_001",
            [bolt.stack_item_id],
            {"U": 2},
        )
        resolved, result, changes = session.resolve_top()
        self.assertEqual(counter, resolved)
        self.assertEqual("RESOLVED", result)
        self.assertEqual("COUNTER", changes[0]["change_type"])
        self.assertEqual([], session.stack)
        self.assertIn("counterspell_001", bob.graveyard)
        self.assertIn("lightning_bolt_001", alice.graveyard)
        self.assertEqual(20, bob.life)


if __name__ == "__main__":
    unittest.main()

