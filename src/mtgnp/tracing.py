"""Readable verbose-mode PDU tracing shared by the client and server."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


class PduTrace:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        print(f"Verbose PDU logging {'enabled' if enabled else 'disabled'}.")

    def log(self, direction: str, peer: str, pdu: dict[str, Any]) -> None:
        if not self.enabled:
            return
        timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
        rendered = json.dumps(pdu, indent=2, ensure_ascii=False, sort_keys=True)
        print(f"\n[{timestamp}] {direction} [{peer}]\n{rendered}", flush=True)

