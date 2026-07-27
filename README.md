# MTGNP

Starter implementation of **Magic: The Gathering Multiplayer Network Protocol
(MTGNP) v1.0** for CSNETWK. The project uses an authoritative Python TCP server
and two thin interactive clients. It has no third-party runtime dependencies.

This repository is an implementation foundation, not the final submission.
Transport, catalog validation, lobby/setup/mulligan, hidden-state rendering,
heartbeats, pass-driven turns, basic land/mana handling, the stack, and a small
effect registry are implemented. Full combat, triggers, and the remaining card
effects are explicitly tracked as later milestones.

## What is included

- Four-byte big-endian JSON message framing with the 65,535-byte limit
- Structural definitions for all 25 RFC PDU types and all RFC error codes
- Server-authoritative state with personalized hand visibility
- Exactly two server seats; later connections are refused
- Deck validation against the supplied 58-card/312-instance catalog
- `LOBBY -> GAME_SETUP -> MULLIGAN -> IN_GAME -> GAME_OVER -> LOBBY`
- London mulligans, priority tokens, stale-action rejection, and cleanup discard
- PING/PONG client heartbeat and server priority timeout
- `--verbose` on both programs, plus `verbose on|off` in the client
- Land play, implicit mana-source selection, LIFO stack resolution, and initial
  effects for Lightning Bolt, Shock, Searing Spear, Lava Spike, Flame Slash,
  Counterspell, Cancel, Unsummon, Giant Growth, and Doom Blade
- Sample decks and standard-library unit tests
- The supplied RFC/rubric under `docs/`

## Requirements and setup

- Python 3.11 or newer
- Three terminals for a normal local demo: one server and two clients

From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m unittest discover -s tests -v
```

No package download is required by the project itself.

## Run a local game

Terminal 1:

```powershell
mtgnp-server --verbose
```

Terminal 2:

```powershell
mtgnp-client --player-id alice --deck .\decks\red_burn.json --verbose
```

Terminal 3:

```powershell
mtgnp-client --player-id bob --deck .\decks\blue_control.json --verbose
```

The default host is `127.0.0.1` for clients, the server binds to `0.0.0.0`, and
the default TCP port is `4444`. Use `--host` and `--port` to change them.

Verbose mode is a grading prerequisite. With `--verbose`, every PDU is printed
with direction, peer, timestamp, and formatted JSON on both the client and
server. A client can also switch tracing during a run with `verbose on` or
`verbose off`.

Useful client commands:

```text
keep [CARD_ID ...]
mulligan
pass
land CARD_ID
cast CARD_ID [TARGET ...] --mana R=1,X=1
attack none
discard CARD_ID [CARD_ID ...]
concede
state
help
```

Use `-` when a cast has no target, for example:

```text
cast goblin_guide_001 - --mana R=1
```

## Design

| Module | Responsibility |
| --- | --- |
| `protocol.py` | PDU names, directions, required fields, errors, phases |
| `framing.py` | exact TCP reads/writes and big-endian length framing |
| `catalog.py` | CSV loading, cross-file reconciliation, deck validation |
| `game.py` | authoritative, transport-independent rules and visible state |
| `server.py` | two-client coordination, sequencing, timeouts, broadcasts |
| `client.py` | thin rendering, commands, heartbeat, runtime verbose toggle |

The server owns every game mutation. The client stores only the last visible
state and the current request/priority token. Card data remains in the supplied
CSV files so card facts are not duplicated across source modules.

## Current limitations

- Non-empty attacker declarations, blockers, damage order, first/double strike,
  and combat damage are not implemented yet. `attack none` advances the turn.
- Activated abilities, triggered abilities, trigger ordering/choice, Auras, and
  most of the 58 card effects are not implemented. Permanent spells can enter
  with their printed base stats, but ability text other than basic mana and
  Haste is not applied yet.
- A disconnected player currently loses immediately. The RFC requires a
  reconnect grace period but does not define a reconnect/authentication PDU.
- Mana is paid atomically from untapped sources and no floating mana pool is
  retained. Advanced alternate costs, kicker, suspend, madness, and prevention
  effects remain future work.
- The RFC permits 1-50 card decks but also requires a seven-card opening hand.
  This implementation accepts a 1-6 card deck, draws all available cards, and
  applies `DECK_EMPTY` on the next required draw.

See [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) for the
rubric-ordered roadmap and specification ambiguities.

## Team workflow

Use one short-lived branch per feature, keep commits focused, and require a
second group member to review each pull request. Merge only after the unit tests
pass and the contributor can explain the relevant PDU exchange.

### Work Distribution Matrix

Fill this with real contributions before submission. Do not estimate or assign
credit in advance.

| Task / Feature | Member 1 | Member 2 | Member 3 | Member 4 |
| --- | --- | --- | --- | --- |
| TCP server, framing, dispatch | TBD | TBD | TBD | TBD |
| Lobby, setup, mulligan | TBD | TBD | TBD | TBD |
| Turn and phase engine | TBD | TBD | TBD | TBD |
| Priority, stack, effects | TBD | TBD | TBD | TBD |
| Combat | TBD | TBD | TBD | TBD |
| Client and state rendering | TBD | TBD | TBD | TBD |
| Error handling, heartbeat, disconnect | TBD | TBD | TBD | TBD |
| Tests and interoperability | TBD | TBD | TBD | TBD |
| Documentation and demo preparation | TBD | TBD | TBD | TBD |

## AI Usage

OpenAI Codex was used to:

- read and reconcile the supplied RFC, rubric, and three CSV files;
- identify specification ambiguities and create the rubric-ordered plan;
- generate this initial Python architecture, protocol/catalog foundation,
  client/server scaffold, and tests; and
- run automated checks and correct issues found during validation.

Every group member must review, test, and understand this code before it is
submitted or demonstrated. Update this section whenever another AI tool or a
new AI-assisted task is used.

## Submission documentation

The rubric asks for the README in PDF form. Keep this Markdown file as the
editable source, complete the contribution matrix and limitations at code
freeze, then export the final version as `README.pdf`. Do not freeze the PDF
while project facts are still changing.
