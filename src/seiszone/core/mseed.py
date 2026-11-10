"""miniSEED 2 records: parse, decode (Steim1, Steim2, int16, int32, float32, float64), encode
(Steim2, lossless; a record whose differences do not fit Steim2 is stored as int32) and read files.

Pure Python + numpy. A record taken from the network is only accepted when its header is
plausible and (for Steim data) its samples decode to exactly the sample count of the header, so
random bytes are never taken for data. A reverse integration constant (Xn) that does not match
the last sample is only counted (xn_mismatches), like libmseed does: some writers get it wrong
while every sample is right. Steim differences are summed with int32 wraparound, as the C
reference decoders do.
"""

import datetime as dt
import re
import struct

import numpy as np

UTC = dt.timezone.utc
FIELD_OK = re.compile(rb"^[A-Za-z0-9 \x00_]*$")
_EPOCH_CACHE = {}
xn_mismatches = 0     # Steim records whose Xn did not match their last sample (kept anyway)


def _txt(b):
    return b.replace(b"\x00", b" ").decode("ascii", "replace").strip()


def _year_epoch(year):
    t = _EPOCH_CACHE.get(year)
    if t is None:
        t = _EPOCH_CACHE[year] = dt.datetime(year, 1, 1, tzinfo=UTC).timestamp()
    return t


def rate_from(factor, mult):
    if factor == 0 or mult == 0:
        return 0.0
    r = float(factor) if factor > 0 else -1.0 / factor
    return r * mult if mult > 0 else r / -mult


def seed_rate(rate):
    """(factor, multiplier) for a sample rate, both fitting int16 (exact for integer rates,
    1/n rates and decimal rates like 40.5; otherwise the closest pair that fits)."""
    candidates = []
    if rate >= 1:
        r = round(rate)
        if abs(rate - r) < 1e-9:
            candidates += [(r, 1)] + [(r // m, m) for m in (10, 100, 1000) if r % m == 0]
        candidates += [(round(rate * m), -m) for m in (1, 10, 100, 1000, 10000)]
    else:
        period = 1 / rate
        candidates += [(-round(period), 1)] + [(-round(period / m), -m) for m in (10, 100, 1000)]
        candidates += [(round(rate * m), -m) for m in (10, 100, 1000, 10000)]
    fits = [(f, m) for f, m in candidates if f and -32768 <= f <= 32767]
    for f, m in fits:
        if abs(rate_from(f, m) - rate) <= 1e-9 * rate:
            return int(f), int(m)
    if not fits:
        raise ValueError(f"sample rate {rate} cannot be written to miniSEED")
    f, m = min(fits, key=lambda p: abs(rate_from(*p) - rate))
    return int(f), int(m)


def _sext(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def steim_decode(data, nsamp, version, endian=">", strict_xn=False):
    """Steim1/Steim2 frames -> list of int. ValueError if damaged (sample count, dnib, no
    frames); an Xn mismatch is an error only with strict_xn, otherwise it is counted."""
    global xn_mismatches
    out = []
    x0 = xn = None
    last = 0
    fmt = endian + "16I"
    for f in range(len(data) // 64):
        words = struct.unpack(fmt, data[f * 64:(f + 1) * 64])
        nib = words[0]
        for w in range(1, 16):
            c = (nib >> (30 - 2 * w)) & 3
            word = words[w]
            if f == 0 and w == 1:
                x0 = _sext(word, 32)
                continue
            if f == 0 and w == 2:
                xn = _sext(word, 32)
                continue
            if c == 0:
                continue
            if c == 1:
                diffs = (_sext(word >> 24, 8), _sext((word >> 16) & 0xFF, 8),
                         _sext((word >> 8) & 0xFF, 8), _sext(word & 0xFF, 8))
            elif version == 1:
                diffs = ((_sext(word >> 16, 16), _sext(word & 0xFFFF, 16)) if c == 2
                         else (_sext(word, 32),))
            else:
                dn = word >> 30
                if c == 2:
                    if dn == 1:
                        diffs = (_sext(word & 0x3FFFFFFF, 30),)
                    elif dn == 2:
                        diffs = (_sext((word >> 15) & 0x7FFF, 15), _sext(word & 0x7FFF, 15))
                    elif dn == 3:
                        diffs = (_sext((word >> 20) & 0x3FF, 10), _sext((word >> 10) & 0x3FF, 10),
                                 _sext(word & 0x3FF, 10))
                    else:
                        raise ValueError("bad Steim2 dnib")
                else:
                    if dn == 0:
                        diffs = [_sext((word >> s) & 0x3F, 6) for s in (24, 18, 12, 6, 0)]
                    elif dn == 1:
                        diffs = [_sext((word >> s) & 0x1F, 5) for s in (25, 20, 15, 10, 5, 0)]
                    elif dn == 2:
                        diffs = [_sext((word >> s) & 0xF, 4) for s in (24, 20, 16, 12, 8, 4, 0)]
                    else:
                        raise ValueError("bad Steim2 dnib")
            for d in diffs:
                if len(out) >= nsamp:
                    break
                # int32 wraparound, like the C decoders (a clip from 2^31-1 to -2^31 is diff +1)
                last = x0 if not out else ((last + d + 0x80000000) & 0xFFFFFFFF) - 0x80000000
                out.append(last)
            if len(out) >= nsamp:
                break
        if len(out) >= nsamp:
            break
    if x0 is None and nsamp:
        raise ValueError("Steim: no frames")
    if len(out) != nsamp:
        raise ValueError(f"Steim: {len(out)} samples, header says {nsamp}")
    if nsamp and last != xn:
        if strict_xn:
            raise ValueError("Steim: reverse integration constant mismatch")
        xn_mismatches += 1
    return out


class Record:
    """One miniSEED 2 record. `raw` holds the exact bytes (forwarded unchanged by the SeedLink
    server and written unchanged to recordings)."""

    __slots__ = ("raw", "net", "sta", "loc", "cha", "quality", "start", "rate", "rfac", "rmul",
                 "nsamp", "encoding", "reclen", "endian", "data_off", "_samples")

    @property
    def sid(self):
        return f"{self.net}.{self.sta}.{self.loc}.{self.cha}"

    @property
    def station(self):
        return f"{self.net}.{self.sta}"

    @property
    def end(self):
        """Time just after the last sample (start of the next record if continuous)."""
        return self.start + (self.nsamp / self.rate if self.rate > 0 else 0.0)

    @property
    def last_sample_time(self):
        return self.start + ((self.nsamp - 1) / self.rate if self.rate > 0 and self.nsamp else 0.0)

    @property
    def samples(self):
        """Decoded samples as a numpy array (int32 / float32 / float64); decoded once."""
        if self._samples is None:
            self._samples = decode_samples(self)
        return self._samples

    def __repr__(self):
        return f"<Record {self.sid} {fmt_time(self.start)} {self.nsamp} @ {self.rate:g} sps>"


def decode_samples(rec):
    body = rec.raw[rec.data_off:rec.reclen]
    n, e, enc = rec.nsamp, rec.endian, rec.encoding
    if n == 0:
        return np.zeros(0, dtype=np.int32)
    if enc in (10, 11):
        usable = (len(body) // 64) * 64
        return np.asarray(steim_decode(body[:usable], n, 1 if enc == 10 else 2, e), dtype=np.int32)
    dtype = {1: "i2", 3: "i4", 4: "f4", 5: "f8"}.get(enc)
    if dtype is None:
        raise ValueError(f"unsupported miniSEED encoding {enc}")
    size = np.dtype(dtype).itemsize * n
    if len(body) < size:
        raise ValueError("record too short for its samples")
    arr = np.frombuffer(body[:size], dtype=e + dtype)
    return arr.astype(np.int32) if enc == 1 else arr.astype(arr.dtype.newbyteorder("="))


def parse_record(buf, off=0, check=True):
    """Parse a miniSEED 2 record at buf[off].
    Returns (Record, reclen); None when it is not a valid record; "short" when the header is
    valid but more bytes are needed. check=True also decodes the samples (and keeps them)."""
    if len(buf) - off < 48:
        return "short"
    h = bytes(buf[off:off + 48])
    if h[6:7] not in (b"D", b"R", b"Q", b"M") or not FIELD_OK.match(h[8:20]):
        return None
    if not all(c in b"0123456789 \x00" for c in h[0:6]):
        return None
    sta, loc, cha, net = _txt(h[8:13]), _txt(h[13:15]), _txt(h[15:18]), _txt(h[18:20])
    if not sta or len(cha) != 3:
        return None
    for endian in (">", "<"):
        year, doy, hh, mm, ss, _u, frac, nsamp, rfac, rmul = struct.unpack(endian + "HHBBBBHHhh", h[20:36])
        if 1950 <= year <= 2200 and 1 <= doy <= 366:
            break
    else:
        return None
    if hh > 23 or mm > 59 or ss > 60 or frac > 9999:
        return None
    act = h[36]
    nblk = h[39]
    tcorr = struct.unpack(endian + "i", h[40:44])[0]
    data_off, blk_off = struct.unpack(endian + "HH", h[44:48])
    if nblk < 1 or not 48 <= blk_off <= 4096:
        return None
    enc = reclen = None
    usec = 0
    p, hops = blk_off, 0
    while p and hops < 8:
        if len(buf) - off < p + 8:
            return "short"
        btype, nxt = struct.unpack(endian + "HH", bytes(buf[off + p:off + p + 4]))
        if btype == 1000:
            enc = buf[off + p + 4]
            exp = buf[off + p + 6]
            if not 7 <= exp <= 16:
                return None
            reclen = 1 << exp
        elif btype == 1001:
            usec = struct.unpack("b", bytes(buf[off + p + 5:off + p + 6]))[0]
        if nxt and nxt <= p:
            break
        p, hops = nxt, hops + 1
    if reclen is None:
        return None
    if nsamp and not 48 <= data_off < reclen:
        return None
    if len(buf) - off < reclen:
        return "short"
    rec = Record()
    rec.raw = bytes(buf[off:off + reclen])
    rec.net, rec.sta, rec.loc, rec.cha = net, sta, loc, cha
    rec.quality = chr(h[6])
    rec.rfac, rec.rmul = rfac, rmul
    rec.rate = rate_from(rfac, rmul)
    t = _year_epoch(year) + (doy - 1) * 86400 + hh * 3600 + mm * 60 + ss + frac / 1e4 + usec / 1e6
    if tcorr and not act & 0x02:
        t += tcorr / 1e4
    rec.start = t
    rec.nsamp = nsamp
    rec.encoding, rec.reclen, rec.endian, rec.data_off = enc, reclen, endian, data_off
    rec._samples = None
    if check and nsamp:
        if rec.rate <= 0:
            return None
        try:
            rec._samples = decode_samples(rec)
        except (ValueError, struct.error, OverflowError):
            return None
    return rec, reclen


def iter_records(buf, check=False):
    """All records in a byte string (any record length). Damaged parts are skipped by
    resyncing on 64-byte steps (records are multiples of 64 bytes long)."""
    off = 0
    n = len(buf)
    while off + 48 <= n:
        res = parse_record(buf, off, check=check)
        if isinstance(res, tuple):
            yield res[0]
            off += res[1]
        else:
            off += 64


def read_file(path, check=False):
    with open(path, "rb") as fh:
        buf = fh.read()
    yield from iter_records(buf, check=check)


# --------------------------------------------------------------------------
# Encoding (Steim2, lossless)
# --------------------------------------------------------------------------

_S2_OPTS = [(7, 4, 3, 2), (6, 5, 3, 1), (5, 6, 3, 0), (4, 8, 1, None),
            (3, 10, 2, 3), (2, 15, 2, 2), (1, 30, 2, 1)]


def _steim2_pack(diffs, nframes):
    i = 0
    frames = []
    nd = len(diffs)
    for f in range(nframes):
        words = [0] * 16
        nib = 0
        for w in range(3 if f == 0 else 1, 16):
            if i >= nd:
                break
            for cnt, bits, c, dn in _S2_OPTS:
                if cnt > nd - i:
                    continue
                lim = 1 << (bits - 1)
                chunk = diffs[i:i + cnt]
                if all(-lim <= d < lim for d in chunk):
                    mask = (1 << bits) - 1
                    val = 0
                    for d in chunk:
                        val = (val << bits) | (d & mask)
                    if dn is not None:
                        val |= dn << 30
                    words[w] = val & 0xFFFFFFFF
                    nib |= c << (30 - 2 * w)
                    i += cnt
                    break
            else:
                raise OverflowError("difference too large for Steim2")
        words[0] = nib
        frames.append(words)
        if i >= nd:
            break
    return frames, i


def btime(t):
    d = dt.datetime.fromtimestamp(round(t, 4), UTC)
    frac = round(d.microsecond / 100)
    if frac >= 10000:
        d = d.replace(microsecond=0) + dt.timedelta(seconds=1)
        frac = 0
    return struct.pack(">HHBBBBH", d.year, d.timetuple().tm_yday, d.hour, d.minute, d.second, 0, frac)


def encode_segment(net, sta, loc, cha, rfac, rmul, start, samples, seq_start=1, reclen=512, quality="D"):
    """Encode one contiguous run of integer samples as Steim2 records. Returns (bytes, next_seq).
    Samples are copied losslessly; nothing is resampled or filtered."""
    samples = [int(v) for v in samples]
    rate = rate_from(rfac, rmul)
    out = bytearray()
    nframes = (reclen - 64) // 64
    seq = seq_start
    pos = 0
    prev = samples[0] if samples else 0
    exp = reclen.bit_length() - 1
    while pos < len(samples):
        chunk_max = min(len(samples) - pos, nframes * 15 * 7)
        diffs = [samples[pos] - prev] + [samples[pos + k] - samples[pos + k - 1] for k in range(1, chunk_max)]
        if pos == 0:
            diffs[0] = 0
        try:
            frames, n = _steim2_pack(diffs, nframes)
            encoding = 11
        except OverflowError:  # a jump too large for Steim2 (sensor glitch): int32, still lossless
            frames, n, encoding = None, min(len(samples) - pos, (reclen - 64) // 4), 3
            if any(not -0x80000000 <= v <= 0x7FFFFFFF for v in samples[pos:pos + n]):
                raise ValueError("sample values beyond int32 cannot be written to miniSEED")
        t0 = start + pos / rate
        hdr = (f"{seq % 1000000:06d}".encode() + quality.encode()[:1] + b" " + sta.encode().ljust(5)[:5]
               + loc.encode().ljust(2)[:2] + cha.encode().ljust(3)[:3] + net.encode().ljust(2)[:2]
               + btime(t0) + struct.pack(">Hhh", n, rfac, rmul)
               + bytes([0, 0, 0, 1]) + struct.pack(">i", 0) + struct.pack(">HH", 64, 48))
        rec = bytearray(hdr + struct.pack(">HHBBBB", 1000, 0, encoding, 1, exp, 0))
        rec += b"\x00" * (64 - len(rec))
        if frames is not None:
            frames[0][1] = samples[pos] & 0xFFFFFFFF
            frames[0][2] = samples[pos + n - 1] & 0xFFFFFFFF
            for words in frames:
                rec += struct.pack(">16I", *words)
        else:
            rec += struct.pack(f">{n}i", *samples[pos:pos + n])
        rec += b"\x00" * (reclen - len(rec))
        out += rec
        prev = samples[pos + n - 1]
        pos += n
        seq += 1
    return bytes(out), seq


def encode_records(net, sta, loc, cha, rate, start, samples, seq_start=1, reclen=512):
    """encode_segment for a sample rate in Hz; returns a list of Record (parsed back)."""
    rfac, rmul = seed_rate(rate)
    data, _ = encode_segment(net, sta, loc, cha, rfac, rmul, start, samples, seq_start, reclen)
    return [parse_record(data, i, check=False)[0] for i in range(0, len(data), reclen)]


def fmt_time(t, local=False, ms=False):
    d = dt.datetime.fromtimestamp(t, UTC)
    if local:
        d = d.astimezone()
    s = d.strftime("%Y-%m-%d %H:%M:%S") + (f".{d.microsecond // 1000:03d}" if ms else "")
    return s + ("" if local else " UTC")
