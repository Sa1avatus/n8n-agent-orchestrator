"""The run-manager shape the session digest reads: the store and the event log.

``handoff.session_digest`` builds a digest from a run manager but touches only its ``store``
and ``events``; the type lives here so neither ``handoff`` nor ``runs`` has to import the
other module to name it (docs/naming.md: a cycle is broken by moving the shared piece to a
third module, not by a lazy import). This module imports neither ``runs`` nor ``handoff``.
"""

from __future__ import annotations

from typing import Protocol

from .events import EventLog
from .store import Store


class DigestSource(Protocol):
    """The store and the event log a run manager must expose for ``session_digest``."""

    store: Store
    events: EventLog
