"""rf_compare.py tallying without a daemon: dispositions of unexpected
accepts, trusted positives (--expect), label validation and the results.md
cells, on synthetic result rows.

Cases from the closure review of a9e7115 and its verification: a software
fixture keeps its confirmed-by-construction default after --events; --expect
works on a recorded capture that also has --events; stale 'false accept'
wording; labels are checked against the template set; recognised and misses
of a whole-capture positive reach the table.
"""
import os
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import rf_compare as rc  # noqa: E402

LABELS = {"FM Broadcast", "DAB+", "CW / Beacon"}


def result(t, label, conf=0.9):
    return (t, {"label": label, "confidence": conf})


def rows(results):
    return {"result": results, "observation": [], "background": []}


def software_spec(**over):
    spec = {"name": "sw", "kind": "software fixture", "software_emitter": True, "on_window_s": [20.0, 60.0],
            "expected_on": ["FM Broadcast"], "expected_off": [], "condition": "synthetic", "source": "test"}
    spec.update(over)
    return spec


def recorded_spec(**over):
    spec = {"name": "rec", "kind": "recorded", "recorded_capture": True, "software_emitter": False,
            "on_window_s": None, "expected_on": [], "expected_off": [], "condition": "as captured", "source": "test"}
    spec.update(over)
    return spec


def test_software_fixture_unexpected_accepts_are_confirmed_by_construction():
    t = rc.tally(software_spec(), rows([result(30, "FM Broadcast"), result(31, "DAB+", 0.7), result(70, "CW / Beacon", 0.8)]))
    assert t["on"]["recognized"] == 1 and t["on"]["confirmed_false_accepts"] == 1
    assert t["on"]["unexpected_accepts_by_label"]["DAB+"]["disposition"] == "confirmed_false"
    assert t["off"]["confirmed_false_accepts"] == 1 and t["off"]["false_accepts"] == 1
    assert t["off"]["unverified_accepts"] == 0 and t["off"]["suspected_false_accepts"] == 0


def test_software_fixture_keeps_its_default_after_events_are_attached(tmp_path):
    ev = tmp_path / "ev.events"
    ev.write_text("0 state/rx/frequency 1090000000\n")
    (tmp_path / "raw").mkdir()
    specs = [software_spec()]
    rc.attach_events(specs, f"sw={ev}", str(tmp_path / "raw"))
    assert specs[0]["kind"].endswith("+ scripted events")
    assert rc.disposition_of(specs[0], "DAB+")[0] == "confirmed_false"


def test_recorded_capture_defaults_to_unverified_and_counts_add_up():
    t = rc.tally(recorded_spec(), rows([result(13, "FM Broadcast", 0.65), result(14, "unknown", 0), result(15, "DAB+", 0.86)]))
    off = t["off"]
    assert off["decisions"] == 3 and off["unexpected_accepts"] == 2
    assert off["confirmed_false_accepts"] == 0 and off["false_accepts"] == 0
    assert off["unverified_accepts"] == 2 and off["suspected_false_accepts"] == 0
    assert off["unexpected_accepts"] == off["confirmed_false_accepts"] + off["suspected_false_accepts"] + off["unverified_accepts"]
    assert off["worst_score_by_disposition"] == {"confirmed_false": None, "suspected_false": None, "unverified": 0.86}
    assert off["first_false_accept"] is None and off["first_unexpected_accept"]["t"] == 13


def test_accept_dispositions_split_the_counts():
    specs = [recorded_spec()]
    rc.attach_accept(specs, "rec=confirmed_false=FM Broadcast::follows the LO", LABELS)
    rc.attach_accept(specs, "rec=suspected_false=DAB+::no allocation here", LABELS)
    t = rc.tally(specs[0], rows([result(13, "FM Broadcast", 0.65), result(14, "DAB+", 0.86), result(15, "CW / Beacon", 0.99)]))
    off = t["off"]
    assert (off["confirmed_false_accepts"], off["suspected_false_accepts"], off["unverified_accepts"]) == (1, 1, 1)
    assert off["false_accepts"] == 1 and off["false_accept_labels"] == {"FM Broadcast": 1}
    by = off["unexpected_accepts_by_label"]
    assert by["FM Broadcast"]["basis"] == "follows the LO" and by["DAB+"]["disposition"] == "suspected_false"
    assert by["CW / Beacon"]["disposition"] == "unverified"
    assert specs[0]["accept_dispositions"]["DAB+"]["basis"] == "no allocation here"


def test_accept_rejects_bad_disposition_unknown_fixture_and_unknown_label():
    specs = [recorded_spec()]
    with pytest.raises(SystemExit):
        rc.attach_accept(specs, "rec=maybe_false=DAB+", LABELS)
    with pytest.raises(SystemExit):
        rc.attach_accept(specs, "nope=unverified=DAB+", LABELS)
    with pytest.raises(SystemExit, match="not in the template set"):
        rc.attach_accept(specs, "rec=confirmed_false=FM Broadcst::typo", LABELS)
    with pytest.raises(SystemExit):
        rc.attach_accept(specs, "rec=confirmed_false", LABELS)


def test_expect_declares_a_trusted_positive_also_with_events(tmp_path):
    ev = tmp_path / "ev.events"
    ev.write_text("0 state/rx/frequency 98000000\n")
    (tmp_path / "raw").mkdir()
    specs = [recorded_spec()]
    rc.attach_events(specs, f"rec={ev}", str(tmp_path / "raw"))
    rc.attach_expect(specs, "rec=FM Broadcast", LABELS)
    assert specs[0]["expected_off"] == ["FM Broadcast"]
    t = rc.tally(specs[0], rows([result(13, "FM Broadcast", 0.9), result(14, "FM Broadcast", 0.8),
                                 result(15, "unknown", 0), result(16, "DAB+", 0.7)]))
    off = t["off"]
    assert off["recognized"] == 2 and off["misses"] == 2 and off["min_correct_score"] == 0.8
    assert off["unexpected_accepts"] == 1 and off["unverified_accepts"] == 1


def test_expect_rejects_software_fixtures_and_unknown_labels():
    with pytest.raises(SystemExit, match="not a recorded capture"):
        rc.attach_expect([software_spec()], "sw=FM Broadcast", LABELS)
    with pytest.raises(SystemExit, match="not in the template set"):
        rc.attach_expect([recorded_spec()], "rec=FM", LABELS)


def test_recorded_fixture_basis_text_matches_the_tally():
    """Recorded frames carry no proof of what was on air, so their manifest
    text must not promise that every accept is tallied as false (software
    fixtures may say so: their emitter is known)."""
    import inspect
    src = inspect.getsource(rc)
    assert "every accepted label is tallied as a false accept" not in src
    assert "DC spur: any accepted label is a false accept" not in src
    assert "as an unexpected accept, unverified unless --accept" in src
