"""Load and reconcile the fixed MTGNP card catalog supplied with the RFC."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .protocol import ErrorCode, ProtocolError

INSTANCE_SUFFIX = re.compile(r"_(\d{3})$")


@dataclass(frozen=True, slots=True)
class CardDefinition:
    base_id: str
    name: str
    card_type: str
    subtype: str
    color: str
    cmc: int
    colored_cost: dict[str, int]
    generic_cost: int
    power: int | None
    toughness: int | None
    copies: int
    effect: str

    @property
    def is_creature(self) -> bool:
        return "Creature" in self.card_type

    @property
    def is_permanent(self) -> bool:
        return self.card_type in {
            "Land",
            "Creature",
            "Artifact Creature",
            "Artifact",
            "Enchantment",
        }

    @property
    def is_instant(self) -> bool:
        return self.card_type == "Instant"


@dataclass(frozen=True, slots=True)
class CardInstance:
    instance_id: str
    base_id: str
    name: str
    card_type: str
    color: str
    copy_number: int


class CardCatalog:
    """Immutable in-memory catalog indexed by base and instance IDs."""

    def __init__(
        self,
        definitions: dict[str, CardDefinition],
        instances: dict[str, CardInstance],
    ) -> None:
        self.definitions = definitions
        self.instances = instances

    @classmethod
    def load(cls, data_dir: Path | None = None) -> "CardCatalog":
        data_dir = data_dir or Path(__file__).resolve().parents[2] / "data"
        master_rows = _read_two_header_csv(data_dir / "master_card_list.csv")
        instance_rows = _read_two_header_csv(data_dir / "card_instances.csv")

        definitions: dict[str, CardDefinition] = {}
        for row in master_rows:
            definition = CardDefinition(
                base_id=row["Card ID Base"],
                name=row["Card Name"],
                card_type=row["Card Type"],
                subtype=row["Subtype"],
                color=row["Color"],
                cmc=int(row["CMC"]),
                colored_cost={color: int(row[color]) for color in "WUBRG"},
                generic_cost=int(row["Generic"]),
                power=_optional_int(row["Power"]),
                toughness=_optional_int(row["Toughness"]),
                copies=int(row["Copies in Set"]),
                effect=row["Simplified Effect"],
            )
            if definition.base_id in definitions:
                raise ValueError(f"Duplicate card definition: {definition.base_id}")
            definitions[definition.base_id] = definition

        instances: dict[str, CardInstance] = {}
        copy_counts: dict[str, int] = {}
        for row in instance_rows:
            instance_id = row["card_id (protocol reference)"]
            match = INSTANCE_SUFFIX.search(instance_id)
            if not match:
                raise ValueError(f"Invalid card instance ID: {instance_id}")
            base_id = INSTANCE_SUFFIX.sub("", instance_id)
            if base_id not in definitions:
                raise ValueError(f"Unknown base ID {base_id!r} for {instance_id!r}")
            definition = definitions[base_id]
            if (
                row["Card Name"] != definition.name
                or row["Card Type"] != definition.card_type
                or row["Color"] != definition.color
            ):
                raise ValueError(f"Catalog mismatch for {instance_id}")
            if instance_id in instances:
                raise ValueError(f"Duplicate card instance: {instance_id}")
            instances[instance_id] = CardInstance(
                instance_id=instance_id,
                base_id=base_id,
                name=row["Card Name"],
                card_type=row["Card Type"],
                color=row["Color"],
                copy_number=int(row["Copy #"]),
            )
            copy_counts[base_id] = copy_counts.get(base_id, 0) + 1

        mismatches = {
            base_id: (definition.copies, copy_counts.get(base_id, 0))
            for base_id, definition in definitions.items()
            if definition.copies != copy_counts.get(base_id, 0)
        }
        if mismatches:
            raise ValueError(f"Master/instance copy-count mismatch: {mismatches}")
        return cls(definitions, instances)

    def definition_for_instance(self, instance_id: str) -> CardDefinition:
        try:
            return self.definitions[self.instances[instance_id].base_id]
        except KeyError as exc:
            raise ProtocolError(
                ErrorCode.ILLEGAL_ACTION,
                f"Unknown card instance ID: {instance_id}.",
            ) from exc

    def validate_deck(self, deck: Iterable[str]) -> list[str]:
        card_ids = list(deck)
        if not 1 <= len(card_ids) <= 50:
            raise ProtocolError(
                ErrorCode.ILLEGAL_DECK,
                f"Deck contains {len(card_ids)} cards; allowed range is 1-50.",
            )
        unknown = sorted({card_id for card_id in card_ids if card_id not in self.instances})
        if unknown:
            raise ProtocolError(
                ErrorCode.ILLEGAL_DECK,
                f"Deck contains unknown card ID(s): {', '.join(unknown)}.",
            )
        if len(card_ids) != len(set(card_ids)):
            raise ProtocolError(
                ErrorCode.ILLEGAL_DECK,
                "A physical card instance ID may appear only once in a deck.",
            )
        return card_ids


def _read_two_header_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if len(rows) < 2:
        raise ValueError(f"{path} does not contain a title and header row")
    header = rows[1]
    return [
        dict(zip(header, row, strict=True))
        for row in rows[2:]
        if any(cell.strip() for cell in row)
    ]


def _optional_int(value: str) -> int | None:
    return None if value in {"", "-"} else int(value)

