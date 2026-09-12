"""Lightweight local metrics — a structured log line plus a small in-memory ring.

Generalised from Edibl's `chat_metrics`, whose shape had already been proven:

* the **log line is the authoritative record**. It is the only thing that spans
  all processes (two gunicorn workers plus the MCP sidecar), survives a restart,
  and can be read back by the debug tooling.
* the **ring is a convenience**. It is per-process, so a request served by the
  other worker is simply not in it — treat it as a recent sample, never a total.
  That caveat is the reason the log line exists rather than the ring being "the"
  metric store.

Nothing here leaves the machine. There is no scrape endpoint and no push.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from threading import Lock
from typing import Any

from ..logsafe import scrub

_LOG = logging.getLogger("mymeal.metrics")


def _safe(value: Any) -> Any:
    """A sample value that is safe to log but still usable as a number.

    Strings get scrubbed: an MCP tool name comes straight from the caller's
    JSON-RPC body, and a newline in it forges a whole log entry — including one
    with a future timestamp, which pushes real audit lines out of the tail that
    the debug tooling returns. Length-capped for the same reason.

    Numbers and booleans keep their type. They cannot carry CR/LF, and
    stringifying them made `summary()` dead code: it selects samples on
    ``isinstance(s["ms"], int)``, which no stored sample could ever satisfy, so
    every summary reported a count of zero.
    """
    if isinstance(value, (bool, int, float)):
        return value
    return scrub(value, limit=120)


RING_SIZE = 200
_RING: deque = deque(maxlen=RING_SIZE)
_LOCK = Lock()


def record(kind: str, **fields: Any) -> dict:
    """Record one sample: log it (durable) and ring it (per-process convenience).

    `kind` groups samples — "job", "mcp_tool", "chat". Field values should be
    small scalars; anything user-supplied is the caller's job to trim, because
    this ends up in a log file the debug tooling can read.
    """
    sample = {"kind": kind, **{k: _safe(v) for k, v in fields.items()}}
    with _LOCK:
        _RING.append(sample)
    # A single structured line, easy to grep by kind: `metric kind=job ...`.
    _LOG.info("metric %s", " ".join(f"{k}={v}" for k, v in sample.items()))
    return sample


def recent(kind: str | None = None, limit: int = 50) -> list[dict]:
    """Most recent samples from THIS process, newest last."""
    with _LOCK:
        items = [s for s in _RING if kind is None or s.get("kind") == kind]
    return items[-limit:]


def summary(kind: str) -> dict:
    """Count and duration percentiles for one kind, from this process's ring."""
    samples = [s for s in recent(kind, limit=RING_SIZE) if isinstance(s.get("ms"), int)]
    if not samples:
        return {"kind": kind, "count": 0}
    durations = sorted(s["ms"] for s in samples)
    ok = sum(1 for s in samples if s.get("ok") is not False)
    return {
        "kind": kind,
        "count": len(durations),
        "ok": ok,
        "failed": len(durations) - ok,
        "p50Ms": durations[len(durations) // 2],
        "maxMs": durations[-1],
    }


class Timer:
    """Time a block and record it on exit. Never swallows the exception.

    Usage::

        with Timer("job", name=job.kind) as t:
            ...
            t.set(items=42)
    """

    def __init__(self, kind: str, **fields: Any):
        self.kind = kind
        self.fields = dict(fields)
        self._started = 0.0

    def set(self, **fields: Any) -> None:
        self.fields.update(fields)

    def mark(self, field: str) -> None:
        """Record ms-since-start under `field`, the FIRST time only.

        For "time to first X" measurements — notably a chat stream's
        time-to-first-token, where the caller sees every token and would
        otherwise have to guard the call site itself. Later calls are ignored,
        so `timer.mark("ttftMs")` can sit unguarded in the delta loop.
        """
        if self.fields.get(field) is None:
            self.fields[field] = int((time.monotonic() - self._started) * 1000)

    def __enter__(self) -> Timer:
        self._started = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.fields.setdefault("ok", exc_type is None)
        self.fields["ms"] = int((time.monotonic() - self._started) * 1000)
        try:
            record(self.kind, **self.fields)
        except Exception:  # noqa: BLE001 - metrics must never break the caller
            pass
        return False  # never suppress
