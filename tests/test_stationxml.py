"""Tests for core.stationxml.ResponseStore, cross-checked with ObsPy (installed for tests only)."""

import os
import time

import pytest
from conftest import DATA

from seiszone import paths
from seiszone.core import stationxml as sx

TEDT = os.path.join(DATA, "CI.TEDT.xml")


def tedt_bytes():
    with open(TEDT, "rb") as fh:
        return fh.read()


def epochs_xml(ns=True, version="1.1", value2="2000.0"):
    """Station XX.EPO: HHZ with a closed 2020 epoch and an open epoch from 2021 (other gain);
    00.BHZ without a response."""
    xmlns = ' xmlns="http://www.fdsn.org/xml/station/1"' if ns else ""
    return (f'<?xml version="1.0" encoding="UTF-8"?><FDSNStationXML{xmlns} schemaVersion="{version}">'
            '<Source>test</Source><Network code="XX"><Station code="EPO">'
            '<Latitude>1.5</Latitude><Longitude>2.5</Longitude><Elevation>10</Elevation>'
            '<Site><Name>Epoch test</Name></Site>'
            '<Channel code="HHZ" locationCode="" startDate="2020-01-01T00:00:00" endDate="2021-01-01T00:00:00Z">'
            '<SampleRate>100</SampleRate><Response><InstrumentSensitivity><Value>1000.0</Value>'
            '<Frequency>1</Frequency><InputUnits><Name>M/S</Name></InputUnits>'
            '<OutputUnits><Name>COUNTS</Name></OutputUnits></InstrumentSensitivity></Response></Channel>'
            '<Channel code="HHZ" locationCode="" startDate="2021-01-01T00:00:00.0000000Z">'
            f'<SampleRate>200</SampleRate><Response><InstrumentSensitivity><Value>{value2}</Value>'
            '<Frequency>1</Frequency><InputUnits><Name>M/S</Name></InputUnits>'
            '<OutputUnits><Name>COUNTS</Name></OutputUnits></InstrumentSensitivity></Response></Channel>'
            '<Channel code="BHZ" locationCode="00" startDate="2020-01-01T00:00:00Z">'
            '<SampleRate>20</SampleRate></Channel>'
            '</Station></Network></FDSNStationXML>').encode()


def t(s):
    return sx.parse_time(s)


def test_sensitivity_matches_obspy(tmp_path):
    obspy = pytest.importorskip("obspy")
    inv = obspy.read_inventory(TEDT)
    store = sx.ResponseStore(str(tmp_path))
    path = store.save("CI", "TEDT", tedt_bytes())
    assert path == os.path.join(str(tmp_path), "CI.TEDT.xml") and store.has("CI", "TEDT")
    checked = 0
    for net in inv:
        for sta in net:
            for ch in sta:
                ref = ch.response.instrument_sensitivity
                sid = f"{net.code}.{sta.code}.{ch.location_code}.{ch.code}"
                value, units = store.sensitivity(sid, ch.start_date.timestamp + 1)
                assert value == pytest.approx(ref.value, rel=1e-12), sid
                assert units == ref.input_units, sid
                assert store.sensitivity(sid) == (value, units)  # now: the open epoch
                checked += 1
    assert checked == 6
    ref_ch = {(c.location_code, c.code, c.sample_rate, c.start_date.timestamp,
               c.end_date.timestamp if c.end_date else None) for c in inv[0][0]}
    got = store.channels("CI", "TEDT")
    assert len(got) == 6
    for (loc, cha, rate, start, end), want in zip(sorted(got), sorted(ref_ch)):
        assert (loc, cha, rate, end) == (want[0], want[1], want[2], want[4])
        assert start == pytest.approx(want[3], abs=1e-6)
    summary = store.load("CI", "TEDT")
    st = inv[0][0]
    assert (summary["net"], summary["sta"], summary["site"]) == ("CI", "TEDT", st.site.name)
    assert summary["lat"] == pytest.approx(st.latitude) and summary["lon"] == pytest.approx(st.longitude)
    assert summary["elev"] == pytest.approx(st.elevation)
    assert summary["responses"] == 6


def test_epochs_and_versions(tmp_path):
    for ns, version in ((True, "1.0"), (True, "1.1"), (True, "1.2"), (False, "1.1")):
        store = sx.ResponseStore(str(tmp_path / f"{ns}{version}"))
        store.save("XX", "EPO", epochs_xml(ns, version))
        assert store.sensitivity("XX.EPO..HHZ", t("2020-06-01T00:00:00Z")) == (1000.0, "M/S")
        assert store.sensitivity("XX.EPO..HHZ", t("2022-06-01T00:00:00Z")) == (2000.0, "M/S")
        assert store.sensitivity("XX.EPO..HHZ") == (2000.0, "M/S")  # now
        # the end date is exclusive; before every epoch -> the newest epoch
        assert store.sensitivity("XX.EPO..HHZ", t("2021-01-01T00:00:00Z")) == (2000.0, "M/S")
        assert store.sensitivity("XX.EPO..HHZ", t("2010-01-01T00:00:00Z")) == (2000.0, "M/S")
        assert store.sensitivity("XX.EPO.--.HHZ", t("2020-06-01")) == (1000.0, "M/S")
        assert store.sensitivity("XX.EPO.00.BHZ") is None      # no response
        assert store.sensitivity("XX.EPO..HHN") is None        # no such channel
        assert store.sensitivity("XX.NONE..HHZ") is None       # no file
        assert store.sensitivity("bad") is None
        assert store.channels("XX", "EPO") == [
            ("", "HHZ", 100.0, t("2020-01-01T00:00:00Z"), t("2021-01-01T00:00:00Z")),
            ("", "HHZ", 200.0, t("2021-01-01T00:00:00Z"), None),
            ("00", "BHZ", 20.0, t("2020-01-01T00:00:00Z"), None)]
        assert store.load("XX", "EPO")["site"] == "Epoch test"


def test_parse_time():
    base = 1577836800.0  # 2020-01-01T00:00:00Z
    for s in ("2020-01-01T00:00:00Z", "2020-01-01T00:00:00", "2020-01-01T00:00:00.000000000Z",
              "2020-01-01T01:00:00+01:00", "2020-01-01"):
        assert t(s) == base, s
    assert t("2020-01-01T00:00:00.5Z") == base + 0.5
    assert t("") is None and t(None) is None and t("yesterday") is None


def test_cache_reloads_changed_file(tmp_path):
    store = sx.ResponseStore(str(tmp_path))
    path = store.save("XX", "EPO", epochs_xml())
    assert store.sensitivity("XX.EPO..HHZ") == (2000.0, "M/S")
    assert store._station("XX", "EPO") is store._station("XX", "EPO")  # parsed once
    with open(path, "wb") as fh:  # changed by hand, outside the store
        fh.write(epochs_xml(value2="3000.0"))
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    assert store.sensitivity("XX.EPO..HHZ") == (3000.0, "M/S")
    store.save("XX", "EPO", epochs_xml(value2="4000.0"))
    assert store.sensitivity("XX.EPO..HHZ") == (4000.0, "M/S")
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]


def test_save_checks_the_data(tmp_path):
    store = sx.ResponseStore(str(tmp_path / "sx"))
    with pytest.raises(ValueError, match="not FDSN StationXML"):
        store.save("CI", "TEDT", b"<html>not it</html>")
    with pytest.raises(ValueError, match="CI.TEDT, not WB.KUM"):
        store.save("WB", "KUM", tedt_bytes())
    with pytest.raises(ValueError):
        store.path("..", "x")
    assert not store.has("..", "x") and not store.has("CI", "TEDT")
    assert store.load("CI", "TEDT") is None and store.channels("CI", "TEDT") == []
    store.save("CI", "TEDT", tedt_bytes().decode("utf-8"))  # text is accepted too
    assert store.has("CI", "TEDT")


def test_damaged_file_and_default_folder(tmp_path):
    assert sx.ResponseStore().folder == paths.stationxml_dir()
    store = sx.ResponseStore(str(tmp_path))
    with open(os.path.join(tmp_path, "XX.BAD.xml"), "wb") as fh:
        fh.write(b"<FDSNStationXML><Network")
    assert store.load("XX", "BAD") is None and store.sensitivity("XX.BAD..HHZ") is None


def test_summarize_for_probe():
    s = sx.summarize(tedt_bytes(), "CI", "TEDT")
    assert s["site"] == "Tetar Dam Top CH1050" and len(s["channels"]) == 6
    assert ("0J", "HNZ", 200.0) in [c[:3] for c in s["channels"]]
    assert sx.summarize(tedt_bytes(), "CI", "OTHER") is None
    assert sx.summarize(b"garbage") is None
    assert sx.summarize(tedt_bytes())["sta"] == "TEDT"
    assert time.time() > s["channels"][0][3]
