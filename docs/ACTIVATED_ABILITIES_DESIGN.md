# Activated abilities

This milestone implements the RFC `ACTIVATE_ABILITY` path as a server-owned
Stack action. A client identifies a battlefield `source_id`, its zero-based
`ability_index`, targets, and the complete `cost_payment`. The server validates
every part of the request before tapping any permanent or pushing an item.

## Activation sequence

1. The activating player must hold the current priority token.
2. The source must be a permanent controlled by that player.
3. The ability index, targets, tap status, summoning sickness, and declared
   payment are validated without changing state.
4. Tap and mana costs are paid atomically.
5. An `ABILITY` item is pushed onto the shared LIFO Stack and the activating
   player retains priority.
6. After both players pass, targets are checked again. An illegal target makes
   the ability fizzle; otherwise the effect resolves and state-based actions run.

The ability remains on the Stack independently of its source. Removing the
source after activation therefore does not remove the ability.

## Supported ability index 0

| Card | Cost | Target and effect |
| --- | --- | --- |
| Prodigal Sorcerer | Tap | Deal 1 damage to a player or creature |
| Royal Assassin | Tap | Destroy target tapped creature |
| Millstone | Pay 2, Tap | Target player mills up to two cards |
| Rod of Ruin | Pay 3, Tap | Deal 1 damage to a player or creature |

Generic mana uses the project's existing `X` payment key. For example,
Millstone sends `{"tap": true, "mana": {"X": 2}}`. Mana is selected and
tapped atomically from untapped basic lands.

## Deferred ability families

Basic land mana production remains implicit in `CAST_SPELL` and
`ACTIVATE_ABILITY` payments, as required by MTGNP 1.0. Llanowar Elves, Elvish
Mystic, and Sol Ring will become implicit mana sources in a later resource
extension.

Merfolk Looter needs a post-draw discard choice, Mother of Runes needs a color
choice, and Troll Ascetic needs a regeneration replacement shield. Those cards
are rejected as unsupported until the corresponding decision or replacement
framework exists. Representative triggered abilities now use the separate RFC
decision PDUs and are documented in `TRIGGERED_ABILITIES_DESIGN.md`.
