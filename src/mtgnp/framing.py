"""MTGNP's four-byte big-endian length-prefixed JSON framing."""

from __future__ import annotations

import asyncio
import json
import struct
from collections.abc import Callable
from typing import Any

from .protocol import ErrorCode, MAX_PDU_BYTES, ProtocolError, validate_pdu

TraceCallback = Callable[[dict[str, Any]], None]


def encode_pdu(pdu: dict[str, Any], *, direction: str | None = None) -> bytes:
    validate_pdu(pdu, direction=direction)
    try:
        payload = json.dumps(
            pdu,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError(ErrorCode.INVALID_JSON, f"PDU is not JSON serializable: {exc}") from exc

    if len(payload) > MAX_PDU_BYTES:
        raise ProtocolError(
            ErrorCode.INVALID_JSON,
            f"PDU payload is {len(payload)} bytes; maximum is {MAX_PDU_BYTES}.",
        )
    return struct.pack("!I", len(payload)) + payload


async def read_pdu(
    reader: asyncio.StreamReader,
    *,
    direction: str | None = None,
    trace: TraceCallback | None = None,
) -> dict[str, Any]:
    try:
        prefix = await reader.readexactly(4)
        (payload_length,) = struct.unpack("!I", prefix)
        if payload_length > MAX_PDU_BYTES:
            raise ProtocolError(
                ErrorCode.INVALID_JSON,
                f"Declared PDU length {payload_length} exceeds {MAX_PDU_BYTES}.",
            )
        payload = await reader.readexactly(payload_length)
    except asyncio.IncompleteReadError:
        raise

    try:
        pdu = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(ErrorCode.INVALID_JSON, f"Invalid UTF-8 JSON: {exc}") from exc

    validate_pdu(pdu, direction=direction)
    if trace:
        trace(pdu)
    return pdu


async def write_pdu(
    writer: asyncio.StreamWriter,
    pdu: dict[str, Any],
    *,
    direction: str | None = None,
    trace: TraceCallback | None = None,
) -> None:
    frame = encode_pdu(pdu, direction=direction)
    writer.write(frame)
    await writer.drain()
    if trace:
        trace(pdu)

