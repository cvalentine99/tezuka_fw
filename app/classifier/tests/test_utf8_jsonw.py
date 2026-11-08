"""The C UTF-8 validator against Python's strict decoder, and the JSON
writer's escaping/overflow behaviour."""
import json
import subprocess

import pytest

from conftest import run_json

UTF8_CASES = [
    "", "41", "7f", "c3a9", "e282ac", "f09f9880", "f48fbfbf",           # valid: ascii, DEL, é, €, emoji, U+10FFFF
    "c0af", "c1bf", "e080af", "f0808080",                                # overlong
    "eda080", "edbfbf",                                                  # UTF-16 surrogates
    "f4908080", "f5808080", "f8888080",                                  # > U+10FFFF / invalid leads
    "80", "bf", "c3", "e282", "f09f98", "c3c3", "e2a9",                  # stray/truncated continuation
]


@pytest.mark.parametrize("hexs", UTF8_CASES)
def test_utf8_validator_matches_python(host_tools, hexs):
    raw = bytes.fromhex(hexs)
    try:
        raw.decode("utf-8")
        expect = 0
    except UnicodeDecodeError:
        expect = 1
    rc = subprocess.run([str(host_tools["scft_check"]), "--utf8", hexs or "00"], capture_output=True).returncode
    if hexs == "":
        rc = subprocess.run([str(host_tools["scft_check"]), "--utf8", ""], capture_output=True).returncode
    assert rc == expect, f"{hexs}: C said {rc}, Python said {expect}"


@pytest.mark.parametrize("label", [
    'plain', 'FM Broadcast 12/34+x', 'Quote " back\\slash', 'tab\tnl\ncr\r', 'ctrl\x01\x1f', 'del\x7f',
    'Émetteur ✓ 😀', '/leading+and+plus/', '',
])
def test_jsonw_escapes_round_trip(tmp_path, host_tools, label):
    p = tmp_path / "label.bin"
    p.write_bytes(label.encode("utf-8"))
    rc, rep = run_json(host_tools["jsonw_check"], p)
    assert rc == 0, rep
    assert rep["label"] == label
    assert rep["nan"] is None and rep["neg"] == -42 and rep["flag"] is True and rep["nothing"] is None


def test_jsonw_replaces_invalid_utf8(tmp_path, host_tools):
    p = tmp_path / "bad.bin"
    p.write_bytes(b"ab\xffc\xc3\x28d\xed\xa0\x80e")
    rc, rep = run_json(host_tools["jsonw_check"], p)
    assert rc == 0
    assert rep["label"] == "ab�c�(d���e"
    assert "�" in rep["label"] and json.dumps(rep)   # valid JSON, valid Unicode


def test_jsonw_overflow_latches(host_tools):
    out = subprocess.run([str(host_tools["jsonw_check"]), "--overflow", "20"], capture_output=True, text=True).stdout.strip()
    assert out == "overflow"
    out = subprocess.run([str(host_tools["jsonw_check"]), "--overflow", "200"], capture_output=True, text=True).stdout.strip()
    assert out == "ok"


# Independent review 2026-11-08, R2: jw_double formatted into a 64-byte local
# buffer and passed snprintf's *would-be* length to the copier, so a large
# finite value read past the buffer (ASan: READ of size 309 for DBL_MAX).
DOUBLE_CASES = [
    ("1.7976931348623157e308", 0),    # DBL_MAX
    ("-1.7976931348623157e308", 0),
    ("1e300", 17),
    ("1e22", 3),                      # 23 chars with "%.3f": the last size that fits the old buffer
    ("1e40", 17),                     # 59 chars: the first size that did not
    ("433000000", 0),                 # ordinary RF values
    ("2500000", 0),
    ("1090000000", 3),
    ("610.351562", 3),
    ("0.001", 3),
    ("-42.5", 1),
    ("0", 0),
]


@pytest.mark.parametrize("text,decimals", DOUBLE_CASES)
def test_jsonw_double_is_bounded_and_valid_json(host_tools, text, decimals):
    p = subprocess.run([str(host_tools["jsonw_check"]), "--double", text, str(decimals)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    rep = json.loads(p.stdout)
    assert rep["v"] == pytest.approx(float(text), rel=1e-15, abs=10.0 ** -decimals)


@pytest.mark.parametrize("text", ["nan", "inf", "-inf"])
def test_jsonw_double_non_finite_is_null(host_tools, text):
    p = subprocess.run([str(host_tools["jsonw_check"]), "--double", text, "3"], capture_output=True, text=True)
    assert p.returncode == 0 and json.loads(p.stdout) == {"v": None}


@pytest.mark.parametrize("text,decimals,cap", [
    ("1.7976931348623157e308", 0, 8), ("1.7976931348623157e308", 0, 16), ("1.7976931348623157e308", 0, 27),
    ("433000000", 0, 4), ("433000000", 0, 14), ("-1e40", 17, 20), ("0.001", 3, 10),
])
def test_jsonw_double_small_destination_fails_safely(host_tools, text, decimals, cap):
    # {"v":...} needs at least 7 + len(number) bytes: with less the writer
    # must latch overflow (exit 70) and never write past the buffer (71 =
    # canary hit; ASan/UBSan on the host tool would also abort).
    p = subprocess.run([str(host_tools["jsonw_check"]), "--double", text, str(decimals), str(cap)], capture_output=True, text=True)
    assert p.returncode == 70, (p.returncode, p.stdout, p.stderr)
    assert p.stdout == ""


@pytest.mark.parametrize("text,decimals,cap", [("1.7976931348623157e308", 0, 32), ("433000000", 0, 16), ("0.001", 3, 12)])
def test_jsonw_double_just_enough_destination_is_ok(host_tools, text, decimals, cap):
    p = subprocess.run([str(host_tools["jsonw_check"]), "--double", text, str(decimals), str(cap)], capture_output=True, text=True)
    assert p.returncode == 0, (p.returncode, p.stderr)
    assert json.loads(p.stdout)["v"] == pytest.approx(float(text), rel=1e-15, abs=10.0 ** -decimals)
