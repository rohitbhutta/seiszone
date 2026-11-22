"""Export a time window [t0, t1) of chosen channels as miniSEED files plus a text report.

Data comes from the recording folders (the files RecordingManager writes, both layouts) and,
for whatever they do not hold, from the hub's live buffer. Records lying completely inside the
window are copied unchanged (fast and lossless); only the records at the window edges, records
that overlap others (the same data received twice, e.g. live and as backfill) and live-buffer
data are decoded, trimmed to exactly the samples inside the window and re-encoded as Steim2.
Nothing is resampled or filtered. Existing files are never overwritten.

A recording file holds the records that START in its period, so for a very low sample rate
(a 512-byte record of 0.1 sps data spans about 2 hours) files from further back are read too:
the look-back is the longest file period plus the longest record of that rate.
recorded_sids() lists the channels the recording folders hold for a window (the export dialog
offers them even when the station is offline now).
"""

import collections
import datetime as dt
import math
import os
import struct

import numpy as np

from . import mseed
from .recorder import FILE_RE, name_time

UTC = dt.timezone.utc
MAX_FILE_SPAN = 2 * 3600.0     # s: a recording file may hold records starting this long before t0
MAX_LOOKBACK = 48 * 3600.0     # s: the furthest look-back (very low sample rates)
MAX_PERIOD = 3600.0            # s: the longest recording file period (recorder.FILE_MINUTES)
MAX_REC_SAMPLES = 1000         # samples in one 512-byte record at most (Steim2: about 735)
EPS = 0.05                     # in samples: tolerance for "this sample is at / after t0"


class _Piece:
    """Contiguous samples of one channel: either an unchanged record or an array to encode."""

    __slots__ = ("start", "n", "rec", "y")

    def __init__(self, start, n, rec=None, y=None):
        self.start, self.n, self.rec, self.y = start, n, rec, y


def _station(sid):
    return ".".join(sid.split(".")[:2])


def find_files(folders, sid, t0, t1, lookback=MAX_FILE_SPAN):
    """Recording files of the folders that may hold `sid` data in [t0, t1) (files starting up
    to `lookback` s before t0): only the day folders touching that span are listed, so years
    of recordings do not slow this down."""
    station = _station(sid)
    out, seen = [], set()
    day = dt.datetime.fromtimestamp(t0 - lookback, UTC).date()
    last = dt.datetime.fromtimestamp(t1, UTC).date()
    while day <= last:
        for folder in folders:
            path = os.path.join(folder, station, day.isoformat())
            try:
                entries = list(os.scandir(path))
            except OSError:
                continue
            for e in entries:
                m = FILE_RE.match(e.name)
                if not m or m.group("code") not in (sid, station):
                    continue
                t = name_time(m)
                key = os.path.normcase(os.path.realpath(e.path))
                if t0 - lookback <= t < t1 and key not in seen:
                    seen.add(key)
                    out.append((t, e.path))
        day += dt.timedelta(days=1)
    out.sort()
    return out


def _older_files(folders, sid, t0, t1, files, rate):
    """Files before the usual look-back that can still hold samples of a low-rate channel.
    rate None (nothing found yet): the newest older file of the channel tells its rate."""
    if rate is None:
        older = [f for f in find_files(folders, sid, t0, t1, MAX_LOOKBACK) if f not in files]
        for _t, path in reversed(older):
            try:
                rate = next((r.rate for r in mseed.read_file(path) if r.sid == sid and r.rate > 0), None)
            except OSError:
                continue
            if rate:
                break
        if not rate:
            return []
    need = min(MAX_PERIOD + MAX_REC_SAMPLES / rate, MAX_LOOKBACK)
    if need <= MAX_FILE_SPAN:
        return []
    return [f for f in find_files(folders, sid, t0, t1, need) if f not in files]


def recorded_sids(folders, t0, t1):
    """Channel ids the recording folders hold files of for the window [t0, t1) (both layouts:
    the records of a station file are read for their channel ids)."""
    out = set()
    first = dt.datetime.fromtimestamp(t0 - MAX_FILE_SPAN, UTC).date()
    last = dt.datetime.fromtimestamp(t1, UTC).date()
    for folder in dict.fromkeys(f for f in folders or [] if f):
        try:
            stations = [e.name for e in os.scandir(folder) if e.is_dir() and e.name.count(".") == 1]
        except OSError:
            continue
        for station in stations:
            day = first
            while day <= last:
                try:
                    entries = list(os.scandir(os.path.join(folder, station, day.isoformat())))
                except OSError:
                    entries = []
                for e in entries:
                    m = FILE_RE.match(e.name)
                    if not m or not t0 - MAX_FILE_SPAN <= name_time(m) < t1:
                        continue
                    code = m.group("code")
                    if code.count(".") == 3:
                        out.add(code)
                    elif code == station:
                        try:
                            out.update(r.sid for r in mseed.read_file(e.path))
                        except OSError:
                            pass
                day += dt.timedelta(days=1)
    return sorted(out)


def _collect(files, sid, t0, t1, notes):
    recs = []
    for _t, path in files:
        try:
            for r in mseed.read_file(path):
                if r.sid == sid and r.rate > 0 and r.nsamp > 0 and r.end > t0 and r.start < t1:
                    recs.append(r)
        except OSError as e:
            notes.append(f"    cannot read {path}: {e}")
    return recs


def _cut(start, y, rate, lo, hi):
    """Part of y (sample k at start + k / rate) with lo <= time < hi, as (start, array)."""
    i0 = max(0, math.ceil((lo - start) * rate))
    i1 = min(len(y), math.ceil((hi - start) * rate))
    if i1 <= i0:
        return None
    return start + i0 / rate, y[i0:i1]


def _from_files(recs, rate, t0, t1, counts):
    """Pieces from recorded records: inner 512-byte records unchanged, the rest trimmed."""
    tol, eps = 0.5 / rate, EPS / rate
    pieces = []
    cursor = -math.inf          # time of the first sample not covered yet
    recs.sort(key=lambda r: (r.start, -r.nsamp))
    for r in recs:
        last = r.last_sample_time
        if last < cursor - tol:         # everything in it is held already
            counts["duplicates"] += 1
            continue
        if last < t0 - eps or r.start >= t1 - eps:
            continue
        lo = max(t0 - eps, cursor - tol)
        if r.start >= lo and last < t1 - eps and r.reclen == 512:
            pieces.append(_Piece(r.start, r.nsamp, rec=r))
            counts["copied"] += 1
        else:
            try:
                y = r.samples
            except (ValueError, struct.error):
                counts["damaged"] += 1
                continue
            cut = _cut(r.start, y, rate, lo, t1 - eps)
            if cut is not None:
                pieces.append(_Piece(cut[0], len(cut[1]), y=cut[1]))
                counts["trimmed"] += 1
            counts["overlaps"] += r.start < cursor - tol
        cursor = max(cursor, r.end)
    return pieces


def _holes(pieces, rate, t0, t1):
    """Uncovered time ranges (lo, hi): a sample at time t is missing when lo <= t < hi."""
    tol, eps = 0.5 / rate, EPS / rate
    holes = []
    lo = t0 - eps
    for p in sorted(pieces, key=lambda p: p.start):
        if p.start - tol > lo:
            holes.append((lo, p.start - tol))
        lo = max(lo, p.start + p.n / rate - tol)
    if lo < t1 - eps:
        holes.append((lo, t1 - eps))
    return holes


def _slots(ref, rate, lo, hi):
    """Sample times ref + k / rate with lo <= t < hi: (time of the first one, how many)."""
    k0 = math.ceil((lo - ref) * rate)
    return ref + k0 / rate, max(0, math.ceil((hi - ref) * rate) - k0)


def _from_hub(hub, sid, rate, holes, counts):
    pieces = []
    for lo, hi in holes:
        for start, r, y in hub.window(sid, lo - 1.0 / rate, hi + 1.0 / rate):
            if abs(r - rate) > 1e-6:
                continue
            cut = _cut(start, y, r, lo, hi)
            if cut is not None:
                pieces.append(_Piece(cut[0], len(cut[1]), y=np.array(cut[1])))
                counts["live"] += len(cut[1])
    return pieces


def _float_records(net, sta, loc, cha, rfac, rmul, start, y, seq):
    """Non-integer samples as big-endian float64 records (512 bytes, lossless)."""
    rate = mseed.rate_from(rfac, rmul)
    per = (512 - 64) // 8
    out = bytearray()
    for pos in range(0, len(y), per):
        chunk = y[pos:pos + per]
        hdr = (f"{seq % 1000000:06d}D ".encode() + sta.encode().ljust(5)[:5] + loc.encode().ljust(2)[:2]
               + cha.encode().ljust(3)[:3] + net.encode().ljust(2)[:2] + mseed.btime(start + pos / rate)
               + struct.pack(">Hhh", len(chunk), rfac, rmul) + bytes([0, 0, 0, 1]) + struct.pack(">i", 0)
               + struct.pack(">HH", 64, 48) + struct.pack(">HHBBBB", 1000, 0, 5, 1, 9, 0))
        rec = hdr + b"\x00" * (64 - len(hdr)) + np.asarray(chunk, dtype=">f8").tobytes()
        out += rec + b"\x00" * (512 - len(rec))
        seq += 1
    return bytes(out), seq


def _encode(pieces, rate, codes, rfac, rmul):
    """Bytes of all pieces in time order; touching arrays are joined into one segment first."""
    tol = 0.5 / rate
    groups = []
    for p in sorted(pieces, key=lambda p: p.start):
        g = groups[-1] if groups else None
        if (p.y is not None and g is not None and g[0] == "y"
                and abs(p.start - (g[1] + sum(len(a) for a in g[2]) / rate)) <= tol):
            g[2].append(p.y)
        else:
            groups.append(("raw", p.rec) if p.rec is not None else ("y", p.start, [p.y]))
    out = bytearray()
    seq = 1
    for g in groups:
        if g[0] == "raw":
            out += g[1].raw
            continue
        y = np.concatenate(g[2])
        if y.dtype.kind == "f" and not np.array_equal(y, np.round(y)):
            data, seq = _float_records(*codes, rfac, rmul, g[1], y, seq)
        else:
            data, seq = mseed.encode_segment(*codes, rfac, rmul, g[1], y.astype(np.int64).tolist(), seq)
        out += data
    return bytes(out)


def _channel(sid, t0, t1, folders, hub, log):
    """Everything about one channel: (bytes or b"", report lines)."""
    notes = []
    counts = collections.Counter()
    files = find_files(folders, sid, t0, t1) if folders else []
    recs = _collect(files, sid, t0, t1, notes)
    if folders:   # a low-rate channel: its records reach further than the usual look-back
        guess = recs[0].rate if recs else None
        if guess is None and hub is not None:
            live = hub.window(sid, t0, t1)
            guess = live[0][1] if live else None
        recs += _collect(_older_files(folders, sid, t0, t1, files, guess), sid, t0, t1, notes)
    rate = None
    if recs:
        rate = collections.Counter(round(r.rate, 6) for r in recs).most_common(1)[0][0]
        other = [r for r in recs if round(r.rate, 6) != rate]
        if other:
            notes.append(f"    {len(other)} records at another sample rate skipped")
            recs = [r for r in recs if round(r.rate, 6) == rate]
    elif hub is not None:
        live = hub.window(sid, t0, t1)
        rate = live[0][1] if live else None
    if rate is None:
        return b"", [f"{sid}: NO DATA in this window"] + notes
    pieces = _from_files(recs, rate, t0, t1, counts)
    if hub is not None:
        holes = _holes(pieces, rate, t0, t1)
        if holes:
            pieces += _from_hub(hub, sid, rate, holes, counts)
    if not pieces:
        return b"", [f"{sid}: NO DATA in this window"] + notes
    pieces.sort(key=lambda p: p.start)
    tpl = recs[0] if recs else None
    codes = (tpl.net, tpl.sta, tpl.loc, tpl.cha) if tpl else tuple(sid.split("."))
    rfac, rmul = (tpl.rfac, tpl.rmul) if tpl else mseed.seed_rate(rate)
    data = _encode(pieces, rate, codes, rfac, rmul)

    dt_ = 1.0 / rate
    nsamp = sum(p.n for p in pieces)
    first, last_end = pieces[0].start, max(p.start + p.n * dt_ for p in pieces)
    expected = _slots(first, rate, t0 - EPS / rate, t1 - EPS / rate)[1]   # in this stream's sample phase
    gaps = []
    for lo, hi in _holes(pieces, rate, t0, t1):
        g0, k = _slots(first, rate, lo, hi)
        if k > 0:
            gaps.append((g0, g0 + k * dt_, k))
    lines = [f"{sid}: {rate:g} sps, {nsamp} samples of {expected} expected, {len(gaps)} gap(s), "
             f"data {mseed.fmt_time(first, ms=True)} -> {mseed.fmt_time(last_end, ms=True)}"]
    src = []
    if recs:
        src.append(f"recordings: {counts['copied']} records copied unchanged, {counts['trimmed']} re-encoded "
                   f"(window edges, overlaps, other record lengths), {counts['duplicates']} duplicate records dropped")
    if counts["live"]:
        src.append(f"live buffer: {counts['live']} samples")
    if counts["damaged"]:
        src.append(f"{counts['damaged']} damaged records skipped")
    lines.append("    from " + "; ".join(src))
    for g0, g1, k in gaps:
        lines.append(f"    GAP {mseed.fmt_time(g0, ms=True)} -> {mseed.fmt_time(g1, ms=True)}  "
                     f"({g1 - g0:.3f} s, {k} samples missing)")
    log(f"{sid}: {nsamp} of {expected} samples, {len(gaps)} gap(s)")
    return data, lines + notes


def _duration_label(seconds):
    if abs(seconds - round(seconds / 60) * 60) < 1e-6:
        return f"{round(seconds / 60)}min"
    return f"{seconds:g}s"


def _unique(path):
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(path):
        path = f"{base}_{n}{ext}"
        n += 1
    return path


def export_window(sids, t0, t1, outdir, mode="separate", folders=None, hub=None, log=print):
    """Write [t0, t1) of the channels `sids` to outdir. mode "separate" = one file per channel,
    "combined" = one file with all channels. Returns (written paths incl. the report file,
    report text)."""
    if t1 <= t0:
        raise ValueError("the end of the window must be after its start")
    if mode not in ("separate", "combined"):
        raise ValueError(f"unknown export mode {mode!r}")
    folders = [f for f in (folders or []) if f]
    os.makedirs(outdir, exist_ok=True)
    stamp = dt.datetime.fromtimestamp(t0, UTC).strftime("%Y%m%dT%H%M%SZ")
    label = _duration_label(t1 - t0)
    report = ["SeisZone export",
              f"Window : {mseed.fmt_time(t0, ms=True)}  ->  {mseed.fmt_time(t1, ms=True)}  ({label})",
              f"Local  : {mseed.fmt_time(t0, local=True, ms=True)}  ->  {mseed.fmt_time(t1, local=True, ms=True)}",
              "Mode   : " + ("one file per channel" if mode == "separate" else "one file with all channels"),
              "Sources: " + (", ".join(folders) if folders else "no recording folders")
              + (" + live buffer" if hub is not None else ""),
              ""]
    encoded = {}
    for sid in sids:
        log(f"Reading {sid} ...")
        data, lines = _channel(sid, t0, t1, folders, hub, log)
        report += lines
        if data:
            encoded[sid] = data
    written = []
    if mode == "separate":
        for sid, data in encoded.items():
            written.append(_write(os.path.join(outdir, f"{sid}__{stamp}_{label}.mseed"), data))
    elif encoded:
        stations = sorted({_station(s) for s in encoded})
        name = stations[0] if len(stations) == 1 else "MULTI"
        path = os.path.join(outdir, f"{name}__{stamp}_{label}_{len(encoded)}ch.mseed")
        written.append(_write(path, b"".join(encoded[s] for s in sids if s in encoded)))
    report += ["", "Files:"] + [f"  {os.path.basename(p)}" for p in written]
    if not written:
        report.append("  (none - no data in this window)")
    text = "\n".join(report)
    info = _unique(os.path.join(outdir, f"export_info__{stamp}.txt"))
    with open(info, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    written.append(info)
    log(f"Export finished: {len(written) - 1} miniSEED file(s) in {outdir}")
    return written, text


def _write(path, data):
    path = _unique(path)
    with open(path, "wb") as fh:
        fh.write(data)
    return path
