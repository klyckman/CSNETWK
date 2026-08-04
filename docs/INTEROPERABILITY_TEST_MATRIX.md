# Interoperability test matrix

Record real dates, participants, versions/commits, and results. Do not mark a
row as passed until it was actually performed.

| ID | Environment | Procedure | Expected result | Actual result | Date / tester |
| --- | --- | --- | --- | --- | --- |
| I-01 | Clean clone, computer A | Follow README test command without undeclared dependencies | All automated tests pass | Pending | Pending |
| I-02 | One computer, three terminals | Start verbose server and two bundled clients | Two seats accepted; third connection refused; game reaches first priority | Pending | Pending |
| I-03 | Two computers on one LAN | Host server on A; connect both clients using A's IPv4 address | Both clients complete setup and at least two turns | Pending | Pending |
| I-04 | Two computers on one LAN | Disconnect one in-game client and rerun its exact command within 30 seconds | Same game and private state resume with a fresh token | Pending | Pending |
| I-05 | Two computers on one LAN | Disconnect one client and wait beyond 30 seconds | Opponent receives `GAME_OVER` reason `DISCONNECT` | Pending | Pending |
| I-06 | Another group's client | Connect their client to this server without sharing source | Lobby, heartbeat, one action, and one error PDU interoperate | Pending | Pending |
| I-07 | Another group's server | Connect this client to their server without sharing source | Lobby, heartbeat, visible state, and priority action interoperate | Pending | Pending |
| I-08 | Demo machines | Run all programs with `--verbose` | Every sent and received PDU is clearly labeled on both sides | Pending | Pending |

## Capture with each run

- OS and Python version
- Git commit or ZIP checksum for both implementations
- Exact server and client commands
- Relevant verbose log excerpt or screenshot
- Firewall/network changes
- Observed deviation and whether it is this implementation, the peer, or an
  RFC ambiguity

Interoperability means exchanging protocol traffic only. Do not copy or merge
another group's source code.
