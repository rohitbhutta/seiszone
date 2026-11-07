"""Kelunji Gecko recorders connecting IN over their serial-to-TCP link (USR-W610, modem).

The Gecko's telemetry port is configured to call a "hub" at this PC (TCP 58772 by default); the
Engine listens there and hands every connection to GeckoReceiver.handle(). No vendor streaming
software is needed: SeisZone speaks the Gecko's native telemetry framing itself. The byte-level
behaviour below (hello, `sampling 1`, `? "sitename"`, acknowledgement rhythm, 120 s silence)
was reverse engineered and proven against a real SMR03 - change it only with a new capture.

Frame:  0x55 | type u8 | len u16 | src i32 | dst i32 | time u32 | seq u8 |
        payload[len] | xor32 u32          (all little-endian)
time = seconds since 2000-01-01, xor32 = for each byte: a ^= b; a = ror(a, 8)
Gecko -> hub: 0xFC hello, 0x8A text (station_name=..., settings answers, SOH lines),
              0x88 512-byte miniSEED record (seq = link counter, wraps 255 -> 0)
hub -> Gecko: 0xFB hello, 0xFD hello reply, 0xFF ack (seq = last packet received),
              0x8A text commands ("sampling 1", '? "setting"')
"""

import asyncio
import logging
import re
import struct
import time

from . import mseed

log = logging.getLogger("seiszone")

RAS_EPOCH = 946684800  # 2000-01-01T00:00:00Z
T_MSEED, T_TEXT, T_HELLO, T_HUB_HELLO, T_HELLO_REPLY, T_ACK = 0x88, 0x8A, 0xFC, 0xFB, 0xFD, 0xFF
HUB_ID = 1
HDR = struct.Struct("<BBHiiIB")  # 17 bytes
SILENCE = 120          # s without any byte before the link is closed (the Gecko calls again)
START_COMMANDS = (b"sampling 1", b'? "sitename"')
NAME_OK = re.compile(r"^[A-Za-z0-9]*$")


def xor32(data):
    a = 0
    for b in data:
        a ^= b
        a = ((a >> 8) | (a << 24)) & 0xFFFFFFFF
    return a


def make_frame(ftype, dst, seq=0, payload=b"", src=HUB_ID):
    """One telemetry frame. `src` is the hub's id for frames we send; tests build Gecko frames
    by passing the recorder's serial."""
    hdr = HDR.pack(0x55, ftype, len(payload), src, dst,
                   (int(time.time()) - RAS_EPOCH) & 0xFFFFFFFF, seq & 0xFF)
    return hdr + payload + struct.pack("<I", xor32(hdr + payload))


class FrameParser:
    """Byte stream -> [(type, src, dst, seq, payload)]. Bytes between frames and frames with a
    bad checksum are skipped (counted in `bad`), so a noisy radio link resynchronises itself."""

    def __init__(self):
        self.buf = bytearray()
        self.bad = 0

    def feed(self, data):
        self.buf += data
        out = []
        i = 0
        buf = self.buf
        while len(buf) - i >= 21:
            if buf[i] != 0x55:
                i += 1
                continue
            ln = buf[i + 2] | (buf[i + 3] << 8)
            if ln > 10000:
                i += 1
                continue
            end = i + 21 + ln
            if end > len(buf):
                break  # wait for the rest of the frame
            body = bytes(buf[i:i + 17 + ln])
            if xor32(body) != struct.unpack_from("<I", buf, i + 17 + ln)[0]:
                self.bad += 1
                i += 1
                continue
            _s, ftype, _l, src, dst, _t, seq = HDR.unpack_from(body)
            out.append((ftype, src, dst, seq, body[17:]))
            i = end
        del buf[:i]
        if len(buf) > 65536:
            del buf[:-32768]
        return out


def serial_key(serial):
    """Rename-table key for a serial: hex, upper case, no 0x and no leading zeros
    (so "0x02000143", "02000143" and "2000143" all name the same recorder)."""
    if isinstance(serial, int):
        serial = f"{serial:08X}"
    return str(serial).upper().lstrip("0X")


class GeckoReceiver:
    """Talks to Gecko recorders directly. One handle() task per TCP connection; records go to
    the hub as source "gecko:<serial hex>"."""

    def __init__(self, hub, rename=None):
        self.hub = hub
        self.links = {}   # "ip:port" -> {peer, serial, station, sitename, records, bytes, since}
        self.rename = {}
        for k, v in (rename or {}).items():
            names = {f: str(v[f]).strip() for f in ("net", "sta", "loc") if v.get(f) is not None}
            if all(NAME_OK.match(x) for x in names.values()):
                self.rename[serial_key(k)] = names
            else:  # a record with such a name would be rejected and the data lost
                log.warning("Gecko rename entry %s ignored: names may only hold letters and digits", k)

    def _renamed(self, serial, rec):
        """Apply the naming table {serial: {"net", "sta", "loc"}} by patching the raw header
        (recordings and the SeedLink server forward raw bytes, so the bytes must carry the name)."""
        m = self.rename.get(serial_key(serial)) if serial else None
        if not m:
            return rec
        raw = bytearray(rec.raw)
        if m.get("sta"):
            raw[8:13] = m["sta"].encode("ascii")[:5].ljust(5)
        if "loc" in m:
            raw[13:15] = m["loc"].encode("ascii")[:2].ljust(2)
        if m.get("net"):
            raw[18:20] = m["net"].encode("ascii")[:2].ljust(2)
        res = mseed.parse_record(bytes(raw), 0, check=True)
        return res[0] if isinstance(res, tuple) else rec

    async def handle(self, reader, writer):
        peer = writer.get_extra_info("peername") or ("?", 0)
        sock = writer.get_extra_info("sockname") or ("?", 0)
        key = f"{peer[0]}:{peer[1]}"
        log.info("Gecko link connected from %s (port %d)", key, sock[1])
        st = {"peer": key, "serial": None, "station": None, "sitename": None, "records": 0,
              "bytes": 0, "since": time.time()}
        self.links[key] = st
        parser = FrameParser()
        serial = 0
        last_seq = None
        pending = 0
        last_ack = time.time()
        cmd_seq = 0
        started = False
        try:
            writer.write(make_frame(T_HUB_HELLO, 0))
            await writer.drain()
            while True:
                try:
                    data = await asyncio.wait_for(reader.read(65536), timeout=SILENCE)
                except asyncio.TimeoutError:
                    log.warning("Gecko link %s silent for %d s, closing (it will call again)", key, SILENCE)
                    break
                if not data:
                    break
                st["bytes"] += len(data)
                for ftype, src, _dst, seq, payload in parser.feed(data):
                    if src and not serial:
                        serial = src
                        st["serial"] = f"{serial:08X}"
                    if ftype == T_HELLO:
                        writer.write(make_frame(T_HELLO_REPLY, serial))
                        if not started:
                            for cmd in START_COMMANDS:
                                writer.write(make_frame(T_TEXT, serial, cmd_seq, cmd))
                                cmd_seq = (cmd_seq + 1) & 0xFF
                            started = True
                    elif ftype == T_MSEED:
                        last_seq = seq
                        pending += 1
                        self._record(serial, payload, st, key)
                    elif ftype == T_TEXT:
                        self._text(serial, payload.decode("latin-1", "replace"), st)
                    else:
                        log.debug("Gecko %s frame type 0x%02X len %d", key, ftype, len(payload))
                # acknowledge like the vendor hub: every read at the start, then every 3 packets / 1 s
                now = time.time()
                if last_seq is not None and pending and (
                        st["records"] < 50 or pending >= 3 or now - last_ack >= 1):
                    writer.write(make_frame(T_ACK, serial, last_seq))
                    pending = 0
                    last_ack = now
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        except Exception:
            log.exception("Gecko link %s error", key)
        finally:
            self.links.pop(key, None)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            log.info("Gecko link %s closed (%s): %d bytes, %d records, %d bad frames",
                     key, st["station"] or "no data", st["bytes"], st["records"], parser.bad)

    def _record(self, serial, payload, st, key):
        res = mseed.parse_record(payload, 0, check=True)
        if not isinstance(res, tuple):
            log.debug("Gecko %s: frame 0x88 without a valid miniSEED record (%d bytes)", key, len(payload))
            return
        rec = self._renamed(serial, res[0])
        st["records"] += 1
        station = rec.station
        if st["station"] != station:
            log.info("Gecko %s (serial %08X) sends data from %s", station, serial, key)
            st["station"] = station
            if st["sitename"]:
                self.hub.descriptions[station] = f"{st['sitename']} - Kelunji Gecko {serial:08X}"
            else:
                self.hub.descriptions.setdefault(station, f"Kelunji Gecko {serial:08X}")
        self.hub.add_record(rec, f"gecko:{serial:08X}")

    def _text(self, serial, txt, st):
        if txt.startswith("station_name="):
            log.info("Gecko %08X identifies as %s", serial, txt.split("=", 1)[1])
        elif txt.startswith('"sitename"='):
            st["sitename"] = txt.split("=", 1)[1].strip().strip('"')
            if st["station"]:
                self.hub.descriptions[st["station"]] = f"{st['sitename']} - Kelunji Gecko {serial:08X}"
        else:
            log.debug("Gecko %08X: %s", serial, txt[:200])
