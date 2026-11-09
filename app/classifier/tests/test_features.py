"""features.c (via features_check) against features.py on a recorded frame
and on synthetic frames with known power ratios."""
import math
import struct

import pytest

from conftest import run_json


def write_frame(path, values):
    path.write_bytes(struct.pack(f"<{len(values)}f", *values))


def compare(host_tools, pyfeatures, path, values, skip_first=False):
    args = [path, "--skip-first"] if skip_first else [path]
    rc, c = run_json(host_tools["features_check"], *args)
    assert rc == 0, c
    py = pyfeatures.extract_frame_features(values[1:] if skip_first else values)
    assert c["valid"] == py.valid
    assert c["semantics"] == "power-v1"
    if py.valid:
        assert c["bandwidth_frac"] == pytest.approx(py.bandwidth_frac, abs=1e-9)
        assert c["papr_db"] == pytest.approx(py.papr_db, abs=1e-4)
        assert c["flatness"] == pytest.approx(py.flatness, abs=1e-6)
        assert c["peak_count"] == py.peak_count
        assert c["peak_bin"] == py.peak_bin
    return c, py


def test_recorded_frame_agrees(host_tools, pyfeatures, frame_fixture):
    raw = frame_fixture.read_bytes()
    vals = list(struct.unpack(f"<{len(raw) // 4}f", raw))
    c, py = compare(host_tools, pyfeatures, frame_fixture, vals, skip_first=True)
    assert c["n"] == 4095 and c["peak_bin"] == 2047     # the DC spur of the real capture
    assert c["peak_count"] == 1


def test_known_ratio_single_bump(tmp_path, host_tools, pyfeatures):
    n = 1000
    vals = [1.0] * n
    bump = [10, 30, 60, 90, 100, 90, 60, 30, 10]
    for k, v in enumerate(bump):
        vals[500 + k] = v
    p = tmp_path / "bump.bin"
    write_frame(p, vals)
    c, _ = compare(host_tools, pyfeatures, p, vals)
    mean = sum(vals) / n
    assert c["papr_db"] == pytest.approx(10 * math.log10(100 / mean), abs=1e-4)    # ~ 18.4 dB
    assert c["bandwidth_frac"] == pytest.approx(9 / n)                              # all 9 bump bins are >= peak/10
    assert c["peak_count"] == 1 and c["peak_bin"] == 504
    geo = math.exp(sum(math.log(v) for v in vals) / n)
    assert c["flatness"] == pytest.approx(geo / mean, abs=1e-6)


def test_known_ratio_comb_counts_peaks(tmp_path, host_tools, pyfeatures):
    n = 1024
    vals = [1.0] * n
    for c0 in (100, 300, 500, 700):      # separated by far more than n/64 = 16 bins
        vals[c0 - 1], vals[c0], vals[c0 + 1] = 50.0, 100.0, 50.0
    vals[702] = 0.0                      # a zero bin: must not break the log/geomean
    p = tmp_path / "comb.bin"
    write_frame(p, vals)
    c, _ = compare(host_tools, pyfeatures, p, vals)
    assert c["peak_count"] == 4
    assert c["bandwidth_frac"] == pytest.approx(3 / n)                              # -10 dB width of the first peak only


def test_two_close_peaks_dedupe(tmp_path, host_tools, pyfeatures):
    n = 1024
    vals = [1.0] * n
    vals[200], vals[205] = 100.0, 90.0   # 5 bins apart < n/64 = 16 -> one peak
    p = tmp_path / "close.bin"
    write_frame(p, vals)
    c, _ = compare(host_tools, pyfeatures, p, vals)
    assert c["peak_count"] == 1


def test_empty_frame_is_invalid_not_garbage(tmp_path, host_tools, pyfeatures):
    vals = [0.0] * 64
    p = tmp_path / "zero.bin"
    write_frame(p, vals)
    c, py = compare(host_tools, pyfeatures, p, vals)
    assert c["valid"] is False and py.valid is False


def test_power_doubling_adds_3db_to_nothing(tmp_path, host_tools, pyfeatures):
    """Features are ratios: scaling the whole frame by 2 changes no feature
    (checks that nothing squares or offsets the power)."""
    n = 512
    base = [1.0 + 0.1 * math.sin(i / 7.0) for i in range(n)]
    base[256] = 40.0
    p1, p2 = tmp_path / "a.bin", tmp_path / "b.bin"
    write_frame(p1, base)
    write_frame(p2, [2 * v for v in base])
    c1, _ = compare(host_tools, pyfeatures, p1, base)
    c2, _ = compare(host_tools, pyfeatures, p2, [2 * v for v in base])
    assert c1["papr_db"] == pytest.approx(c2["papr_db"], abs=1e-4)
    assert c1["flatness"] == pytest.approx(c2["flatness"], abs=1e-6)
    assert c1["bandwidth_frac"] == c2["bandwidth_frac"]
