# Turn and priority engine

The current engine executes the complete turn skeleton with spells and creature
combat. Untap is automatic, Upkeep opens priority, Draw performs the
turn-based draw before priority, both Main Phases open priority, Begin Combat
opens priority, an empty attacker declaration skips to End of Combat, End of
Combat and End Step open priority, and Cleanup performs hand-size and damage
cleanup before switching the active player.

The first player skips the Draw action on turn 1 but still receives the Draw
Step priority window. Later draws move the top library card into the active
hand and publish personalized state. Drawing from an empty library broadcasts
`GAME_OVER` with `DECK_EMPTY`, clears both ready submissions, and returns the
same connected clients to `LOBBY`.

At every supported priority window, the active player receives the first
`PRIORITY_GRANT`. A valid `PRIORITY_PASS` transfers priority to the opponent.
The second consecutive pass closes an empty-stack window and advances the
phase. With a non-empty Stack it resolves exactly the top item, then grants the
Active Player priority again. Each grant has its own request token, and stale or wrong-player passes
leave phase state unchanged. An invalid pass by the current holder is followed
by a fresh grant so the interactive client can retry.

`PHASE_TRANSITION` into each combat decision step is the acting player's request
token. Empty attacker declarations still skip directly to End of Combat;
non-empty declarations enter the attacker, blocker, optional damage-order,
first-strike, and regular damage state machine described in `COMBAT_DESIGN.md`.

At Cleanup, a hand above seven receives a personalized state update whose
sequence number is the `DISCARD` request token. One or more valid discards may
be used, but a request cannot discard below seven. Discarded cards enter the
active player's graveyard. Once the hand is seven or fewer, marked creature
damage is cleared, state is broadcast, the turn increments, and the other
player begins Untap.

The server enforces the 60-second limit advertised in every priority grant. A
matching action consumes that exact game's seat-and-sequence deadline; a
rejected action receives a fresh grant and timer. Expiry ends the game with
reason `DISCONNECT`, closes the timed-out connection, and retains the opponent.
Four activated abilities use these priority windows. Supported triggers
temporarily pause the next grant while target choices and APNAP ordering finish,
then join the same Stack before priority resumes. A true reconnect grace
extension remains future work because the RFC defines no reconnect identity PDU.
