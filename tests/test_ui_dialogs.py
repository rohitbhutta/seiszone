"""Offscreen tests of the sources panel and the dialogs: Add source (detect -> choose channels ->
add, replace an existing source, nothing found), Recordings, Export and Settings.

Nothing here touches the network: the instrument probe and export_window are replaced by fakes
(the addresses in the texts are only strings), FakeEngine never connects and FakeRecorder writes
nothing. Screenshots of the dialogs go to build/screens/.
"""

import copy
import math
import os
import sys
import time

import pytest

# the offscreen platform has no font list of its own: without this the screenshots show boxes
if sys.platform == "win32" and os.path.isdir(r"C:\Windows\Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from seiszone import paths
from seiszone.config import Config, State
from seiszone.core import probe as probe_mod
from seiszone.ui import theme

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENS = os.path.join(ROOT, "build", "screens")
KUMARI = {"id": "fake1", "name": "KUMARI", "kind": "seedlink", "host": "10.8.0.182", "port": 18000,
          "stations": [{"net": "WB", "sta": "KUM", "desc": "KUMARI", "selectors": []}]}


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    theme.apply(app, "dark")
    return app


def pump(app, seconds=0.05):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_for(app, cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


_BASE = []


@pytest.fixture
def make_engine(qapp, tmp_path):
    """FakeEngine with its own config file; the synthetic data (slow to encode) is made once
    and its hub shared - the dialogs only read it."""
    from fake_engine import FakeEngine, FakeRecorder

    def make(with_source=True):
        if not _BASE:
            _BASE.append(FakeEngine(Config(os.path.join(paths.config_dir(), "dialogs_base.json")), seconds=60))
        engine = copy.copy(_BASE[0])
        engine.config = Config(str(tmp_path / "config.json"))
        engine.state = State(str(tmp_path / "state.json"))
        engine.recorder, engine.calls, engine.responses = FakeRecorder(), [], FakeResponses()
        if with_source:
            engine.config.add_source(KUMARI)
        return engine
    return make


class FakeResponses:
    """The ResponseStore calls the dialogs use."""

    def __init__(self, have=()):
        self.saved = {}
        self.have = set(have)
        self.asked = []

    def has(self, net, sta):
        self.asked.append((net, sta))
        return (net, sta) in self.have or (net, sta) in self.saved

    def save(self, net, sta, data):
        self.saved[(net, sta)] = data
        return f"stationxml/{net}.{sta}.xml"


def channel(loc, cha, rate, gcf="", units="m/s**2"):
    return {"loc": loc, "cha": cha, "rate": rate, "gcf": gcf, "sensitivity": 1.0e6 if rate else None,
            "units": units if rate else ""}


def seedlink_instrument(host="10.8.0.182", port=18000):
    inst = probe_mod.Instrument(host)
    inst.method, inst.port = "seedlink", port
    inst.stations = [{"net": "WB", "sta": "KUM", "desc": "KUMARI",
                      "channels": [channel("0J", "HNZ", 200.0), channel("0J", "HNN", 200.0),
                                   channel("0J", "HNE", 200.0), channel("", "SOH", 0.0)]}]
    inst.details = {"product": "Guralp Fortimus", "hostname": "FORT-6A43", "software": "SeedLink v3.1",
                    "organization": "Guralp", "serial": "6A43", "firmware": "2.1-1234",
                    "lat": "23.73", "lon": "86.41", "stationxml_name": "WB.KUM.xml"}
    inst.stationxml = b"<FDSNStationXML>" + b" " * 4096 + b"</FDSNStationXML>"
    inst.notes = ["The instrument also sends GCF on port 1567; SeedLink is used so missed data is backfilled."]
    return inst


def gcf_instrument():
    inst = probe_mod.Instrument("10.8.0.181")
    inst.method, inst.port = "gcf-udp", 1567
    inst.stations = [{"net": "WB", "sta": "KUM", "desc": "KUMARI",
                      "channels": [channel("0J", "HNZ", 200.0, "6A43Z2"), channel("0J", "HNN", 200.0, "6A43N2"),
                                   channel("0J", "HNE", 200.0, "6A43E2"), channel("", "XXZ", 0.0, "6A4300")]}]
    inst.gcf_map = {c["gcf"]: (c["loc"], c["cha"]) for c in inst.stations[0]["channels"]}
    inst.details = {"system_id": "6A43"}
    return inst


def fake_probe(monkeypatch, inst, calls=None):
    """Replace core.probe.probe: log a few progress lines, return `inst`."""
    def probe(text, log=None, prefer_direct=True, need_xml=None):
        if calls is not None:
            calls.append({"text": text, "prefer_direct": prefer_direct, "need_xml": need_xml})
        for line in (f"Checking {text} ...", "SeedLink HELLO ... answered", "Reading the station list ..."):
            log(line)
            time.sleep(0.01)
        if need_xml is not None:
            need_xml("WB", "KUM")
        return inst
    monkeypatch.setattr(probe_mod, "probe", probe)


def detect(app, dlg, text, enter=True):
    """Type the address like a user and start the detection (Enter or the Detect button)."""
    dlg.show()
    dlg.edit.clear()
    QTest.keyClicks(dlg.edit, text)
    if enter:
        QTest.keyClick(dlg.edit, Qt.Key_Return)
    else:
        dlg.detect_button.click()
    assert wait_for(app, lambda: dlg.state in ("found", "nothing", "idle") and dlg._thread is None)


def child_rows(dlg):
    top = dlg.channels.topLevelItem(0)
    return {top.child(k).text(0): top.child(k) for k in range(top.childCount())}


# ------------------------------------------------------------------------------------------
# sources panel
# ------------------------------------------------------------------------------------------

def find_action(menu, prefix):
    for a in menu.actions():
        if a.text().startswith(prefix):
            return a
    raise AssertionError(f"no menu entry {prefix!r} in {[a.text() for a in menu.actions()]}")


def test_sources_panel_lists_sources_gecko_and_server(make_engine):
    from seiszone.ui.sources_panel import C_ADDR, C_KIND, C_LAST, C_NAME, C_PACK, C_STATE, SourcesPanel
    engine = make_engine()
    panel = SourcesPanel(engine, engine.config)
    tree = panel.tree
    assert tree.topLevelItemCount() == 3
    src, gecko, server = (tree.topLevelItem(i) for i in range(3))
    assert src.text(C_NAME) == "KUMARI"
    assert src.text(C_ADDR) == "10.8.0.182:18000"
    assert src.text(C_KIND) == "SeedLink"
    assert src.text(C_STATE) == "receiving"
    assert src.text(C_PACK) == "1234" and src.text(C_LAST).endswith("ago")
    assert src.childCount() == 1 and src.child(0).text(C_NAME) == "WB.KUM"
    assert src.child(0).text(C_KIND) == "3 ch"
    assert gecko.text(C_NAME) == "Gecko receiver :58772" and gecko.text(C_STATE) == "listening"
    assert gecko.childCount() == 1
    assert gecko.child(0).text(C_NAME) == "0000A1B2  SS.SMR03"
    assert gecko.child(0).text(C_ADDR) == "10.10.100.254:50123" and gecko.child(0).text(C_PACK) == "4567"
    assert server.text(C_NAME) == "SeedLink server :18000" and server.text(C_LAST) == "1 client"
    assert server.child(0).text(C_NAME) == "127.0.0.1:50555"
    assert panel.summary.text() == "1 of 1 source receiving"

    # refresh keeps the items (selection and expansion survive)
    tree.setCurrentItem(src.child(0))
    assert panel.selected_source_id() == "fake1"
    panel.refresh()
    assert tree.topLevelItem(0) is src and tree.currentItem() is src.child(0)


def test_sources_panel_context_actions_call_the_engine(make_engine, qapp, monkeypatch):
    from seiszone.ui.sources_panel import C_STATE, SourcesPanel
    engine = make_engine()
    panel = SourcesPanel(engine, engine.config)
    selected, requested = [], []
    panel.sourceSelected.connect(selected.append)
    panel.channelsRequested.connect(requested.append)
    item = panel.tree.topLevelItem(0)

    menu = panel.build_menu(item)
    assert selected == ["fake1"]
    find_action(menu, "Show channels (3)").trigger()
    assert requested == [["WB.KUM.0J.HNE", "WB.KUM.0J.HNN", "WB.KUM.0J.HNZ"]]
    find_action(menu, "Reconnect").trigger()
    assert ("reconnect", "fake1") in engine.calls
    find_action(menu, "Disable").trigger()
    assert ("set_enabled", "fake1", False) in engine.calls
    assert panel.tree.topLevelItem(0).text(C_STATE) == "disabled"
    menu = panel.build_menu(panel.tree.topLevelItem(0))
    assert not find_action(menu, "Reconnect").isEnabled()
    find_action(menu, "Enable").trigger()
    assert ("set_enabled", "fake1", True) in engine.calls

    title, data = panel.properties("fake1")
    assert title == "Source KUMARI" and data["host"] == "10.8.0.182"
    assert data["link"]["state"] == "receiving" and "details" in data
    dlg = panel.show_properties("fake1")
    assert dlg.tree.topLevelItemCount() >= 5
    dlg.close()

    gecko_menu = panel.build_menu(panel.tree.topLevelItem(1))
    find_action(gecko_menu, "Show channels (1)").trigger()
    assert requested[-1] == ["SS.SMR03..HHZ"]
    assert panel.properties(None, ("gecko",))[1]["ports"] == [58772]
    assert panel.properties(None, ("server",))[1]["port"] == 18000

    # Remove asks first: No keeps the source, Yes removes it through the engine
    panel.tree.setCurrentItem(panel.tree.topLevelItem(0))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    find_action(panel.build_menu(panel.tree.topLevelItem(0)), "Remove").trigger()
    assert not any(c[0] == "remove_source" for c in engine.calls)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    find_action(panel.build_menu(panel.tree.topLevelItem(0)), "Remove").trigger()
    assert ("remove_source", "fake1") in engine.calls
    assert panel.tree.topLevelItemCount() == 2
    assert panel.summary.text() == "0 of 0 sources receiving"


def test_sources_panel_add_button_opens_the_add_dialog(make_engine, qapp, monkeypatch):
    from seiszone.ui.sources_panel import SourcesPanel
    engine = make_engine(with_source=False)
    fake_probe(monkeypatch, seedlink_instrument())
    panel = SourcesPanel(engine, engine.config)
    assert panel.tree.topLevelItemCount() == 2
    dlg = panel.add_source("10.8.0.182:18000")
    assert dlg.edit.text() == "10.8.0.182:18000"
    dlg.detect()
    assert wait_for(qapp, lambda: dlg.state == "found")
    dlg.add_button.click()
    pump(qapp)
    assert panel.tree.topLevelItemCount() == 3
    assert panel.selected_source_id() == engine.config.sources()[0]["id"]


def held_by_old_service(engine, held):
    """engine.status() as while the old Gecko SeedLink service holds 58772 and 18010 (as long
    as held["on"] is true), with the SeisZone SeedLink server set to 18010 in Settings."""
    base = engine.status

    def status():
        st = base()
        if held["on"]:
            st["gecko"].update(listening=[], links=[], owner="old-service", owner_pid=4321,
                               error="port 58772 is used by the old Gecko SeedLink service")
            st["server"].update(port=18010, listening=False, clients=0, peers=[], owner="old-service", owner_pid=4321,
                                error="port 18010 is used by the old Gecko SeedLink service")
        return st
    engine.status = status


def test_sources_panel_frees_the_ports_of_the_old_gecko_service(make_engine, qapp, monkeypatch):
    from seiszone.ui import sources_panel as sp
    engine = make_engine()
    held = {"on": True}
    held_by_old_service(engine, held)
    opened, stops, answers = [], [], [False, True]
    monkeypatch.setattr(QMessageBox, "open", lambda self: opened.append(self))     # never shown
    monkeypatch.setattr(sp.ports, "stop_old_service", lambda: stops.append(1) or answers.pop(0))

    panel = sp.SourcesPanel(engine, engine.config)
    pump(qapp)
    assert opened == []                           # the window is not on screen yet (tray): no question
    panel.show()
    panel.refresh()
    gecko, server = panel.tree.topLevelItem(1), panel.tree.topLevelItem(2)
    assert gecko.text(sp.C_STATE) == "error - port 58772 is used by the old Gecko SeedLink service"
    assert server.text(sp.C_STATE) == "error - port 18010 is used by the old Gecko SeedLink service"
    assert "old Gecko SeedLink service holds this port" in gecko.toolTip(sp.C_NAME)
    assert not panel.old_service_bar.isHidden() and panel.free_button.isEnabled()
    assert "58772 and 18010" in panel.old_service_label.text()
    assert panel.free_button.text() == "Free the port – stop the old Gecko SeedLink service"

    # the one question of this run, with its two answers
    pump(qapp)
    assert len(opened) == 1
    box = opened[0]
    assert box.text() == sp.OFFER_TEXT and box.text().startswith("Your Gecko recorder is connected to the old")
    assert "administrator permission" in box.text() and "Windows Services" in box.text()
    buttons = {b.text(): b for b in box.buttons()}
    assert set(buttons) == {"Stop old service", "Not now"}
    buttons["Not now"].click()
    assert stops == [] and panel.offer_box is None
    panel.refresh()
    pump(qapp)
    assert len(opened) == 1                       # not asked again in this run

    # the context menu of both rows offers it; the user says No to Windows' question first
    for row in (gecko, server):
        find_action(panel.build_menu(row), sp.FREE_ACTION_TEXT)
    find_action(panel.build_menu(gecko), sp.FREE_ACTION_TEXT).trigger()
    assert stops == [1] and gecko.text(sp.C_STATE).startswith("error - ")
    assert panel.free_button.isEnabled()
    panel.free_button.click()                     # now permission is given
    assert stops == [1, 1]
    assert gecko.text(sp.C_STATE) == server.text(sp.C_STATE) == sp.WAITING_TEXT == "Waiting for the port…"
    assert not panel.free_button.isEnabled() and panel.old_service_label.text().startswith(sp.WAITING_TEXT)
    assert not find_action(panel.build_menu(gecko), sp.FREE_ACTION_TEXT).isEnabled()
    assert not panel.free_ports()                 # one stop at a time
    panel.refresh()
    assert gecko.text(sp.C_STATE) == sp.WAITING_TEXT

    # the engine has the ports: back to normal
    held["on"] = False
    panel.refresh()
    assert gecko.text(sp.C_STATE) == "listening" and server.text(sp.C_STATE) == "listening"
    assert panel.old_service_bar.isHidden() and panel._freeing is None
    with pytest.raises(AssertionError):
        find_action(panel.build_menu(gecko), sp.FREE_ACTION_TEXT)
    assert not panel.free_ports() and stops == [1, 1]     # nothing to free: Windows is not asked
    panel.close()


def test_sources_panel_waits_until_the_engine_listens(make_engine, qapp, monkeypatch):
    """The old service has let go but the port is still busy a moment (no known owner): the rows
    keep waiting until the engine listens, they do not fall back to an error."""
    from seiszone.ui import sources_panel as sp
    engine = make_engine()
    held = {"on": True}
    held_by_old_service(engine, held)
    monkeypatch.setattr(sp.ports, "stop_old_service", lambda: True)
    panel = sp.SourcesPanel(engine, engine.config)
    assert panel.free_ports()
    base = engine.status

    def busy_without_owner():
        st = base()
        st["gecko"].update(listening=[], owner=None, owner_pid=None, error="port 58772 is in use by another program")
        return st
    held["on"] = False
    engine.status = busy_without_owner
    panel.refresh()
    assert panel.tree.topLevelItem(1).text(sp.C_STATE) == sp.WAITING_TEXT
    assert panel.tree.topLevelItem(2).text(sp.C_STATE) == "listening"     # this one has its port
    assert not panel.old_service_bar.isHidden() and "58772" in panel.old_service_label.text()
    engine.status = base
    panel.refresh()
    assert panel.tree.topLevelItem(1).text(sp.C_STATE) == "listening" and panel._freeing is None
    assert panel.old_service_bar.isHidden()


def test_sources_panel_offer_answered_stop_and_wait_runs_out(make_engine, qapp, monkeypatch):
    from seiszone.ui import sources_panel as sp
    engine = make_engine()
    held = {"on": True}
    held_by_old_service(engine, held)
    opened, stops = [], []
    monkeypatch.setattr(QMessageBox, "open", lambda self: opened.append(self))
    monkeypatch.setattr(sp.ports, "stop_old_service", lambda: stops.append(1) or True)
    panel = sp.SourcesPanel(engine, engine.config)
    panel.show()
    panel.refresh()
    pump(qapp)
    assert len(opened) == 1
    {b.text(): b for b in opened[0].buttons()}["Stop old service"].click()
    assert stops == [1] and panel.tree.topLevelItem(1).text(sp.C_STATE) == sp.WAITING_TEXT
    # the port stays held: after FREE_WAIT the error and the button come back
    panel._freeing -= sp.FREE_WAIT + 1
    panel.refresh()
    assert panel.tree.topLevelItem(1).text(sp.C_STATE).startswith("error - port 58772 is used by the old")
    assert panel.free_button.isEnabled()
    pump(qapp)
    assert len(opened) == 1                       # still only one question per run
    panel.close()


def test_sources_panel_no_offer_for_another_program(make_engine, qapp, monkeypatch):
    from seiszone.ui import sources_panel as sp
    engine = make_engine()
    base = engine.status

    def status():
        st = base()
        st["gecko"].update(listening=[], owner="other", owner_pid=99, error="port 58772 is in use by another program")
        return st
    engine.status = status
    monkeypatch.setattr(QMessageBox, "open", lambda self: pytest.fail("asked about another program"))
    monkeypatch.setattr(sp.ports, "stop_old_service", lambda: pytest.fail("old service stopped"))
    panel = sp.SourcesPanel(engine, engine.config)
    panel.show()                                  # on screen, where the question would be asked
    panel.refresh()
    pump(qapp)
    assert panel.tree.topLevelItem(1).text(sp.C_STATE) == "error - port 58772 is in use by another program"
    assert panel.old_service_bar.isHidden()
    with pytest.raises(AssertionError):
        find_action(panel.build_menu(panel.tree.topLevelItem(1)), sp.FREE_ACTION_TEXT)
    panel.close()


# ------------------------------------------------------------------------------------------
# add source dialog
# ------------------------------------------------------------------------------------------

def test_add_dialog_full_flow_seedlink(make_engine, qapp, monkeypatch):
    from seiszone.ui.add_source_dialog import AddSourceDialog
    engine = make_engine(with_source=False)
    inst = seedlink_instrument()
    calls = []
    fake_probe(monkeypatch, inst, calls)
    dlg = AddSourceDialog(engine, engine.config)
    assert not dlg.add_button.isEnabled() and dlg.card.isHidden()

    detect(qapp, dlg, "10.8.0.182:18000", enter=True)   # Enter in the field starts Detect
    assert calls and calls[0]["text"] == "10.8.0.182:18000" and calls[0]["prefer_direct"] is True
    assert engine.responses.asked == [("WB", "KUM")]   # need_xml asks the response store
    assert dlg.state == "found" and dlg.card.isVisible()
    progress = dlg.progress.toPlainText()
    assert "Checking 10.8.0.182:18000 ..." in progress and "Reading the station list" in progress
    assert "Guralp Fortimus FORT-6A43" in dlg.card_summary.text()
    assert "port 1567" in dlg.card_notes.text()
    assert dlg.name.text() == "KUMARI"
    rows = child_rows(dlg)
    assert set(rows) == {"0J.HNZ", "0J.HNN", "0J.HNE", "--.SOH"}
    assert rows["0J.HNZ"].checkState(0) == Qt.Checked
    assert rows["--.SOH"].checkState(0) == Qt.Unchecked     # state of health: not ticked
    assert dlg.add_button.isEnabled() and dlg.add_button.isDefault()
    assert dlg.channels.topLevelItem(0).text(3) == "3 of 4 ticked"

    os.makedirs(SCREENS, exist_ok=True)
    dlg.resize(720, 680)
    pump(qapp, 0.2)
    assert dlg.grab().save(os.path.join(SCREENS, "add_source.png"))

    rows["0J.HNE"].setCheckState(0, Qt.Unchecked)
    dlg._tick_all(False)
    assert not dlg.add_button.isEnabled()
    dlg._tick_all(True)
    rows["0J.HNE"].setCheckState(0, Qt.Unchecked)
    rows["--.SOH"].setCheckState(0, Qt.Unchecked)
    dlg.name.setText("Kumari Fortimus")
    dlg.add_button.click()

    assert dlg.result() == QDialog.Accepted
    added = [c[1] for c in engine.calls if c[0] == "add_source"]
    assert len(added) == 1
    src = added[0]
    want = inst.to_source(selected=[("WB", "KUM", "0J", "HNZ"), ("WB", "KUM", "0J", "HNN")])
    assert src["kind"] == "seedlink" and src["host"] == "10.8.0.182" and src["port"] == 18000
    assert src["stations"] == want["stations"]
    assert src["name"] == "Kumari Fortimus"
    assert engine.responses.saved == {("WB", "KUM"): inst.stationxml}
    assert dlg.added_source["id"] == engine.config.sources()[0]["id"]


def test_add_dialog_gcf_instrument(make_engine, qapp, monkeypatch):
    from seiszone.ui.add_source_dialog import AddSourceDialog
    engine = make_engine(with_source=False)
    fake_probe(monkeypatch, gcf_instrument())
    dlg = AddSourceDialog(engine, engine.config, initial_text="10.8.0.181:1567")
    detect(qapp, dlg, "10.8.0.181:1567", enter=False)
    rows = child_rows(dlg)
    assert rows["--.XXZ"].checkState(0) == Qt.Unchecked     # GCF stream of unknown rate
    assert "rate unknown" in rows["--.XXZ"].text(3)
    rows["0J.HNN"].setCheckState(0, Qt.Unchecked)
    dlg.add()
    src = [c[1] for c in engine.calls if c[0] == "add_source"][0]
    assert src["kind"] == "gcf-udp" and src["port"] == 1567
    assert src["gcf"]["map"] == {"6A43Z2": ["0J", "HNZ"], "6A43E2": ["0J", "HNE"]}
    assert engine.responses.saved == {}          # no StationXML found: nothing saved


def test_add_dialog_replaces_an_existing_source(make_engine, qapp, monkeypatch):
    from seiszone.ui.add_source_dialog import AddSourceDialog
    engine = make_engine(with_source=True)
    fake_probe(monkeypatch, seedlink_instrument())
    dlg = AddSourceDialog(engine, engine.config)
    detect(qapp, dlg, "10.8.0.182:18000")

    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(a[2]) or QMessageBox.No))
    dlg.add()
    assert asked and "already in SeisZone as 'KUMARI'" in asked[0]
    assert dlg.result() != QDialog.Accepted and dlg.isVisible()
    assert not any(c[0] in ("add_source", "update_source") for c in engine.calls)

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    dlg.add()
    assert dlg.result() == QDialog.Accepted
    updated = [c[1] for c in engine.calls if c[0] == "update_source"]
    assert len(updated) == 1 and updated[0]["id"] == "fake1"    # same id: SeedLink resumes
    assert len(engine.config.sources()) == 1
    assert engine.config.source("fake1")["stations"][0]["selectors"] != []   # SOH not chosen
    assert not any(c[0] == "add_source" for c in engine.calls)


def test_add_dialog_nothing_found_add_anyway(make_engine, qapp, monkeypatch):
    from seiszone.ui.add_source_dialog import AddSourceDialog
    engine = make_engine(with_source=False)
    nothing = probe_mod.Instrument("10.8.0.199")
    nothing.notes = ["No SeedLink server answered on port 18000.", "No GCF data on port 1567."]
    fake_probe(monkeypatch, nothing)
    dlg = AddSourceDialog(engine, engine.config)
    detect(qapp, dlg, "10.8.0.199")
    assert dlg.state == "nothing"
    assert "No SeedLink server answered" in dlg.card_summary.text()
    assert dlg.manual_button.isVisible() and not dlg.add_button.isEnabled()
    assert dlg.channels.isHidden()
    dlg.add()                                    # nothing to add from a failed detection
    assert not engine.calls

    dlg.manual_button.click()
    assert dlg.result() == QDialog.Accepted
    src = [c[1] for c in engine.calls if c[0] == "add_source"][0]
    assert src["kind"] == "seedlink" and src["host"] == "10.8.0.199" and src["port"] == 18000
    assert src["stations"] == [] and src["name"] == "10.8.0.199"


def test_add_dialog_validation_and_retyping(make_engine, qapp, monkeypatch):
    from seiszone.ui.add_source_dialog import AddSourceDialog
    engine = make_engine(with_source=False)
    calls = []
    fake_probe(monkeypatch, seedlink_instrument(), calls)
    dlg = AddSourceDialog(engine, engine.config)
    dlg.show()
    dlg.detect()
    assert dlg.error.isVisible() and "Type the IP address" in dlg.error.text()
    for bad in ("10.8.0.182:abc", "10.8.0.182:70000", "not an address!"):
        dlg.edit.setText(bad)
        dlg.detect()
        assert dlg.error.isVisible() and "Type it like 10.8.0.182:18000" in dlg.error.text(), bad
    assert not calls and dlg.state == "idle"

    detect(qapp, dlg, "10.8.0.182:18000")
    assert dlg.state == "found" and not dlg.error.isVisible()
    QTest.keyClicks(dlg.edit, "1")               # a changed address: the old result is dropped
    assert dlg.state == "idle" and not dlg.add_button.isEnabled() and dlg.card.isHidden()
    dlg._use_example("rtserve.iris.washington.edu:18000")
    assert dlg.edit.text() == "rtserve.iris.washington.edu:18000"
    dlg.reject()


def test_add_dialog_closed_while_detecting(make_engine, qapp, monkeypatch):
    from seiszone.ui import add_source_dialog as mod
    engine = make_engine(with_source=False)
    inst = seedlink_instrument()

    def slow_probe(text, log=None, prefer_direct=True, need_xml=None):
        time.sleep(0.3)
        return inst
    monkeypatch.setattr(probe_mod, "probe", slow_probe)
    dlg = mod.AddSourceDialog(engine, engine.config, initial_text="10.8.0.182")
    dlg.show()
    dlg.detect()
    assert dlg.state == "detecting" and not dlg.edit.isEnabled() and dlg.busy.isVisible()
    thread = dlg._thread
    dlg.reject()                                 # must not wait for, or destroy, the running probe
    assert thread in mod._RUNNING
    assert wait_for(qapp, thread.isFinished)
    pump(qapp, 0.1)
    assert dlg.inst is None and not engine.calls


# ------------------------------------------------------------------------------------------
# recordings
# ------------------------------------------------------------------------------------------

def test_record_dialog_start_and_stop(make_engine, qapp, monkeypatch, tmp_path):
    from seiszone.ui.record_dialog import RecordDialog
    engine = make_engine()
    folder = str(tmp_path / "rec")
    dlg = RecordDialog(engine, engine.config, sids=["WB.KUM.0J.HNZ"])
    assert dlg.ticked() == ["WB.KUM.0J.HNZ"]
    assert dlg.all_radio.isChecked() and not dlg.channel_list.isEnabled()
    assert dlg.channel_list.count() == len(engine.hub.stream_ids())
    assert dlg.empty.isVisible() or not dlg.isVisible()
    assert "Free space" in dlg.disk.text()
    assert dlg.minutes.currentData() == 60 and dlg.layout_box.currentData() == "channel"

    dlg.chosen_radio.setChecked(True)
    dlg._tick(False)
    dlg.folder.setText(folder)
    assert dlg.start_recording() is None and "Tick at least one channel" in dlg.message.text()
    assert engine.recorder.sessions() == []
    dlg._tick(True)
    dlg.name.setText("Quake watch")
    dlg.minutes.setCurrentIndex(dlg.minutes.findData(15))
    dlg.layout_box.setCurrentIndex(dlg.layout_box.findData("station"))
    rid = dlg.start_recording()
    [s] = engine.recorder.sessions()
    assert s["id"] == rid and s["name"] == "Quake watch" and s["folder"] == folder
    assert s["sids"] == sorted(engine.hub.stream_ids())
    assert s["file_minutes"] == 15 and s["layout"] == "station"
    assert dlg.table.topLevelItemCount() == 1 and dlg.selected_id() == rid
    assert dlg.table.topLevelItem(0).text(1) == "recording"
    assert dlg.stop_button.isEnabled()

    dlg.all_radio.setChecked(True)
    rid2 = dlg.start_recording()
    assert engine.recorder.sessions()[1]["sids"] is None       # all channels, also later ones
    dlg._select(rid2)

    os.makedirs(SCREENS, exist_ok=True)
    dlg.show()
    pump(qapp, 0.2)
    assert dlg.grab().save(os.path.join(SCREENS, "record.png"))

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    assert not dlg.stop_selected()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    dlg._select(rid)
    assert dlg.stop_selected()
    states = {s["id"]: s["state"] for s in engine.recorder.sessions()}
    assert states == {rid: "stopped", rid2: "recording"}
    dlg._select(rid)
    assert not dlg.stop_button.isEnabled() and dlg.open_button.isEnabled()
    dlg.reject()


def test_record_dialog_shows_disk_warning(make_engine, qapp):
    from seiszone.ui.record_dialog import RecordDialog
    engine = make_engine()
    engine.recorder.disk_status = lambda folder: (2 * 1024 ** 3, "Low disk space: 2.0 GB free.")
    dlg = RecordDialog(engine, engine.config)
    assert dlg.disk.text() == "Low disk space: 2.0 GB free."
    assert theme.COLORS["danger"] in dlg.disk.styleSheet()
    dlg.reject()


# ------------------------------------------------------------------------------------------
# export
# ------------------------------------------------------------------------------------------

def test_export_dialog_runs_the_export_in_a_thread(make_engine, qapp, monkeypatch, tmp_path):
    from seiszone.core import export as export_mod
    from seiszone.ui.export_dialog import ExportDialog
    engine = make_engine()
    engine.recorder.start("old", str(tmp_path / "rec_a"))
    seen = []

    def fake_export(sids, t0, t1, outdir, mode="separate", folders=None, hub=None, log=print):
        seen.append(dict(sids=sids, t0=t0, t1=t1, outdir=outdir, mode=mode, folders=folders, hub=hub))
        log("reading recordings ...")
        os.makedirs(outdir, exist_ok=True)
        files = [os.path.join(outdir, f"{s}.mseed") for s in sids]
        info = os.path.join(outdir, "export_info__20261108T000000Z.txt")
        for p in files + [info]:
            with open(p, "wb") as fh:
                fh.write(b"x")
        return files + [info], "REPORT: 2 channels, no gaps"
    monkeypatch.setattr(export_mod, "export_window", fake_export)

    t1 = int(time.time()) - 5 + 0.4                  # the view's range has fractions of a second
    dlg = ExportDialog(engine, engine.config, sids=["WB.KUM.0J.HNZ", "WB.KUM.0J.HNN"], t0=t1 - 120, t1=t1)
    assert sorted(dlg.ticked()) == ["WB.KUM.0J.HNN", "WB.KUM.0J.HNZ"]
    assert dlg.outdir.text() == paths.exports_dir()
    w0, w1 = dlg.window()
    assert (w0, w1) == (math.floor(t1 - 120), math.ceil(t1))   # whole seconds covering the view
    assert dlg.length.text() == "Length: 2 min 1 s"

    out = str(tmp_path / "out")
    dlg.outdir.setText(out)
    dlg.combined.setChecked(True)
    dlg.export_button.click()
    assert wait_for(qapp, lambda: not dlg.is_running())
    [kw] = seen
    assert sorted(kw["sids"]) == ["WB.KUM.0J.HNN", "WB.KUM.0J.HNZ"] and kw["outdir"] == out
    assert kw["mode"] == "combined" and kw["hub"] is engine.hub
    assert kw["folders"] == [str(tmp_path / "rec_a"), engine.config.get("recording", "folder")]
    assert (kw["t0"], kw["t1"]) == pytest.approx((w0, w1))
    text = dlg.text.toPlainText()
    assert "reading recordings ..." in text and "REPORT: 2 channels, no gaps" in text
    assert "Done: 2 miniSEED file(s)" in dlg.message.text()
    assert dlg.export_button.isEnabled()

    dlg.set_last(3600)
    assert dlg.length.text() == "Length: 1 h"
    dlg.view_button.click()
    assert dlg.window() == pytest.approx((w0, w1), abs=0.002)

    # checks before the export starts, in plain words
    dlg._tick(False)
    dlg.export()
    assert "Tick at least one channel" in dlg.message.text() and len(seen) == 1
    dlg._tick(True)
    dlg.set_window(t1, t1 - 10)
    dlg.export()
    assert "end of the window must be after its start" in dlg.message.text() and len(seen) == 1
    dlg.reject()


def test_export_dialog_offers_recorded_channels_of_an_offline_station(make_engine, qapp, tmp_path):
    from seiszone.core import mseed, recorder
    from seiszone.ui.export_dialog import ID_ROLE, ExportDialog
    engine = make_engine()
    folder = str(tmp_path / "rec")
    engine.config.set("recording", "folder", folder)
    t0 = int(time.time()) - 3 * 86400                 # yesterday's event, station offline now
    rec = mseed.encode_records("XX", "OFF", "", "HHZ", 100, t0, list(range(500)))[0]
    path = recorder.file_path(folder, rec.sid, rec.start, 60, "channel")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(rec.raw)
    assert "XX.OFF..HHZ" not in engine.hub.stream_ids()
    dlg = ExportDialog(engine, engine.config, sids=["WB.KUM.0J.HNZ"], t0=t0 - 60, t1=t0 + 60)
    items = {dlg.channel_list.item(i).data(ID_ROLE): dlg.channel_list.item(i)
             for i in range(dlg.channel_list.count())}
    assert "XX.OFF..HHZ" in items and "recorded only" in items["XX.OFF..HHZ"].text()
    assert items["XX.OFF..HHZ"].checkState() == Qt.Unchecked and dlg.ticked() == ["WB.KUM.0J.HNZ"]
    dlg.reject()


def test_export_dialog_reports_a_failed_export(make_engine, qapp, monkeypatch):
    from seiszone.core import export as export_mod
    from seiszone.ui.export_dialog import ExportDialog
    engine = make_engine()

    def broken(*a, **k):
        raise OSError("the folder is read-only")
    monkeypatch.setattr(export_mod, "export_window", broken)
    dlg = ExportDialog(engine, engine.config, sids=["WB.KUM.0J.HNZ"])
    dlg.export()
    assert wait_for(qapp, lambda: not dlg.is_running())
    assert "Export failed: the folder is read-only" in dlg.message.text()
    dlg.reject()


# ------------------------------------------------------------------------------------------
# settings
# ------------------------------------------------------------------------------------------

def test_settings_dialog_validates_ports(make_engine, qapp):
    from seiszone.ui.settings_dialog import TAB_GECKO, TAB_SERVER, SettingsDialog
    engine = make_engine()
    dlg = SettingsDialog(engine, engine.config)
    assert dlg.gecko_ports.text() == "58772" and dlg.server_port.text() == "18000"

    bad_gecko = {"0": "not possible", "70000": "not possible", "abc": "not a number", "": "at least one port",
                 "58772, 58772": "listed twice", "58772;x": "not a number"}
    for text, words in bad_gecko.items():
        dlg.gecko_ports.setText(text)
        dlg.accept()
        assert dlg.result() != QDialog.Accepted, text
        assert not dlg.error.isHidden() and words in dlg.error.text(), (text, dlg.error.text())
        assert dlg.tabs.currentIndex() == TAB_GECKO
    dlg.gecko_ports.setText("58772, 58773")

    for text, words in {"0": "not possible", "70000": "not possible", "18o10": "not a number",
                        "58773": "both the SeedLink server and the Gecko receiver"}.items():
        dlg.tabs.setCurrentIndex(TAB_GECKO)
        dlg.server_port.setText(text)
        dlg.accept()
        assert dlg.result() != QDialog.Accepted, text
        assert words in dlg.error.text(), (text, dlg.error.text())
        assert dlg.tabs.currentIndex() == TAB_SERVER
    dlg.server_port.setText("18005")
    dlg.server_bind.setCurrentText("my pc")
    dlg.accept()
    assert "is not an IP address" in dlg.error.text()
    dlg.server_bind.setCurrentText("0.0.0.0")

    dlg.add_rename_row("XYZ", "SS", "", "")
    dlg.accept()
    assert "not a Gecko serial number" in dlg.error.text()
    dlg.rename.item(0, 0).setText("0000a1b2")
    dlg.rename.item(0, 2).setText("TOOLONG")
    dlg.accept()
    assert "1-5 letters or digits" in dlg.error.text()
    dlg.rename.item(0, 2).setText("smr3")
    dlg.rename.item(0, 3).setText("--")
    assert dlg.error.isHidden()                                   # out of date once the user edits
    assert not any(c[0] == "apply_settings" for c in engine.calls)
    assert engine.config.get("gecko", "ports") == [58772]        # nothing saved while a field is wrong

    os.makedirs(SCREENS, exist_ok=True)
    dlg.tabs.setCurrentIndex(TAB_GECKO)
    dlg.show()
    pump(qapp, 0.2)
    assert dlg.grab().save(os.path.join(SCREENS, "settings.png"))

    dlg.buffer.setValue(120)
    dlg.accept()
    assert dlg.result() == QDialog.Accepted
    assert ("apply_settings",) in engine.calls
    cfg = Config(engine.config.path)                              # saved to disk
    assert cfg.get("gecko", "ports") == [58772, 58773]
    assert cfg.get("gecko", "rename") == {"0000A1B2": {"net": "SS", "sta": "SMR3", "loc": ""}}
    assert cfg.get("seedlink_server", "port") == 18005 and cfg.get("buffer_minutes") == 120
    assert cfg.get("ui", "theme") == "dark"


def test_settings_dialog_rename_table_round_trip(make_engine, qapp):
    from seiszone.ui.settings_dialog import SettingsDialog, parse_ports, rename_from_rows
    engine = make_engine()
    engine.config.update_section("gecko", {"rename": {"0000A1B2": {"net": "SS", "sta": "SMR3", "loc": ""}}})
    dlg = SettingsDialog(engine, engine.config)
    assert dlg.rename_rows() == [("0000A1B2", "SS", "SMR3", "--")]
    assert rename_from_rows(dlg.rename_rows()) == {"0000A1B2": {"net": "SS", "sta": "SMR3", "loc": ""}}
    dlg.rename.selectRow(0)
    dlg._remove_rename_rows()
    assert dlg.rename_rows() == []
    assert parse_ports(" 58772 ,58773,") == [58772, 58773]
    dlg.reject()
    assert not any(c[0] == "apply_settings" for c in engine.calls)
