"""Thread-safe, runtime-toggleable PDU tracing for clients and servers."""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime
from enum import StrEnum
from typing import Any, Callable, Mapping, TextIO

from .protocol import Sender


class TraceAction(StrEnum):
    SEND = "SEND"
    RECEIVE = "RECEIVE"


class PDUTracer:
    """Print complete, labeled PDUs when verbose mode is enabled.

    One tracer can be shared by multiple connection threads. Each event is
    written while holding a lock so two formatted JSON documents cannot become
    interleaved in the console.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        stream: TextIO | None = None,
        timestamp: Callable[[], str] | None = None,
        leading_newline: bool = False,
        heartbeat_border: str | None = None,
    ) -> None:
        self._enabled = enabled
        self._stream = stream if stream is not None else sys.stdout
        self._timestamp = timestamp if timestamp is not None else self._now
        self._leading_newline = leading_newline
        self._heartbeat_border = heartbeat_border
        self._lock = threading.RLock()

    @staticmethod
    def _now() -> str:
        return datetime.now().astimezone().isoformat(timespec="milliseconds")

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """Turn verbose tracing on or off while the process is running."""

        with self._lock:
            self._enabled = bool(enabled)

    def trace(
        self,
        action: TraceAction,
        sender: Sender,
        pdu: Mapping[str, Any],
        *,
        peer: str | None = None,
    ) -> None:
        """Print one complete PDU event if tracing is currently enabled."""

        with self._lock:
            if not self._enabled:
                return

            wire_direction = "C->S" if sender == Sender.CLIENT else "S->C"
            peer_label = f" peer={peer}" if peer else ""
            pdu_type = pdu.get("type", "<missing>")
            seq_num = pdu.get("seq_num", "<missing>")
            header = (
                f"[{self._timestamp()}] {action.value} {wire_direction}"
                f"{peer_label} type={pdu_type} seq_num={seq_num}"
            )
            prefix = "\n" if self._leading_newline else ""
            if pdu_type in {"PING", "PONG"}:
                extra_fields = " ".join(
                    f"{key}={json.dumps(pdu[key], ensure_ascii=False)}"
                    for key in sorted(pdu)
                    if key not in {"type", "seq_num"}
                )
                suffix = f" {extra_fields}" if extra_fields else ""
                if self._heartbeat_border is not None:
                    if pdu_type == "PING":
                        print(
                            f"{prefix}\n{self._heartbeat_border}\n"
                            f"{header}{suffix}",
                            file=self._stream,
                            flush=True,
                        )
                    else:
                        print(
                            f"{prefix}{header}{suffix}\n"
                            f"{self._heartbeat_border}\n",
                            file=self._stream,
                            flush=True,
                        )
                    return
                print(
                    f"{prefix}{header}{suffix}",
                    file=self._stream,
                    flush=True,
                )
                return

            formatted = json.dumps(pdu, ensure_ascii=False, indent=2, sort_keys=True)
            print(f"{prefix}{header}\n{formatted}", file=self._stream, flush=True)
