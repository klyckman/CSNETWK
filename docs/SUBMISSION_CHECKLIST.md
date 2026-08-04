# Submission and demo checklist

## Automated and local checks

- [x] All 25 PDU types declared and structurally validated.
- [x] Four-byte big-endian framing and maximum length tested.
- [x] Exactly two concurrent seats; additional connection refused.
- [x] Personalized state hides the opponent's hand.
- [x] Lifecycle, Mulligan, turns, Draw Step, Cleanup, and repeat game tested.
- [x] More than five effects, LIFO Stack, state-based actions, and combat tested.
- [x] `PING`/`PONG`, priority timeout, concession, reconnect, and expiry tested.
- [x] Both programs expose `--verbose`.
- [x] README contains build/run instructions, limitations, matrix, and AI usage.
- [x] README PDF generated and visually checked.

## Human-owned checks before submission

- [ ] Replace Work Distribution Matrix placeholders with truthful names/tasks.
- [ ] Add any AI tools used after the current disclosure was written.
- [ ] Complete every applicable row in `INTEROPERABILITY_TEST_MATRIX.md`.
- [ ] Test a clean clone on a second computer.
- [ ] Test over the actual LAN and firewall configuration used for the demo.
- [ ] Test with another group's independently written client or server.
- [ ] Regenerate `output/pdf/README.pdf` after editing the Markdown README.
- [ ] Confirm the submission archive contains source, data, decks, tests, docs,
  `pyproject.toml`, and the final README PDF but no virtual environment/cache.
- [ ] Have every member explain framing, sequence tokens, hidden state, Stack,
  combat, heartbeat, reconnect, and one error path.

## Five-minute demo path

1. Enable `--verbose` on server and clients.
2. Connect two clients and show a refused third connection.
3. Keep opening hands and explain personalized state.
4. Play a land, cast/respond/pass, and resolve the Stack.
5. Demonstrate combat or an activated/triggered ability.
6. Disconnect and reconnect one client within the grace period.
7. Concede, return to Lobby, and begin a repeat game.
8. Run the test suite or show its final result.
