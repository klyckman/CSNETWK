"""TCP framing and a validated connection wrapper for MTGNP PDUs."""

from __future__ import annotations

import socket
import struct
import threading
from collections.abc import Mapping
from types import TracebackType
from typing import Any, Protocol, TypeVar

from .protocol import MAX_PDU_SIZE, Sender, decode_pdu, encode_pdu
from .tracing import PDUTracer, TraceAction


LENGTH_PREFIX = struct.Struct("!I")


class SocketLike(Protocol):
    def recv(self, size: int) -> bytes: ...

    def sendall(self, data: bytes) -> None: ...

    def close(self) -> None: ...


class TransportError(ConnectionError):
    """Base class for MTGNP transport failures."""


class ConnectionClosed(TransportError):
    """The peer closed the stream before a requested read completed."""

    def __init__(self, expected: int, received: int) -> None:
        super().__init__(
            f"Connection closed after {received} of {expected} expected bytes."
        )
        self.expected = expected
        self.received = received


class FrameTooLarge(TransportError):
    """A frame declared or supplied more than the RFC payload limit."""

    def __init__(self, size: int) -> None:
        super().__init__(f"PDU frame is {size} bytes; maximum is {MAX_PDU_SIZE}.")
        self.size = size


def receive_exact(stream: SocketLike, byte_count: int) -> bytes:
    """Receive exactly byte_count bytes, handling normal TCP fragmentation."""

    if byte_count < 0:
        raise ValueError("byte_count cannot be negative")
    if byte_count == 0:
        return b""

    data = bytearray()
    while len(data) < byte_count:
        try:
            chunk = stream.recv(byte_count - len(data))
        except InterruptedError:
            continue
        if not chunk:
            raise ConnectionClosed(byte_count, len(data))
        data.extend(chunk)
    return bytes(data)


def pack_frame(payload: bytes) -> bytes:
    """Return one complete length-prefixed MTGNP frame."""

    size = len(payload)
    if size > MAX_PDU_SIZE:
        raise FrameTooLarge(size)
    return LENGTH_PREFIX.pack(size) + payload


def send_frame(stream: SocketLike, payload: bytes) -> None:
    """Atomically request transmission of one complete MTGNP frame."""

    stream.sendall(pack_frame(payload))


def receive_frame(stream: SocketLike) -> bytes:
    """Read one complete MTGNP payload without consuming a following frame."""

    prefix = receive_exact(stream, LENGTH_PREFIX.size)
    (payload_size,) = LENGTH_PREFIX.unpack(prefix)
    if payload_size > MAX_PDU_SIZE:
        raise FrameTooLarge(payload_size)
    return receive_exact(stream, payload_size)


_ConnectionType = TypeVar("_ConnectionType", bound="FramedConnection")


class FramedConnection:
    """Send and receive validated PDUs over one connected TCP socket.

    Separate locks protect sends and receives. This prevents a future heartbeat
    thread and command thread from interleaving their frame bytes while still
    allowing one read and one write to proceed concurrently.
    """

    def __init__(
        self,
        stream: socket.socket,
        *,
        local_sender: Sender,
        tracer: PDUTracer | None = None,
        peer_label: str | None = None,
    ) -> None:
        self._stream = stream
        self.local_sender = local_sender
        self.remote_sender = (
            Sender.SERVER if local_sender == Sender.CLIENT else Sender.CLIENT
        )
        self.tracer = tracer if tracer is not None else PDUTracer()
        self.peer_label = peer_label
        self._send_lock = threading.Lock()
        self._receive_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._closed = False

    @property
    def closed(self) -> bool:
        with self._close_lock:
            return self._closed

    def set_verbose(self, enabled: bool) -> None:
        self.tracer.set_enabled(enabled)

    def send(self, pdu: Mapping[str, Any]) -> None:
        """Validate, frame, send, and optionally trace one local PDU."""

        payload = encode_pdu(pdu, sender=self.local_sender)
        with self._send_lock:
            if self.closed:
                raise ConnectionClosed(len(payload), 0)
            send_frame(self._stream, payload)
            self.tracer.trace(
                TraceAction.SEND,
                self.local_sender,
                pdu,
                peer=self.peer_label,
            )

    def receive(self) -> dict[str, Any]:
        """Receive, decode, validate, and optionally trace one remote PDU."""

        with self._receive_lock:
            if self.closed:
                raise ConnectionClosed(LENGTH_PREFIX.size, 0)
            payload = receive_frame(self._stream)
            pdu = decode_pdu(payload, sender=self.remote_sender)
            self.tracer.trace(
                TraceAction.RECEIVE,
                self.remote_sender,
                pdu,
                peer=self.peer_label,
            )
            return pdu

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._stream.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._stream.close()

    def __enter__(self: _ConnectionType) -> _ConnectionType:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

