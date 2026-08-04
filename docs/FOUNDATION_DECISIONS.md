# Foundation decisions

These decisions keep the first implementation milestone internally consistent.
They should be confirmed with the instructor before interoperability testing.

## Canonical protocol source

Section 10.2 PDU schemas are treated as the canonical source for wire field
names when the prose and examples disagree. In particular, stack messages use
`source` and `controller`, and stack state changes use `change_type`.

Every PDU must contain `type` and a non-negative integer `seq_num`. During the
current lobby milestone, each ordinary server PDU gets the next server counter
value. `PONG` echoes the corresponding `PING`, and `ERROR` echoes the rejected
action's number when one was available, following their Section 10.2 schemas.
The RFC's priority-token increment/reuse policy is internally inconsistent.
The current priority engine assigns a fresh server sequence number to every
`PRIORITY_GRANT`; the client echoes that grant. After an invalid pass from the
current holder, the server sends a fresh grant and expects its new number
rather than reusing the old number. This preserves monotonic physical server
messages and should be confirmed for interoperability.

## Catalog and decks

The master list defines card properties. The instance list defines legal
protocol IDs. The color summary is not used by gameplay.

Deck validation currently requires:

- 1 to 50 entries;
- every entry must be a known instance ID;
- instance IDs must be unique within one deck by default.

The RFC does not explicitly state whether the same physical instance ID can be
repeated or shared by both players. The validator exposes an option to relax
the within-deck uniqueness rule until this is clarified. Cross-player ownership
is now enforced by the lobby: submitted decks cannot share an instance ID.
Permanent and Stack targets use global instance IDs and must resolve to exactly
one owner.

## Scope boundary

The protocol layer checks required fields and basic JSON shapes. The
authoritative game engine separately checks priority tokens, timing, ownership,
targets, mana sources, the Stack, and current game state before applying a
request atomically.

## Opening hands for short decks

The RFC permits 1-to-50-card decks but instructs the server to draw seven cards
during setup. For a legal deck containing fewer than seven cards, the current
implementation draws every available card and does not declare a loss during
setup. Empty-library loss remains tied to a later required draw. Confirm this
interpretation with the instructor.
