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
