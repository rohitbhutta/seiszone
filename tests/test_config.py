"""config.json and state.json that cannot be read at start (locked for a moment by a virus
scanner or backup program, or damaged): nothing the user set up is overwritten."""

import glob
import json
import msvcrt
import os
import sys

import pytest

from seiszone import config as cfg_mod
from seiszone.config import Config, State

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows file locks")


class Locked:
    """Another program holds the whole file locked (reads fail with PermissionError)."""

    def __init__(self, path):
        self.fh = open(path, "r+b")
        self.size = max(1, os.path.getsize(path))
        msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, self.size)

    def release(self):
        self.fh.seek(0)
        msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, self.size)
        self.fh.close()


@pytest.fixture
def fast_retries(monkeypatch):
    monkeypatch.setattr(cfg_mod, "READ_TRIES", 3)


def good_state(path):
    cfg_mod.atomic_write_json(path, {"seedlink_seq": {"src1|WB.KUM": 123456},
                                     "recordings": [{"id": "r1", "state": "recording"}]})


def test_locked_state_is_never_overwritten(tmp_path, fast_retries):
    path = str(tmp_path / "state.json")
    good_state(path)
    lock = Locked(path)
    try:
        st = State(path)
    finally:
        lock.release()
    assert st.data == {} and st.load_error and st.read_only
    st.set_seq("src1", "WB.KUM", 999)
    st.flush()                       # the original is kept as .bad first, then replaced
    bad = glob.glob(path + ".*.bad")
    assert len(bad) == 1
    with open(bad[0], encoding="utf-8") as fh:
        assert json.load(fh)["seedlink_seq"] == {"src1|WB.KUM": 123456}


def test_state_lock_released_during_the_retries(tmp_path, monkeypatch):
    path = str(tmp_path / "state.json")
    good_state(path)
    lock = Locked(path)
    real_sleep = cfg_mod.time.sleep
    calls = []

    def sleep(s):
        calls.append(s)
        if len(calls) == 2:
            lock.release()           # the scanner is done after a moment
        real_sleep(s)
    monkeypatch.setattr(cfg_mod.time, "sleep", sleep)
    st = State(path)
    assert st.get_seq("src1", "WB.KUM") == 123456 and not st.load_error
    assert st.get("recordings")[0]["state"] == "recording"


def test_damaged_state_kept_as_bad(tmp_path):
    path = str(tmp_path / "state.json")
    good_state(path)
    with open(path, "r+b") as fh:
        fh.truncate(20)
    st = State(path)
    assert st.data == {} and "kept as" in st.load_error and not st.read_only
    assert len(glob.glob(path + ".*.bad")) == 1


def test_locked_config_keeps_the_sources(tmp_path, fast_retries):
    path = str(tmp_path / "config.json")
    cfg = Config(path)
    for host in ("10.8.0.180", "10.8.0.181"):
        cfg.add_source({"kind": "seedlink", "host": host, "port": 18000})
    lock = Locked(path)
    try:
        cfg2 = Config(path)
    finally:
        lock.release()
    assert cfg2.read_only and cfg2.load_error and cfg2.sources() == []
    cfg2.update_section("ui", {"geometry": "abc"})      # what saving the window layout does
    assert len(Config(path).sources()) == 2
    assert glob.glob(path + "*.bad") == []


def test_damaged_config_copies_never_overwrite_each_other(tmp_path):
    path = str(tmp_path / "config.json")
    for n in range(2):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"sources": [' + str(n))
        cfg = Config(path)
        assert not cfg.read_only and "kept as" in cfg.load_error
        cfg.save()
    assert len(glob.glob(path + ".*.bad")) == 2
