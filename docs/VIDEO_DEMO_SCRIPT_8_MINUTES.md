# MTGNP base-rubric video demo script

**Target duration:** 7 minutes 35 seconds  
**Hard stop:** 8 minutes  
**Scope:** Base 100 points only; do not spend video time on bonus criteria.

This runbook separates live protocol evidence from behavior that cannot be
proved by normal gameplay output. Text in **Say** is suggested narration. Text
in **Look for** is the exact terminal evidence the reviewer should be able to
see.

## Before recording (not included in the eight minutes)

1. Arrange four readable terminal panes:
   - server;
   - Alice client;
   - Bob client;
   - evidence/tests.
2. Open every terminal in the `MTGNP` directory and run:

   ```powershell
   $env:PYTHONPATH = "src"
   ```

3. Prepare these commands so they can be pasted without typing delays:

   **Server**

   ```powershell
   python -m mtgnp.server --verbose
   ```

   **Alice (manual mulligan demonstration)**

   ```powershell
   python -m mtgnp.client --player-id alice --deck decks/stack_demo_red_green.json --verbose
   ```

   **Bob**

   ```powershell
   python -m mtgnp.client --player-id bob --deck decks/stack_demo_blue_black.json --auto-keep --verbose
   ```

   **Third-client refusal**

   ```powershell
   python -m mtgnp.client --player-id charlie --deck decks/red_starter.json --auto-keep --verbose
   ```

4. Do one unrecorded practice run. Use the actual card instance IDs displayed
   in each opening hand. Do not promise a particular starting player because
   the server uses a random coin flip.
5. Keep a timer visible to the presenter. If a live card or combat example is
   unavailable because of the shuffled hand, move on immediately; the targeted
   evidence command near the end covers it deterministically.

## Timed recording script

### 0:00-0:35 — Start verbose server and two clients

**Do**

1. Start the server.
2. Start Alice and Bob.
3. Keep all three panes visible.

**Say**

> Both server and clients are running with the required `--verbose` startup
> toggle. Every PDU is labelled with direction, type, sequence number, peer,
> and its complete payload.

**Look for**

```text
MTGNP server listening on 127.0.0.1:4444
Accepted seat_1
Accepted seat_2
SEND C->S ... type=PLAYER_READY seq_num=1
RECEIVE C->S ... type=PLAYER_READY seq_num=1
```

This proves the verbose prerequisite, port 4444, two accepted clients, visible
PDU direction, and the required `type` and `seq_num` fields.

### 0:35-1:00 — Two-seat limit and Lobby

**Do**

1. Start the prepared Charlie command in the evidence terminal.
2. Return focus to the server and the two accepted clients.

**Say**

> The server accepts exactly two active clients. A third TCP connection is
> refused, while Alice and Bob remain in the authoritative Lobby.

**Look for**

```text
Refused additional client
type=GAME_STATE_UPDATE
"phase": "LOBBY"
"players_connected": 2
"players_ready": 1
"waiting_for": ["seat_2"]
```

The precise ready count may pass quickly because both commands start close
together. It is enough to pause on one Lobby update and the refusal line.

### 1:00-1:45 — Setup, London Mulligan, and hidden information

**Do**

1. In Alice, enter `m` at the first mulligan prompt.
2. At Alice's redrawn hand, enter `k`.
3. When asked to bottom one card, paste any one card ID from Alice's displayed
   hand.
4. Bob keeps automatically.
5. Briefly compare Alice's and Bob's state panes.

**Say**

> Both players begin at 20 life and receive seven-card personalized hands.
> Alice demonstrates the London Mulligan: redraw seven, then bottom one card.
> Each `GAME_STATE_UPDATE` contains only the receiving player's hand, while
> public counts, battlefield, graveyard, library, and Stack remain visible.
> The active player shown here was selected by the server's random coin flip.

**Look for**

```text
Keep this hand or mulligan? [k/m]:
type=MULLIGAN_CHOICE
"keep": false
"phase": "MULLIGAN"
Life: alice=20 | bob=20
Your hand (7):
```

After the redraw, also show a second `MULLIGAN_CHOICE` with `"keep": true` and
one ID in `cards_to_bottom`. In each raw state PDU, the `hand` object should
contain only that client's player ID.

### 1:45-3:25 — Phases, client rendering, priority, and Stack

Use whichever client the terminal identifies as the turn owner.

**Do**

1. Pass through Upkeep and Draw priority windows until `PRECOMBAT_MAIN`.
2. At the active player's `Action:` prompt, play a land shown in that hand:

   ```text
   land CARD_ID
   ```

3. If the hand and available mana permit, cast one displayed spell or creature:

   ```text
   cast CARD_ID [TARGET]
   ```

4. The caster retains priority; enter `pass` there. Enter `pass` on the other
   client so the top Stack item resolves.
5. Pause briefly on the resulting state update.

**Say**

> The server broadcasts every phase transition and grants priority with a
> deadline token. The client sends intent only. The server validates the echoed
> priority sequence number, pushes the spell, resolves the Stack after both
> players pass, and broadcasts the authoritative personalized state.

**Look for**

```text
type=PHASE_TRANSITION
"to_phase": "UPKEEP"
"to_phase": "DRAW"
"to_phase": "PRECOMBAT_MAIN"
[PRIORITY] Granted to ... for up to 60000 ms.
type=PLAY_LAND
type=CAST_SPELL
type=STACK_PUSH
Stack (bottom -> top):
type=STACK_RESOLVE
[STATE] Turn ... | PRECOMBAT_MAIN | Active: ...
```

If no legal spell is available, show `PLAY_LAND`, priority passing, and the
phase transition. Do not waste recording time restarting; the deterministic
tests at 5:35 show Stack resolution and five effects.

### 3:25-4:25 — Combat sequence

**Do**

1. Both players pass the remaining empty priority windows until combat.
2. At the dedicated prompt, enter eligible creature IDs separated by spaces,
   or `none` if no creature is eligible:

   ```text
   Attacker IDs separated by spaces, or 'none':
   ```

3. At the defending client, enter blocker mappings when offered, or `none`:

   ```text
   BLOCKER_ID=ATTACKER_ID
   ```

4. If multiple blockers exist, enter every blocker ID in lethal-damage order.

**Say**

> Combat uses dedicated request tokens rather than the ordinary action prompt.
> The server validates summoning sickness and tapping, attackers, blockers,
> damage order, optional first-strike damage, and regular combat damage.

**Look for**

```text
"to_phase": "BEGIN_COMBAT"
"to_phase": "DECLARE_ATTACKERS"
type=DECLARE_ATTACKERS
"to_phase": "DECLARE_BLOCKERS"
type=DECLARE_BLOCKERS
"to_phase": "ASSIGN_DAMAGE_ORDER"
type=ASSIGN_DAMAGE_ORDER
type=COMBAT_DAMAGE_RESULT
[COMBAT DAMAGE]
Life totals:
Creatures died:
```

Some steps are conditionally skipped when there are no attackers, blockers, or
first-strike creatures. The targeted combat tests later prove those branches.

### 4:25-4:55 — PING/PONG heartbeat

**Do**

Pause on the first heartbeat pair that has appeared naturally during the live
game. Do not wait more than ten seconds specifically for this shot; one should
already exist because the client sends PING every 30 seconds.

**Say**

> Heartbeats run independently from turns and input prompts. PING and its
> matching PONG use the same sequence number and timestamp. They remain fully
> visible in verbose mode but are compact and separated from gameplay.

**Look for**

```text
+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
SEND C->S ... type=PING seq_num=N timestamp=T
RECEIVE S->C ... type=PONG seq_num=N timestamp=T
+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
[GAME] Turn ... | Phase: ... | Owner: ... | Mana: ... | Stack: ...
Action (continue; existing input is preserved):
```

### 4:55-5:35 — ERROR handling, GAME_OVER, and same-session restart

If a server `ERROR` was already produced by an illegal live action, pause on it.
Otherwise, rely on the deterministic error test in the next segment rather
than improvising malformed input.

**Do**

1. At either active `Action:` prompt, enter:

   ```text
   concede
   ```

2. Keep both clients visible through GAME_OVER and the Lobby update.

**Say**

> CONCEDE may be sent at any priority prompt. The server broadcasts GAME_OVER
> with the correct winner, loser, and reason, returns both clients to Lobby,
> retains the existing TCP sessions, and the clients submit fresh PLAYER_READY
> PDUs for another game.

**Look for**

```text
type=CONCEDE
type=GAME_OVER
"reason": "CONCEDE"
GAME OVER: ... (CONCEDE)
"phase": "LOBBY"
Re-entered the Lobby and resubmitted the same deck.
type=PLAYER_READY
```

For an actual error PDU, the visible client cue is:

```text
type=ERROR
"code": "STALE_ACTION"          # or another RFC error code
[SERVER ERROR: STALE_ACTION]
```

A locally printed `Invalid action` is not enough; that means the command never
reached the server.

### 5:35-7:05 — Deterministic evidence for non-visible and conditional paths

**Do**

Paste this prepared command into the evidence terminal:

```powershell
python -m unittest -v `
  tests.test_framing `
  tests.test_game.GameSetupTests.test_setup_initializes_life_shuffles_and_draws_personalized_hands `
  tests.test_game.GameSetupTests.test_redraw_then_keep_bottoms_one_card `
  tests.test_lobby.LobbyTests.test_illegal_deck_is_reported_as_illegal_deck `
  tests.test_server_integration.ServerIntegrationTests.test_two_player_lobby_third_refusal_errors_updates_and_ping `
  tests.test_server_integration.ServerIntegrationTests.test_player_can_reconnect_during_game_and_resume_priority `
  tests.test_server_integration.ServerIntegrationTests.test_pass_only_engine_completes_turn_one_and_opens_turn_two_upkeep `
  tests.test_server_integration.ServerIntegrationTests.test_concede_rejects_spoofing_then_restarts_on_same_connections `
  tests.test_server_integration.ServerIntegrationTests.test_land_and_lightning_bolt_flow_through_the_network_stack `
  tests.test_server_integration.ServerIntegrationTests.test_attack_block_and_combat_damage_flow_across_both_clients `
  tests.test_spell_stack.SpellStackTests.test_counterspell_resolves_first_and_removes_the_target_spell `
  tests.test_spell_stack.SpellStackTests.test_unsummon_giant_growth_and_doom_blade_effects `
  tests.test_spell_stack.SpellStackTests.test_lethal_spell_damage_causes_life_zero_game_over `
  tests.test_turn_engine.TurnEngineTests.test_empty_library_can_transition_to_game_over `
  tests.test_combat.CombatTests.test_summoning_sick_and_tapped_creatures_cannot_attack `
  tests.test_combat.CombatTests.test_first_strike_creature_kills_blocker_before_regular_damage `
  tests.test_combat.CombatTests.test_multi_blocker_order_assigns_lethal_before_next_blocker `
  tests.test_resilience
```

**Say while the results remain visible**

> These focused tests cover the four-byte big-endian frame, exact and fragmented
> reads, the 65,535-byte limit, personalized state, London Mulligan, two-seat
> refusal, reconnect, heartbeat, RFC errors including stale actions, duplicate
> IDs, and illegal decks, complete turn progression, all three required
> GAME_OVER paths, same-session restart, Stack behavior, combat, summoning
> sickness, first strike, multi-block damage order, and missing-PONG failure.
> The card-effect tests visibly name Lightning Bolt, Counterspell, Unsummon,
> Giant Growth, and Doom Blade—at least five required effects.

**Look for**

```text
test_pack_frame_uses_four_byte_big_endian_length ... ok
test_receive_exact_joins_fragmented_reads ... ok
test_oversized_outbound_and_inbound_frames_are_rejected ... ok
test_setup_initializes_life_shuffles_and_draws_personalized_hands ... ok
test_redraw_then_keep_bottoms_one_card ... ok
test_illegal_deck_is_reported_as_illegal_deck ... ok
test_two_player_lobby_third_refusal_errors_updates_and_ping ... ok
test_player_can_reconnect_during_game_and_resume_priority ... ok
test_land_and_lightning_bolt_flow_through_the_network_stack ... ok
test_counterspell_resolves_first_and_removes_the_target_spell ... ok
test_unsummon_giant_growth_and_doom_blade_effects ... ok
test_lethal_spell_damage_causes_life_zero_game_over ... ok
test_empty_library_can_transition_to_game_over ... ok
test_attack_block_and_combat_damage_flow_across_both_clients ... ok
test_first_strike_creature_kills_blocker_before_regular_damage ... ok
test_multi_blocker_order_assigns_lethal_before_next_blocker ... ok
test_missing_pong_closes_client_connection ... ok
OK
```

### 7:05-7:35 — Code quality and closing statement

**Do**

Briefly show these files in the editor or name them on screen:

- `src/mtgnp/framing.py` — frame packing, exact reads, connection wrapper;
- `src/mtgnp/protocol.py` — PDU schemas and error codes;
- `src/mtgnp/server.py` — socket lifecycle and dispatch;
- `src/mtgnp/game.py` — authoritative phase, priority, Stack, and combat state;
- `src/mtgnp/client.py` — intent submission and authoritative rendering.

**Say**

> The implementation separates framing, protocol validation, server dispatch,
> authoritative game logic, and client rendering. Non-obvious concurrency,
> deadline, framing, and state-transition behavior is documented and covered by
> focused tests. This concludes the base-rubric demonstration.

There is no honest gameplay-output marker for Readability & Comments; it is a
source-inspection criterion. Do not claim that verbose logs alone prove it.

## Rubric-to-terminal cue map

| Criterion | What the reviewer should see | If live output is insufficient |
| --- | --- | --- |
| Verbose prerequisite | `SEND`/`RECEIVE`, direction, peer, `type`, `seq_num`, complete payload on server and clients | Must remain enabled for the whole demo |
| TCP Server Setup & Client Accept | `listening ...:4444`, `Accepted seat_1`, `Accepted seat_2`, `Refused additional client`, reconnect messages | Integration test names the two-client/refusal path |
| Message Framing | No normal gameplay line exposes the binary prefix | Show framing test names for four-byte big-endian, fragmented reads, and oversize rejection |
| PDU Structure & seq_num | Every verbose header/payload has `type` and `seq_num`; priority action echoes grant token | Show `STALE_ACTION` through the integration evidence test |
| Lobby & PLAYER_READY | `phase: LOBBY`, `PLAYER_READY`, connected/ready counts, `waiting_for` | Test covers duplicate ID and invalid phase; Lobby unit tests cover illegal decks |
| Game Setup & Mulligan | life totals 20, seven-card own hand, `MULLIGAN_CHOICE`, active player | Setup and redraw/bottom tests |
| Phase & Step Transitions | `PHASE_TRANSITION` through Untap, Upkeep, Draw, both Main phases, Combat, End, Cleanup | Pass-only turn integration test |
| GAME_OVER & Restart | `GAME_OVER`, reason, Lobby state, fresh `PLAYER_READY` without restarting processes | Concede/restart integration test |
| State & Hidden Info | personalized `GAME_STATE_UPDATE`, own hand only, public counts/zones | Personalized setup-state test |
| Priority & Stack | `PRIORITY_GRANT`, `STACK_PUSH`, two passes, `STACK_RESOLVE`; five named effects | Stack and spell-effect tests |
| Combat | attacker/blocker/order prompts, declaration PDUs, `COMBAT_DAMAGE_RESULT` | Combat integration plus summoning-sickness, first-strike, and multi-block tests |
| Client Sending & Rendering | client `SEND C->S` action PDUs and readable `[STATE]`, hand, battlefield, Stack | Client rendering tests if questioned |
| PING/PONG | boxed PING/PONG with matching sequence and timestamp; restored `[GAME]` line | Missing-PONG resilience test |
| Error PDU Handling | `type=ERROR`, RFC `code`, `[SERVER ERROR: ...]`, client remains alive | Integration test covers duplicate, wrong-phase, and stale errors |
| Readability & Comments | No definitive gameplay prompt | Brief source inspection is required |

## Presenter safeguards

- Do not confuse the 60-second priority deadline with the 10-second heartbeat
  reply timeout.
- Do not type `attack CARD_ID` at `Action:`. Wait for the dedicated attacker
  prompt and enter only card IDs.
- Both players must pass consecutively to resolve the Stack or advance an empty
  priority window.
- A server `ERROR` PDU proves server-side validation; a local `Invalid action`
  message does not.
- If the live shuffle does not provide a desired card, move on to the named
  deterministic test instead of restarting during the recording.
- Stop the spoken close by 7:35 even if a terminal is still scrolling.
