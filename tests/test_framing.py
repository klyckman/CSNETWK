import asyncio
import json
import struct
import unittest

from mtgnp.framing import encode_pdu, read_pdu
from mtgnp.protocol import ProtocolError


class FramingTests(unittest.IsolatedAsyncioTestCase):
    async def test_fragmented_frame_is_read_exactly(self) -> None:
        pdu = {"type": "PING", "seq_num": 7, "timestamp": 123}
        frame = encode_pdu(pdu, direction="client")
        reader = asyncio.StreamReader()

        async def feed() -> None:
            reader.feed_data(frame[:2])
            await asyncio.sleep(0)
            reader.feed_data(frame[2:9])
            await asyncio.sleep(0)
            reader.feed_data(frame[9:])

        feeder = asyncio.create_task(feed())
        self.assertEqual(pdu, await read_pdu(reader, direction="client"))
        await feeder

    async def test_invalid_utf8_json_is_rejected(self) -> None:
        payload = b"\xff"
        reader = asyncio.StreamReader()
        reader.feed_data(struct.pack("!I", len(payload)) + payload)
        with self.assertRaises(ProtocolError) as caught:
            await read_pdu(reader, direction="client")
        self.assertEqual("INVALID_JSON", caught.exception.code.value)

    def test_payload_over_65535_bytes_is_rejected(self) -> None:
        pdu = {
            "type": "PING",
            "seq_num": 1,
            "timestamp": 0,
            "padding": "x" * 66_000,
        }
        with self.assertRaises(ProtocolError):
            encode_pdu(pdu, direction="client")

    def test_prefix_is_big_endian_payload_length(self) -> None:
        pdu = {"type": "PING", "seq_num": 1, "timestamp": 0}
        frame = encode_pdu(pdu, direction="client")
        expected = len(json.dumps(pdu, separators=(",", ":")).encode("utf-8"))
        self.assertEqual(expected, struct.unpack("!I", frame[:4])[0])


if __name__ == "__main__":
    unittest.main()

