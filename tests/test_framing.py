from __future__ import annotations

import io
import socket
import struct
import unittest

from mtgnp.framing import (
    ConnectionClosed,
    FrameTooLarge,
    FramedConnection,
    pack_frame,
    receive_exact,
    receive_frame,
    send_frame,
)
from mtgnp.protocol import MAX_PDU_SIZE, Sender
from mtgnp.tracing import PDUTracer


class FragmentedSocket:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)

    def recv(self, size: int) -> bytes:
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        returned = chunk[:size]
        remainder = chunk[size:]
        if remainder:
            self.chunks.insert(0, remainder)
        return returned


class FramingTests(unittest.TestCase):
    def test_pack_frame_uses_four_byte_big_endian_length(self) -> None:
        self.assertEqual(pack_frame(b"abc"), b"\x00\x00\x00\x03abc")

    def test_receive_exact_joins_fragmented_reads(self) -> None:
        stream = FragmentedSocket([b"a", b"bc", b"d", b"ef"])
        self.assertEqual(receive_exact(stream, 6), b"abcdef")

    def test_receive_frame_handles_fragmented_prefix_and_payload(self) -> None:
        frame = pack_frame(b"payload")
        stream = FragmentedSocket([bytes([value]) for value in frame])
        self.assertEqual(receive_frame(stream), b"payload")

    def test_back_to_back_frames_remain_separate(self) -> None:
        sender, receiver = socket.socketpair()
        self.addCleanup(sender.close)
        self.addCleanup(receiver.close)
        send_frame(sender, b"first")
        send_frame(sender, b"second")
        self.assertEqual(receive_frame(receiver), b"first")
        self.assertEqual(receive_frame(receiver), b"second")

    def test_partial_frame_then_disconnect_is_reported(self) -> None:
        stream = FragmentedSocket([b"ab"])
        with self.assertRaises(ConnectionClosed) as caught:
            receive_exact(stream, 4)
        self.assertEqual(caught.exception.expected, 4)
        self.assertEqual(caught.exception.received, 2)

    def test_oversized_outbound_and_inbound_frames_are_rejected(self) -> None:
        with self.assertRaises(FrameTooLarge):
            pack_frame(b"x" * (MAX_PDU_SIZE + 1))

        stream = FragmentedSocket([struct.pack("!I", MAX_PDU_SIZE + 1)])
        with self.assertRaises(FrameTooLarge):
            receive_frame(stream)

    def test_framed_connections_validate_both_wire_directions(self) -> None:
        client_socket, server_socket = socket.socketpair()
        client = FramedConnection(
            client_socket, local_sender=Sender.CLIENT, peer_label="server"
        )
        server = FramedConnection(
            server_socket, local_sender=Sender.SERVER, peer_label="client"
        )
        self.addCleanup(client.close)
        self.addCleanup(server.close)

        ping = {"type": "PING", "seq_num": 1, "timestamp": 1234}
        client.send(ping)
        self.assertEqual(server.receive(), ping)

        pong = {"type": "PONG", "seq_num": 1, "timestamp": 1234}
        server.send(pong)
        self.assertEqual(client.receive(), pong)

    def test_connection_wrapper_traces_complete_send_and_receive_events(self) -> None:
        client_socket, server_socket = socket.socketpair()
        output = io.StringIO()
        tracer = PDUTracer(
            enabled=True,
            stream=output,
            timestamp=lambda: "2026-08-03T12:00:00+08:00",
        )
        client = FramedConnection(
            client_socket,
            local_sender=Sender.CLIENT,
            tracer=tracer,
            peer_label="server",
        )
        server = FramedConnection(
            server_socket,
            local_sender=Sender.SERVER,
            tracer=tracer,
            peer_label="client",
        )
        self.addCleanup(client.close)
        self.addCleanup(server.close)

        ping = {"type": "PING", "seq_num": 9, "timestamp": 4321}
        client.send(ping)
        server.receive()

        trace = output.getvalue()
        self.assertIn("SEND C->S peer=server type=PING seq_num=9", trace)
        self.assertIn("RECEIVE C->S peer=client type=PING seq_num=9", trace)
        self.assertEqual(trace.count('"type": "PING"'), 2)

    def test_closed_wrapper_refuses_further_operations(self) -> None:
        left, right = socket.socketpair()
        connection = FramedConnection(left, local_sender=Sender.CLIENT)
        connection.close()
        right.close()
        with self.assertRaises(ConnectionClosed):
            connection.send({"type": "PING", "seq_num": 1, "timestamp": 1})


if __name__ == "__main__":
    unittest.main()
