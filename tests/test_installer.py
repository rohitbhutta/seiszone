"""installer/SeisZone.iss: SeisZone is closed only once the user has committed (Install clicked,
removal confirmed), so cancelling Setup or the uninstall question leaves it running."""

import os
import re

ISS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "installer", "SeisZone.iss")


def code_section():
    with open(ISS, encoding="utf-8-sig") as fh:
        text = fh.read()
    return text[text.index("\n[Code]"):]


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
