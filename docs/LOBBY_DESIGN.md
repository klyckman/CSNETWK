# Lobby design

The runnable server owns exactly two logical seats, `seat_1` and `seat_2`.
Connections reserve the first free seat. While both are occupied, another TCP
connection is accepted only long enough to close it immediately; no undefined
`SERVER_FULL` error code is added to the protocol. A disconnected seat is reset
and may be claimed by a later connection.

Each valid `PLAYER_READY` atomically replaces the submitting seat's previous
player ID and deck. The player ID must be non-empty and different from the
other connected player's ID. The deck must pass the shared catalog's 1-to-50,
known-instance, and instance-uniqueness checks. Failed requests leave lobby
state unchanged and produce an RFC `ERROR` PDU.

After a valid submission, the server sends a `GAME_STATE_UPDATE` to every
connected player. The lobby view contains the connection count, ready count,
seat or player labels still awaited, and claimed ready-player IDs. No hand or
other private game state exists at this stage.

When both players are ready, the server publishes the final lobby view and
automatically begins `GAME_SETUP`. Shuffling, opening hands, first-player
selection, personalized hidden state, and mulligans are described in
`GAME_SETUP_DESIGN.md`.

The server also answers `PING` with a matching `PONG`. Other recognized client
actions receive `WRONG_PHASE` while the server is in `LOBBY`.
