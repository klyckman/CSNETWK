from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from mtgnp.catalog import load_catalog
from mtgnp.client import (
    _choose_trigger_choice,
    _choose_trigger_order,
    _land_action_error,
    _parse_attacker_declaration,
    _parse_blocker_declaration,
    _parse_damage_order,
    _parse_priority_action,
    _priority_role_message,
    _render_action_submission,
    _render_game_state,
    _render_pdu,
    _state_after_phase_transition,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ClientActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog(PROJECT_ROOT / "data")

    def test_pass_and_land_commands(self) -> None:
        self.assertEqual(_parse_priority_action("pass", self.catalog), ("pass", {}))
        self.assertEqual(
            _parse_priority_action("concede", self.catalog),
            ("concede", {}),
        )
        self.assertEqual(
            _parse_priority_action("land mountain_001", self.catalog),
            ("land", {"card_id": "mountain_001"}),
        )

    def test_cast_infers_catalog_mana_and_accepts_targets(self) -> None:
        self.assertEqual(
            _parse_priority_action(
                "cast lightning_bolt_001 bob", self.catalog
            ),
            (
                "cast",
                {
                    "card_id": "lightning_bolt_001",
                    "targets": ("bob",),
                    "mana_payment": {"R": 1},
                },
            ),
        )

    def test_cast_accepts_explicit_colored_and_generic_payment(self) -> None:
        self.assertEqual(
            _parse_priority_action(
                "cast doom_blade_001 grizzly_bears_001 --mana B=1,X=1",
                self.catalog,
            )[1]["mana_payment"],
            {"B": 1, "X": 1},
        )

    def test_activate_builds_the_rfc_cost_payment_shape(self) -> None:
        self.assertEqual(
            _parse_priority_action(
                "activate prodigal_sorcerer_001 0 bob", self.catalog
            ),
            (
                "activate",
                {
                    "source_id": "prodigal_sorcerer_001",
                    "ability_index": 0,
                    "targets": ("bob",),
                    "cost_payment": {"tap": True, "mana": {}},
                },
            ),
        )
        self.assertEqual(
            _parse_priority_action(
                "activate rod_of_ruin_001 0 bob --mana X=3", self.catalog
            )[1]["cost_payment"],
            {"tap": True, "mana": {"X": 3}},
        )

    def test_invalid_command_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _parse_priority_action("cast", self.catalog)
        with self.assertRaises(ValueError):
            _parse_priority_action(
                "cast lightning_bolt_001 bob --mana red", self.catalog
            )
        with self.assertRaises(ValueError):
            _parse_priority_action("land lightning_bolt_001", self.catalog)
        with self.assertRaises(ValueError):
            _parse_priority_action(
                "activate grizzly_bears_001 0 bob", self.catalog
            )

    def test_land_guidance_explains_active_player_phase_and_turn_limit(self) -> None:
        state = {
            "active_player": "alice",
            "phase": "PRECOMBAT_MAIN",
            "land_played_this_turn": False,
        }
        self.assertIn("Alice".lower(), _land_action_error(state, "bob").lower())
        self.assertIsNone(_land_action_error(state, "alice"))

        state["phase"] = "UPKEEP"
        self.assertIn("Main Phase", _land_action_error(state, "alice"))
        state["phase"] = "POSTCOMBAT_MAIN"
        state["land_played_this_turn"] = True
        self.assertIn("already played", _land_action_error(state, "alice"))

    def test_priority_role_distinguishes_response_priority_from_turn_owner(self) -> None:
        state = {"active_player": "alice", "phase": "PRECOMBAT_MAIN"}
        response = _priority_role_message(state, "bob")
        self.assertIn("Turn owner: alice", response)
        self.assertIn("response priority", response)
        self.assertIn("not the start of your turn", response)
        self.assertIn("This is your turn", _priority_role_message(state, "alice"))

    def test_combat_declaration_parsers_build_protocol_shapes(self) -> None:
        self.assertEqual(
            _parse_attacker_declaration(
                "goblin_guide_001 grizzly_bears_001",
                ("goblin_guide_001", "grizzly_bears_001"),
                "bob",
            ),
            [
                {"creature_id": "goblin_guide_001", "target": "bob"},
                {"creature_id": "grizzly_bears_001", "target": "bob"},
            ],
        )
        self.assertEqual(
            _parse_blocker_declaration(
                "wall_of_stone_001=goblin_guide_001",
                ("wall_of_stone_001",),
                ("goblin_guide_001",),
            ),
            [
                {
                    "creature_id": "wall_of_stone_001",
                    "blocking_id": "goblin_guide_001",
                }
            ],
        )
        self.assertEqual(
            _parse_damage_order(
                "bear_002 bear_001", ("bear_001", "bear_002")
            ),
            ("bear_002", "bear_001"),
        )
        self.assertEqual(_parse_attacker_declaration("none", (), "bob"), [])
        self.assertEqual(_parse_blocker_declaration("none", (), ()), [])

    def test_invalid_combat_declarations_are_rejected_locally(self) -> None:
        with self.assertRaises(ValueError):
            _parse_attacker_declaration("bear bear", ("bear",), "bob")
        with self.assertRaises(ValueError):
            _parse_blocker_declaration("bear", ("bear",), ("attacker",))
        with self.assertRaises(ValueError):
            _parse_damage_order("one", ("one", "two"))

    def test_turn_banner_and_action_divider_are_rendered(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            _render_pdu(
                {
                    "type": "PHASE_TRANSITION",
                    "seq_num": 1,
                    "from_phase": "CLEANUP",
                    "to_phase": "UNTAP",
                    "turn": 2,
                    "active_player": "bob",
                }
            )
            _render_action_submission("land", {"card_id": "swamp_001"})
            _render_action_submission(
                "activate",
                {
                    "source_id": "prodigal_sorcerer_001",
                    "ability_index": 0,
                    "targets": ("bob",),
                    "cost_payment": {"tap": True, "mana": {}},
                },
            )
        rendered = output.getvalue()
        self.assertIn("TURN 2  |  ACTIVE PLAYER: BOB", rendered)
        self.assertIn("ACTION SUBMITTED: PLAY LAND swamp_001", rendered)
        self.assertIn(
            "ACTION SUBMITTED: ACTIVATE prodigal_sorcerer_001 ability 0",
            rendered,
        )
        self.assertIn("=" * 78, rendered)
        self.assertIn("-" * 78, rendered)

    def test_phase_transition_updates_land_legality_context(self) -> None:
        draw_state = {
            "turn": 1,
            "phase": "DRAW",
            "active_player": "bob",
            "land_played_this_turn": False,
        }
        transition = {
            "type": "PHASE_TRANSITION",
            "seq_num": 4,
            "from_phase": "DRAW",
            "to_phase": "PRECOMBAT_MAIN",
            "turn": 1,
            "active_player": "bob",
        }
        main_state = _state_after_phase_transition(draw_state, transition)
        self.assertEqual(main_state["phase"], "PRECOMBAT_MAIN")
        self.assertIsNone(_land_action_error(main_state, "bob"))
        self.assertEqual(draw_state["phase"], "DRAW")

        output = io.StringIO()
        with redirect_stdout(output):
            _render_pdu(transition)
        self.assertIn(
            "ENTERING PHASE: PRECOMBAT_MAIN | from: DRAW",
            output.getvalue(),
        )

    def test_game_state_uses_readable_battlefield_and_empty_stack_lines(self) -> None:
        state = {
            "turn": 2,
            "phase": "PRECOMBAT_MAIN",
            "active_player": "bob",
            "life_totals": {"alice": 20, "bob": 17},
            "available_mana": {
                "alice": {"W": 0, "U": 0, "B": 0, "R": 0, "G": 0},
                "bob": {"W": 0, "U": 1, "B": 1, "R": 0, "G": 0},
            },
            "hand": {"bob": ["swamp_001"]},
            "battlefield": {
                "alice": [{"id": "mountain_001", "tapped": True}],
                "bob": [],
            },
            "stack": [],
            "graveyard": {"alice": ["lightning_bolt_001"], "bob": []},
        }
        output = io.StringIO()
        with redirect_stdout(output):
            _render_game_state(state)
        rendered = output.getvalue()
        self.assertIn("mountain_001 [tapped]", rendered)
        self.assertIn("bob: U=1 | B=1", rendered)
        self.assertIn("Stack (bottom -> top):\n    (empty)", rendered)
        self.assertIn("bob: (empty)", rendered)

    def test_automatic_trigger_choices_use_first_target_and_server_order(self) -> None:
        order_pdu = {
            "trigger_ids": ["trg_0002", "trg_0001"],
        }
        self.assertEqual(
            _choose_trigger_order(order_pdu, automatic=True),
            ("trg_0002", "trg_0001"),
        )
        choice_pdu = {
            "requires_target": True,
            "legal_targets": ["black_knight_001", "grizzly_bears_001"],
            "optional": False,
        }
        self.assertEqual(
            _choose_trigger_choice(choice_pdu, automatic=True),
            (True, "black_knight_001"),
        )

    def test_trigger_pdus_render_readable_prompts(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            _render_pdu(
                {
                    "type": "TRIGGER_CHOICE",
                    "seq_num": 10,
                    "trigger_id": "trg_0001",
                    "source_id": "gravedigger_001",
                    "effect_summary": "Return a creature card.",
                    "requires_target": True,
                    "legal_targets": ["black_knight_001"],
                }
            )
            _render_pdu(
                {
                    "type": "TRIGGER_ORDER",
                    "seq_num": 11,
                    "player_id": "alice",
                    "trigger_ids": ["trg_0001", "trg_0002"],
                }
            )
        rendered = output.getvalue()
        self.assertIn("[TRIGGER CHOICE] trg_0001 from gravedigger_001", rendered)
        self.assertIn("Legal targets: ['black_knight_001']", rendered)
        self.assertIn("[TRIGGER ORDER] alice", rendered)
        self.assertIn("first is Stack bottom", rendered)


if __name__ == "__main__":
    unittest.main()
