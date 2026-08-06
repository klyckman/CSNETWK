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
from mtgnp.spells import SPELLS, SpellEffect, SpellTarget


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
    return put_permanent_on_battlefield(game, seat_id, card_id)


def put_permanent_on_battlefield(
    game: GameSession, seat_id: str, card_id: str, *, tapped: bool = False
) -> dict[str, object]:
    player = game.players[seat_id]
    player.hand.remove(card_id)
    definition = CATALOG.definition_for_instance(card_id)
    permanent: dict[str, object] = {"id": card_id, "tapped": tapped}
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
        raise AssertionError("Two passes did not request Stack resolution.")
    return item, game.resolve_top_stack_item()


class OptionalSpellEffectTests(unittest.TestCase):
    def test_optional_spell_family_has_declarative_rules(self) -> None:
        expected = {
            "shock": (SpellTarget.ANY, SpellEffect.DAMAGE),
            "lava_spike": (SpellTarget.PLAYER, SpellEffect.DAMAGE),
            "flame_slash": (SpellTarget.CREATURE, SpellEffect.DAMAGE),
            "searing_spear": (SpellTarget.ANY, SpellEffect.DAMAGE),
            "cancel": (SpellTarget.SPELL, SpellEffect.COUNTER),
            "negate": (SpellTarget.NONCREATURE_SPELL, SpellEffect.COUNTER),
        }
        self.assertEqual(
            {
                base_id: (SPELLS[base_id].target, SPELLS[base_id].effect)
                for base_id in expected
            },
            expected,
        )

    def test_player_damage_spells_use_their_catalog_amounts(self) -> None:
        cases = (
            ("shock_001", ("mountain_001",), {"R": 1}, 2),
            ("lava_spike_001", ("mountain_001",), {"R": 1}, 3),
            (
                "searing_spear_001",
                ("mountain_001", "forest_001"),
                {"R": 1, "X": 1},
                3,
            ),
        )
        for spell_id, lands, payment, damage in cases:
            with self.subTest(spell_id=spell_id):
                game = ready_game((*lands, spell_id), ("island_001",))
                for land_id in lands:
                    put_land_on_battlefield(game, "seat_1", land_id)
                _, resolution = cast_then_pass_twice(
                    game, "seat_1", spell_id, ("bob",), payment
                )
                self.assertEqual(
                    resolution.outcome, StackResolutionOutcome.RESOLVED
                )
                self.assertEqual(game.players["seat_2"].life, 20 - damage)
                self.assertIn(spell_id, game.players["seat_1"].graveyard)
                self.assertIn(
                    {
                        "change_type": "DAMAGE",
                        "target": "bob",
                        "amount": damage,
                    },
                    resolution.state_changes,
                )

    def test_flame_slash_deals_four_damage_only_to_a_creature(self) -> None:
        game = ready_game(
            ("mountain_001", "flame_slash_001"),
            ("grizzly_bears_001",),
        )
        put_land_on_battlefield(game, "seat_1", "mountain_001")
        put_creature_on_battlefield(game, "seat_2", "grizzly_bears_001")
        _, resolution = cast_then_pass_twice(
            game,
            "seat_1",
            "flame_slash_001",
            ("grizzly_bears_001",),
            {"R": 1},
        )
        self.assertEqual(resolution.outcome, StackResolutionOutcome.RESOLVED)
        self.assertIn("grizzly_bears_001", game.players["seat_2"].graveyard)
        self.assertEqual(game.players["seat_2"].battlefield, [])

    def test_burn_target_restrictions_reject_without_mutation(self) -> None:
        cases = (
            ("lava_spike_001", "grizzly_bears_001"),
            ("flame_slash_001", "bob"),
        )
        for spell_id, target in cases:
            with self.subTest(spell_id=spell_id):
                game = ready_game(
                    ("mountain_001", spell_id),
                    ("grizzly_bears_001",),
                )
                put_land_on_battlefield(game, "seat_1", "mountain_001")
                put_creature_on_battlefield(
                    game, "seat_2", "grizzly_bears_001"
                )
                game.record_priority_grant("seat_1", 10)
                before = game.visible_state("seat_1")
                with self.assertRaises(GameRuleError) as caught:
                    game.process_cast_spell(
                        "seat_1",
                        seq_num=10,
                        card_id=spell_id,
                        targets=(target,),
                        mana_payment={"R": 1},
                    )
                self.assertEqual(caught.exception.code, ErrorCode.ILLEGAL_TARGET)
                self.assertEqual(game.visible_state("seat_1"), before)

    def test_lava_spike_obeys_sorcery_timing(self) -> None:
        game = ready_game(
            ("mountain_001", "lava_spike_001"),
            ("island_001",),
        )
        put_land_on_battlefield(game, "seat_1", "mountain_001")
        game.current_step = TurnStep.DRAW
        game.record_priority_grant("seat_1", 10)
        before = game.visible_state("seat_1")
        with self.assertRaises(GameRuleError) as caught:
            game.process_cast_spell(
                "seat_1",
                seq_num=10,
                card_id="lava_spike_001",
                targets=("bob",),
                mana_payment={"R": 1},
            )
        self.assertEqual(caught.exception.code, ErrorCode.WRONG_PHASE)
        self.assertEqual(game.visible_state("seat_1"), before)

    def test_cancel_counters_a_creature_spell(self) -> None:
        game = ready_game(
            ("mountain_001", "goblin_guide_001"),
            ("island_001", "island_002", "island_003", "cancel_001"),
        )
        put_land_on_battlefield(game, "seat_1", "mountain_001")
        for land_id in ("island_001", "island_002", "island_003"):
            put_land_on_battlefield(game, "seat_2", land_id)

        game.record_priority_grant("seat_1", 10)
        creature_spell = game.process_cast_spell(
            "seat_1",
            seq_num=10,
            card_id="goblin_guide_001",
            targets=(),
            mana_payment={"R": 1},
        )
        game.record_priority_grant("seat_1", 11)
        game.process_priority_pass("seat_1", seq_num=11)
        _, resolution = cast_then_pass_twice(
            game,
            "seat_2",
            "cancel_001",
            (creature_spell.stack_item_id,),
            {"U": 2, "X": 1},
            first_seq=12,
        )
        self.assertEqual(resolution.outcome, StackResolutionOutcome.RESOLVED)
        self.assertEqual(game.stack, [])
        self.assertIn("goblin_guide_001", game.players["seat_1"].graveyard)
        self.assertIn("cancel_001", game.players["seat_2"].graveyard)

    def test_negate_counters_only_noncreature_spells(self) -> None:
        game = ready_game(
            ("mountain_001", "shock_001"),
            ("island_001", "island_002", "negate_001"),
        )
        put_land_on_battlefield(game, "seat_1", "mountain_001")
        put_land_on_battlefield(game, "seat_2", "island_001")
        put_land_on_battlefield(game, "seat_2", "island_002")

        game.record_priority_grant("seat_1", 10)
        shock = game.process_cast_spell(
            "seat_1",
            seq_num=10,
            card_id="shock_001",
            targets=("bob",),
            mana_payment={"R": 1},
        )
        game.record_priority_grant("seat_1", 11)
        game.process_priority_pass("seat_1", seq_num=11)
        _, resolution = cast_then_pass_twice(
            game,
            "seat_2",
            "negate_001",
            (shock.stack_item_id,),
            {"U": 1, "X": 1},
            first_seq=12,
        )
        self.assertEqual(resolution.outcome, StackResolutionOutcome.RESOLVED)
        self.assertEqual(game.players["seat_2"].life, 20)
        self.assertEqual(game.stack, [])

        creature_game = ready_game(
            ("mountain_001", "goblin_guide_001"),
            ("island_001", "island_002", "negate_001"),
        )
        put_land_on_battlefield(creature_game, "seat_1", "mountain_001")
        put_land_on_battlefield(creature_game, "seat_2", "island_001")
        put_land_on_battlefield(creature_game, "seat_2", "island_002")
        creature_game.record_priority_grant("seat_1", 20)
        creature_spell = creature_game.process_cast_spell(
            "seat_1",
            seq_num=20,
            card_id="goblin_guide_001",
            targets=(),
            mana_payment={"R": 1},
        )
        creature_game.record_priority_grant("seat_1", 21)
        creature_game.process_priority_pass("seat_1", seq_num=21)
        creature_game.record_priority_grant("seat_2", 22)
        before = creature_game.visible_state("seat_2")
        with self.assertRaises(GameRuleError) as caught:
            creature_game.process_cast_spell(
                "seat_2",
                seq_num=22,
                card_id="negate_001",
                targets=(creature_spell.stack_item_id,),
                mana_payment={"U": 1, "X": 1},
            )
        self.assertEqual(caught.exception.code, ErrorCode.ILLEGAL_TARGET)
        self.assertEqual(creature_game.visible_state("seat_2"), before)

    def test_damage_spell_fizzles_if_its_creature_target_leaves(self) -> None:
        game = ready_game(
            ("mountain_001", "shock_001"),
            ("island_001", "unsummon_001", "grizzly_bears_001"),
        )
        put_land_on_battlefield(game, "seat_1", "mountain_001")
        put_land_on_battlefield(game, "seat_2", "island_001")
        put_creature_on_battlefield(game, "seat_2", "grizzly_bears_001")

        game.record_priority_grant("seat_1", 10)
        shock = game.process_cast_spell(
            "seat_1",
            seq_num=10,
            card_id="shock_001",
            targets=("grizzly_bears_001",),
            mana_payment={"R": 1},
        )
        game.record_priority_grant("seat_1", 11)
        game.process_priority_pass("seat_1", seq_num=11)
        unsummon, unsummon_resolution = cast_then_pass_twice(
            game,
            "seat_2",
            "unsummon_001",
            ("grizzly_bears_001",),
            {"U": 1},
            first_seq=12,
        )
        self.assertEqual(unsummon_resolution.item, unsummon)
        self.assertIn("grizzly_bears_001", game.players["seat_2"].hand)

        game.open_priority_window()
        game.record_priority_grant("seat_1", 20)
        game.process_priority_pass("seat_1", seq_num=20)
        game.record_priority_grant("seat_2", 21)
        game.process_priority_pass("seat_2", seq_num=21)
        shock_resolution = game.resolve_top_stack_item()
        self.assertEqual(shock_resolution.item, shock)
        self.assertEqual(shock_resolution.outcome, StackResolutionOutcome.FIZZLE)
        self.assertEqual(shock_resolution.state_changes, ())
        self.assertIn("shock_001", game.players["seat_1"].graveyard)

    def test_spell_targeting_respects_protection(self) -> None:
        game = ready_game(
            ("swamp_001", "swamp_002", "doom_blade_001"),
            ("white_knight_001",),
        )
        put_land_on_battlefield(game, "seat_1", "swamp_001")
        put_land_on_battlefield(game, "seat_1", "swamp_002")
        put_creature_on_battlefield(game, "seat_2", "white_knight_001")
        game.record_priority_grant("seat_1", 10)
        with self.assertRaises(GameRuleError) as caught:
            game.process_cast_spell(
                "seat_1",
                seq_num=10,
                card_id="doom_blade_001",
                targets=("white_knight_001",),
                mana_payment={"B": 1, "X": 1},
            )
        self.assertEqual(caught.exception.code, ErrorCode.ILLEGAL_TARGET)

    def test_naturalize_and_terror_destroy_their_allowed_targets(self) -> None:
        naturalize_game = ready_game(
            (
                "forest_001",
                "island_001",
                "naturalize_001",
                "sol_ring_001",
                "pacifism_001",
            ),
            ("island_002",),
        )
        put_permanent_on_battlefield(
            naturalize_game, "seat_1", "forest_001"
        )
        put_permanent_on_battlefield(
            naturalize_game, "seat_1", "island_001"
        )
        artifact = put_permanent_on_battlefield(
            naturalize_game, "seat_1", "sol_ring_001"
        )
        _, artifact_resolution = cast_then_pass_twice(
            naturalize_game,
            "seat_1",
            "naturalize_001",
            ("sol_ring_001",),
            {"G": 1, "X": 1},
        )
        self.assertEqual(
            artifact_resolution.outcome, StackResolutionOutcome.RESOLVED
        )
        self.assertNotIn(artifact, naturalize_game.players["seat_1"].battlefield)
        self.assertIn("sol_ring_001", naturalize_game.players["seat_1"].graveyard)

        enchantment_game = ready_game(
            (
                "forest_001",
                "island_001",
                "naturalize_001",
                "sol_ring_001",
                "pacifism_001",
            ),
            ("island_002",),
        )
        put_permanent_on_battlefield(
            enchantment_game, "seat_1", "forest_001"
        )
        put_permanent_on_battlefield(
            enchantment_game, "seat_1", "island_001"
        )
        aura = put_permanent_on_battlefield(
            enchantment_game, "seat_1", "pacifism_001"
        )
        _, aura_resolution = cast_then_pass_twice(
            enchantment_game,
            "seat_1",
            "naturalize_001",
            ("pacifism_001",),
            {"G": 1, "X": 1},
        )
        self.assertEqual(aura_resolution.outcome, StackResolutionOutcome.RESOLVED)
        self.assertNotIn(aura, enchantment_game.players["seat_1"].battlefield)
        self.assertIn("pacifism_001", enchantment_game.players["seat_1"].graveyard)

        terror_game = ready_game(
            (
                "swamp_001",
                "swamp_002",
                "terror_001",
                "grizzly_bears_001",
                "black_knight_001",
            ),
            ("grizzly_bears_002",),
        )
        put_permanent_on_battlefield(terror_game, "seat_1", "swamp_001")
        put_permanent_on_battlefield(terror_game, "seat_1", "swamp_002")
        creature = put_permanent_on_battlefield(
            terror_game, "seat_2", "grizzly_bears_002"
        )
        _, terror_resolution = cast_then_pass_twice(
            terror_game,
            "seat_1",
            "terror_001",
            ("grizzly_bears_002",),
            {"B": 1, "X": 1},
        )
        self.assertEqual(terror_resolution.outcome, StackResolutionOutcome.RESOLVED)
        self.assertNotIn(creature, terror_game.players["seat_2"].battlefield)
        self.assertIn("grizzly_bears_002", terror_game.players["seat_2"].graveyard)

        illegal_game = ready_game(
            (
                "swamp_001",
                "swamp_002",
                "terror_001",
                "grizzly_bears_001",
                "black_knight_001",
            ),
            ("black_knight_002",),
        )
        put_permanent_on_battlefield(illegal_game, "seat_1", "swamp_001")
        put_permanent_on_battlefield(illegal_game, "seat_1", "swamp_002")
        put_permanent_on_battlefield(illegal_game, "seat_2", "black_knight_002")
        illegal_game.record_priority_grant("seat_1", 10)
        with self.assertRaises(GameRuleError) as caught:
            illegal_game.process_cast_spell(
                "seat_1",
                seq_num=10,
                card_id="terror_001",
                targets=("black_knight_002",),
                mana_payment={"B": 1, "X": 1},
            )
        self.assertEqual(caught.exception.code, ErrorCode.ILLEGAL_TARGET)

    def test_raise_dead_rampant_growth_dark_ritual_and_incinerate(self) -> None:
        raise_dead_game = ready_game(
            ("swamp_001", "raise_dead_001", "black_knight_001"),
            ("island_001",),
        )
        put_permanent_on_battlefield(raise_dead_game, "seat_1", "swamp_001")
        raise_dead_game.players["seat_1"].hand.remove("black_knight_001")
        raise_dead_game.players["seat_1"].graveyard.append("black_knight_001")
        _, raise_dead_resolution = cast_then_pass_twice(
            raise_dead_game,
            "seat_1",
            "raise_dead_001",
            ("black_knight_001",),
            {"B": 1},
        )
        self.assertEqual(raise_dead_resolution.outcome, StackResolutionOutcome.RESOLVED)
        self.assertIn("black_knight_001", raise_dead_game.players["seat_1"].hand)
        self.assertNotIn("black_knight_001", raise_dead_game.players["seat_1"].graveyard)

        growth_game = ready_game(
            ("forest_001", "rampant_growth_001", "mountain_001", "island_001"),
            ("island_002",),
        )
        put_permanent_on_battlefield(growth_game, "seat_1", "forest_001")
        put_permanent_on_battlefield(growth_game, "seat_1", "mountain_001")
        growth_game.players["seat_1"].library = ["plains_001", "island_002"]
        _, growth_resolution = cast_then_pass_twice(
            growth_game,
            "seat_1",
            "rampant_growth_001",
            (),
            {"G": 1, "X": 1},
        )
        self.assertEqual(growth_resolution.outcome, StackResolutionOutcome.RESOLVED)
        self.assertIn(
            "plains_001",
            [card["id"] for card in growth_game.players["seat_1"].battlefield],
        )
        tapped_land = next(
            card
            for card in growth_game.players["seat_1"].battlefield
            if card["id"] == "plains_001"
        )
        self.assertTrue(tapped_land["tapped"])
        self.assertEqual(growth_game.players["seat_1"].library, ["island_002"])

        ritual_game = ready_game(
            (
                "swamp_001",
                "dark_ritual_001",
                "raise_dead_001",
                "black_knight_001",
            ),
            ("island_001",),
        )
        put_permanent_on_battlefield(ritual_game, "seat_1", "swamp_001")
        ritual_game.players["seat_1"].graveyard.append("black_knight_001")
        _, ritual_resolution = cast_then_pass_twice(
            ritual_game,
            "seat_1",
            "dark_ritual_001",
            (),
            {"B": 1},
        )
        self.assertEqual(ritual_resolution.outcome, StackResolutionOutcome.RESOLVED)
        self.assertEqual(
            ritual_game.visible_state("seat_1")["mana_pool"]["alice"]["B"], 3
        )
        ritual_game.open_priority_window()
        ritual_game.record_priority_grant("seat_1", 20)
        _, second_resolution = cast_then_pass_twice(
            ritual_game,
            "seat_1",
            "raise_dead_001",
            ("black_knight_001",),
            {"B": 1},
            first_seq=20,
        )
        self.assertEqual(second_resolution.outcome, StackResolutionOutcome.RESOLVED)
        self.assertEqual(
            ritual_game.visible_state("seat_1")["mana_pool"]["alice"]["B"], 2
        )
        self.assertIn("black_knight_001", ritual_game.players["seat_1"].hand)

        incinerate_game = ready_game(
            ("mountain_001", "mountain_002", "incinerate_001"),
            ("grizzly_bears_002",),
        )
        put_permanent_on_battlefield(incinerate_game, "seat_1", "mountain_001")
        put_permanent_on_battlefield(incinerate_game, "seat_1", "mountain_002")
        target = put_creature_on_battlefield(
            incinerate_game, "seat_2", "grizzly_bears_002"
        )
        _, incinerate_resolution = cast_then_pass_twice(
            incinerate_game,
            "seat_1",
            "incinerate_001",
            ("grizzly_bears_002",),
            {"R": 1, "X": 1},
        )
        self.assertEqual(incinerate_resolution.outcome, StackResolutionOutcome.RESOLVED)
        self.assertIn("grizzly_bears_002", incinerate_game.players["seat_2"].graveyard)
        self.assertGreaterEqual(target["damage"], 3)
        self.assertIn(
            {
                "change_type": "CANT_REGENERATE",
                "target": "grizzly_bears_002",
                "duration": "END_OF_TURN",
            },
            incinerate_resolution.state_changes,
        )


if __name__ == "__main__":
    unittest.main()
