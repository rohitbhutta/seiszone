"""installer/SeisZone.iss: SeisZone is closed only once the user has committed (Install clicked,
removal confirmed), so cancelling Setup or the uninstall question leaves it running; the task
that stops and disables the old Gecko SeedLink service (offered only when it is installed)."""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ISS = os.path.join(ROOT, "installer", "SeisZone.iss")


def script():
    with open(ISS, encoding="utf-8-sig") as fh:
        return fh.read()


def code_section():
    text = script()
    return text[text.index("\n[Code]"):]


def section(name):
    text = script()
    start = text.index(f"\n[{name}]") + len(name) + 3
    end = text.find("\n[", start)
    return text[start:end if end >= 0 else None]


def body(code, name):
    m = re.search(rf"(function|procedure) {name}\b.*?\nend;", code, re.S)
    return m.group(0) if m else ""


def test_seiszone_closed_only_after_the_user_committed():
    code = code_section()
    for early in ("InitializeSetup", "InitializeUninstall"):
        assert "CloseSeisZone" not in body(code, early)
    assert "CloseSeisZone" in body(code, "PrepareToInstall")
    late = body(code, "CurUninstallStepChanged")
    assert "usUninstall" in late and "CloseSeisZone" in late
    # a silent update brings it back, as the normal user
    post = body(code, "CurStepChanged")
    assert "ssPostInstall" in post and "WizardSilent" in post and "ExecAsOriginalUser" in post


def test_task_stops_and_disables_the_old_gecko_service():
    tasks = [line for line in section("Tasks").splitlines() if line.startswith("Name: stopoldservice;")]
    assert len(tasks) == 1
    task = tasks[0]
    assert ('Description: "Stop and disable the old Gecko SeedLink service, so your Gecko recorder connects '
            'to SeismoVerse (frees ports 58772 and 18010; the SeismoVerse SeedLink server uses port 18000)"') in task
    assert "Check: OldServiceInstalled" in task
    assert "unchecked" not in task                     # on by default
    code = code_section()
    check = body(code, "OldServiceInstalled")
    assert r"RegKeyExists(HKLM, 'SYSTEM\CurrentControlSet\Services\{#OldService}')" in check
    assert '#define OldService "GeckoSeedLink"' in script()
    stop = body(code, "StopOldService")
    assert stop.index("'stop {#OldService}'") < stop.index("'config {#OldService} start= disabled'")
    assert stop.count(r"{sys}\sc.exe") == 2 and stop.count("SW_HIDE, ewWaitUntilTerminated") == 2
    assert "delete" not in stop.lower()                # stopped and disabled, never removed
    post = body(code, "CurStepChanged")
    assert "WizardIsTaskSelected('stopoldservice')" in post and "StopOldService" in post
    # the firewall rule and the close-SeisZone logic are still there
    run = section("Run")
    assert 'add rule name=""{#AppName}""' in run and 'delete rule name=""{#AppName}""' in run
    assert 'delete rule name=""{#OldName}""' in run     # the rule of the old name goes
    assert "CloseSeisZone" in body(code, "PrepareToInstall")


def test_no_forbidden_names_in_the_installer_and_the_new_texts():
    banned = ("om" + "ega", "gaia" + "code", "kel" + "lith")
    files = [ISS, os.path.join(ROOT, "src", "seiszone", "core", "ports.py"),
             os.path.join(ROOT, "src", "seiszone", "core", "engine.py"),
             os.path.join(ROOT, "src", "seiszone", "ui", "sources_panel.py")]
    for path in files:
        with open(path, encoding="utf-8-sig") as fh:
            text = fh.read().lower()
        for word in banned:
            assert word not in text, f"{word!r} in {path}"
