"""Find out everything about an instrument from its IP address (and optionally a port), so the
user only has to type 'IP' or 'IP:port' to add it.

What is tried (read-only: nothing on the instrument is changed):
  - SeedLink   (default 18000): HELLO, INFO STREAMS / STATIONS -> network, station, site
                description, channels
  - GCF / UDP  (default 1567, Scream protocol): GCFPING -> GCFACKN, a few seconds of
                GCFSEND:B, then GCFSTOP -> system id, GCF streams, sample rates
  - GCF / TCP  (serial-to-TCP bridges, Scream servers): a few seconds of BTP / raw blocks
  - Guralp web page (port 80, no login): host name, site label, type, serial, firmware,
                SEED network/station, GNSS position, the channel table (GCF stream ->
                location.channel, sample rate, sensitivity) and the instrument's StationXML

The web page is read in parallel with the data ports, and every network step is retried,
because over a VPN single requests are lost (proven on the Fortimus units in the field).

    inst = probe("10.8.0.182:1567", log=print)
    if inst.found:
        engine.add_source(inst.to_source())
"""

import html
import logging
import re
import socket
import struct
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET

from .. import VERSION
from . import gcf, stationxml

logger = logging.getLogger("seiszone")

SEEDLINK_PORT = 18000
GCF_PORT = 1567
HTTP_PORT = 80
WEB_TRIES = 3
WEB_RETRY_PAUSE = 2         # s between tries of the web page
GCF_SAMPLE_SECONDS = 12     # s of GCF data read to learn the streams and rates
WEB_WAIT = 150              # s the probe waits at most for the web reader (StationXML download)
USER_AGENT = f"SeisZone/{VERSION} instrument probe"
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?$")


def parse_target(text):
    """'10.8.0.203' -> ('10.8.0.203', None); '10.8.0.203:1567' -> (..., 1567). ValueError if invalid."""
    s = (text or "").strip()
    for prefix in ("http://", "https://", "udp://", "tcp://"):
        if s.lower().startswith(prefix):
            s = s[len(prefix):]
    s = s.rstrip("/")
    host, port = s, None
    if s.count(":") == 1:
        host, p = s.split(":")
        if not p.isdigit() or not 1 <= int(p) <= 65535:
            raise ValueError(f"'{p}' is not a port number (1-65535)")
        port = int(p)
    if not HOST_RE.match(host):
        raise ValueError(f"'{host}' is not an IP address or host name")
    return host, port


# --------------------------------------------------------------------------
# SeedLink
# --------------------------------------------------------------------------

def seedlink_hello(host, port, timeout=8):
    """Banner lines (software, organization) or None if no SeedLink server answers."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(b"HELLO\r\n")
            buf = b""
            t0 = time.time()
            while buf.count(b"\r\n") < 2 and time.time() - t0 < timeout:
                d = s.recv(4096)
                if not d:
                    break
                buf += d
    except OSError:
        return None
    lines = buf.decode("latin-1", "replace").split("\r\n")
    if len(lines) < 2 or "seedlink" not in lines[0].lower():
        return None
    return lines[0].strip(), lines[1].strip()


def seedlink_info(host, port, level, timeout=15):
    """XML text of INFO <level> (all SLINFO packets joined). OSError if the server refuses."""
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall(b"HELLO\r\n")
        h = b""
        while h.count(b"\r\n") < 2:
            d = s.recv(4096)
            if not d:
                raise OSError("connection closed")
            h += d
        s.sendall(f"INFO {level}\r\n".encode())
        xml, buf = b"", b""
        while True:
            while len(buf) < 520:
                if buf.startswith(b"ERROR") and b"\r\n" in buf:
                    raise OSError(f"server answered INFO {level} with ERROR")
                d = s.recv(65536)
                if not d:
                    raise OSError("connection closed during INFO")
                buf += d
            pkt, buf = buf[:520], buf[520:]
            if pkt[:6] != b"SLINFO":
                raise OSError("unexpected reply to INFO")
            rec = pkt[8:]
            n, off = struct.unpack(">H", rec[30:32])[0], struct.unpack(">H", rec[44:46])[0]
            xml += rec[off:off + n].rstrip(b"\x00 ")
            if pkt[6:8] != b" *":
                break
    return xml.decode("utf-8", "replace")


def parse_seedlink_streams(xml_text):
    """[{net, sta, desc, streams: [(loc, cha)]}] from INFO STREAMS / STATIONS XML."""
    out = []
    root = ET.fromstring(xml_text)
    for st in root.iter("station"):
        streams = []
        for s in st.iter("stream"):
            if s.get("type", "D") == "D":
                streams.append(((s.get("location") or "").strip(), s.get("seedname", "").strip()))
        out.append({"net": st.get("network", ""), "sta": st.get("name", ""),
                    "desc": (st.get("description") or "").strip(), "streams": streams})
    return out


def banner_host(software):
    """'SeedLink v3.0 (FMUS-A46D Tetar Dam Top) :: SLPROTO:3.0' -> 'FMUS-A46D Tetar Dam Top'."""
    m = re.search(r"\(([^)]*)\)", software or "")
    return m.group(1).strip() if m else ""


GURALP_HOSTS = ("FMUS-", "MIN-", "MINP-", "MINI-", "AFF-", "RAD-", "CMG-", "EAM-")


def looks_guralp(banner):
    """True when a SeedLink banner (software, organization) is a Guralp unit's: only then is the
    web page worth the full wait (it holds the names and the StationXML)."""
    host = banner_host((banner or ("",))[0]).upper()
    text = " ".join(banner or ()).lower()
    return host.startswith(GURALP_HOSTS) or "guralp" in text or "güralp" in text


# --------------------------------------------------------------------------
# GCF (Scream protocol)
# --------------------------------------------------------------------------

def gcf_udp_sample(host, port, seconds=None, timeout=4):
    """Pull GCF over UDP (GCFPING, GCFSEND:B, then GCFSTOP twice so the instrument does not keep
    sending to a port nobody reads). Returns {'acked': True, 'blocks': [Block], 'status': [text]}
    or None if nothing answered."""
    seconds = seconds or GCF_SAMPLE_SECONDS
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(1)
    try:
        dest = (socket.gethostbyname(host), port)
        s.bind(("0.0.0.0", 0))
        acked = False
        for _ in range(2):  # one GCFPING may be lost over a VPN
            s.sendto(b"GCFPING", dest)
            t0 = time.time()
            while time.time() - t0 < timeout and not acked:
                try:
                    d, _a = s.recvfrom(65536)
                except socket.timeout:
                    continue
                acked = d.startswith(b"GCFACKN")
            if acked:
                break
        if not acked:
            return None
        s.sendto(b"GCFSEND:B", dest)
        blocks, status = [], []
        t0 = time.time()
        try:
            while time.time() - t0 < seconds:
                try:
                    d, _a = s.recvfrom(65536)
                except socket.timeout:
                    continue
                b = gcf.parse_datagram(d)
                if b is None:
                    continue
                if b.is_status:
                    status.append(b.status_text)
                else:
                    blocks.append(b)
        finally:
            for _ in range(2):
                s.sendto(b"GCFSTOP", dest)
        return {"acked": True, "blocks": blocks, "status": status}
    except OSError:
        return None
    finally:
        s.close()


def gcf_tcp_sample(host, port, seconds=10):
    """Read GCF blocks from a TCP port (serial-to-TCP bridge / Scream TCP server).
    BTP frames are acknowledged so the sender keeps going. None if nothing decodable."""
    try:
        with socket.create_connection((host, port), timeout=6) as s:
            s.settimeout(1)
            p = gcf.StreamParser()
            blocks, status = [], []
            t0 = time.time()
            while time.time() - t0 < seconds:
                try:
                    d = s.recv(65536)
                except socket.timeout:
                    continue
                if not d:
                    break
                for b, ackb in p.feed(d):
                    if ackb:
                        s.sendall(ackb)
                    if b.is_status:
                        status.append(b.status_text)
                    else:
                        blocks.append(b)
    except OSError:
        return None
    if not blocks and not status:
        return None
    return {"blocks": blocks, "status": status}


def summarize_blocks(blocks):
    """{stream_id: {'system_id', 'rate', 'last'}} (blocks of one digitiser: see pick_system)"""
    out = {}
    for b in blocks:
        e = out.setdefault(b.stream_id, {"system_id": b.system_id, "rate": b.rate, "last": b.end})
        e["last"] = max(e["last"], b.end)
    return out


def pick_system(blocks, web=None):
    """(system id, [the others]) of the digitisers seen: a Scream server can relay several, and
    their stream ids may be the same. The one the web page names wins, else the one with the
    most blocks."""
    counts = {}
    for b in blocks:
        counts[b.system_id] = counts.get(b.system_id, 0) + 1
    if not counts:
        return "", []
    named = " ".join(str((web or {}).get(k) or "") for k in ("hostname", "serial")).upper()
    hits = [k for k in sorted(counts) if k and k.upper() in named]   # e.g. host name FMUS-A46D
    chosen = hits[0] if hits else max(sorted(counts), key=counts.get)
    return chosen, sorted(k for k in counts if k != chosen)


# --------------------------------------------------------------------------
# Guralp web interface
# --------------------------------------------------------------------------

def http_get(host, path, port=None, timeout=20, limit=8 << 20):
    port = port or HTTP_PORT
    url = f"http://{host}:{port}{path}" if port != 80 else f"http://{host}{path}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit)


def _clean(s):
    s = html.unescape(s or "").replace("\xa0", " ")
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()


def parse_guralp_status(page):
    """Fields of the Guralp status page (tab 'Status', index.html): name="..." -> value.
    None if the page is not a Guralp status page."""
    fields = {}
    for m in re.finditer(r'name="([^"]+)">(.*?)</', page, re.S):
        k, v = m.group(1), _clean(m.group(2))
        if k != "A" and k not in fields:
            fields[k] = v
    info = {
        "hostname": fields.get("Host name", ""),
        "label": fields.get("Host label", ""),
        "system_type": fields.get("System type", ""),
        "product": fields.get("Product type", ""),
        "serial": fields.get("Serial number", ""),
        "firmware": fields.get("Firmware version", ""),
        "ip": fields.get("IPv4 address", ""),
        "lat": fields.get("Latitude", ""),
        "lon": fields.get("Longitude", ""),
        "alt": fields.get("Altitude", ""),
        "gnss": fields.get("GNSS Lock state", ""),
        "sensors": fields.get("Number of sensors detected", ""),
    }
    m = re.match(r"([A-Za-z0-9]{1,2})\.([A-Za-z0-9]{1,5})\s*(?:\((.*)\))?", fields.get("SEED network and station", ""))
    if m:
        info["net"], info["sta"], info["site"] = m.group(1), m.group(2), (m.group(3) or "").strip()
    return info if info["hostname"] or info.get("sta") else None


def parse_guralp_streams(page):
    """Channel table of the 'Data Stream' page: [{gcf, loc, cha, rate, sensitivity, units}]."""
    out = []
    for part in page.split("Tap UID:")[1:]:
        m_id = re.search(r'<select name="([0-9A-Z]{3,7})"', part)
        m_rate = re.search(r'<option selected="selected" value="(\d+)"', part)
        m_loc = re.search(r'_location"[^>]*value="([^"]*)"', part)
        m_cha = re.search(r'_channel"[^>]*value="([^"]*)"', part)
        if not (m_id and m_loc and m_cha):
            continue
        rate = int(m_rate.group(1)) if m_rate else 0
        m_sens = re.search(r"Sensitivity:\s*([0-9.Ee+\-]+)\s*([^<]*)", part)
        m_units = re.search(r"Units:\s*([^<]*)", part)
        out.append({"gcf": m_id.group(1).lstrip("0") or "0", "loc": m_loc.group(1).strip(),
                    "cha": m_cha.group(1).strip(), "rate": rate,
                    "sensitivity": m_sens.group(1) if m_sens else "",
                    "units": _clean(m_units.group(1)) if m_units else ""})
    return out


def find_stationxml_links(page):
    """StationXML files listed on the 'Storage' page (newest first as listed)."""
    links = re.findall(r'href="([^"]+\.xml)"', page)
    return list(dict.fromkeys(link for link in links if not link.lower().startswith(("http:", "https:"))
                              and not link.lower().startswith("page")))


def check_stationxml(data, net=None, sta=None):
    """Summary dict (stationxml.summarize) if `data` is FDSN StationXML for net.sta, else None."""
    return stationxml.summarize(data, net, sta) if data else None


def guralp_web(host, log=lambda m: None, need_xml=None, box=None):
    """Everything the Guralp web interface shows without a login, or None if not a Guralp unit.
    need_xml(net, sta) -> False: StationXML is not downloaded (SeisZone has it already; over a
    slow VPN the ~1 MB file takes a minute). box: gets {'web': info} as soon as the names and
    streams are read, so a caller that stops waiting during the StationXML download still has
    them."""
    page = None
    for attempt in range(WEB_TRIES):  # over a VPN one request can be lost; the names come from here
        if box is not None and box.get("abandoned"):   # the probe no longer waits for it
            return None
        try:
            page = http_get(host, "/").decode("utf-8", "replace")
            break
        except Exception as e:  # URLError, timeouts, broken HTTP: all mean "no page this time"
            logger.debug("web page of %s, try %d: %s", host, attempt + 1, e)
            if box is not None:
                box["first_failed"] = True
            if attempt + 1 < WEB_TRIES:
                time.sleep(WEB_RETRY_PAUSE)
    if page is None:
        return None
    info = parse_guralp_status(page)
    if not info:
        return None
    log(f"Web page: {info.get('hostname')} '{info.get('label')}' ({info.get('system_type')})")
    try:
        info["streams"] = parse_guralp_streams(http_get(host, "/tab6.html").decode("utf-8", "replace"))
    except Exception as e:
        logger.debug("data stream page of %s: %s", host, e)
        info["streams"] = []
    info["stationxml"] = None
    if box is not None:
        box["web"] = info
    try:
        skip = need_xml is not None and info.get("net") and info.get("sta") and not need_xml(info["net"], info["sta"])
    except Exception:
        skip = False  # cannot tell: download it
    if skip:
        info["stationxml_skipped"] = True
        log(f"StationXML of {info['net']}.{info['sta']} is already in SeisZone - not downloaded again.")
        return info
    try:
        storage = http_get(host, "/tab9.html").decode("utf-8", "replace")
        for link in find_stationxml_links(storage):
            try:
                data = http_get(host, "/" + link.lstrip("/"), timeout=90, limit=32 << 20)
            except Exception as e:
                logger.debug("StationXML %s of %s: %s", link, host, e)
                continue
            summary = check_stationxml(data, info.get("net"), info.get("sta"))
            if summary:
                info["stationxml"], info["stationxml_name"], info["stationxml_summary"] = data, link, summary
                n = len({(c[0], c[1]) for c in summary["channels"]})
                log(f"StationXML: {link} ({len(data) // 1024} KB, {n} channels)")
                break
    except Exception as e:
        logger.debug("storage page of %s: %s", host, e)
    return info


# --------------------------------------------------------------------------
# Putting it together
# --------------------------------------------------------------------------

METHOD_TEXT = {"seedlink": "SeedLink", "gcf-udp": "GCF over UDP (Scream protocol)", "gcf-tcp": "GCF over TCP"}


def _json_safe(v):
    """Copy of v with only what JSON can hold (bytes and other objects left out)."""
    if isinstance(v, dict):
        return {str(k): _json_safe(x) for k, x in v.items() if _keep(x)}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v if _keep(x)]
    return v


def _keep(v):
    return v is None or isinstance(v, (str, int, float, bool, dict, list, tuple))


def _sid(item):
    """'NET.STA.LOC.CHA' from a SEED id string or a (net, sta, loc, cha) tuple."""
    return item if isinstance(item, str) else ".".join(item)


class Instrument:
    """What was found at one address, and how SeisZone gets its data."""

    def __init__(self, host):
        self.host = host
        self.method = None        # "seedlink", "gcf-udp" or "gcf-tcp"
        self.port = None
        self.stations = []        # [{net, sta, desc, channels: [{loc, cha, rate, gcf, sensitivity, units}]}]
        self.details = {}         # host name, site, type, serial, firmware, position, ...
        self.stationxml = None    # bytes, from the instrument's web interface
        self.gcf_map = {}         # GCF stream id -> (loc, cha)
        self.notes = []           # things the user should know, in plain words

    @property
    def found(self):
        return bool(self.method and self.stations)

    def _chosen(self, st, selected):
        if selected is None:
            return list(st["channels"])
        return [c for c in st["channels"] if f"{st['net']}.{st['sta']}.{c['loc']}.{c['cha']}" in selected]

    def _details(self):
        return _json_safe({k: v for k, v in self.details.items() if k not in ("stationxml", "stationxml_summary")})

    def to_source(self, selected=None):
        """A config source dict (valid for config.validate_source) for the channels the user
        chose. selected: SEED ids 'NET.STA.LOC.CHA' (or (net, sta, loc, cha) tuples); None =
        everything found. Stations without a chosen channel are left out. ValueError if nothing
        was found or nothing is chosen."""
        if not self.found:
            raise ValueError(f"no instrument was found at {self.host}")
        sel = None if selected is None else {_sid(x) for x in selected}
        if self.method == "seedlink":
            stations = []
            for st in self.stations:
                chosen = self._chosen(st, sel)
                if not chosen and (sel is not None or st["channels"]):
                    continue
                text = selectors_for(chosen, st["channels"])
                stations.append({"net": st["net"], "sta": st["sta"], "desc": st["desc"],
                                 "selectors": text.split(",") if text else []})
            if not stations:
                raise ValueError("no channel is chosen")
            name = (stations[0]["desc"] if len(stations) == 1 else "") \
                or self.details.get("hostname") or self.host
            return {"kind": "seedlink", "host": self.host, "port": self.port, "name": name,
                    "stations": stations, "details": self._details()}
        st = self.stations[0]
        chosen = self._chosen(st, sel)
        if not chosen:
            raise ValueError("no channel is chosen")
        gmap = {c["gcf"]: [c["loc"], c["cha"]] for c in chosen if c.get("gcf")}
        g = {"net": st["net"], "sta": st["sta"], "desc": st["desc"], "map": gmap}
        if self.details.get("system_id"):
            g["system_id"] = self.details["system_id"]
        return {"kind": self.method, "host": self.host, "port": self.port,
                "name": st["desc"] or self.details.get("hostname") or self.host,
                "gcf": g, "details": self._details()}

    def summary_lines(self):
        """Plain lines for the Add source dialog: what was found and how SeisZone connects.
        The notes (self.notes) are shown separately."""
        d = self.details
        if not self.found:
            return [f"Nothing usable was found at {self.host}" + (f":{self.port}" if self.port else "") + "."]
        kind = " ".join(x for x in (d.get("system_type") or d.get("product"), d.get("hostname")) if x)
        if kind and d.get("label"):
            kind += f" '{d['label']}'"
        if not kind:
            kind = (f"Guralp GCF system {d['system_id']}" if d.get("system_id")
                    else d.get("software") or "SeedLink server")
        lines = [f"Instrument: {kind}"]
        how = f"Connection: {METHOD_TEXT.get(self.method, self.method)} on {self.host}:{self.port}"
        if d.get("typed"):
            how += f" (typed {d['typed']})"
        lines.append(how)
        if self.method == "seedlink" and d.get("organization"):
            lines.append(f"Server: {d.get('software', '')} - {d['organization']}")
        for st in self.stations:
            rates = {}
            for c in st["channels"]:
                rates[c["rate"]] = rates.get(c["rate"], 0) + 1
            parts = [f"{n} x {r:g} sps" if r else f"{n} rate unknown" for r, n in sorted(rates.items(), reverse=True)]
            head = f"Station {st['net']}.{st['sta']}" + (f" '{st['desc']}'" if st["desc"] else "")
            if st["channels"]:
                lines.append(f"{head}: {len(st['channels'])} channel(s) ({', '.join(parts)})")
            else:
                lines.append(f"{head}: channel list not available (all channels will be requested)")
        if d.get("site"):
            lines.append(f"Site: {d['site']}")
        if d.get("serial"):
            lines.append(f"Serial number: {d['serial']}")
        if d.get("firmware"):
            lines.append(f"Firmware: {d['firmware']}")
        if d.get("lat") and d.get("lon"):
            pos = f"Position: latitude {d['lat']}, longitude {d['lon']}"
            if d.get("alt"):
                pos += f", altitude {d['alt']}"
            if d.get("gnss"):
                pos += f" (GNSS {d['gnss']})"
            lines.append(pos)
        if self.stationxml:
            name = d.get("stationxml_name") or "StationXML"
            lines.append(f"StationXML: found on the instrument ({name}, {len(self.stationxml) // 1024} KB)")
        elif d.get("stationxml_skipped"):
            lines.append("StationXML: already in SeisZone (not downloaded again)")
        else:
            lines.append("StationXML: not found (values are shown in counts)")
        return lines


_UNREAD = object()


def probe(text, log=None, prefer_direct=True, need_xml=None, web=_UNREAD):
    """Detect the instrument at 'IP' or 'IP:port'. Never raises for network problems
    (ValueError only for text that is not an address).
    log: fn(message) for progress lines (default: the program log).
    prefer_direct: a GCF unit that also has a SeedLink server is returned as that SeedLink
    server (SeisZone then backfills missed data from the instrument after outages).
    need_xml: see guralp_web. web: the unit's web page already read (None = it did not answer)."""
    if log is None:
        log = logger.info
    host, port = parse_target(text)
    inst = Instrument(host)
    web_box = {}
    t = None
    if web is _UNREAD:
        t = threading.Thread(target=lambda: web_box.update(web=guralp_web(host, log, need_xml, web_box)),
                             name="seiszone-probe-web", daemon=True)
        t.start()  # the web page is read in parallel with the data ports
    else:
        web_box["web"] = web

    def finish(full=True):
        """The web page result. full=False (a SeedLink server whose banner is not a Guralp
        unit's): stop waiting once the first try of the page has failed, unless it answered."""
        if t and full:
            t.join(WEB_WAIT)
        elif t:
            end = time.monotonic() + WEB_WAIT
            while t.is_alive() and time.monotonic() < end and not web_box.get("first_failed"):
                if web_box.get("web"):   # a Guralp page after all: let it finish
                    t.join(max(0.0, end - time.monotonic()))
                    break
                t.join(0.1)
            if t.is_alive() and not web_box.get("web"):
                web_box["abandoned"] = True   # no more tries in the background
        web = web_box.get("web")
        if web:
            web = dict(web)  # a copy: the reader may still be adding the StationXML to it
            inst.details.update({k: v for k, v in web.items() if k not in ("streams", "stationxml")})
            if web.get("stationxml"):
                inst.stationxml = web["stationxml"]
        return web

    ports = [port] if port else [SEEDLINK_PORT, GCF_PORT]
    # 1. SeedLink: read natively, with backfill after outages, so it is preferred
    sl_ports = ports if port else [SEEDLINK_PORT]
    for p in sl_ports:
        log(f"Trying SeedLink on {host}:{p} ...")
        banner = seedlink_hello(host, p)
        if not banner:
            continue
        inst.method, inst.port = "seedlink", p
        inst.details["software"], inst.details["organization"] = banner
        inst.details.setdefault("hostname", banner_host(banner[0]))
        stations, err, level = None, None, None
        for level in ("STREAMS", "STREAMS", "STATIONS"):  # over a VPN one INFO request can be lost
            try:
                stations = parse_seedlink_streams(seedlink_info(host, p, level))
                break
            except Exception as e:  # OSError, timeouts, broken XML
                err = e
        if stations is None:
            stations = []
            inst.notes.append(f"The SeedLink server answered but did not list its stations ({err}).")
        if stations and (level == "STATIONS" or all(not st["streams"] for st in stations)):
            inst.details["no_streams"] = True  # channel list not read: all channels are requested
        web = finish(looks_guralp(banner))
        for st in stations:
            chans = []
            web_ch = {(c["loc"], c["cha"]): c for c in (web or {}).get("streams", [])} \
                if web and web.get("sta") == st["sta"] else {}
            sx = check_stationxml(inst.stationxml, st["net"], st["sta"]) if inst.stationxml else None
            sx_rates = {(c[0], c[1]): c[2] for c in sx["channels"]} if sx else {}
            for loc, cha in st["streams"]:
                w = web_ch.get((loc, cha), {})
                chans.append({"loc": loc, "cha": cha, "rate": w.get("rate") or sx_rates.get((loc, cha), 0),
                              "gcf": w.get("gcf", ""), "sensitivity": w.get("sensitivity", ""),
                              "units": w.get("units", "")})
            inst.stations.append({"net": st["net"], "sta": st["sta"],
                                  "desc": st["desc"] or (web or {}).get("site") or (web or {}).get("label", ""),
                                  "channels": chans})
        log(f"SeedLink server found: {len(inst.stations)} station(s).")
        return inst
    # 2. GCF over UDP (Scream protocol), then GCF over TCP on the same port
    gcf_ports = ports if port else [GCF_PORT]
    for p in gcf_ports:
        log(f"Trying GCF (Scream UDP) on {host}:{p} ...")
        res = gcf_udp_sample(host, p)
        if res:
            inst.method, inst.port = "gcf-udp", p
            web = finish()
            inst = _finish_gcf(inst, res, web, log)
            return _direct(inst, f"{host}:{p}", log, web) if prefer_direct else inst
        log(f"Trying GCF over TCP on {host}:{p} ...")
        res = gcf_tcp_sample(host, p)
        if res:
            inst.method, inst.port = "gcf-tcp", p
            web = finish()
            inst = _finish_gcf(inst, res, web, log)
            return _direct(inst, f"{host}:{p}", log, web) if prefer_direct else inst
    web = finish()
    if web:  # a Guralp unit whose data ports did not answer
        inst.notes.append("The instrument's web page answered, but no data port did "
                          f"({', '.join(map(str, ports))}). Check its Network / Data Stream settings.")
    else:
        inst.notes.append(f"Nothing answered at {host}" + (f":{port}" if port else
                          f" (tried SeedLink {SEEDLINK_PORT} and GCF {GCF_PORT})") + ".")
    return inst


def _finish_gcf(inst, res, web, log):
    chosen, others = pick_system(res["blocks"], web)
    streams = summarize_blocks([b for b in res["blocks"] if b.system_id == chosen])
    inst.details["gcf_status"] = res.get("status", [])[-3:]
    sysids = [chosen] if chosen else []
    inst.details["system_id"] = chosen
    if others:
        inst.notes.append(f"This port also sends data of digitiser(s) {', '.join(others)}; SeisZone "
                          f"uses {chosen} only. Add the others through their own address if needed.")
    web_by_gcf = {s["gcf"]: s for s in (web or {}).get("streams", [])}
    net = (web or {}).get("net") or "XX"
    sta = (web or {}).get("sta") or (sysids[0][:5] if sysids else "GCF")
    chans = []
    seen = set()
    # every enabled channel the web page lists, plus every stream seen on the wire
    for gid, s in list(web_by_gcf.items()) + [(g, None) for g in streams if g not in web_by_gcf]:
        if s is not None and not s["rate"]:
            continue
        rate = streams.get(gid, {}).get("rate") or (s or {}).get("rate", 0)
        if s:
            loc, cha = s["loc"], s["cha"]
        else:
            loc, cha = gcf.default_seed_name(gid, rate)
        if (loc, cha) in seen:
            continue
        seen.add((loc, cha))
        inst.gcf_map[gid] = (loc, cha)
        chans.append({"loc": loc, "cha": cha, "rate": rate, "gcf": gid,
                      "sensitivity": (s or {}).get("sensitivity", ""), "units": (s or {}).get("units", "")})
    if not web:
        inst.details["standard_names"] = True  # not the instrument's own NET.STA
        inst.notes.append("SEED names were made from the GCF stream ids (network XX, station = "
                          "system id), because the instrument's web page with its own names did not "
                          "answer. Press Detect again if it normally has one.")
    desc = (web or {}).get("site") or (web or {}).get("label") or f"Guralp {inst.details['system_id']}"
    inst.stations.append({"net": net, "sta": sta, "desc": desc, "channels": chans})
    log(f"GCF instrument found: system {inst.details['system_id']}, {len(chans)} channel(s).")
    if web and web.get("sta"):  # the same unit may also offer SeedLink, which SeisZone can backfill from
        if seedlink_hello(inst.host, SEEDLINK_PORT):
            inst.details["seedlink_port"] = SEEDLINK_PORT
            inst.notes.append(f"This instrument also has a SeedLink server on port {SEEDLINK_PORT}; "
                              f"adding {inst.host}:{SEEDLINK_PORT} instead lets SeisZone fetch missed "
                              "data after outages.")
    return inst


def _direct(inst, typed, log, web):
    """A GCF unit that also has SeedLink: SeisZone connects to that SeedLink server instead, so
    data missed during VPN or PC outages is fetched again (GCF has no backfill).
    web: the web page (and StationXML) already read - not fetched a second time."""
    if not inst.details.get("seedlink_port"):
        return inst
    sl_port = inst.details["seedlink_port"]
    log(f"The instrument also has SeedLink on port {sl_port} - using it directly ...")
    direct = probe(f"{inst.host}:{sl_port}", log, prefer_direct=False, web=web)
    want = {(s["net"], s["sta"]) for s in inst.stations}
    if not (direct.found and want <= {(s["net"], s["sta"]) for s in direct.stations}):
        return inst
    direct.details["typed"] = typed
    direct.notes = [f"Typed {typed} (GCF). SeisZone connects to the instrument's own SeedLink server "
                    f"{inst.host}:{sl_port} instead, so it fetches missed data after VPN or PC outages. "
                    "Untick 'Connect directly' to pull GCF instead."]
    return direct


def selectors_for(chosen, available):
    """SeedLink selectors as comma-separated text (one SELECT per item): 'LLBI?' when every
    channel of that location/band/instrument group is chosen, otherwise the single channels.
    '' (= everything) when all available channels are chosen."""
    def key(c):
        return c["loc"], c["cha"]
    chosen_k = [key(c) for c in chosen]
    avail_k = [key(c) for c in available]
    if set(chosen_k) >= set(avail_k):
        return ""
    out = []
    for loc, cha in chosen_k:
        group = [k for k in avail_k if k[0] == loc and k[1][:2] == cha[:2]]
        sel = f"{loc}{cha[:2]}?" if all(k in chosen_k for k in group) else f"{loc}{cha}"
        if sel not in out:
            out.append(sel)
    return ",".join(out)
