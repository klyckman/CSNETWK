# Concession, deadlines, heartbeat, and reconnect resilience

This milestone implements the MTGNP 1.0 failure paths that can be expressed
using the protocol's existing PDUs. The server remains authoritative for game
termination and retains every healthy connection when a game returns to the
Lobby.

## Concession

`CONCEDE` is accepted during Mulligan or `IN_GAME`, independent of priority.
The `player_id` must match the identity assigned to the sending seat; a client
cannot concede on its opponent's behalf. A valid concession immediately
declares the opponent the winner, broadcasts `GAME_OVER` with reason
`CONCEDE`, clears game state and ready submissions, and publishes a fresh Lobby
state on the same TCP connections.

The terminal exposes `concede` at every priority prompt. After any
`GAME_OVER`, both terminal clients automatically submit their existing player
ID and deck again when the Lobby update arrives. This exercises the RFC's
same-connection repeat-game path without restarting either process.

## Priority deadlines

Every `PRIORITY_GRANT` advertises and arms a 60,000 millisecond server timer.
The timer is identified by the exact game, seat, and request sequence. A
matching priority action consumes it before validation; rejected actions get a
fresh grant and a fresh deadline. Replaced, cancelled, stale, and previous-game
timers cannot terminate the current game.

If the current holder does not respond before the deadline, the server
broadcasts `GAME_OVER` with reason `DISCONNECT`, retains the opponent's TCP
connection, returns that player to the Lobby, and closes the timed-out
connection. Tests configure a shorter duration through the server constructor;
normal execution always uses the RFC's 60-second value.

## Client heartbeats

The terminal client sends `PING` every 30 seconds and requires the matching
`PONG` within 10 seconds. PING sequence numbers use a counter independent of
game-action request tokens. A background receiver matches PONG responses while
the main terminal thread is waiting for user input, so a long interactive
prompt does not cause a false heartbeat failure. A missing PONG closes the
client connection.

## Reconnect grace

Unexpected TCP loss during Mulligan or `IN_GAME` pauses the game, cancels the
current priority deadline, and reserves the disconnected seat for 30 seconds.
The authoritative `GameSession` remains unchanged. Since MTGNP requires
reconnect but defines no session-resumption PDU, the extension reuses
`PLAYER_READY`: a new connection assigned to the reserved seat must submit the
same `player_id` and exact ordered `deck_list` used to start the interrupted
game.

After verification, both clients receive fresh personalized state and the
server reissues the interrupted priority, combat, trigger, Mulligan, or Cleanup
request with a new sequence token. A mismatched identity or deck is rejected
and its connection is closed without extending the original timer. If the
timer expires, the opponent wins with reason `DISCONNECT` and the healthy
connection returns to Lobby.

The existing protocol provides no authentication, so this handshake protects
against accidental seat takeover rather than an attacker who already knows the
player ID and full ordered deck. The grace period is configurable with
`--reconnect-grace SECONDS` for testing and demonstrations.
