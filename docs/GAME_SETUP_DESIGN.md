# Game setup and mulligan design

When the second valid `PLAYER_READY` arrives, the server first publishes the
final `2/2 ready` lobby view and then creates one authoritative `GameSession`.
It initializes both life totals to 20, independently shuffles both submitted
decks, draws opening hands, chooses the first player randomly, and sends each
client a personalized `GAME_STATE_UPDATE` in `MULLIGAN`.

Only the viewing player's hand appears in the `hand` object. Both hand counts,
library counts, life totals, empty battlefields, empty graveyards, and the empty
stack are public. The server records the sequence number of each personalized
update as that player's current mulligan request token.

Each `MULLIGAN_CHOICE` must echo the player's latest token. A stale token is
rejected without mutation. `keep=false` requires an empty `cards_to_bottom`,
returns the hand to the library, reshuffles, draws a new opening hand, increments
the mulligan count, and sends a new personalized request. `keep=true` requires
exactly N distinct cards from the current hand after N mulligans; those cards
are appended to the bottom of the library in the submitted order.

Players decide independently. A player who has kept receives no replacement
mulligan request and waits for the opponent. Once both have kept, the server
broadcasts `MULLIGAN -> UNTAP`, sets the turn to 1, and sends personalized
`IN_GAME` state. Turn behavior after this transition is described in
`TURN_ENGINE_DESIGN.md`.

The RFC permits decks as small as one card while also describing a seven-card
opening hand. This implementation draws all available cards when a deck has
fewer than seven. The normal sample decks contain eight cards. This decision
should be confirmed with the instructor.
