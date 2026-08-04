# Architecture and demo walkthrough

## Component view

```mermaid
flowchart LR
    C1["Player 1 terminal"] <-->|"TCP and framed JSON"| S["MTGNP server"]
    C2["Player 2 terminal"] <-->|"TCP and framed JSON"| S
    S --> F["Framing, PDU validation, tracing"]
    S --> L["Lobby and lifecycle"]
    S --> G["Authoritative game engine"]
    G --> A["Abilities and triggers"]
    G --> D["Fixed CSV catalog"]
```

`FramedConnection` owns exact-byte reads, the four-byte length prefix, PDU
validation, and tracing. `MTGNPServer` owns connections and translates valid
client intent into calls on `Lobby` or `GameSession`. `GameSession` is the only
authoritative game state. The terminal client renders server state and never
decides whether an action succeeds.

## Priority and Stack sequence

```mermaid
sequenceDiagram
    participant A as Active client
    participant S as Server
    participant B as Other client
    S->>A: PRIORITY_GRANT token N
    A->>S: CAST_SPELL token N
    S->>A: STACK_PUSH and personalized state
    S->>B: STACK_PUSH and personalized state
    S->>A: PRIORITY_GRANT token N+1
    A->>S: PRIORITY_PASS token N+1
    S->>B: PRIORITY_GRANT token N+2
    B->>S: PRIORITY_PASS token N+2
    S->>A: STACK_RESOLVE and state
    S->>B: STACK_RESOLVE and state
```

## Reconnect sequence

```mermaid
sequenceDiagram
    participant A as Disconnected client
    participant S as Server
    participant B as Connected client
    A--xS: TCP connection lost
    S->>S: Pause game, cancel priority timer, reserve seat
    Note over S,B: Game state remains authoritative for 30 seconds
    A->>S: New TCP connection
    A->>S: PLAYER_READY with same ID and deck
    S->>S: Verify reservation and cancel reconnect timer
    S->>A: Personalized GAME_STATE_UPDATE
    S->>B: Personalized GAME_STATE_UPDATE
    S->>A: Fresh request or PRIORITY_GRANT
```

## Suggested oral walkthrough

1. Start the server and both clients with `--verbose`; point out the four-byte
   frame boundary and labeled send/receive logs.
2. Show two valid `PLAYER_READY` PDUs, personalized opening hands, and London
   Mulligan tokens.
3. Explain active player versus priority holder while playing a land, casting
   an Instant, passing twice, and resolving the Stack in LIFO order.
4. Demonstrate attacker and blocker declarations and identify the server-side
   validation that prevents illegal combat.
5. Stop one client during a priority prompt. Show that the opponent does not
   immediately win, then rerun the same command and show the fresh state/token.
6. Concede and show `GAME_OVER`, retained TCP connections, Lobby reset, and a
   second game beginning without restarting the server.
7. Show the automated tests and explain one malformed-frame test and one
   state-mutation atomicity test.

Every member should be able to explain the same path from received bytes,
through PDU validation and server dispatch, to game mutation and personalized
state broadcast.
