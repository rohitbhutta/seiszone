"""
Guralp Compressed Format (GCF) blocks, as sent by Guralp digitisers and Scream servers.

Written from Scream 4.6's own documentation (GCF_Specification, Block_Transfer_and_BRP) and
checked against blocks pulled from a real Fortimus (sample values identical to ObsPy's reader).

    block  = 16-byte header + body
    header = system id | stream id | date code | reserved, sample rate, compression, records
    body   = FIC (first sample), first differences (8/16/32 bit), RIC (last sample, check value)

Network transport ("Scream protocol", UDP, default port 1567):
    client -> server  GCFPING            server -> client  GCFACKN
    client -> server  GCFSEND:B          server streams one block per datagram (big-endian)
    client -> server  GCFSTOP            server -> client  GCFACKN
TCP / serial links may wrap each block in the Block Transfer Protocol (BTP):
    'G', block number, body size (2 bytes), body, checksum (2 bytes, sum of all bytes mod 65536)
"""

import datetime as dt
import struct

SHORT = "short"          # parse_block: the header is plausible, the rest has not arrived yet
MAX_BLOCK = 24 + 255 * 4  # largest block (data: 24 + 4 * records)

B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GCF_EPOCH = dt.datetime(1989, 11, 17, tzinfo=dt.timezone.utc).timestamp()

# sample-rate byte codes above 250 sps / below 1 sps -> (rate, start-time fraction denominator)
SPECIAL_RATES = {157: (0.1, 1), 161: (0.125, 1), 162: (0.2, 1), 164: (0.25, 1), 167: (0.5, 1),
                 171: (400, 8), 174: (500, 2), 182: (625, 5), 176: (1000, 4), 191: (1250, 5),
                 179: (2000, 8), 193: (2500, 10), 181: (4000, 16), 194: (5000, 20)}


def base36(v):
    s = ""
    while True:
        s = B36[v % 36] + s
        v //= 36
        if v == 0:
            return s


def decode_system_id(v):
    """(id, type): type 0 = 6 characters, 1 = extended (5), 2 = double-extended (4)."""
    if v & 0x80000000:
        if v & 0x40000000:
            return base36(v & 0x001FFFFF), 2
        return base36(v & 0x03FFFFFF), 1
    return base36(v), 0


class Block:
    __slots__ = ("system_id", "sys_type", "stream_id", "start", "rate", "samples", "status_text", "size")

    @property
    def is_status(self):
        return self.rate == 0

    @property
    def end(self):
        return self.start + (len(self.samples) / self.rate if self.rate else 0)

    @property
    def component(self):
        return self.stream_id[-2] if len(self.stream_id) >= 2 else "?"

    @property
    def tap(self):
        return self.stream_id[-1]


def parse_block(buf, off=0):
    """Decode one GCF block at buf[off]. Returns Block, None (not a valid block) or SHORT (the
    header is plausible but buf ends before the block does: wait for more bytes).
    The FIC/RIC integration check must pass, so random bytes are never taken for data."""
    if len(buf) - off < 24:
        return None
    sysv, strv, date = struct.unpack_from(">III", buf, off)
    _res, rate_code, comp_byte, nrec = buf[off + 12:off + 16]
    if rate_code in SPECIAL_RATES:
        rate, den = SPECIAL_RATES[rate_code]
    else:
        rate, den = float(rate_code), 1
    secs = date & 0x1FFFF
    if secs >= 86400 + 1:  # +1: leap second
        return None
    b = Block()
    b.system_id, b.sys_type = decode_system_id(sysv)
    b.stream_id = base36(strv)
    num = ((comp_byte >> 4) & 0xF) | (((comp_byte >> 3) & 1) << 4) if den > 1 else 0
    b.start = GCF_EPOCH + (date >> 17) * 86400 + secs + (num / den if den > 1 else 0)
    b.rate = rate
    b.status_text = None
    if rate_code == 0:  # status block: text
        size = 16 + nrec * 4
        if len(buf) - off < size:
            return SHORT
        b.samples = []
        b.status_text = bytes(buf[off + 16:off + size]).decode("latin-1").rstrip("\x00 \r\n")
        b.size = size
        return b
    cc = comp_byte & 7
    if cc not in (1, 2, 4) or nrec == 0:
        return None
    size = 24 + nrec * 4
    if len(buf) - off < size:
        return SHORT
    fic = struct.unpack_from(">i", buf, off + 16)[0]
    n = cc * nrec
    diffs = struct.unpack_from({1: ">%di", 2: ">%dh", 4: ">%db"}[cc] % n, buf, off + 20)
    ric = struct.unpack_from(">i", buf, off + 20 + nrec * 4)[0]
    v = fic
    out = []
    for d in diffs:  # the first difference is 0 (FIC is explicit)
        v += d
        out.append(v)
    if out[-1] != ric:
        return None
    b.samples = out
    b.size = size
    return b


def parse_datagram(d):
    """A UDP datagram from a Scream server / Guralp digitiser: a raw block (Fortimus adds a
    trailer after it) or one BTP frame. Returns Block or None."""
    if len(d) >= 30 and d[0:1] == b"G":
        size = struct.unpack_from(">H", d, 2)[0]
        if 24 <= size <= len(d) - 4:
            b = parse_block(d, 4)
            if isinstance(b, Block):
                return b
    b = parse_block(d, 0)
    return b if isinstance(b, Block) else None


def plausible_status(b):
    """A status block found in a bare byte stream (no BTP checksum) is believed only when its
    text is mostly printable: a status header is easy to hit by chance (rate byte 0)."""
    text = b.status_text or ""
    if not text:
        return False
    good = sum(1 for c in text if " " <= c <= "~" or c in "\r\n\t")
    return good >= 0.9 * len(text)


class StreamParser:
    """TCP / serial byte stream: BTP frames or bare blocks. feed() returns
    [(Block, ack_bytes or None)]; send the ack bytes back for BTP frames. A block cut by a read
    boundary waits for the rest (at most MAX_BLOCK bytes); bare status blocks are only taken
    when plausible_status() says so."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        self.buf += data
        out = []
        pos = 0
        while len(self.buf) - pos >= 24:
            if self.buf[pos] == 0x47:  # 'G' - Block Transfer Protocol frame
                size = struct.unpack_from(">H", self.buf, pos + 2)[0]
                if 24 <= size <= 1024:
                    end = pos + 4 + size + 2
                    if len(self.buf) < end:
                        break
                    chk = struct.unpack_from(">H", self.buf, end - 2)[0]
                    if sum(self.buf[pos:end - 2]) & 0xFFFF == chk:
                        b = parse_block(self.buf, pos + 4)
                        if isinstance(b, Block):
                            sid = struct.unpack_from(">I", self.buf, pos + 8)[0]
                            out.append((b, ack(sid)))
                            pos = end
                            continue
            b = parse_block(self.buf, pos)
            if b is SHORT:
                break  # the rest of the block is on its way (a block is never longer than MAX_BLOCK)
            if b is not None and (not b.is_status or plausible_status(b)):
                out.append((b, None))
                pos += b.size
                continue
            pos += 1  # resync
        del self.buf[:pos]
        if len(self.buf) > 65536:
            del self.buf[:-4096]
        return out


def ack(stream_value):
    """6-byte BTP/BRP acknowledgement for the last received block of a stream."""
    s = stream_value.to_bytes(4, "big")
    return bytes([1, s[3], 0, s[2], s[1], s[0]])


# --------------------------------------------------------------------------
# SEED names when the instrument does not publish its own mapping
# --------------------------------------------------------------------------

def band_code(rate):
    if rate >= 1000:
        return "F"
    if rate >= 250:
        return "C"
    if rate >= 80:
        return "H"
    if rate >= 10:
        return "B"
    if rate > 1:
        return "M"
    if rate >= 0.99:
        return "L"
    if rate >= 0.095:
        return "V"
    return "U"


def default_seed_name(stream_id, rate):
    """(location, channel) for a GCF stream: e.g. ACCZ0 @200 -> ('00', 'HNZ'), 1234Z2 @100 -> ('02', 'HHZ').
    Accelerometer streams (ACC*/AXL*) get instrument code N, everything else H."""
    comp = stream_id[-2] if len(stream_id) >= 2 else "Z"
    tap = stream_id[-1]
    inst = "N" if stream_id[:3] in ("ACC", "AXL") else "H"
    if comp not in "ZNE123":
        comp = "X"
    loc = ("0" + tap) if tap.isdigit() else "00"
    return loc, band_code(rate) + inst + comp


def seed_rate(rate):
    """miniSEED sample-rate factor and multiplier."""
    if rate >= 1:
        return int(round(rate)), 1
    return -int(round(1 / rate)), 1
