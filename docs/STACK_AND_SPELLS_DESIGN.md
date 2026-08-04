# Land, mana, spells, and the Stack

The server is authoritative for every gameplay zone and mutation. A client
submits an action using the current `PRIORITY_GRANT` token; the server validates
the complete action before changing the hand, battlefield, mana sources, or
Stack. Rejected actions are atomic and receive an RFC error plus a fresh grant
when the same player still holds priority.

## Land and mana rules

`PLAY_LAND` is legal only for the Active Player in a Precombat or Postcombat
Main Phase, with an empty Stack, and only once per turn. The land moves directly
from hand to battlefield and the player retains priority.

Mana abilities are implicit. `CAST_SPELL` declares its complete colored cost
with `W`, `U`, `B`, `R`, or `G` and its generic cost with `X`. The declared
payment must match the catalog cost. The server first selects enough untapped
basic lands, and taps them only after timing, target, ownership, and full mana
validation succeed. Generic mana can use any remaining basic land.

Personalized state includes `available_mana` for both players, grouped by
color. This is not a persistent mana pool: it is the amount currently
producible by untapped basic lands. The terminal renders the counter after land
play, casting, resolution, draws, and Untap state updates.

## Casting and resolution

Instants may be cast whenever their controller holds priority. Creatures and
artifacts use sorcery timing: the Active Player's Main Phase with an empty
Stack. Lands cannot be cast. Implemented non-permanent effects are explicitly
allowlisted so an unsupported catalog effect receives `ILLEGAL_ACTION` instead
of silently resolving incorrectly.

Each accepted spell receives a unique `stk_####` ID and is appended to the end
of the Stack array. The casting player retains priority. Two consecutive passes
with a non-empty Stack pop exactly one item from the end, broadcast
`STACK_RESOLVE`, publish personalized state, run state-based checks, and grant
priority to the Active Player. Illegal targets at resolution cause `FIZZLE`;
instant cards still move to their controller's graveyard.

## Implemented card effects

| Card | Server-authoritative result |
| --- | --- |
| Lightning Bolt | Three damage to a player or creature |
| Unsummon | Target creature returns to its owner's hand |
| Counterspell | Target spell is removed from the Stack and put in its owner's graveyard |
| Giant Growth | Target creature gets +3/+3 until Cleanup |
| Doom Blade | Target nonblack creature is destroyed |

Creature and artifact spells also resolve as permanents. Creatures receive
catalog power/toughness, zero marked damage, and summoning-sickness state.

After resolution, creatures with zero toughness or lethal marked damage move
to their owner's graveyard. A player at zero or less life loses immediately
with reason `LIFE_ZERO`; if both are at zero or less, the Active Player loses as
specified by the RFC.

Activated abilities, replacement effects, protection, regeneration, triggers,
and catalog effects outside the five listed above are deliberately outside this
milestone.
