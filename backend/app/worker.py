"""Background job worker — a daemon poller thread started per process.

Ported from HomeHoard. Each gunicorn worker starts one poller; they share the
``jobs`` table and claim work with an atomic compare-and-swap, so running two
pollers is safe. A poller that dies leaves a stale ``running`` row that
``reap_stale`` requeues. Disabled under tests (``WORKER_ENABLED=False``). The MCP
server runs as a separate process that never calls create_app, so it won't start
a second poller.
"""
from __future__ import annotations

import logging
import os
import threading
import time

_LOGGER = logging.getLogger("mymeal.worker")

IDLE_POLL_S = 2.0
_started_pid = None
_lock = threading.Lock()


def start_worker(app) -> None:
    """Start the poller once for THIS process (idempotent, PID-keyed so a fork
    doesn't inherit a 'running' flag without the thread)."""
    global _started_pid
    if not app.config.get("WORKER_ENABLED", True):
        return
    with _lock:
        if _started_pid == os.getpid():
            return
        _started_pid = os.getpid()
    threading.Thread(target=_loop, args=(app,), name="mymeal-job-worker",
                     daemon=True).start()
    _LOGGER.info("job worker started")


def _loop(app) -> None:
    from .services.jobs import claim_one, reap_stale, run_job
    while True:
        worked = False
        try:
            with app.app_context():
                reap_stale()
                job = claim_one()
                if job is not None:
                    run_job(job)
                    worked = True
        except Exception:  # noqa: BLE001 - the poller must never die on one bad job
            _LOGGER.exception("worker loop error")
        if not worked:
            time.sleep(IDLE_POLL_S)
