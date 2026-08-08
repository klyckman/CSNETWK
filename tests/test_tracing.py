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
        self.assertIn("timestamp=1000", first_output)
        self.assertEqual(len(first_output.splitlines()), 1)

        self.tracer.set_enabled(False)
        self.tracer.trace(TraceAction.RECEIVE, Sender.CLIENT, self.pdu)
        self.assertEqual(self.output.getvalue(), first_output)

    def test_server_messages_are_labeled_in_server_to_client_direction(self) -> None:
        self.tracer.set_enabled(True)
        pong = {"type": "PONG", "seq_num": 7, "timestamp": 1000}
        self.tracer.trace(TraceAction.RECEIVE, Sender.SERVER, pong)
        self.assertIn("RECEIVE S->C", self.output.getvalue())

    def test_interactive_trace_can_start_on_a_fresh_line(self) -> None:
        output = io.StringIO()
        tracer = PDUTracer(
            enabled=True,
            stream=output,
            timestamp=lambda: "2026-08-03T12:00:00+08:00",
            leading_newline=True,
        )

        tracer.trace(TraceAction.SEND, Sender.CLIENT, self.pdu)

        self.assertTrue(output.getvalue().startswith("\n[2026-08-03"))

    def test_non_heartbeat_pdu_keeps_readable_multiline_json(self) -> None:
        self.tracer.set_enabled(True)
        self.tracer.trace(
            TraceAction.SEND,
            Sender.CLIENT,
            {"type": "PRIORITY_PASS", "seq_num": 12},
        )

        output = self.output.getvalue()
        self.assertIn("type=PRIORITY_PASS seq_num=12", output)
        self.assertIn('\n{\n  "seq_num": 12,', output)

    def test_interactive_heartbeat_pair_is_surrounded_by_border(self) -> None:
        output = io.StringIO()
        tracer = PDUTracer(
            enabled=True,
            stream=output,
            timestamp=lambda: "2026-08-03T12:00:00+08:00",
            leading_newline=True,
            heartbeat_border="++++++++",
        )

        tracer.trace(TraceAction.SEND, Sender.CLIENT, self.pdu)
        tracer.trace(
            TraceAction.RECEIVE,
            Sender.SERVER,
            {"type": "PONG", "seq_num": 7, "timestamp": 1000},
        )

        rendered = output.getvalue()
        self.assertTrue(rendered.startswith("\n\n++++++++\n"))
        self.assertIn("type=PING seq_num=7 timestamp=1000", rendered)
        self.assertIn("type=PONG seq_num=7 timestamp=1000", rendered)
        self.assertTrue(rendered.endswith("++++++++\n\n"))
        self.assertEqual(rendered.count("++++++++"), 2)


if __name__ == "__main__":
    unittest.main()
