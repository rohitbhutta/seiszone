"""SeisZone without the window (python -m seiszone ...), for servers, checks and field tests:

    --engine [--seconds N] [--config PATH]   run the engine with the configured sources and
                                             print the status every 10 s (Ctrl+C ends it)
    --probe IP[:port]                        detect an instrument and print what was found
                                             (read-only: nothing is saved or changed)
    --engine --probe IP[:port] --seconds 60  detect it and receive from it for 60 s, still
                                             read-only (temporary settings, no listeners)
    --add IP[:port]                          detect an instrument and add it to the sources
    --list                                   print the configured sources
    --version
    --output PATH                            write everything printed into this file instead
                                             (the installed SeisZone.exe has no console window)

--engine and --add use the user's config.json and state.json (the active recordings, the
SeedLink resume positions), so they only run while SeisZone itself does not: quit it from the
tray icon (Quit) first - closing the window only hides it. They hold the same single-instance
mutex as the window (exit code 3 when SeisZone is running). --engine --probe and runs with
another --config file are independent of it. --engine with a --config file that does not exist
is an error (exit code 2); --engine ends with exit code 1 when the engine failed.
"""

import argparse
import copy
import logging
import os
import sys
import tempfile
import threading
import time

from .. import APP_NAME, VERSION, logsetup, paths, single_instance
from .. import config as cfg_mod
from . import mseed

log = logging.getLogger("seiszone")

STATUS_EVERY = 10.0   # s between status prints of --engine


def emit(text=""):
    """Print, or log when there is no console (the windowed program has no stdout)."""
    if sys.stdout is not None:
        print(text, flush=True)
    else:
        log.info(text)


def build_parser():
    p = argparse.ArgumentParser(prog="seiszone", description=f"{APP_NAME} {VERSION} without the window")
    p.add_argument("--engine", action="store_true", help="run the data engine headless")
    p.add_argument("--seconds", type=float, default=0, help="with --engine: stop after N seconds (0 = until Ctrl+C)")
    p.add_argument("--config", metavar="PATH", help="settings file to use instead of the user's config.json")
    p.add_argument("--probe", metavar="IP[:PORT]", help="detect the instrument at this address (read-only)")
    p.add_argument("--add", metavar="IP[:PORT]", help="detect the instrument and add it to the sources")
    p.add_argument("--list", action="store_true", help="print the configured sources")
    p.add_argument("--version", action="store_true", help="print the version")
    p.add_argument("--output", metavar="PATH", help="write the output into this file (for the windowed exe)")
    p.add_argument("--verbose", action="store_true", help="debug messages")
    p.add_argument("--interval", type=float, default=STATUS_EVERY, help=argparse.SUPPRESS)
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args, _unknown = build_parser().parse_known_args(argv)
    if not args.output:
        return _run(args)
    saved = sys.stdout, sys.stderr
    with open(args.output, "w", encoding="utf-8", buffering=1) as out:
        sys.stdout = sys.stderr = out
        try:
            code = _run(args)
            out.write(f"exit code {code}\n")
            return code
        except BaseException as e:  # the file is the only place a windowed exe can report this
            out.write(f"failed: {e!r}\n")
            raise
        finally:
            lg = logging.getLogger("seiszone")   # the console log handler writes into `out`
            for h in [h for h in lg.handlers if getattr(h, "stream", None) is out]:
                lg.removeHandler(h)
            sys.stdout, sys.stderr = saved


def _uses_user_files(args):
    """True for the commands that change the user's own config.json / state.json."""
    if not (args.add or (args.engine and not args.probe)):
        return False
    return not args.config or os.path.normcase(os.path.abspath(args.config)) == \
        os.path.normcase(os.path.abspath(paths.config_file()))


def _run(args):
    if args.version:
        emit(f"{APP_NAME} {VERSION}")
        return 0
    if args.engine and args.config and not os.path.isfile(args.config):   # a typo: no silent defaults
        emit(f"Error: config file not found: {args.config}")
        return 2
    mutex = None
    if _uses_user_files(args):
        mutex = single_instance.acquire_single_instance(single_instance.MUTEX_NAME)
        if mutex is None:
            emit(f"{APP_NAME} is running - quit it from the tray icon (Quit), then run this again.")
            return 3
    try:
        return _run_unlocked(args)
    finally:
        single_instance.release_single_instance(mutex)


def _run_unlocked(args):
    logsetup.setup(verbose=args.verbose, console=bool(args.engine and not args.probe))
    config = cfg_mod.Config(args.config) if args.config else cfg_mod.Config()
    if args.list:
        return list_sources(config)
    if args.add:
        return add(config, args.add)
    if args.probe:
        inst = run_probe(args.probe)
        if args.engine:
            if not inst.found:
                return 1
            return trial(config, inst, args.seconds, args.interval)
        return 0 if inst.found else 1
    if args.engine:
        return run_engine(config, args.seconds, args.interval, _state_for(args.config))
    build_parser().print_help()
    return 2


def _state_for(config_path):
    """The state file beside a --config file; the user's state file otherwise."""
    if config_path:
        return cfg_mod.State(os.path.join(os.path.dirname(os.path.abspath(config_path)), "state.json"))
    return cfg_mod.State()


# ------------------------------------------------------------------------------ sources
def list_sources(config):
    srcs = config.sources()
    if not srcs:
        emit("No sources configured.")
    for s in srcs:
        emit(f"{s['id']}  {s['kind']:<8}  {'on ' if s.get('enabled', True) else 'off'}  {cfg_mod.source_label(s)}")
    return 0


def run_probe(text):
    """Detect an instrument and print the summary and notes. Returns the Instrument."""
    from . import probe
    try:
        inst = probe.probe(text, log=lambda m: emit(f"  {m}"))
    except ValueError as e:
        emit(f"Error: {e}")
        return probe.Instrument(text)
    emit("")
    for line in inst.summary_lines():
        emit(line)
    for note in inst.notes:
        emit(f"Note: {note}")
    return inst


def add(config, text):
    """Probe, then add the instrument (or replace the source with the same address)."""
    inst = run_probe(text)
    if not inst.found:
        return 1
    src = inst.to_source()
    existing = config.find_source(src["host"], src["port"])
    if existing:
        src.update(id=existing["id"], added=existing.get("added", time.time()))
        saved = config.update_source(src)
        emit(f"Replaced: {cfg_mod.source_label(saved)}")
    else:
        saved = config.add_source(src)
        emit(f"Added: {cfg_mod.source_label(saved)}")
    if inst.stationxml:
        from .stationxml import ResponseStore
        store = ResponseStore()
        for st in inst.stations:
            try:
                emit(f"StationXML saved: {store.save(st['net'], st['sta'], inst.stationxml)}")
            except ValueError as e:
                emit(f"StationXML of {st['net']}.{st['sta']} not saved: {e}")
    return 0


# ------------------------------------------------------------------------------ engine
def trial(config, inst, seconds, interval):
    """Receive from a probed instrument without touching the user's settings: a temporary
    config with only that source and no listeners (the window may hold the ports)."""
    with tempfile.TemporaryDirectory(prefix="seiszone_trial_") as tmp:
        with config.lock:
            data = copy.deepcopy(config.data)
        data["sources"] = []
        data["gecko"]["enabled"] = False
        data["seedlink_server"]["enabled"] = False
        cfg_mod.atomic_write_json(os.path.join(tmp, "config.json"), data)
        temp = cfg_mod.Config(os.path.join(tmp, "config.json"))
        temp.add_source(inst.to_source())
        return run_engine(temp, seconds or 60, interval, cfg_mod.State(os.path.join(tmp, "state.json")))


def run_engine(config, seconds, interval, state):
    from .engine import Engine
    engine = Engine(config, state)
    done = threading.Event()
    engine.start()
    if not engine.running:
        emit("Error: the engine did not start (see the log)")
        engine.stop()
        return 1
    emit(f"{APP_NAME} {VERSION} engine running" + (f" for {seconds:g} s" if seconds else " - Ctrl+C ends it"))
    failed = False
    end = time.monotonic() + seconds if seconds else None
    try:
        while not done.is_set():
            wait = interval if end is None else min(interval, end - time.monotonic())
            if wait > 0 and done.wait(wait):
                break
            emit(status_text(engine))
            if end is not None and time.monotonic() >= end:
                break
    except KeyboardInterrupt:
        emit("stopping ...")
    finally:
        failed = not engine.running
        engine.stop()
    if failed:
        emit("Error: the engine failed (see the log)")
    return 1 if failed else 0


def status_text(engine, now=None):
    """Sources with state and packets, listeners, and every channel with its latest sample."""
    now = time.time() if now is None else now
    st = engine.status()
    lines = [f"--- {mseed.fmt_time(now)} ---"]
    names = {s["id"]: s for s in engine.config.sources()}
    for sid, s in st["sources"].items():
        label = cfg_mod.source_label(names[sid]) if sid in names else s["address"]
        detail = f" ({s['detail']})" if s.get("detail") else ""
        lines.append(f"source {label}: {s['state']}{detail}, {s['packets']} packets")
    g, srv = st["gecko"], st["server"]
    if g["enabled"]:
        lines.append("Gecko receiver: " + (g["error"] or f"listening on {','.join(map(str, g['listening']))}")
                     + f", {len(g['links'])} link(s)")
    if srv["enabled"]:
        lines.append("SeedLink server: " + (srv["error"] or f"port {srv['port']}") + f", {srv['clients']} client(s)")
    stats = engine.hub.stats_snapshot()
    if not stats:
        lines.append("no channels yet")
    for sid in sorted(stats):
        c = stats[sid]
        latency = now - c["last_end"]
        lines.append(f"  {sid:<18} {c['rate']:g} sps  latest {mseed.fmt_time(c['last_end'])}  "
                     f"latency {latency:.1f} s  packets {c['packets']}  gaps {c['gaps']}")
    return "\n".join(lines)
