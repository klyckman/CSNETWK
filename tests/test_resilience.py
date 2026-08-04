from __future__ import annotations

import socket
import threading
import time
import unittest

from mtgnp.client import MTGNPClient
from mtgnp.framing import ConnectionClosed, FramedConnection
from mtgnp.protocol import Sender


def wait_for(predicate, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class ClientHeartbeatTests(unittest.TestCase):
    def test_background_receiver_matches_pong_while_main_thread_is_idle(self) -> None:
        client_socket, server_socket = socket.socketpair()
        client = MTGNPClient()
        client.connection = FramedConnection(
            client_socket, local_sender=Sender.CLIENT
        )
        server = FramedConnection(server_socket, local_sender=Sender.SERVER)
        stop = threading.Event()

        def answer_pings() -> None:
            try:
                while not stop.is_set():
                    ping = server.receive()
                    server.send(
                        {
                            "type": "PONG",
                            "seq_num": ping["seq_num"],
                            "timestamp": ping["timestamp"],
                        }
                    )
            except (ConnectionClosed, OSError):
                pass

        responder = threading.Thread(target=answer_pings, daemon=True)
        responder.start()
        try:
            client.start_heartbeat(interval_seconds=0.01, timeout_seconds=0.1)
            self.assertTrue(wait_for(lambda: not client._incoming.empty()))
            self.assertEqual(client.receive()["type"], "PONG")
            self.assertFalse(client.heartbeat_failed)
        finally:
            stop.set()
            client.close()
            server.close()
            responder.join(timeout=1)

    def test_missing_pong_closes_client_connection(self) -> None:
        client_socket, silent_server_socket = socket.socketpair()
        client = MTGNPClient()
        client.connection = FramedConnection(
            client_socket, local_sender=Sender.CLIENT
        )
        try:
            client.start_heartbeat(interval_seconds=0.01, timeout_seconds=0.03)
            self.assertTrue(wait_for(lambda: client.heartbeat_failed))
            self.assertTrue(client.connection.closed)
        finally:
            client.close()
            silent_server_socket.close()


if __name__ == "__main__":
    unittest.main()
