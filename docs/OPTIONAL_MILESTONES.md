# MTGNP Optional Milestones

## Goal and priority

The required implementation is complete and its 100 base points must remain
stable. Optional work therefore proceeds in small catalog-driven slices, with a
full regression run after every slice. The first priority is the rubric's
**Full Card Effects** bonus because it is objective and directly testable. A
graphical client is deliberately later: it should consume a complete rules
engine rather than duplicate unfinished rules in a second interface.

The rubric lists two optional categories:

| Bonus category | Rubric value | Project position |
| --- | ---: | --- |
| Full Card Effects | +10 | In progress through milestones O1A-O1F |
| Bonus Features | +10 | Triggered-ability terminal UI already exists; credit remains demonstration-dependent |

## Ordered roadmap

| Milestone | Scope | Acceptance gate | Status |
| --- | --- | --- | --- |
| O1A. Simple spell families | Data-driven spell registry; Shock, Lava Spike, Flame Slash, Searing Spear, Cancel, and Negate | Correct targets, timing, mana, Stack resolution, fizzle, protection, and atomic rejection tests | Implemented |
| O1B. Zones and resources | Naturalize, Terror, Raise Dead, Rampant Growth, Dark Ritual, and Incinerate's regeneration restriction | Zone changes and mana changes are authoritative, visible, reversible only where rules allow, and tested over the network | Next |
| O1C. Choices and conditional resolution | Ponder, Mana Leak, Swords to Plowshares, Path to Exile, Healing Salve, and Mind Rot | Server-issued choices cannot leak hidden information; decline/pay/invalid-choice branches are tested | Planned |
| O1D. Additional and alternative costs | Kicker for Goblin Bushwhacker and Vines of Vastwood, Madness for Reckless Wurm, Suspend for Rift Bolt | Costs and timing are declared in protocol-compatible fields and remain atomic | Planned |
| O1E. Permanent and turn-wide rules | Merfolk Looter, Llanowar Elves, Elvish Mystic, Sol Ring, Troll Ascetic, Mother of Runes, Pacifism, Skullcrack, regeneration, and trample | Activated/static/continuous effects survive state updates and interact correctly with combat, targeting, and Cleanup | Planned |
| O1F. Full-catalog closure | Audit all 58 definitions and 312 instances, including vanilla creatures, lands, and existing keywords/triggers | Every definition is classified as fully implemented; automated coverage matrix has no unsupported effect | Planned |
| O2. Optional UI polish | Card-name/effect help, clearer choice prompts, supported-card view, and demonstration scripts | A new player can complete the demonstration without knowing instance IDs in advance | Planned |
| O3. One creative client extension | Choose a read-only spectator/replay client or a graphical player client | Feature uses the authoritative server, reveals no hidden information, and is demonstrable on two machines | Planned after O1 |
| O4. Bonus demonstration hardening | Scripted decks, feature checklist, clean-clone test, LAN run, and recorded interoperability evidence | Every claimed bonus can be reproduced and explained during checking | Planned |

## O1A implementation record

O1A introduces `src/mtgnp/spells.py` as the shared source of truth for a
supported spell's target family, resolution operation, amount, stat change, or
color restriction. Casting validation and Stack resolution now consult the
same specification, preventing the two stages from silently disagreeing.

The newly playable cards are:

- **Shock:** two damage to a player or creature.
- **Lava Spike:** three damage to a player, at sorcery speed.
- **Flame Slash:** four damage to a creature, at sorcery speed.
- **Searing Spear:** three damage to a player or creature.
- **Cancel:** counters any spell.
- **Negate:** counters only a noncreature spell.

The same refactor retains Lightning Bolt, Unsummon, Counterspell, Giant Growth,
and Doom Blade. Creature-targeting spells now also reject protection at cast
time and recheck protection when resolving. If a creature target leaves the
battlefield before resolution, the spell fizzles and reports no state changes.
The paired `decks/optional_spells_red.json` and
`decks/optional_spells_blue.json` files provide an immediate manual
demonstration of all six additions.

## Rules for every later optional slice

1. The server remains the only rules authority; clients send intent and render
   personalized state.
2. No new PDU type is introduced unless the RFC cannot express the choice with
   an existing action or trigger-choice message.
3. Rejected actions must leave hands, mana sources, battlefield, Stack, and
   priority state unchanged.
4. Each effect needs success, illegal target/timing, insufficient resource,
   response, fizzle, state-based-action, and hidden-information tests where
   applicable.
5. The entire automated suite must pass before a milestone is marked complete.
