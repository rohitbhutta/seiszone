"""The SeedLink server's disk buffer (core/packet_store.py): packets kept on disk for days, read
back in order after a restart, old files removed, the hub and the server reaching back into it,
and the viewers reading its per-station files. Everything is local (tmp_path, 127.0.0.1 with
random ports); no instrument is contacted."""

import asyncio
import os
import time

import pytest

from seiszone.core import history, packet_store
from seiszone.core.hub import DataHub
from seiszone.core.packet_store import PacketStore
from seiszone.core.seedlink_server import SeedLinkServer, StationReq

from test_seedlink import (Running, client, make_records, new_state, start_server, stop_server,
                           wait_until)

NOW = 1795000000.0      # 2026-11-17 ...: "now" for the store (arrival times)


def recs(t0, seconds, **kw):
    return make_records(t0, seconds, **kw)


def fill(store, records, seq0=1, now=NOW, step=0.0):
    for i, rec in enumerate(records):
        store.append(seq0 + i, rec, now=now + i * step)
    store.flush()
    return seq0 + len(records) - 1


def test_round_trip_in_order_and_after_a_restart(tmp_path):
    store = PacketStore(str(tmp_path), days=2)
    a = recs(NOW - 600, 60)
    b = recs(NOW - 600, 60, sta="NDH", net="IS")
    records = sorted(a + b, key=lambda r: r.start)
    last = fill(store, records, now=NOW, step=60.0)    # arrivals spread over many hours
    assert store.first_seq() == 1 and store.last_seq() == last
    assert len(store.files) > 1                        # one packet file per arrival hour
    got, after = store.read_from(1, 100000)
    assert [r.raw for _s, r in got] == [r.raw for r in records]
    assert [s for s, _r in got] == list(range(1, last + 1)) and after == last + 1
    # a station file per data hour, as a recording "one file per station" names it
    names = sorted(os.listdir(tmp_path / "IS.NDH" / time.strftime("%Y-%m-%d", time.gmtime(NOW - 600))))
    assert names and all(n.startswith("IS.NDH_") and n.endswith(".mseed") for n in names)
    store.close()
    again = PacketStore(str(tmp_path), days=2)           # SeisZone restarted
    assert again.first_seq() == 1 and again.last_seq() == last
    mid = last // 2
    got, after = again.read_from(mid, 50)
    assert [s for s, _r in got] == list(range(mid, mid + 50)) and after == mid + 50
    assert got[0][1].raw == records[mid - 1].raw
    again.close()


def test_numbers_never_given_out_are_skipped_and_stop_before_the_ring(tmp_path):
    store = PacketStore(str(tmp_path), days=2)
    records = recs(NOW - 300, 30)
    fill(store, records[:10], seq0=1)
    fill(store, records[10:20], seq0=100011, now=NOW + 3600)   # restarted: numbers jumped
    assert store.holds(50) and store.holds(100015) and not store.holds(100021)
    got, after = store.read_from(11, 5)                         # in the gap: the next ones
    assert [s for s, _r in got] == [100011, 100012, 100013, 100014, 100015] and after == 100016
    got, after = store.read_from(1, 100, stop_before=8)         # 8.. are in memory
    assert [s for s, _r in got] == list(range(1, 8)) and after == 8
    store.close()


def test_old_files_go_and_the_newest_stays(tmp_path, monkeypatch):
    store = PacketStore(str(tmp_path), days=1)
    old = recs(NOW - 3 * 86400, 30)
    new = recs(NOW - 60, 30)
    fill(store, old, seq0=1, now=NOW - 3 * 86400)
    last = fill(store, new, seq0=1000, now=NOW)
    n_files = len(store.files)
    removed = store.purge(now=NOW + 60)
    assert removed >= 2 and len(store.files) == n_files - 1    # the old packet file + station files
    assert store.first_seq() == 1000 and store.last_seq() == last
    day = time.strftime("%Y-%m-%d", time.gmtime(NOW - 3 * 86400))
    assert not os.path.exists(tmp_path / "WB.KUM" / day)        # old station files and folder gone
    assert os.listdir(tmp_path / "WB.KUM")                      # the recent ones stay
    # packet numbers beyond the half range SeedLink compares in: the oldest file goes as well
    monkeypatch.setattr(packet_store, "MAX_SPAN", 10)
    fill(store, recs(NOW + 3600, 20), seq0=2000, now=NOW + 3600)
    store.purge(now=NOW + 3600)
    assert store.first_seq() == 2000
    # the user's other files in the folder are never touched
    (tmp_path / "notes.txt").write_text("mine")
    store.purge(now=NOW + 10 * 86400)
    assert (tmp_path / "notes.txt").exists()
    store.close()


def test_the_hub_reaches_back_into_the_disk(tmp_path):
    hub = DataHub(ring_size=5)
    hub.store = PacketStore(str(tmp_path), days=2)
    records = recs(time.time() - 120, 20)
    for rec in records:
        hub.add_record(rec, "test")
    hub.store.flush()
    n = len(records)
    assert hub.first_seq() == 1 and hub.packets[0][0] == n - 4   # memory: the last 5 only
    assert hub.has_seq(1) and hub.has_seq(n) and not hub.has_seq(n + 1)
    got, after = hub.packets_from(1, 10000)                      # disk first ...
    assert [s for s, _r in got] == list(range(1, n - 4)) and after == n - 4
    got2, after2 = hub.packets_from(after, 10000)               # ... then the ring
    assert [s for s, _r in got2] == list(range(n - 4, n + 1)) and after2 == n + 1
    assert [r.raw for _s, r in got + got2] == [r.raw for r in records]
    assert hub.seq_for_time(time.time() - 7200) == 1
    hub.store.close()


@pytest.mark.parametrize("style", ["resume", "fetch"])
def test_a_client_away_for_hours_backfills_across_a_seiszone_restart(tmp_path, monkeypatch, style):
    """Another computer reads this PC's SeedLink server, is switched off, and SeisZone restarts
    meanwhile (its memory ring starts empty): the client still gets every packet it missed.
    resume: "DATA <last + 1>" as SeisComP / ObsPy / libslink clients ask; fetch: SeisZone's own
    client (live first, then the missed time over a second connection with TIME)."""
    from seiszone.core import links, seedlink_client
    monkeypatch.setattr(links, "RECONNECT_DELAY", 0.2)
    if style == "resume":
        monkeypatch.setattr(seedlink_client, "BACKFILL_SECONDS", 0)
    else:
        monkeypatch.setattr(seedlink_client, "LIVE_WAIT", 0.5)
        monkeypatch.setattr(seedlink_client, "BACKFILL_IDLE", 1.5)
    buf = str(tmp_path / "buffer")
    t0 = time.time() - 3 * 3600

    async def main():
        hub1 = DataHub(ring_size=50)
        hub1.store = PacketStore(buf, days=2)
        early, missed = recs(t0, 30), recs(t0 + 30, 120)
        for rec in early:
            hub1.add_record(rec, "test")
        srv, aserver, port = await start_server(hub1)
        hub2, state = DataHub(), new_state(tmp_path)
        state.set_seq("src1", "WB.KUM", 0)          # it has read this PC before: from packet 1
        try:
            async with Running(client(port, hub2, state)):
                await wait_until(lambda: len(hub2.packets) == len(early))
            for rec in missed:                  # the client is off; far more than the ring holds
                hub1.add_record(rec, "test")
        finally:
            await stop_server(aserver)
        last_seq = hub1.seq
        hub1.store.close()
        # SeisZone restarts: an empty ring, the numbers go on after a gap, the disk buffer stays
        hub3 = DataHub(ring_size=50)
        hub3.store = PacketStore(buf, days=2)
        hub3.seq = last_seq + 1000
        live = recs(t0 + 150, 10)
        for rec in live:
            hub3.add_record(rec, "test")
        srv, aserver, port = await start_server(hub3)
        try:
            async with Running(client(port, hub2, state)):
                await wait_until(lambda: len(hub2.packets) == len(early) + len(missed) + len(live), 20)
        finally:
            await stop_server(aserver)
            hub3.store.close()
        got = sorted(r.raw for _s, r in hub2.snapshot_packets())
        assert got == sorted(r.raw for r in early + missed + live)       # nothing lost, nothing twice
        assert "fell behind" not in " ".join(srv.control)

    asyncio.run(main())


def test_a_time_request_starts_near_its_begin_time(tmp_path):
    buf = str(tmp_path / "buffer")

    async def main():
        hub = DataHub(ring_size=20)
        hub.store = PacketStore(buf, days=2)
        now = time.time()
        old = recs(now - 5 * 3600, 20)
        for rec in old:
            hub.store.append(hub.seq + 1, rec, now=now - 5 * 3600)
            hub.seq += 1
        recent = recs(now - 600, 60)
        for rec in recent:
            hub.add_record(rec, "test")
        hub.store.flush()
        start = hub.seq_for_time(now - 900)
        assert start == len(old) + 1                      # the old hours are not read at all
        srv = SeedLinkServer(hub)
        req = StationReq("KUM", "WB")
        req.begin = now - 900
        assert srv._start_seq(req, fetch=False) == start  # "TIME <15 min ago>" begins there
        got, nxt = [], start
        while True:                                       # disk first, then the memory ring
            part, nxt = hub.packets_from(nxt, 10000)
            if not part:
                break
            got += part
        assert [r.raw for _s, r in got] == [r.raw for r in recent]
        hub.store.close()

    asyncio.run(main())


def test_the_viewers_read_the_buffer_like_a_recording(tmp_path):
    store = PacketStore(str(tmp_path), days=2)
    t0 = float(int(time.time()) - 2 * 3600)
    records = recs(t0, 60)
    fill(store, records, now=time.time())
    store.close()
    data = history.RecordedData(lambda: [str(tmp_path)])
    sid = records[0].sid
    deadline = time.time() + 10
    segs, pending = data.segments(sid, t0, t0 + 60)
    while pending and time.time() < deadline:
        time.sleep(0.05)
        segs, pending = data.segments(sid, t0, t0 + 60)
    data.stop()
    assert segs and abs(segs[0][0] - t0) < 0.02 and sum(len(s[2]) for s in segs) == 60 * 100


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
