"""DataHub: one record with a wrong time stamp, and what the packet ring keeps."""

import time

import numpy as np

from seiszone.core import mseed
from seiszone.core.hub import DataHub

SID = "WB.KUM.0J.HNZ"


def recs(start, seconds, rate=100):
    y = np.random.default_rng(int(start) % 1000).normal(0, 300, int(seconds * rate)).astype(int)
    return mseed.encode_records("WB", "KUM", "0J", "HNZ", rate, start, y)


def test_one_future_record_does_not_wipe_the_live_buffer():
    hub = DataHub()
    seen = []
    hub.add_listener(lambda rec, source: seen.append(rec))
    now = int(time.time())
    for r in recs(now - 80, 60):
        hub.add_record(r, "test")
    bad = recs(now + 30 * 86400, 1)[0]                  # GPS glitch: 30 days ahead
    hub.add_record(bad, "test")
    for r in recs(now - 20, 20):
        hub.add_record(r, "test")
    st = hub.stats_snapshot()[SID]
    assert abs(st["last_end"] - now) < 5 and st["future_rejected"] == 1
    assert sum(len(y) for _t, _r, y in hub.window(SID, now - 80, now + 30)) == 8000
    assert bad in seen and any(r is bad for _s, r in hub.snapshot_packets())   # still recorded / served


def test_ring_keeps_raw_bytes_only():
    hub = DataHub()
    for r in recs(time.time() - 60, 30):
        hub.add_record(r, "test")
    assert all(r._samples is None for _s, r in hub.snapshot_packets())
    _s, r = hub.snapshot_packets()[0]
    assert len(r.samples) == r.nsamp                    # decoded again when needed
