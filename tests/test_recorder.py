"""RecordingManager with a real DataHub fed by encoded records: file names and periods, byte-
identical contents (cross-checked with ObsPy), backfill, channel selection, stop, restart,
gap / silence logging, write errors, disk warning. Nothing here touches the network."""

import collections
import logging
import os
import shutil
import time

import numpy as np
import pytest

from seiszone.config import State
from seiszone.core import mseed, recorder
from seiszone.core.hub import DataHub
from seiszone.core.recorder import RecordingManager

HOUR = 1761998400            # an exact UTC hour (2025-11-01 12:00:00)
RATE = 20.0


def records(sid, start, seconds, rate=RATE, seed=0):
    net, sta, loc, cha = sid.split(".")
    y = np.random.default_rng(seed).normal(0, 400, int(seconds * rate)).astype(int)
    return mseed.encode_records(net, sta, loc, cha, rate, start, y), y


def make(tmp_path, name="state.json"):
    hub = DataHub()
    state = State(str(tmp_path / name))
    return hub, state, RecordingManager(hub, state, str(tmp_path / "default"), background=False)


def feed(hub, recs):
    for r in recs:
        hub.add_record(r, "test")


def all_files(folder):
    out = []
    for root, _dirs, files in os.walk(folder):
        out += [os.path.relpath(os.path.join(root, f), folder) for f in files if f.endswith(".mseed")]
    return sorted(out)


def rec_log(folder):
    with open(os.path.join(folder, recorder.LOG_NAME), encoding="utf-8") as fh:
        return fh.read()


@pytest.mark.parametrize("minutes", [15, 30, 60])
def test_period_files_are_byte_identical(tmp_path, minutes):
    from obspy import read
    hub, state, mgr = make(tmp_path)
    folder = str(tmp_path / "rec")
    rid = mgr.start("test", folder, file_minutes=minutes)
    sid = "WB.KUM.0J.HNZ"
    t0 = HOUR - 50 * 60 + 7.3                  # 11:10:07.3 -> 12:20:07.3 UTC, crosses period edges
    recs, y = records(sid, t0, 70 * 60)
    feed(hub, recs)
    expected = collections.OrderedDict()
    for r in recs:
        expected.setdefault(recorder.file_path(folder, sid, r.start, minutes, "channel"), []).append(r.raw)
    assert all_files(folder) == sorted(os.path.relpath(p, folder) for p in expected)
    for path, raws in expected.items():
        name = os.path.basename(path)
        stamp = name.split("__")[1][:15]
        p0 = recorder.stamp_time(stamp)
        assert p0 % (minutes * 60) == 0
        assert os.path.basename(os.path.dirname(path)) == mseed.fmt_time(p0)[:10]
        with open(path, "rb") as fh:
            assert fh.read() == b"".join(raws)
    st = read(os.path.join(folder, "WB.KUM", "*", "*.mseed"))
    st.merge()
    assert len(st) == 1 and st[0].stats.sampling_rate == RATE
    assert np.array_equal(st[0].data, y)
    assert abs(st[0].stats.starttime.timestamp - t0) < 1e-3
    s = mgr.sessions()[0]
    assert s["id"] == rid and s["state"] == "recording" and s["files"] == len(expected)
    assert s["bytes"] == sum(len(r.raw) for r in recs) and s["last_error"] == ""
    text = rec_log(folder)
    assert "RECORDING STARTED" in text and f"{minutes} min" in text and text.count("new file") == len(expected)
    assert " local / " in text and " UTC" in text
    mgr.close()


def test_backfill_goes_into_the_older_file(tmp_path):
    hub, state, mgr = make(tmp_path)
    folder = str(tmp_path / "rec")
    mgr.start("bf", folder, file_minutes=15)
    sid = "WB.KUM.0J.HNZ"
    old, _ = records(sid, HOUR + 60, 120, seed=1)            # 12:01-12:03 (arrives late)
    new, _ = records(sid, HOUR + 20 * 60, 120, seed=2)       # 12:20-12:22
    feed(hub, new)
    feed(hub, old)
    p_old = recorder.file_path(folder, sid, HOUR + 60, 15, "channel")
    p_new = recorder.file_path(folder, sid, HOUR + 20 * 60, 15, "channel")
    assert p_old != p_new
    with open(p_old, "rb") as fh:
        assert fh.read() == b"".join(r.raw for r in old)
    with open(p_new, "rb") as fh:
        assert fh.read() == b"".join(r.raw for r in new)
    mgr.close()


def test_channel_selection_and_station_layout(tmp_path):
    hub, state, mgr = make(tmp_path)
    one = str(tmp_path / "one")
    sta = str(tmp_path / "sta")
    mgr.start("one", one, sids=["WB.KUM.0J.HNZ"])
    mgr.start("station", sta, sids=["WB.KUM.0J.HNZ", "WB.KUM.0J.HNN"], layout="station")
    z, _ = records("WB.KUM.0J.HNZ", HOUR + 10, 60, seed=1)
    n, _ = records("WB.KUM.0J.HNN", HOUR + 10, 60, seed=2)
    e, _ = records("WB.KUM.0J.HNE", HOUR + 10, 60, seed=3)
    for a, b, c in zip(z, n, e):
        feed(hub, [a, b, c])
    assert all_files(one) == [os.path.join("WB.KUM", "2025-11-01", "WB.KUM.0J.HNZ__20251101T120000Z.mseed")]
    assert all_files(sta) == [os.path.join("WB.KUM", "2025-11-01", "WB.KUM__20251101T120000Z.mseed")]
    got = list(mseed.read_file(os.path.join(sta, all_files(sta)[0])))
    assert {r.sid for r in got} == {"WB.KUM.0J.HNZ", "WB.KUM.0J.HNN"}
    assert len(got) == len(z) + len(n)
    with pytest.raises(ValueError):
        mgr.start("bad", one, file_minutes=20)
    with pytest.raises(ValueError):
        mgr.start("bad", one, sids=["nonsense"])
    mgr.close()


def test_all_channels_includes_new_ones(tmp_path):
    hub, state, mgr = make(tmp_path)
    folder = str(tmp_path / "all")
    mgr.start("all", folder)          # sids=None
    a, _ = records("WB.KUM.0J.HNZ", HOUR, 30)
    feed(hub, a)
    assert len(all_files(folder)) == 1
    b, _ = records("SS.SMR03..HHZ", HOUR + 30, 30, rate=100.0)   # a channel that appears later
    feed(hub, b)
    files = all_files(folder)
    assert os.path.join("SS.SMR03", "2025-11-01", "SS.SMR03..HHZ__20251101T120000Z.mseed") in files
    assert "SS.SMR03..HHZ: first data" in rec_log(folder)
    assert mgr.sessions()[0]["sids"] is None
    mgr.close()


def test_stop_and_nothing_deleted(tmp_path):
    hub, state, mgr = make(tmp_path)
    folder = tmp_path / "rec"
    folder.mkdir()
    (folder / "notes.txt").write_text("keep me")
    sid = "WB.KUM.0J.HNZ"
    rid = mgr.start("first", str(folder))
    recs, _ = records(sid, HOUR, 120)
    feed(hub, recs[:5])
    assert mgr.stop(rid) and not mgr.stop(rid)
    feed(hub, recs[5:10])                       # after stop: not recorded
    path = recorder.file_path(str(folder), sid, HOUR, 60, "channel")
    size = os.path.getsize(path)
    assert size == sum(len(r.raw) for r in recs[:5])
    s = mgr.sessions()[0]
    assert s["state"] == "stopped" and s["stopped"] >= s["started"]
    assert "RECORDING STOPPED" in rec_log(str(folder))
    # a second recording into the same folder appends to the existing file (never truncates)
    mgr.start("second", str(folder))
    feed(hub, recs[10:12])
    assert os.path.getsize(path) == size + sum(len(r.raw) for r in recs[10:12])
    assert (folder / "notes.txt").read_text() == "keep me"
    assert mgr.sessions()[1]["files"] == 0       # the file already existed
    mgr.close()
    assert os.path.exists(path) and (folder / "notes.txt").exists()


def test_restart_continues(tmp_path):
    hub, state, mgr = make(tmp_path)
    folder = str(tmp_path / "rec")
    sid = "WB.KUM.0J.HNZ"
    rid = mgr.start("long", folder, sids=[sid], file_minutes=30)
    recs, y = records(sid, HOUR + 5, 240)
    feed(hub, recs[:10])
    mgr.close()                                  # SeisZone quits; the recording stays active
    saved = State(state.path).get("recordings")
    assert saved[0]["id"] == rid and saved[0]["state"] == "recording" and saved[0]["sids"] == [sid]
    for key in ("name", "folder", "file_minutes", "layout", "started", "stopped", "files", "bytes"):
        assert key in saved[0]

    hub2 = DataHub()
    mgr2 = RecordingManager(hub2, State(state.path), str(tmp_path / "default"), background=False)
    s = mgr2.sessions()[0]
    assert s["id"] == rid and s["state"] == "recording" and s["files"] == 1
    feed(hub2, recs[10:])
    path = recorder.file_path(folder, sid, HOUR + 5, 30, "channel")
    with open(path, "rb") as fh:
        assert fh.read() == b"".join(r.raw for r in recs)
    assert mgr2.sessions()[0]["files"] == 1
    text = rec_log(folder)
    assert "SeisZone restarted - recording continues" in text
    assert "GAP" not in text                     # continuous data across the restart
    mgr2.close()

    # same State object, a new manager (engine restarted inside one program run)
    mgr3 = RecordingManager(DataHub(), mgr2.state, str(tmp_path / "default"), background=False)
    assert mgr3.sessions()[0]["state"] == "recording"
    mgr3.close()


def test_gap_and_silence_logged(tmp_path):
    hub, state, mgr = make(tmp_path)
    folder = str(tmp_path / "rec")
    sid = "WB.KUM.0J.HNZ"
    mgr.start("gaps", folder, sids=[sid])
    recs, _ = records(sid, HOUR, 300)
    feed(hub, recs[:10])
    feed(hub, recs[13:20])                       # 3 records missing
    gap = recs[13].start - recs[10].start
    text = rec_log(folder)
    assert f"GAP {sid}: {gap:.3f} s ({round(gap * RATE)} samples)" in text
    mgr.tick(time.time() + 400)
    assert "WB.KUM: stream silent for > 5 min" in rec_log(folder)
    feed(hub, recs[20:22])
    assert "WB.KUM: data back after" in rec_log(folder)
    mgr.close()


def test_write_error_keeps_recording(tmp_path):
    hub, state, mgr = make(tmp_path)
    folder = tmp_path / "rec"
    sid = "WB.KUM.0J.HNZ"
    rid = mgr.start("usb", str(folder), sids=[sid])
    recs, y = records(sid, HOUR, 300)
    feed(hub, recs[:5])
    # the drive is gone: the folder is replaced by a file (done by the test, not the program;
    # Windows cannot delete a file while it is open, so let the idle handle close first)
    mgr.tick(time.time() + recorder.IDLE_CLOSE + 1)
    shutil.rmtree(folder)
    folder.write_bytes(b"not a folder")
    feed(hub, recs[5:10])                        # must not raise
    s = mgr.sessions()[0]
    assert s["state"] == "recording" and s["last_error"] and s["pending"] == 5
    mgr.tick()
    assert mgr.sessions()[0]["state"] == "recording"
    # the drive is back: the waiting records are written first, nothing received is lost
    os.remove(folder)
    folder.mkdir()
    feed(hub, recs[10:])
    s = mgr.sessions()[0]
    assert s["last_error"] == "" and s["pending"] == 0
    path = recorder.file_path(str(folder), sid, HOUR, 60, "channel")
    with open(path, "rb") as fh:
        assert fh.read() == b"".join(r.raw for r in recs[5:])
    assert "writing works again" in rec_log(str(folder))
    assert mgr.stop(rid)
    mgr.close()


def test_disk_status(tmp_path, monkeypatch):
    hub, state, mgr = make(tmp_path)
    free, warn = mgr.disk_status(str(tmp_path / "does" / "not" / "exist"))
    assert free > 0
    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(recorder.shutil, "disk_usage", lambda p: usage(1000 * 1024 ** 3, 0, 3 * 1024 ** 3))
    free, warn = mgr.disk_status(str(tmp_path))
    assert free == 3 * 1024 ** 3 and "Low disk space" in warn and "never deletes" in warn
    monkeypatch.setattr(recorder.shutil, "disk_usage", lambda p: usage(4000 * 1024 ** 3, 0, 100 * 1024 ** 3))
    assert mgr.disk_status(str(tmp_path))[1] is not None        # 2.5 % free
    monkeypatch.setattr(recorder.shutil, "disk_usage", lambda p: usage(1000 * 1024 ** 3, 0, 500 * 1024 ** 3))
    assert mgr.disk_status(str(tmp_path))[1] is None
    mgr.close()


def test_idle_handles_closed(tmp_path):
    hub, state, mgr = make(tmp_path)
    mgr.start("idle", str(tmp_path / "rec"))
    recs, _ = records("WB.KUM.0J.HNZ", HOUR, 60)
    feed(hub, recs)
    assert len(mgr._handles) == 1
    mgr.tick(time.time() + recorder.IDLE_CLOSE + 1)
    assert mgr._handles == {}
    mgr.close()


def test_background_thread_and_unreadable_state_entry(tmp_path):
    state = State(str(tmp_path / "state.json"))
    state.set("recordings", [{"name": "no id"}])
    hub = DataHub()
    mgr = RecordingManager(hub, state, str(tmp_path / "default"))     # background thread on
    assert mgr._thread.is_alive()
    rid = mgr.start("bg", None)
    assert mgr.sessions()[0]["folder"] == str(tmp_path / "default")
    mgr.close()
    assert not mgr._thread.is_alive() and mgr.on_record not in hub.listeners
    saved = State(state.path).get("recordings")
    assert saved[0] == {"name": "no id"} and saved[1]["id"] == rid   # never dropped


# ---------------------------------------------------------------------- review fixes

def break_folder(mgr, folder):
    """The drive is gone: the folder is replaced by a file (handles closed first: Windows)."""
    mgr.tick(time.time() + recorder.IDLE_CLOSE + 1)
    shutil.rmtree(folder)
    folder.write_bytes(b"not a folder")


def test_many_waiting_records_cost_little_per_record(tmp_path):
    hub, state, mgr = make(tmp_path)
    folder = tmp_path / "rec"
    sid = "WB.KUM.0J.HNZ"
    mgr.start("full", str(folder), sids=[sid])
    recs, _ = records(sid, HOUR, 3000)
    feed(hub, recs[:2])
    break_folder(mgr, folder)
    s = mgr._sessions[0]
    path = recorder.file_path(str(folder), sid, HOUR, 60, "channel")
    s.pending.extend([(path, recs[0].raw)] * 50000)     # the disk has been full for a while
    s.pending_bytes += 50000 * len(recs[0].raw)
    t = time.perf_counter()
    feed(hub, recs[2:202])
    took = time.perf_counter() - t
    assert len(recs) >= 202 and mgr.sessions()[0]["pending"] == 50200
    assert took < 0.5, f"{took / 200 * 1000:.1f} ms per record while writes fail"
    mgr.close()


def test_new_handle_never_evicts_itself(tmp_path, monkeypatch):
    """More than MAX_OPEN files open and the clock steps back: the record still gets written."""
    monkeypatch.setattr(recorder, "MAX_OPEN", 4)
    hub, state, mgr = make(tmp_path)
    folder = str(tmp_path / "rec")
    mgr.start("lru", folder)
    for k, cha in enumerate(("HNZ", "HNN", "HNE", "BNZ")):
        feed(hub, records(f"WB.KUM.0J.{cha}", HOUR, 10, seed=k)[0])
    assert len(mgr._handles) == 4
    rec = records("WB.KUM.0J.BNN", HOUR, 10, seed=9)[0][0]
    s = mgr._sessions[0]
    mgr._take(s, rec, time.time() - 18.8, set())        # PC clock stepped back 18.8 s
    path = recorder.file_path(folder, rec.sid, rec.start, 60, "channel")
    with open(path, "rb") as fh:
        assert fh.read() == rec.raw
    assert not s.pending and s.last_error == "" and len(mgr._handles) == 4
    mgr.close()


def test_resend_after_a_crash_is_not_appended_twice(tmp_path):
    """The SeedLink resume position is saved every few seconds: after a crash the server sends
    records again that are already in the files."""
    hub, state, mgr = make(tmp_path)
    folder = str(tmp_path / "rec")
    sid = "WB.KUM.0J.HNZ"
    mgr.start("crash", folder, sids=[sid])
    recs, _ = records(sid, HOUR + 5, 240)
    feed(hub, recs[:6])
    mgr._persist(force=True)                    # the state saved before the last records ...
    feed(hub, recs[6:12])                       # ... these are written, then SeisZone crashes
    stale = State(state.path)
    for h in list(mgr._handles.values()):
        h[0].close()
    mgr._handles.clear()

    hub2 = DataHub()
    mgr2 = RecordingManager(hub2, stale, str(tmp_path / "default"), background=False)
    feed(hub2, recs[6:20])                      # resent from the stale position, then new data
    path = recorder.file_path(folder, sid, HOUR + 5, 60, "channel")
    with open(path, "rb") as fh:
        assert fh.read() == b"".join(r.raw for r in recs[:20])
    assert "GAP" not in rec_log(folder)
    mgr2.close()


def test_unwritten_records_at_close_are_reported_and_logged_as_gap(tmp_path):
    hub, state, mgr = make(tmp_path)
    folder = tmp_path / "rec"
    sid = "WB.KUM.0J.HNZ"
    mgr.start("usb", str(folder), sids=[sid])
    recs, _ = records(sid, HOUR, 300)
    feed(hub, recs[:4])
    break_folder(mgr, folder)
    feed(hub, recs[4:8])                         # wait in memory
    log_lines = []
    handler = logging.Handler()
    handler.emit = lambda r: log_lines.append(r.getMessage())
    logging.getLogger("seiszone").addHandler(handler)
    try:
        mgr.close()                              # SeisZone ends while the drive is still gone
    finally:
        logging.getLogger("seiszone").removeHandler(handler)
    assert any("4 records could not be written and are lost" in m and sid in m for m in log_lines)
    os.remove(folder)
    folder.mkdir()
    mgr2 = RecordingManager(DataHub(), State(state.path), str(tmp_path / "default"), background=False)
    mgr2.hub.add_record(recs[8], "test")
    gap = recs[8].start - recs[4].start
    assert f"GAP {sid}: {gap:.3f} s" in rec_log(str(folder))
    mgr2.close()
