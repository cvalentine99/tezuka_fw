"""The frame contract against recorded frames (SignalSDR Pro, maia-httpd
spectrometer fft_size 4096, Average mode, nint 1023, fs 2.5 MHz, LO
1.09 GHz — see fixtures/wf_avg4096_meta.json) and against the template
tools' copy of the same module."""
import hashlib
import importlib.util
import math
import sys

import pytest

from tezuka_mcp import frame as F


def test_fixture_integrity(fixture_frames, fixture_meta):
    for raw, meta in zip(fixture_frames, fixture_meta["frames"]):
        assert hashlib.sha256(raw).hexdigest() == meta["sha256"]
        assert len(raw) == meta["bytes"] == 16384


def test_decode_matches_producer_metadata(fixture_frames, fixture_meta):
    spec = fixture_meta["api_before"]["spectrometer"]
    fs = fixture_meta["api_before"]["ad9361"]["sampling_frequency"]
    lo = fixture_meta["api_before"]["ad9361"]["rx_lo_frequency"]
    assert spec["fft_size"] == 4096 and spec["input"] == "AD9361" and spec["mode"] == "Average"
    steps = []
    for raw, meta in zip(fixture_frames, fixture_meta["frames"]):
        fr = F.decode_frame(raw, span_hz=fs, center_hz=lo)
        assert fr.fft_size == 4096 == meta["n_floats"]
        assert len(fr.bins) == 4095
        assert fr.step_word == meta["f0"]
        steps.append(fr.step_word)
        # first float is a small integer (fastlock profile / step), not a power
        assert fr.step_word == int(fr.step_word) and 0 <= fr.step_word <= 7
        assert fr.bins[0] == pytest.approx(meta["f1"]) and fr.bins[1] == pytest.approx(meta["f2"])
        assert min(fr.bins) == pytest.approx(meta["min_tail"]) and max(fr.bins) == pytest.approx(meta["max_tail"])
        # the DC spur is the strongest bin and sits at usable index fft_size/2 - 1
        assert fr.bins.index(max(fr.bins)) == fr.dc_index == 2047
        assert fr.hz_per_bin == pytest.approx(2.5e6 / 4096)
        assert fr.bin_center_hz(fr.dc_index) == pytest.approx(lo)
    # the three consecutive frames carry a cycling step word 0,1,2 even outside sweep mode
    assert steps == [0.0, 1.0, 2.0]


def test_grid_helpers_are_consistent():
    n, fc, span = 4096, 1.09e9, 2.5e6
    assert F.hz_per_bin(span, n) == span / n
    assert F.bins_index_to_hz(n // 2 - 1, n, fc, span) == pytest.approx(fc)
    # first usable bin is one bin above the -fs/2 edge; last is one bin below +fs/2
    assert F.bins_index_to_hz(0, n, fc, span) == pytest.approx(fc - span / 2 + span / n)
    assert F.bins_index_to_hz(n - 2, n, fc, span) == pytest.approx(fc + span / 2 - span / n)
    for j in (0, 1, 2047, 4094):
        assert F.hz_to_bins_index(F.bins_index_to_hz(j, n, fc, span), n, fc, span) == pytest.approx(j)


def test_power_to_db_and_mean_power(fixture_frames):
    frames = [F.decode_frame(r) for r in fixture_frames]
    avg = F.mean_power(frames)
    assert len(avg) == 4095
    # power-domain mean of the fixture frames, not a dB mean
    j = 100
    assert avg[j] == pytest.approx(sum(f.bins[j] for f in frames) / 3)
    assert F.power_to_db(1000.0) == pytest.approx(30.0)
    assert F.power_to_db(0.0) == pytest.approx(-100.0)   # floor, never -inf
    assert math.isfinite(F.power_to_db(-5.0))


def test_matches_template_tools_module(fixture_frames, repo_root):
    """tools/classifier_templates/waterfall_frame.py is the template tools'
    copy of this contract; both must agree exactly."""
    path = repo_root / "tools" / "classifier_templates" / "waterfall_frame.py"
    spec = importlib.util.spec_from_file_location("waterfall_frame", path)
    W = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(W)
    for raw in fixture_frames:
        a = F.decode_frame(raw, 2.5e6, 1.09e9)
        b = W.decode_frame(raw, 2.5e6, 1.09e9)
        assert a.fft_size == b.fft_size and a.step_word == b.step_word and a.bins == b.bins
        assert a.dc_index == b.dc_index and a.hz_per_bin == b.hz_per_bin
        for j in (0, 17, a.dc_index, 4094):
            assert a.bin_center_hz(j) == b.bin_center_hz(j)
    assert W.hz_per_bin(2.5e6, 4096) == F.hz_per_bin(2.5e6, 4096)
    assert W.power_to_db(123.0) == F.power_to_db(123.0)
