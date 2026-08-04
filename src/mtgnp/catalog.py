"""Load and reconcile the instructor-supplied MTGNP card catalog."""

from __future__ import annotations

import csv
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence


MASTER_FILE = "master_card_list.csv"
INSTANCES_FILE = "card_instances.csv"
SUMMARY_FILE = "color_summary.csv"

MANA_COLORS = ("W", "U", "B", "R", "G")
INSTANCE_ID_PATTERN = re.compile(r"^(?P<base>[a-z0-9_]+)_(?P<copy>[0-9]{3})$")


class CatalogError(ValueError):
    """Raised when catalog data or a submitted deck is invalid."""


@dataclass(frozen=True, slots=True)
class CardDefinition:
    base_id: str
    name: str
    card_type: str
    subtype: str | None
    color: str
    mana_value: int
    colored_cost: Mapping[str, int]
    generic_cost: int
    power: int | None
    toughness: int | None
    copies_in_set: int
    simplified_effect: str


@dataclass(frozen=True, slots=True)
class CardInstance:
    card_id: str
    base_id: str
    card_name: str
    card_type: str
    color: str
    copy_number: int


@dataclass(frozen=True, slots=True)
class CardCatalog:
    definitions: Mapping[str, CardDefinition]
    instances: Mapping[str, CardInstance]

    def definition_for_instance(self, card_id: str) -> CardDefinition:
        try:
            instance = self.instances[card_id]
        except KeyError as exc:
            raise CatalogError(f"Unknown card instance: {card_id}") from exc
        return self.definitions[instance.base_id]

    def validate_deck(
        self,
        card_ids: Sequence[str],
        *,
        require_unique_instances: bool = True,
    ) -> tuple[str, ...]:
        """Validate an RFC deck list and return an immutable normalized copy."""

        normalized = tuple(card_ids)
        if not 1 <= len(normalized) <= 50:
            raise CatalogError(
                f"Deck must contain 1 to 50 cards; received {len(normalized)}."
            )

        non_strings = [value for value in normalized if not isinstance(value, str)]
        if non_strings:
            raise CatalogError("Every deck entry must be a card_id string.")

        unknown = sorted({card_id for card_id in normalized if card_id not in self.instances})
        if unknown:
            raise CatalogError(f"Deck contains unknown card IDs: {', '.join(unknown)}")

        if require_unique_instances:
            duplicates = sorted(
                card_id for card_id, count in Counter(normalized).items() if count > 1
            )
            if duplicates:
                raise CatalogError(
                    "Deck repeats physical card instance IDs: " + ", ".join(duplicates)
                )

        return normalized


def _read_decorated_csv(path: Path) -> tuple[str, list[dict[str, str]]]:
    """Read a CSV whose first row is a title and second row is the header."""

    try:
        handle = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise CatalogError(f"Cannot read catalog file {path}: {exc}") from exc

    with handle:
        title = handle.readline().strip()
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise CatalogError(f"Catalog file has no header row: {path}")
        rows = [dict(row) for row in reader if any((value or "").strip() for value in row.values())]
    return title, rows


def _require_columns(path: Path, rows: list[dict[str, str]], required: set[str]) -> None:
    if not rows:
        raise CatalogError(f"Catalog file contains no data rows: {path}")
    missing = required.difference(rows[0])
    if missing:
        raise CatalogError(f"{path.name} is missing columns: {', '.join(sorted(missing))}")


def _parse_int(value: str, field: str, row_name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise CatalogError(f"Invalid {field} for {row_name}: {value!r}") from exc
    if number < 0:
        raise CatalogError(f"{field} cannot be negative for {row_name}.")
    return number


def _parse_stat(value: str, field: str, row_name: str) -> int | None:
    if value in ("", "-"):
        return None
    return _parse_int(value, field, row_name)


def _load_definitions(path: Path) -> dict[str, CardDefinition]:
    _, rows = _read_decorated_csv(path)
    required = {
        "Card ID Base",
        "Card Name",
        "Card Type",
        "Subtype",
        "Color",
        "CMC",
        "Generic",
        "Power",
        "Toughness",
        "Copies in Set",
        "Simplified Effect",
        *MANA_COLORS,
    }
    _require_columns(path, rows, required)

    definitions: dict[str, CardDefinition] = {}
    for row in rows:
        base_id = row["Card ID Base"].strip()
        if not base_id or not re.fullmatch(r"[a-z0-9_]+", base_id):
            raise CatalogError(f"Invalid base card ID: {base_id!r}")
        if base_id in definitions:
            raise CatalogError(f"Duplicate base card ID: {base_id}")

        colored_cost = MappingProxyType(
            {color: _parse_int(row[color], color, base_id) for color in MANA_COLORS}
        )
        definitions[base_id] = CardDefinition(
            base_id=base_id,
            name=row["Card Name"].strip(),
            card_type=row["Card Type"].strip(),
            subtype=row["Subtype"].strip() or None,
            color=row["Color"].strip(),
            mana_value=_parse_int(row["CMC"], "CMC", base_id),
            colored_cost=colored_cost,
            generic_cost=_parse_int(row["Generic"], "Generic", base_id),
            power=_parse_stat(row["Power"], "Power", base_id),
            toughness=_parse_stat(row["Toughness"], "Toughness", base_id),
            copies_in_set=_parse_int(row["Copies in Set"], "Copies in Set", base_id),
            simplified_effect=row["Simplified Effect"].strip(),
        )
    return definitions


def _load_instances(
    path: Path, definitions: Mapping[str, CardDefinition]
) -> dict[str, CardInstance]:
    _, rows = _read_decorated_csv(path)
    id_column = "card_id (protocol reference)"
    required = {id_column, "Card Name", "Card Type", "Color", "Copy #"}
    _require_columns(path, rows, required)

    instances: dict[str, CardInstance] = {}
    for row in rows:
        card_id = row[id_column].strip()
        match = INSTANCE_ID_PATTERN.fullmatch(card_id)
        if not match:
            raise CatalogError(f"Invalid card instance ID: {card_id!r}")
        if card_id in instances:
            raise CatalogError(f"Duplicate card instance ID: {card_id}")

        base_id = match.group("base")
        copy_number = _parse_int(row["Copy #"], "Copy #", card_id)
        if copy_number != int(match.group("copy")):
            raise CatalogError(f"Copy number does not match ID suffix for {card_id}.")

        try:
            definition = definitions[base_id]
        except KeyError as exc:
            raise CatalogError(f"Instance {card_id} has no master definition.") from exc

        expected = (definition.name, definition.card_type, definition.color)
        actual = (
            row["Card Name"].strip(),
            row["Card Type"].strip(),
            row["Color"].strip(),
        )
        if actual != expected:
            raise CatalogError(
                f"Instance metadata for {card_id} does not match its master definition."
            )

        instances[card_id] = CardInstance(
            card_id=card_id,
            base_id=base_id,
            card_name=actual[0],
            card_type=actual[1],
            color=actual[2],
            copy_number=copy_number,
        )

    counts = Counter(instance.base_id for instance in instances.values())
    for base_id, definition in definitions.items():
        actual_count = counts.get(base_id, 0)
        if actual_count != definition.copies_in_set:
            raise CatalogError(
                f"{base_id} declares {definition.copies_in_set} copies but has "
                f"{actual_count} instance rows."
            )
    return instances


def _validate_summary(
    path: Path,
    definitions: Mapping[str, CardDefinition],
    instances: Mapping[str, CardInstance],
) -> None:
    _, rows = _read_decorated_csv(path)
    required = {
        "Color",
        "Color Code",
        "Unique Card Types",
        "Total Copies in Set",
        "Card Names",
    }
    _require_columns(path, rows, required)

    total_row = next((row for row in rows if row["Color"].strip() == "TOTAL"), None)
    if total_row is None:
        raise CatalogError("Color summary is missing its TOTAL row.")
    if _parse_int(total_row["Unique Card Types"], "Unique Card Types", "TOTAL") != len(
        definitions
    ):
        raise CatalogError("Color summary definition total does not match the master list.")
    if _parse_int(total_row["Total Copies in Set"], "Total Copies", "TOTAL") != len(
        instances
    ):
        raise CatalogError("Color summary copy total does not match the instance list.")

    for row in rows:
        color_code = row["Color Code"].strip()
        if not color_code:
            continue
        definitions_for_color = [
            definition for definition in definitions.values() if definition.color == color_code
        ]
        instances_for_color = [
            instance for instance in instances.values() if instance.color == color_code
        ]
        expected_types = _parse_int(
            row["Unique Card Types"], "Unique Card Types", row["Color"].strip()
        )
        expected_copies = _parse_int(
            row["Total Copies in Set"], "Total Copies", row["Color"].strip()
        )
        if len(definitions_for_color) != expected_types:
            raise CatalogError(f"Color summary type count mismatch for {color_code}.")
        if len(instances_for_color) != expected_copies:
            raise CatalogError(f"Color summary copy count mismatch for {color_code}.")


def load_catalog(data_directory: str | Path) -> CardCatalog:
    """Load all three catalog CSVs and verify their cross-file consistency."""

    data_path = Path(data_directory)
    definitions = _load_definitions(data_path / MASTER_FILE)
    instances = _load_instances(data_path / INSTANCES_FILE, definitions)
    _validate_summary(data_path / SUMMARY_FILE, definitions, instances)
    return CardCatalog(
        definitions=MappingProxyType(definitions),
        instances=MappingProxyType(instances),
    )

