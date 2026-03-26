"""
NotificationHub — SSE pub/sub for job events.
Extracted from JobManager._notify() / subscribe().

Key fix vs. original: queue maxsize raised from 500 → 5000 and dropped
messages are counted and logged periodically instead of silently discarded.
"""
from __future__ import annotations
import json
import logging
import queue
import threading
from typing import Optional

log = logging.getLogger(__name__)


class NotificationHub:
    """Thread-safe SSE dispatcher.

    Subscribers register a per-connection Queue.  _publish() puts the
    serialised payload onto every registered queue without blocking;
    if a queue is full it increments a drop counter instead of raising.
    """

    QUEUE_SIZE = 5000  # raised from original 500

    def __init__(self):
        self._sse: dict[str, list[queue.Queue]] = {}
        self._sse_lock = threading.Lock()
        self._dropped_count = 0

    # ── Subscription ──────────────────────────────────────────────────────────

    def subscribe(self, job_id: str) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self.QUEUE_SIZE)
        with self._sse_lock:
            self._sse.setdefault(job_id, []).append(q)
        return q

    def unsubscribe(self, job_id: str, q: queue.Queue):
        with self._sse_lock:
            subs = self._sse.get(job_id, [])
            if q in subs:
                subs.remove(q)

    def subscribe_all(self) -> queue.Queue:
        return self.subscribe("__all__")

    def unsubscribe_all(self, q: queue.Queue):
        self.unsubscribe("__all__", q)

    # ── Publishing ────────────────────────────────────────────────────────────

    def publish(self, job_id: str, payload: dict) -> None:
        """Serialise payload to JSON and deliver to all subscribers for
        job_id and to the global "__all__" channel."""
        try:
            data = json.dumps(payload)
        except Exception:
            log.exception("NotificationHub: failed to serialise payload for job %s", job_id)
            return
        with self._sse_lock:
            for q in list(self._sse.get(job_id, [])):
                self._put(q, data)
            if job_id != "__all__":
                for q in list(self._sse.get("__all__", [])):
                    self._put(q, data)

    def _put(self, q: queue.Queue, data: str) -> None:
        try:
            q.put_nowait(data)
        except queue.Full:
            self._dropped_count += 1
            if self._dropped_count % 100 == 0:
                log.warning(
                    "NotificationHub: SSE queue full — %d messages dropped total",
                    self._dropped_count,
                )
