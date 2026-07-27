import unittest

from mtgnp.protocol import MessageType, ProtocolError, validate_pdu


class ProtocolTests(unittest.TestCase):
    def test_all_25_rfc_message_types_are_declared(self) -> None:
        self.assertEqual(25, len(MessageType))

    def test_unknown_type_is_rejected(self) -> None:
        with self.assertRaises(ProtocolError) as caught:
            validate_pdu({"type": "NOT_REAL", "seq_num": 1}, direction="client")
        self.assertEqual("UNKNOWN_TYPE", caught.exception.code.value)

    def test_boolean_is_not_accepted_as_sequence_number(self) -> None:
        with self.assertRaises(ProtocolError):
            validate_pdu(
                {
                    "type": "PING",
                    "seq_num": True,
                    "timestamp": 0,
                },
                direction="client",
            )


if __name__ == "__main__":
    unittest.main()

