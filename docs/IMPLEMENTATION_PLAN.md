# MTGNP Implementation Plan

## Priority order

The project should be completed in rubric-risk order, not card-set order.
Verbose logging is the hard prerequisite, then the 30 networking points and the
40 lifecycle/game-state points establish the protocol foundation on which
combat and effects depend.

| Milestone | Main work | Acceptance gate | Status |
| --- | --- | --- | --- |
| 0. Protocol shell | Framing, PDU validation, verbose logs, catalog | Fragmented/coalesced frames pass; all 25 PDU names declared; both programs log every PDU | Implemented |
| 1. Lifecycle | Two seats, ready/deck checks, setup, London mulligan, hidden state, restart | Two real clients reach first priority; private hands never cross | Implemented |
| 2. Turn + stack | All non-combat phases, tokens, land/mana, LIFO, SBAs, five varied effects | Pass-only full turns and counter-stack integration tests pass | Implemented |
| 3. Combat | Attackers, blockers, damage order, first/double strike, simultaneous damage | Table-driven combat tests cover unblocked, single-blocked, multi-blocked, lethal, summoning sick | Implemented |
| 4. Abilities/effects | Activated and triggered abilities, AP/NAP trigger order, remaining catalog | At least five effects have target, cost, resolution, fizzle, and SBA tests | Base gate implemented; full-catalog bonus remains optional |
| 5. Resilience | Reconnect grace, timeout races, malformed-client isolation, repeat games | Disconnect, reconnect, expiry, heartbeat, and restart integration tests pass | Implemented |
| 6. Submission | Contribution evidence, AI log, limitations, diagrams, finalized README PDF | Clean-clone demo on two machines; every member completes oral walkthrough | Local package implemented; external/manual gates pending |

## Rubric traceability

- **Verbose prerequisite:** retain one tracing path shared by framing calls; do
  not add ad-hoc prints that miss a PDU.
- **TCP and framing (20):** keep framing transport-only; add socket integration
  tests for split prefix, split body, back-to-back PDUs, oversize length, and
  abrupt EOF.
- **Lifecycle (30):** model transitions explicitly and test every invalid PDU in
  every lifecycle state. Preserve the same TCP connections after `GAME_OVER`.
- **Game state/stack/combat (30):** keep rules in `game.py`, never in client
  command handlers. Apply state-based actions after every event and before
  priority.
- **Client/heartbeat (10):** client sends intent only; server updates overwrite
  client state. Test missing PONG independently from priority timeout.
- **Errors/quality (10):** each rejected mutation must prove state is unchanged.
  Comments should explain protocol constraints, not restate Python.
- **Bonus:** attempt only after the 100 base points are demonstrably stable.

## Test strategy

1. Pure unit tests for catalog, PDU structure, phase transitions, targeting,
   mana, stack order, SBAs, and combat math.
2. Async loopback tests with two clients for framing, hidden state, tokens,
   timeouts, disconnect, and same-connection restart.
3. Scripted golden PDU traces derived from the RFC examples.
4. Manual LAN test on two computers.
5. Interoperability test against another group's independently written client
   or server; exchange traffic, not source code.

For every action, include success, stale token, wrong player, wrong phase,
illegal target, and insufficient-resource cases where applicable.

## RFC ambiguities to resolve consistently

These are implementation decisions that the group should confirm with the
instructor and then preserve in tests:

1. **Server sequence numbers:** normative text says increment per PDU sent, but
   examples reuse a number across a broadcast or between a transition and a
   grant. Current code gives each physical server-to-client PDU its own number.
2. **Error/retry sequencing:** the RFC both requires a monotonic server counter
   and describes retrying with the same priority token. Current code sends a
   fresh `PRIORITY_GRANT` after a rejected priority action.
3. **Opening hand versus minimum deck:** legal deck size starts at one, while
   setup mandates drawing seven. Current code draws all available opening cards
   and loses only on a later required empty-library draw.
4. **Visible hand shape:** examples alternate between a bare hand array and the
   schema's `{player_id: [...]}` object. Current code follows section 10.2.2.
5. **Land and priority:** section 7.5 says land play does not require priority,
   while section 5.4 calls `PLAY_LAND` priority-bearing and examples echo a
   grant. Current code requires the active player, a Main Phase, and the current
   token.
6. **Reconnect:** reconnect is required, but no session identity or reconnect
   handshake is defined. The implementation reserves the seat for 30 seconds
   and treats an exact repeat `PLAYER_READY` as the reconnect handshake.
7. **Field spelling:** examples use both `summoning_sick` and
   `summoning_sickness`, and trigger examples vary between `targets` and
   `legal_targets`. Current code follows the section 10 schemas.
8. **Rubric total:** the heading says 120/100 and lists two 10-point bonus rows,
   while the final row says `100 + 10 bonus`. Confirm whether the bonus ceiling
   is 10 or 20.

## Suggested group split

- Networking owner: framing, sockets, timeouts, reconnect, verbose traces.
- State-engine owner: lifecycle, turn/phase machine, visible state, SBAs.
- Rules owner: mana, stack, effects, targets, triggers.
- Combat/client/QA owner: combat engine, client UX, integration tests, docs.

Ownership is for implementation focus only. Every member must review and be
able to explain every module during the demo.

