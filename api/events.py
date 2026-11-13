"""Relay Postgres notifications to browsers (server-sent events).

Migration 004 makes Postgres announce every committed decision and plan on the
`piggyback_events` channel. One background thread per API process LISTENs on its
own connection and fans each event out to the asyncio queues of the clients
watching that scenario. So two operators on different API processes still see
each other's decisions within a moment, and nothing is announced before it commits.

If the connection drops, the listener reconnects with backoff and tells clients to
re-sync (`{"type": "resync"}`), since notifications sent meanwhile are lost.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import defaultdict

import psycopg

CHANNEL = "piggyback_events"
log = logging.getLogger("piggyback.events")


class EventHub:
    def __init__(self, conninfo: str, queue_size: int = 256):
        self.conninfo = conninfo
        self.queue_size = queue_size
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.connected = threading.Event()

    # ------------------------------------------------------------------ lifecycle

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._thread = threading.Thread(target=self._run, name="pg-listen", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        backoff = 0.5
        first = True
        while not self._stop.is_set():
            try:
                with psycopg.connect(self.conninfo, autocommit=True) as conn:
                    conn.execute(f"LISTEN {CHANNEL}")
                    self.connected.set()
                    if not first:  # anything sent while we were away is gone
                        self._broadcast({"type": "resync"})
                    first, backoff = False, 0.5
                    while not self._stop.is_set():
                        for n in conn.notifies(timeout=1.0):
                            self._dispatch(n.payload)
            except psycopg.OperationalError as e:
                self.connected.clear()
                log.warning("event listener lost its connection (%s); retrying in %.1f s", e, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 10.0)
        self.connected.clear()

    # ------------------------------------------------------------------ fan-out

    def subscribe(self, sid: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self.queue_size)
        with self._lock:
            self._subs[sid].add(q)
        return q

    def unsubscribe(self, sid: str, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs[sid].discard(q)
            if not self._subs[sid]:
                del self._subs[sid]

    def subscribers(self, sid: str) -> int:
        with self._lock:
            return len(self._subs.get(sid, ()))

    def _dispatch(self, payload: str) -> None:
        try:
            event = json.loads(payload)
            sid = event["scenario_id"]
        except (ValueError, KeyError, TypeError):
            log.warning("ignoring malformed event %r", payload[:200])
            return
        with self._lock:
            queues = list(self._subs.get(sid, ()))
        for q in queues:
            self._put(q, event)

    def _broadcast(self, event: dict) -> None:
        with self._lock:
            queues = [q for qs in self._subs.values() for q in qs]
        for q in queues:
            self._put(q, event)

    def _put(self, q: asyncio.Queue, event: dict) -> None:
        def put():
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # a stalled client: make it re-sync rather than grow without bound
                while not q.empty():
                    q.get_nowait()
                q.put_nowait({"type": "resync"})
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(put)
