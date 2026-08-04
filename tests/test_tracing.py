from __future__ import annotations

import io
import unittest

from mtgnp.protocol import Sender
from mtgnp.tracing import PDUTracer, TraceAction


class TracingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.output = io.StringIO()
        self.tracer = PDUTracer(
            stream=self.output,
            timestamp=lambda: "2026-08-03T12:00:00+08:00",
        )
        self.pdu = {"type": "PING", "seq_num": 7, "timestamp": 1000}

    def test_disabled_tracer_prints_nothing(self) -> None:
        self.tracer.trace(TraceAction.SEND, Sender.CLIENT, self.pdu, peer="server")
        self.assertEqual(self.output.getvalue(), "")

    def test_runtime_toggle_enables_and_disables_complete_pdu_logging(self) -> None:
        self.tracer.set_enabled(True)
        self.tracer.trace(TraceAction.SEND, Sender.CLIENT, self.pdu, peer="server")
        first_output = self.output.getvalue()
        self.assertIn("SEND C->S", first_output)
        self.assertIn("peer=server", first_output)
        self.assertIn("type=PING seq_num=7", first_output)
        self.assertIn('"timestamp": 1000', first_output)

        self.tracer.set_enabled(False)
        self.tracer.trace(TraceAction.RECEIVE, Sender.CLIENT, self.pdu)
        self.assertEqual(self.output.getvalue(), first_output)

    def test_server_messages_are_labeled_in_server_to_client_direction(self) -> None:
        self.tracer.set_enabled(True)
        pong = {"type": "PONG", "seq_num": 7, "timestamp": 1000}
        self.tracer.trace(TraceAction.RECEIVE, Sender.SERVER, pong)
        self.assertIn("RECEIVE S->C", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()

