"""Add many instruments at once from an IP survey (docs/reports/*-ip-survey.json), 2026-11-22.

    python tools/add_from_survey.py probe SURVEY.json PENDING.json   read-only: probe each unit that
        answered SeedLink (or only GCF) and is not configured yet; keep only the live seismic
        channels the survey found (no state-of-health / mass position); write the sources to
        PENDING.json and the units' StationXML to SeisZone's stationxml folder. SeisZone may run.
    python tools/add_from_survey.py write PENDING.json               add them to config.json (and
        give old one-character-location selectors their "?": "1HH?" -> "1?HH?"). SeisZone must
        NOT be running (its config would be overwritten): quit it from the tray icon first.

Never adds this PC's own SeisZone server (a banner with "SeisZone").
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from seiszone import config as cfg_mod, single_instance   # noqa: E402
from seiszone.core import probe                             # noqa: E402
from seiszone.core.seedlink_client import wire_selector     # noqa: E402
from seiszone.core.stationxml import ResponseStore          # noqa: E402


def _wanted(r):
    """SEED ids of the live seismic channels the survey saw (None: let the probe decide)."""
    act = r.get("active")
    if not isinstance(act, list) or not act or not r.get("stations"):
        return None
    out = set()
    for st in r["stations"]:
        code = st.split(" ", 1)[0]                     # "IS.PAN (Panchavada)" -> "IS.PAN"
        for lc in act:
            loc, cha = lc.split(".")
            out.add(f"{code}.{'' if loc == '--' else loc}.{cha}")
    return out


def probe_all(survey_path, pending_path):
    survey = json.load(open(survey_path, encoding="utf-8"))
    config = cfg_mod.Config()
    have = {s.get("host") for s in config.sources()}
    todo = []
    for r in survey:
        banner = (r.get("hello") or [""])[0]
        if "SeisZone" in banner or r["ip"] in have:
            continue
        if r.get("hello"):
            todo.append((r, r["ip"]))
        elif r.get("gcfping"):
            todo.append((r, f"{r['ip']}:1567"))
    print(f"{len(todo)} unit(s) to probe")
    store = ResponseStore()

    def one(item):
        r, target = item
        lines = []
        try:
            inst = probe.probe(target, log=lines.append)
        except Exception as e:  # noqa: BLE001 - one bad unit must not stop the others
            return r, target, None, f"probe failed: {e}"
        if not inst.found:
            return r, target, None, "nothing usable found: " + "; ".join(lines[-3:])
        want = _wanted(r)
        try:
            src = inst.to_source(want) if want else inst.to_source()
        except ValueError:
            src = inst.to_source()
        src["name"] = src.get("name") or r["site"]
        if inst.stationxml:
            for st in inst.stations:
                try:
                    store.save(st["net"], st["sta"], inst.stationxml)
                except ValueError:
                    pass
        return r, target, src, ""

    pending, report = [], []
    with ThreadPoolExecutor(6) as ex:
        for r, target, src, err in ex.map(one, todo):
            if src:
                pending.append(src)
                chans = ", ".join(f"{st['net']}.{st['sta']} {st['selectors'] or 'all'}" for st in src.get("stations", [])) \
                    if src["kind"] == "seedlink" else f"GCF {src['gcf'].get('net')}.{src['gcf'].get('sta')} {sorted(src['gcf'].get('map', {}))}"
                report.append(f"OK    {target:18s} {r['site'][:22]:22s} {src['name'][:24]:24s} {chans}")
            else:
                report.append(f"SKIP  {target:18s} {r['site'][:22]:22s} {err}")
    json.dump(pending, open(pending_path, "w", encoding="utf-8"), indent=1)
    print("\n".join(sorted(report)))
    print(f"{len(pending)} source(s) written to {pending_path}")


def write_all(pending_path):
    mutex = single_instance.acquire_single_instance(single_instance.MUTEX_NAME)
    if mutex is None:
        sys.exit("SeisZone is running - quit it from the tray icon first.")
    try:
        config = cfg_mod.Config()
        for s in config.sources():                     # old selectors of one-character locations
            changed = False
            for st in s.get("stations") or []:
                new = [wire_selector(x) for x in st.get("selectors") or []]
                if new != st.get("selectors"):
                    st["selectors"], changed = new, True
            if changed:
                config.update_source(s)
                print(f"selectors fixed: {cfg_mod.source_label(s)}")
        n = 0
        for src in json.load(open(pending_path, encoding="utf-8")):
            if config.find_source(src["host"], src["port"]):
                continue
            src["added"] = time.time()
            config.add_source(cfg_mod.validate_source(src))
            n += 1
        print(f"{n} source(s) added; {len(config.sources())} in all")
    finally:
        single_instance.release_single_instance(mutex)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "probe":
        probe_all(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 3 and sys.argv[1] == "write":
        write_all(sys.argv[2])
    else:
        sys.exit(__doc__)
