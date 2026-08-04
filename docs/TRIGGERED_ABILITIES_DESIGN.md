# Triggered abilities

This milestone implements the RFC trigger-decision path without giving either
client authority over the game state. Trigger detection happens immediately
after the event that caused it. The server pauses priority, collects any target
choices and simultaneous-trigger orders, places the accepted triggers on the
shared Stack, publishes the new state, and only then resumes priority.

## Detection and placement sequence

1. A spell is cast, an ability is activated, a permanent enters, or attackers
   are declared.
2. The game engine records every supported trigger caused by that event.
3. A targeted trigger sends `TRIGGER_CHOICE` to its controller. The response
   must echo the request sequence and choose a currently legal target.
4. If one player controls several simultaneous triggers, that player receives
   `TRIGGER_ORDER` and returns every trigger ID exactly once. The first ID is
   placed closest to the Stack bottom.
5. Active Player triggers are placed first, followed by Nonactive Player
   triggers. The Nonactive Player's triggers are therefore nearer the top and
   resolve first, matching APNAP order.
6. Every placed item is broadcast as `STACK_PUSH` with item type
   `TRIGGER_ABILITY`. Normal priority passes and LIFO `STACK_RESOLVE` then
   control resolution.

Invalid, stale, duplicate, missing, wrong-player, and illegal-target responses
receive the RFC trigger-specific error code and a fresh decision request. No
partial order or target mutation is accepted.

## Supported catalog triggers

| Card | Event | Resolution |
| --- | --- | --- |
| Goblin Guide | Attacks | Reveal the defending player's top card; if it is a land, put it into that player's hand |
| Monastery Swiftspear | Its controller casts a noncreature spell | It gets +1/+1 until Cleanup |
| Phantasmal Bear | It becomes the target of a spell or ability | Sacrifice it; the original Stack item remains and may later fizzle |
| Gray Merchant of Asphodel | Enters the battlefield | Opponent loses life equal to the controller's black devotion; controller gains that much |
| Gravedigger | Enters the battlefield | Controller chooses a target creature card in their graveyard and returns it to hand on resolution |

Targets are rechecked during resolution. Gravedigger fizzles if its selected
card leaves the graveyard first. Non-targeted triggers still resolve if their
source left the battlefield; their effect simply does as much as possible.

Goblin Bushwhacker is intentionally deferred. Its enter-the-battlefield
trigger depends on whether its kicker cost was paid, and the current
`CAST_SPELL` request has no kicker-decision field. Implementing it safely first
requires the later additional-cost framework.

The trigger registry and event mapping live in `src/mtgnp/triggers.py`; the
authoritative queue, APNAP ordering, target validation, placement, and effects
remain in `src/mtgnp/game.py`.
