from __future__ import annotations

import unittest
from pathlib import Path

from mtgnp.catalog import CatalogError, load_catalog


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog(PROJECT_ROOT / "data")

    def test_catalog_counts_and_cross_file_reconciliation(self) -> None:
        self.assertEqual(len(self.catalog.definitions), 58)
        self.assertEqual(len(self.catalog.instances), 312)

    def test_instance_resolves_to_master_definition(self) -> None:
        card = self.catalog.definition_for_instance("lightning_bolt_001")
        self.assertEqual(card.base_id, "lightning_bolt")
        self.assertEqual(card.name, "Lightning Bolt")
        self.assertEqual(card.colored_cost["R"], 1)

    def test_valid_deck_is_returned_as_immutable_tuple(self) -> None:
        deck = self.catalog.validate_deck(["mountain_001", "lightning_bolt_001"])
        self.assertEqual(deck, ("mountain_001", "lightning_bolt_001"))

    def test_deck_size_is_enforced(self) -> None:
        with self.assertRaises(CatalogError):
            self.catalog.validate_deck([])
        with self.assertRaises(CatalogError):
            self.catalog.validate_deck(["mountain_001"] * 51, require_unique_instances=False)

    def test_unknown_and_duplicate_instances_are_rejected(self) -> None:
        with self.assertRaises(CatalogError):
            self.catalog.validate_deck(["not_a_card_001"])
        with self.assertRaises(CatalogError):
            self.catalog.validate_deck(["mountain_001", "mountain_001"])

    def test_duplicate_policy_can_be_relaxed_pending_clarification(self) -> None:
        deck = self.catalog.validate_deck(
            ["mountain_001", "mountain_001"], require_unique_instances=False
        )
        self.assertEqual(len(deck), 2)


if __name__ == "__main__":
    unittest.main()

