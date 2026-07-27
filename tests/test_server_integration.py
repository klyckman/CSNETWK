import asyncio
import unittest

from mtgnp.framing import read_pdu, write_pdu
from mtgnp.server import MTGNPServer

from test_game import P1_DECK, P2_DECK


class ServerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.mtgnp = MTGNPServer(
            "127.0.0.1",
            0,
            priority_timeout_ms=5_000,
        )
        self.listener = await asyncio.start_server(
            self.mtgnp._accept_connection,
            "127.0.0.1",
            0,
        )
        self.port = self.listener.sockets[0].getsockname()[1]
        self.listener_task = asyncio.create_task(self.listener.serve_forever())
        self.r1, self.w1 = await asyncio.open_connection("127.0.0.1", self.port)
        self.r2, self.w2 = await asyncio.open_connection("127.0.0.1", self.port)

    async def asyncTearDown(self) -> None:
        self.w1.close()
        self.w2.close()
        await asyncio.gather(
            self.w1.wait_closed(),
            self.w2.wait_closed(),
            return_exceptions=True,
        )
        self.listener.close()
        await self.listener.wait_closed()
        self.listener_task.cancel()
        await asyncio.gather(self.listener_task, return_exceptions=True)
        await asyncio.sleep(0)

    async def test_two_clients_reach_priority_and_restart_on_same_connections(self) -> None:
        await self._send(
            self.w1,
            {
                "type": "PLAYER_READY",
                "seq_num": 1,
                "player_id": "alice",
                "deck_list": P1_DECK,
            },
        )
        first_lobby = await self._until(
            self.r1,
            lambda pdu: pdu["type"] == "GAME_STATE_UPDATE"
            and pdu["state"].get("phase") == "LOBBY",
        )
        self.assertEqual(1, first_lobby["state"]["players_ready"])

        await self._send(
            self.w2,
            {
                "type": "PLAYER_READY",
                "seq_num": 1,
                "player_id": "bob",
                "deck_list": P2_DECK,
            },
        )
        alice_mulligan, bob_mulligan = await asyncio.gather(
            self._until(
                self.r1,
                lambda pdu: pdu["type"] == "GAME_STATE_UPDATE"
                and pdu["state"].get("phase") == "MULLIGAN",
            ),
            self._until(
                self.r2,
                lambda pdu: pdu["type"] == "GAME_STATE_UPDATE"
                and pdu["state"].get("phase") == "MULLIGAN",
            ),
        )
        self.assertEqual({"alice"}, set(alice_mulligan["state"]["hand"]))
        self.assertEqual({"bob"}, set(bob_mulligan["state"]["hand"]))

        await self._send(
            self.w1,
            {
                "type": "MULLIGAN_CHOICE",
                "seq_num": alice_mulligan["seq_num"],
                "keep": True,
                "cards_to_bottom": [],
            },
        )
        await self._send(
            self.w2,
            {
                "type": "MULLIGAN_CHOICE",
                "seq_num": bob_mulligan["seq_num"],
                "keep": True,
                "cards_to_bottom": [],
            },
        )

        active_id = alice_mulligan["state"]["active_player"]
        active_reader, active_writer = (
            (self.r1, self.w1) if active_id == "alice" else (self.r2, self.w2)
        )
        other_reader, other_writer = (
            (self.r2, self.w2) if active_id == "alice" else (self.r1, self.w1)
        )
        grant = await self._until(
            active_reader,
            lambda pdu: pdu["type"] == "PRIORITY_GRANT",
        )
        await self._send(
            active_writer,
            {"type": "PRIORITY_PASS", "seq_num": grant["seq_num"]},
        )
        other_grant = await self._until(
            other_reader,
            lambda pdu: pdu["type"] == "PRIORITY_GRANT",
        )
        self.assertNotEqual(grant["seq_num"], other_grant["seq_num"])

        conceding_id = "bob" if active_id == "alice" else "alice"
        await self._send(
            other_writer,
            {
                "type": "CONCEDE",
                "seq_num": other_grant["seq_num"],
                "player_id": conceding_id,
            },
        )
        game_over_1, game_over_2 = await asyncio.gather(
            self._until(self.r1, lambda pdu: pdu["type"] == "GAME_OVER"),
            self._until(self.r2, lambda pdu: pdu["type"] == "GAME_OVER"),
        )
        self.assertEqual("CONCEDE", game_over_1["reason"])
        self.assertEqual(game_over_1["winner_id"], game_over_2["winner_id"])

        await self._send(
            self.w1,
            {
                "type": "PLAYER_READY",
                "seq_num": 2,
                "player_id": "alice",
                "deck_list": P1_DECK,
            },
        )
        restart_lobby = await self._until(
            self.r1,
            lambda pdu: pdu["type"] == "GAME_STATE_UPDATE"
            and pdu["state"].get("phase") == "LOBBY",
        )
        self.assertEqual(1, restart_lobby["state"]["players_ready"])

    async def _send(self, writer, pdu) -> None:
        await write_pdu(writer, pdu, direction="client")

    async def _until(self, reader, predicate):
        for _ in range(20):
            pdu = await asyncio.wait_for(
                read_pdu(reader, direction="server"),
                timeout=2,
            )
            if predicate(pdu):
                return pdu
        self.fail("Expected PDU was not received.")


if __name__ == "__main__":
    unittest.main()

