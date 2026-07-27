import unittest

from mtgnp.catalog import CardCatalog
from mtgnp.protocol import ProtocolError


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = CardCatalog.load()

    def test_supplied_files_reconcile(self) -> None:
        self.assertEqual(58, len(self.catalog.definitions))
        self.assertEqual(312, len(self.catalog.instances))
        self.assertEqual(
            312,
            sum(definition.copies for definition in self.catalog.definitions.values()),
        )

    def test_known_card_cost_and_stats(self) -> None:
        bolt = self.catalog.definition_for_instance("lightning_bolt_001")
        bears = self.catalog.definition_for_instance("grizzly_bears_004")
        self.assertEqual({"W": 0, "U": 0, "B": 0, "R": 1, "G": 0}, bolt.colored_cost)
        self.assertEqual((2, 2), (bears.power, bears.toughness))

    def test_deck_rejects_unknown_or_duplicate_instances(self) -> None:
        with self.assertRaises(ProtocolError):
            self.catalog.validate_deck(["not_a_card_001"])
        with self.assertRaises(ProtocolError):
            self.catalog.validate_deck(["mountain_001", "mountain_001"])


if __name__ == "__main__":
    unittest.main()

