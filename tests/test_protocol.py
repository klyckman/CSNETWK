from __future__ import annotations

import unittest

from mtgnp.protocol import (
    ErrorCode,
    MessageType,
    PDU_SPECS,
    PDUValidationError,
    Sender,
    decode_pdu,
    encode_pdu,
    validate_pdu,
)


class ProtocolTests(unittest.TestCase):
    def test_all_25_message_types_have_specs(self) -> None:
        self.assertEqual(len(MessageType), 25)
        self.assertEqual(set(PDU_SPECS), set(MessageType))

    def test_all_12_error_codes_are_declared(self) -> None:
        self.assertEqual(len(ErrorCode), 12)

    def test_valid_player_ready_round_trip(self) -> None:
        pdu = {
            "type": "PLAYER_READY",
            "seq_num": 1,
            "player_id": "alice",
            "deck_list": ["mountain_001"],
        }
        payload = encode_pdu(pdu, sender=Sender.CLIENT)
        self.assertEqual(decode_pdu(payload, sender=Sender.CLIENT), pdu)

    def test_activate_ability_round_trip_uses_required_cost_shape(self) -> None:
        pdu = {
            "type": "ACTIVATE_ABILITY",
            "seq_num": 9,
            "source_id": "prodigal_sorcerer_001",
            "ability_index": 0,
            "targets": ["bob"],
            "cost_payment": {"tap": True, "mana": {}},
        }
        payload = encode_pdu(pdu, sender=Sender.CLIENT)
        self.assertEqual(decode_pdu(payload, sender=Sender.CLIENT), pdu)

        pdu["ability_index"] = True
        with self.assertRaises(PDUValidationError):
            validate_pdu(pdu, sender=Sender.CLIENT)

    def test_trigger_choice_response_accepts_one_optional_string_target(self) -> None:
        pdu = {
            "type": "TRIGGER_CHOICE_RESPONSE",
            "seq_num": 12,
            "trigger_id": "trg_0001",
            "accept": True,
            "chosen_target": "black_knight_001",
        }
        payload = encode_pdu(pdu, sender=Sender.CLIENT)
        self.assertEqual(decode_pdu(payload, sender=Sender.CLIENT), pdu)

        pdu["chosen_target"] = ["black_knight_001"]
        with self.assertRaises(PDUValidationError):
            validate_pdu(pdu, sender=Sender.CLIENT)

    def test_concede_round_trip_identifies_the_sending_player(self) -> None:
        pdu = {
            "type": "CONCEDE",
            "seq_num": 22,
            "player_id": "alice",
        }
        payload = encode_pdu(pdu, sender=Sender.CLIENT)
        self.assertEqual(decode_pdu(payload, sender=Sender.CLIENT), pdu)

    def test_unknown_type_has_protocol_error_code(self) -> None:
        with self.assertRaises(PDUValidationError) as caught:
            validate_pdu({"type": "NOPE", "seq_num": 1})
        self.assertEqual(caught.exception.code, ErrorCode.UNKNOWN_TYPE)

    def test_missing_required_field_is_rejected(self) -> None:
        with self.assertRaises(PDUValidationError) as caught:
            validate_pdu(
                {"type": "PLAYER_READY", "seq_num": 1, "player_id": "alice"},
                sender=Sender.CLIENT,
            )
        self.assertEqual(caught.exception.code, ErrorCode.ILLEGAL_ACTION)
        self.assertIn("deck_list", str(caught.exception))

    def test_wrong_sender_is_rejected(self) -> None:
        with self.assertRaises(PDUValidationError):
            validate_pdu(
                {"type": "PING", "seq_num": 1, "timestamp": 1000},
                sender=Sender.SERVER,
            )

    def test_sequence_number_rejects_boolean_and_negative_values(self) -> None:
        for value in (True, -1):
            with self.subTest(value=value), self.assertRaises(PDUValidationError):
                validate_pdu(
                    {"type": "PING", "seq_num": value, "timestamp": 1000},
                    sender=Sender.CLIENT,
                )

    def test_invalid_utf8_json_is_reported(self) -> None:
        with self.assertRaises(PDUValidationError) as caught:
            decode_pdu(b"\xff")
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_JSON)

    def test_json_array_is_not_a_pdu(self) -> None:
        with self.assertRaises(PDUValidationError) as caught:
            decode_pdu(b"[]")
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_JSON)

    def test_error_pdu_must_use_defined_error_code(self) -> None:
        with self.assertRaises(PDUValidationError):
            validate_pdu(
                {
                    "type": "ERROR",
                    "seq_num": 8,
                    "code": "NOT_DEFINED",
                    "message": "bad",
                    "rejected_action": {},
                },
                sender=Sender.SERVER,
            )


if __name__ == "__main__":
    unittest.main()
