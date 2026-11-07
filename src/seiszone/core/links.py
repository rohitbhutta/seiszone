"""Base class for a data source link (one entry of config["sources"]).

Every link runs as one asyncio task in the engine thread:

    link = SeedLinkClient(src, hub, state)       # or GcfLink(...)
    task = loop.create_task(link.run(stop))      # stop: asyncio.Event; run() returns soon after it is set

run() never raises for network problems: it reconnects forever (RECONNECT_DELAY s between
tries, longer after repeated failures) and keeps `state` / `detail` up to date for the user.
"""

import asyncio
import time

RECONNECT_DELAY = 5
MAX_DELAY = 60

# user-visible states
STARTING, CONNECTING, CONNECTED, RECEIVING, WAITING, ERROR, STOPPED = (
    "starting", "connecting", "connected", "receiving", "waiting to reconnect", "error", "stopped")


class Link:
    kind = ""

    def __init__(self, src, hub, state=None):
        self.src = src
        self.hub = hub
        self.state_store = state
        self.state = STARTING
        self.detail = ""          # last error / extra information, shown next to the state
        self.packets = 0          # records accepted from this link
        self.duplicates = 0
        self.bytes = 0
        self.last_packet = 0.0    # wall time of the last record
        self.since = 0.0          # wall time the current connection was made
        self.failures = 0         # consecutive failed connections
        self.kick = asyncio.Event()  # set by Engine.reconnect(): drop the connection, connect again

    @property
    def id(self):
        return self.src["id"]

    @property
    def address(self):
        return f"{self.src['host']}:{self.src['port']}"

    def set_state(self, state, detail=""):
        self.state = state
        self.detail = detail

    def feed(self, rec):
        """Hand one record to the hub; True if it was new."""
        if self.hub.add_record(rec, self.id):
            self.packets += 1
            self.last_packet = time.time()
            if self.state != RECEIVING:
                self.set_state(RECEIVING)
            return True
        self.duplicates += 1
        return False

    def status(self):
        return {"id": self.id, "kind": self.kind, "address": self.address, "state": self.state,
                "detail": self.detail, "packets": self.packets, "duplicates": self.duplicates,
                "bytes": self.bytes, "last_packet": self.last_packet, "since": self.since}

    async def pause(self, stop):
        """Wait before reconnecting (or until stopped / kicked). Returns True if stopped."""
        delay = min(MAX_DELAY, RECONNECT_DELAY * (2 ** min(self.failures, 4)) if self.failures > 1
                    else RECONNECT_DELAY)
        self.state = WAITING if self.state != ERROR else self.state
        stop_t = asyncio.ensure_future(stop.wait())
        kick_t = asyncio.ensure_future(self.kick.wait())
        try:
            await asyncio.wait({stop_t, kick_t}, timeout=delay, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop_t.cancel()
            kick_t.cancel()
        self.kick.clear()
        return stop.is_set()

    async def run(self, stop):  # pragma: no cover - implemented by subclasses
        raise NotImplementedError
