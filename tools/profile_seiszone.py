"""Speed measurements of SeisZone's hot paths, with synthetic data only (no network, no real
instrument, a temporary SEISZONE_HOME). Run it before and after a speed change and compare.

    python tools/profile_seiszone.py                 # all measurements (about 3 minutes)
    python tools/profile_seiszone.py --only view     # one part: ingest | view
    python tools/profile_seiszone.py --profile NAME  # pyinstrument call tree of one view case

Parts:
  ingest - decoding miniSEED records and DataHub.add_record (what every arriving record costs),
           and a day of backfill for one channel.
  view   - a WaveformView live, in real time: every 250 ms new samples arrive (as from the
           instruments) and the view refreshes, like a viewer window on screen. Measured per
           case: the time refresh() takes (computing the traces) and the time Qt then takes to
           draw (processEvents with the offscreen platform), as a share of the main thread.
"""

import argparse
import math
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("SEISZONE_HOME", tempfile.mkdtemp(prefix="seiszone_prof_"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32" and os.path.isdir(r"C:\Windows\Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import numpy as np  # noqa: E402

from seiszone.core import history, mseed  # noqa: E402
from seiszone.core.hub import DataHub, StreamBuffer  # noqa: E402

RNG = np.random.default_rng(3)


def signal_like(n, rate, t_start=0.0):
    t = t_start + np.arange(n) / rate
    return (2000 * np.sin(2 * np.pi * 1.3 * t) + 500 * np.sin(2 * np.pi * 7 * t)
            + RNG.normal(0, 150, n)).astype(np.int64)


# ------------------------------------------------------------------------------------------
# ingest
# ------------------------------------------------------------------------------------------
def bench_ingest():
    print("\n== ingest: decode + DataHub.add_record ==")
    seconds = 600
    chans = [(s, c, r) for s in ("KDT", "KAND", "KUM") for c, r in
             (("HNZ", 200), ("HNN", 200), ("HNE", 200), ("MNZ", 5), ("MNN", 5), ("MNE", 5))]
    t0 = time.time() - seconds
    raw = []
    for sta, cha, rate in chans:
        y = signal_like(int(seconds * rate), rate)
        loc = "0J" if rate == 200 else "9J"
        for rec in mseed.encode_records("WB", sta, loc, cha, rate, t0, y):
            raw.append(rec.raw if hasattr(rec, "raw") else bytes(rec.data))
    raw.sort(key=lambda b: b[20:30])   # roughly by time, as they arrive
    nsamp = 0
    t = time.perf_counter()
    recs = [mseed.parse_record(b)[0] for b in raw]
    t_parse = time.perf_counter() - t
    t = time.perf_counter()
    for r in recs:
        nsamp += len(r.samples)
    t_decode = time.perf_counter() - t
    recs = [mseed.parse_record(b)[0] for b in raw]
    hub = DataHub(buffer_seconds=3600)
    t = time.perf_counter()
    for r in recs:
        hub.add_record(r, "src")
    t_add = time.perf_counter() - t    # includes the decode again (rec.samples) + buffer + summary
    n = len(raw)
    real_rate = n / seconds
    print(f"  {n} records ({nsamp} samples, 9 x 200 sps + 9 x 5 sps, {seconds} s of data)")
    print(f"  parse header   {t_parse / n * 1e6:7.1f} us/record")
    print(f"  decode Steim   {t_decode / n * 1e6:7.1f} us/record  ({nsamp / t_decode / 1e6:.1f} M samples/s)")
    print(f"  add_record     {t_add / n * 1e6:7.1f} us/record  (decode + live buffer + day summary + stats)")
    print(f"  live load: {real_rate:.1f} records/s arrive -> engine thread busy "
          f"{(t_parse + t_add) / seconds * 100:.3f} % of the time")
    # one day of one 200 sps channel arriving as backfill (oldest first)
    day = 6 * 3600
    y = signal_like(day * 200, 200)
    recs = list(mseed.encode_records("WB", "KUM", "0J", "HNZ", 200, time.time() - day, y))
    raw = [r.raw if hasattr(r, "raw") else bytes(r.data) for r in recs]
    hub = DataHub(buffer_seconds=3600)
    t = time.perf_counter()
    for b in raw:
        hub.add_record(mseed.parse_record(b)[0], "src")
    took = time.perf_counter() - t
    print(f"  backfill: 6 h of one 200 sps channel ({len(raw)} records) in {took:.2f} s "
          f"-> a day of 9 channels about {took * 4 * 9:.0f} s of engine-thread time")
    return {"parse_us": t_parse / n * 1e6, "add_us": t_add / n * 1e6}


# ------------------------------------------------------------------------------------------
# live viewer
# ------------------------------------------------------------------------------------------
class Eng:
    """The parts of Engine a WaveformView uses."""


def make_engine(n_channels, buffer_seconds=3600, day=False):
    from seiszone.config import Config
    eng = Eng()
    eng.config = Config(os.path.join(os.environ["SEISZONE_HOME"], f"cfg{n_channels}{day}.json"))
    eng.responses = None
    eng.history = None
    eng.hub = hub = DataHub(buffer_seconds=buffer_seconds)
    now = time.time()
    sids = []
    for i in range(n_channels):
        sta, cha = ["KDT", "KAND", "KUM", "S04"][i // 3], ["HNZ", "HNN", "HNE"][i % 3]
        sid = f"WB.{sta}.0J.{cha}"
        sids.append(sid)
        buf = hub.buffers[sid] = StreamBuffer(200.0, buffer_seconds)
        n = buffer_seconds * 200
        buf.add(now - buffer_seconds, signal_like(n, 200.0).astype(np.float64))
        s = hub.summaries[sid] = history.Summary()
        s.rate = 200.0
        if day:   # per-second bins for the whole day (as kept by the hub)
            b = np.arange(int(now) - 86400, int(now), dtype=np.int64)
            lo = RNG.normal(-2500, 100, len(b))
            s._merge(b, lo, lo + 5000, np.zeros(len(b)), np.full(len(b), 200, dtype=np.int64))
        hub.stats[sid] = {"sid": sid, "net": "WB", "sta": sta, "loc": "0J", "cha": cha, "rate": 200.0,
                          "source": "", "packets": 1, "first": now - buffer_seconds, "last_end": now,
                          "last_arrival": now, "latency": 0.0, "gaps": 0, "gap_seconds": 0.0,
                          "last_gap": None, "backfilled": 0, "last_value": 0.0, "future_rejected": 0}
    return eng, sids


def feed(hub, sids, upto):
    """Samples up to `upto` (epoch) for every channel, as the engine thread adds them."""
    with hub.lock:
        for sid in sids:
            st = hub.stats[sid]
            n = int((upto - st["last_end"]) * 200)
            if n <= 0:
                continue
            y = signal_like(n, 200.0, st["last_end"]).astype(np.float64)
            hub.buffers[sid].add(st["last_end"], y)
            hub.summaries[sid].add(st["last_end"], 200.0, y)
            st["last_end"] += n / 200.0
            st["last_arrival"] = time.time()
            st["packets"] += 1


CASES = {   # name: (channels, window seconds, filter key or None)
    "3ch-1min": (3, 60, None),
    "3ch-10min": (3, 600, None),
    "9ch-1min": (9, 60, None),
    "9ch-1min-bp": (9, 60, "bp1-10"),
    "9ch-10min": (9, 600, None),
    "9ch-10min-bp": (9, 600, "bp1-10"),
    "9ch-60min": (9, 3600, None),
    "9ch-60min-bp": (9, 3600, "bp1-10"),
    "12ch-60min-bp": (12, 3600, "bp1-10"),
    "9ch-24h": (9, 86400, None),
}


def run_case(app, name, seconds=12.0, profiler=None):
    from seiszone.ui.waveform_view import WaveformView
    n, window, filt = CASES[name]
    eng, sids = make_engine(n, day=window > 3600)
    state = {"filter_on": bool(filt), "filter": filt or "default"}
    v = WaveformView(eng, eng.config, state=state)
    v.resize(1600, 900)
    v.show()
    app.processEvents()
    v.set_channels(sids)
    app.processEvents()
    v.set_window(window)
    v.refresh()
    for _ in range(20):            # settle: everything computed once
        app.processEvents()
        v.refresh()
    import gc
    gc.collect()
    ref, paint, slow = [], [], 0
    recomputed = 0
    if profiler:
        profiler.start()
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        tick = time.perf_counter()
        feed(eng.hub, sids, time.time())
        before = [tr.computed for tr in v._traces]
        t = time.perf_counter()
        v.refresh()
        ref.append(time.perf_counter() - t)
        recomputed += sum(1 for tr, b in zip(v._traces, before) if tr.computed != b)
        t = time.perf_counter()
        app.processEvents()
        paint.append(time.perf_counter() - t)
        if ref[-1] + paint[-1] > 0.1:
            slow += 1
        rest = 0.25 - (time.perf_counter() - tick)
        if rest > 0:
            time.sleep(rest)
    if profiler:
        profiler.stop()
    ticks = len(ref)
    busy = (sum(ref) + sum(paint)) / seconds * 100
    print(f"  {name:15s} refresh avg {np.mean(ref) * 1000:6.1f} ms max {max(ref) * 1000:6.1f} | "
          f"draw avg {np.mean(paint) * 1000:6.1f} ms max {max(paint) * 1000:6.1f} | "
          f"traces recomputed/tick {recomputed / ticks:4.1f} of {n} | main thread busy {busy:5.1f} % | "
          f"ticks > 100 ms: {slow}")
    v.close()
    v.deleteLater()
    app.processEvents()
    return {"refresh_ms": np.mean(ref) * 1000, "draw_ms": np.mean(paint) * 1000, "busy": busy}


def bench_view(only=None, profile=None):
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication
    from seiszone.ui import theme
    app = QApplication.instance() or QApplication([])
    theme.apply(app, "dark")
    app.setFont(QFont("Segoe UI", 9))
    if profile:
        from pyinstrument import Profiler
        prof = Profiler(interval=0.0005)
        print(f"\n== pyinstrument: {profile} ==")
        run_case(app, profile, seconds=15.0, profiler=prof)
        print(prof.output_text(unicode=True, color=False, show_all=False, timeline=False))
        return
    print("\n== live viewer (1600 x 900, a tick every 250 ms, 12 s per case) ==")
    out = {}
    for name in CASES:
        if only and only not in name:
            continue
        out[name] = run_case(app, name)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["ingest", "view"])
    ap.add_argument("--case", help="view cases whose name contains this text")
    ap.add_argument("--profile", choices=list(CASES))
    a = ap.parse_args()
    import platform
    print(f"SeisZone speed check - Python {platform.python_version()}, numpy {np.__version__}, "
          f"{os.cpu_count()} CPUs")
    if a.profile:
        bench_view(profile=a.profile)
        return
    if a.only in (None, "ingest"):
        bench_ingest()
    if a.only in (None, "view"):
        bench_view(only=a.case)


if __name__ == "__main__":
    main()
