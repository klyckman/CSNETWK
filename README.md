# MTGNP

Clean implementation of the Magic: The Gathering Multiplayer Network Protocol
(MTGNP) v1.0 for CSNETWK.

## Current milestone

This project currently implements the shared protocol/data and TCP transport
foundation:

- validated loading of the supplied 58-card, 312-instance catalog;
- reconciliation of the master list, instance list, and color summary;
- legal deck validation for 1 to 50 known card-instance IDs;
- constants for every RFC PDU, error code, lifecycle state, and turn step;
- sender and required-field definitions for all 25 PDU types;
- UTF-8 JSON PDU encoding, decoding, and base structural validation;
- exact-byte TCP reads and four-byte big-endian length framing;
- rejection of frames larger than the 65,535-byte RFC limit;
- a thread-safe validated connection wrapper for client and server use;
- complete, labeled PDU tracing that can be toggled at runtime;
- standard-library unit tests for the foundation.

The project also includes its first runnable vertical slice:

- a TCP server on port `4444` with exactly two reusable seats;
- immediate closure/refusal of additional connections while both seats are full;
- a minimal client that submits `PLAYER_READY` and renders lobby state;
- legal deck and unique player-ID enforcement;
- per-PDU server sequence numbers and RFC `ERROR` responses;
- `PING`/`PONG` handling;
- `--verbose` support in both executable programs;
- two sample legal deck files.

The setup/mulligan state engine now also provides:

- automatic transition from two ready players into `GAME_SETUP`;
- server-side shuffling, 20 starting life, opening hands, and random first player;
- personalized state that never reveals the opponent's hand;
- sequence-token-validated London mulligans and bottom-card selection;
- transition to turn 1 `UNTAP` after both players keep.

The turn engine foundation additionally supports:

- automatic Untap, Draw, Cleanup, active-player switching, and turn increments;
- the first player's turn-1 draw skip;
- priority grants, tokens, consecutive passes, and phase advancement;
- empty-attacker combat traversal through End of Combat;
- cleanup discard requests and graveyard movement;
- empty-library `GAME_OVER` followed by a same-connection return to Lobby;
- interactive decisions or unattended operation with `--auto-pass`.

The resource, spell, and Stack milestone now supports:

- one active-player land play during either Main Phase;
- implicit, atomic tapping of basic lands from declared `mana_payment`;
- visible per-player mana availability from untapped basic lands;
- instant-speed and sorcery-speed timing checks;
- server-authoritative `CAST_SPELL`, `STACK_PUSH`, and LIFO `STACK_RESOLVE`;
- creatures and artifacts entering the battlefield after resolution;
- Lightning Bolt, Unsummon, Counterspell, Giant Growth, and Doom Blade;
- zero-toughness, lethal-damage, and `LIFE_ZERO` state-based actions;
- interactive `pass`, `land`, and `cast` terminal commands;
- two longer demonstration decks for manual Stack testing.

The creature-combat milestone additionally supports:

- atomic attacker validation, tapping, summoning sickness, haste, vigilance,
  and defender restrictions;
- defender-controlled blocker assignments, including multiple blockers;
- flying and protection-based blocking restrictions;
- attacker-selected damage order for multi-blocker combat;
- first-strike and regular simultaneous damage steps;
- server-broadcast `COMBAT_DAMAGE_RESULT`, creature deaths, player damage, and
  combat-based `LIFE_ZERO` wins;
- interactive attacker, blocker, and damage-order terminal prompts.

Activated abilities, triggers, concession, enforced priority timeouts, and
disconnect grace remain later milestones.

## Run the tests

From this directory in PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

The project has no third-party runtime or test dependencies.

## Run the lobby locally

Open three PowerShell terminals in this directory. Set the source path in each:

```powershell
$env:PYTHONPATH = "src"
```

Terminal 1:

```powershell
python -m mtgnp.server --verbose
```

Terminal 2:

```powershell
python -m mtgnp.client --player-id alice --deck decks/stack_demo_red_green.json --auto-keep
```

Terminal 3:

```powershell
python -m mtgnp.client --player-id bob --deck decks/stack_demo_blue_black.json --auto-keep
```

Each client displays its private opening hand and asks whether to keep or
mulligan. Add `--auto-keep` to both client commands for a non-interactive setup
smoke test. Add `--auto-pass` as well to let the turn engine run unattended.
With the eight-card sample decks, it eventually demonstrates cleanup discard
and `DECK_EMPTY` game over, then returns both connections to Lobby. Stop a
program with `Ctrl+C`.

## Try land play and spell casting

For a longer manual game, start the clients without `--auto-pass` and use the
Stack demo decks:

```powershell
python -m mtgnp.client --player-id alice --deck decks/stack_demo_red_green.json --auto-keep
python -m mtgnp.client --player-id bob --deck decks/stack_demo_blue_black.json --auto-keep
```

When a client receives priority, it accepts these commands:

```text
pass
land mountain_001
cast goblin_guide_001
cast lightning_bolt_001 bob
cast counterspell_001 stk_0001
cast doom_blade_001 goblin_guide_001
```

Mana payment is inferred from the catalog. An explicit payment can be supplied
when diagnosing protocol behavior, for example:

```text
cast doom_blade_001 goblin_guide_001 --mana B=1,X=1
```

Combat decisions use guided prompts. Attacker declarations accept creature
IDs separated by spaces or `none`. Blocker declarations use
`BLOCKER_ID=ATTACKER_ID` pairs, for example:

```text
phantasmal_bear_001=goblin_guide_001
```

When several creatures block one attacker, the attacking client is prompted
to list those blocker IDs from first damaged to last.

## Data ownership

The CSV files under `data/` are local copies of the instructor-supplied
catalog. `master_card_list.csv` defines card facts, `card_instances.csv`
defines the legal IDs exchanged by the protocol, and `color_summary.csv` is
used as a consistency check only.

See `docs/FOUNDATION_DECISIONS.md` for specification interpretations that must
remain visible to the group and be confirmed with the instructor. The framing
and concurrency decisions are described in `docs/TRANSPORT_DESIGN.md`.
The current state and scope boundary are described in `docs/LOBBY_DESIGN.md`.
Setup, hidden-state, and mulligan decisions are described in
`docs/GAME_SETUP_DESIGN.md`.
Turn, priority-pass, and cleanup behavior are described in
`docs/TURN_ENGINE_DESIGN.md`.
Land, mana, spell, Stack, and effect behavior are described in
`docs/STACK_AND_SPELLS_DESIGN.md`.
Attacker, blocker, and combat-damage behavior is described in
`docs/COMBAT_DESIGN.md`.
