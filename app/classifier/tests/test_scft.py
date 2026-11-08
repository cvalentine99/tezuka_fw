"""Cross-language SCFT tests: files written by the real Python writer are
loaded by the real C parser (scft_check links scft.c), and malformed
files get the same verdict from both sides."""
import hashlib
import math
import struct

import pytest

from conftest import run_json

LABELS = ["FM Broadcast 12/34+x", 'Quote " back\\slash', "Émetteur ✓ français", "DAB+", "a/b+c d"]


def shape(n, k):
    # a smooth bump that differs per k, normalized by the writer's helper
    return [math.exp(-((i - n / 2 - 7 * k) / (n / 10 + k)) ** 2) for i in range(n)]


def make_templates(tf, n, with_ranges=False, with_hz=False, labels=LABELS):
    out = []
    for k, lab in enumerate(labels):
        fr = None
        if with_ranges:
            fr = tf.FeatureRanges(bandwidth_frac=(0.01, 0.2), papr_db=tf.FeatureRanges.UNBOUNDED,
                                  flatness=(0.0, 0.9), peak_count=(1.0, 3.0))
        out.append(tf.Template(label=lab, template_id=k + 1, data=tf.normalize(shape(n, k)),
                               feature_ranges=fr, hz_per_bin=(100.0 + k) if with_hz else 0.0))
    return out


@pytest.mark.parametrize("ver", [1, 2, 3])
def test_roundtrip_python_writer_to_c_loader(tmp_path, tf, host_tools, ver):
    n = 64
    tpls = make_templates(tf, n, with_ranges=(ver >= 2), with_hz=(ver == 3))
    path = tmp_path / f"v{ver}.bin"
    tf.write_templates(str(path), n, tpls)
    rc, rep = run_json(host_tools["scft_check"], path)
    assert rc == 0, rep
    assert rep["valid"] and rep["status"] == "ok" and rep["version"] == ver
    assert rep["count"] == len(tpls) and rep["canonical_n"] == n
    assert rep["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert rep["size_bytes"] == path.stat().st_size
    for t, got in zip(tpls, rep["templates"]):
        assert got["label"] == t.label            # spaces, +, /, quotes, backslash, UTF-8 all survive
        assert got["id"] == t.template_id
        assert got["has_gate"] is (ver >= 2)
        assert got["l2"] == pytest.approx(1.0, abs=1e-6)
        assert got["data_head"] == pytest.approx(t.data[:4], abs=1e-6)
        assert got["hz_per_bin"] == pytest.approx(t.hz_per_bin, abs=1e-6)
        if ver >= 2:
            assert got["ranges"]["papr_db"] is None                      # unbounded -> null (hi < lo convention kept)
            assert got["ranges"]["bandwidth_frac"] == pytest.approx([0.01, 0.2], abs=1e-6)
    # and the Python reader agrees with itself
    n2, back = tf.read_templates(str(path))
    assert n2 == n and [t.label for t in back] == [t.label for t in tpls]


def test_stock_bundle_is_valid_on_both_sides(tf, host_tools, stock_bundle):
    rc, rep = run_json(host_tools["scft_check"], stock_bundle)
    assert rc == 0 and rep["valid"] and rep["version"] == 3 and rep["count"] == 3
    assert [t["label"] for t in rep["templates"]] == ["FM Broadcast", "DAB+", "CW / Beacon"]
    py = tf.validate_file(str(stock_bundle))
    assert py["valid"] and py["sha256"] == rep["sha256"] and py["labels"] == [t["label"] for t in rep["templates"]]


def test_label_truncation_is_utf8_boundary_safe(tmp_path, tf, host_tools):
    label = "é" * 20                      # 40 bytes of UTF-8; 31 fits 15 chars + 1 stray byte
    tf.write_templates(str(tmp_path / "t.bin"), 8, [tf.Template(label, 1, tf.normalize([1, 2, 3, 4, 5, 6, 7, 9]))])
    rc, rep = run_json(host_tools["scft_check"], tmp_path / "t.bin")
    assert rc == 0 and rep["templates"][0]["label"] == "é" * 15


@pytest.mark.parametrize("bad", ["", "a\nb", "tab\there", "del\x7f", "nul\x00x"])
def test_writer_refuses_bad_labels(tmp_path, tf, bad):
    with pytest.raises(tf.TemplateFormatError) as e:
        tf.write_templates(str(tmp_path / "x.bin"), 8, [tf.Template(bad, 1, tf.normalize([1, 2, 3, 4, 5, 6, 7, 9]))])
    assert e.value.status == "bad_label"


def test_writer_refuses_duplicate_ids_and_unnormalized(tmp_path, tf):
    v = tf.normalize([1, 2, 3, 4, 5, 6, 7, 9])
    with pytest.raises(tf.TemplateFormatError) as e:
        tf.write_templates(str(tmp_path / "x.bin"), 8, [tf.Template("a", 1, v), tf.Template("b", 1, v)])
    assert e.value.status == "duplicate_id"
    with pytest.raises(tf.TemplateFormatError) as e:
        tf.write_templates(str(tmp_path / "x.bin"), 8, [tf.Template("a", 1, [1.0] * 8)])
    assert e.value.status == "not_normalized"


# --- malformed files: C and Python must agree on the status -----------------

def good_v3(tf, tmp_path, n=16, count=2):
    tpls = make_templates(tf, n, with_ranges=True, with_hz=True, labels=["one", "two", "three"][:count])
    p = tmp_path / "good.bin"
    tf.write_templates(str(p), n, tpls)
    return bytearray(p.read_bytes()), n


def put_f32(buf, off, v):
    buf[off:off + 4] = struct.pack("<f", v)


def entry_offset(n, i, version=3):
    entry = 32 + 4 + 4 * n + (32 if version >= 2 else 0) + (4 if version >= 3 else 0)
    return 16 + i * entry


CASES = {
    "truncated": lambda b, n: (b[:-5], "bad_size"),
    "trailing_byte": lambda b, n: (b + b"\0", "bad_size"),
    "bad_magic": lambda b, n: (b"SCFX" + b[4:], "bad_magic"),
    "version_4": lambda b, n: (b[:4] + struct.pack("<I", 4) + b[8:], "unsupported_version"),
    "version_0": lambda b, n: (b[:4] + struct.pack("<I", 0) + b[8:], "unsupported_version"),
    "count_0": lambda b, n: (b[:8] + struct.pack("<I", 0) + b[12:], "bad_header"),
    "count_huge": lambda b, n: (b[:8] + struct.pack("<I", 1 << 31) + b[12:], "bad_header"),
    "canonical_1": lambda b, n: (b[:12] + struct.pack("<I", 1) + b[16:], "bad_header"),
    "canonical_huge": lambda b, n: (b[:12] + struct.pack("<I", (1 << 20) + 1) + b[16:], "bad_header"),
    "size_mismatch_header": lambda b, n: (b[:12] + struct.pack("<I", n + 1) + b[16:], "bad_size"),
    "header_only": lambda b, n: (b[:16], "bad_size"),
    "tiny": lambda b, n: (b[:7], "bad_size"),
}


def _mutate(name, buf, n):
    buf = bytearray(buf)
    if name == "dup_id":
        o0, o1 = entry_offset(n, 0) + 32, entry_offset(n, 1) + 32
        buf[o1:o1 + 4] = buf[o0:o0 + 4]
        return bytes(buf), "duplicate_id"
    if name == "nan_sample":
        put_f32(buf, entry_offset(n, 1) + 36 + 4 * 3, float("nan")); return bytes(buf), "non_finite_data"
    if name == "inf_sample":
        put_f32(buf, entry_offset(n, 0) + 36, float("inf")); return bytes(buf), "non_finite_data"
    if name == "unnormalized":
        put_f32(buf, entry_offset(n, 0) + 36, 5.0); return bytes(buf), "not_normalized"
    if name == "nan_range":
        put_f32(buf, entry_offset(n, 0) + 36 + 4 * n + 8, float("nan")); return bytes(buf), "bad_range"
    if name == "neg_hz":
        put_f32(buf, entry_offset(n, 0) + 36 + 4 * n + 32, -1.0); return bytes(buf), "bad_resolution"
    if name == "nan_hz":
        put_f32(buf, entry_offset(n, 1) + 36 + 4 * n + 32, float("nan")); return bytes(buf), "bad_resolution"
    if name == "huge_hz":
        put_f32(buf, entry_offset(n, 1) + 36 + 4 * n + 32, 2e9); return bytes(buf), "bad_resolution"
    if name == "label_no_nul":
        o = entry_offset(n, 0); buf[o:o + 32] = b"x" * 32; return bytes(buf), "bad_label"
    if name == "label_empty":
        o = entry_offset(n, 0); buf[o:o + 32] = b"\0" * 32; return bytes(buf), "bad_label"
    if name == "label_control":
        o = entry_offset(n, 0); buf[o:o + 32] = b"ab\x01c".ljust(32, b"\0"); return bytes(buf), "bad_label"
    if name == "label_del":
        o = entry_offset(n, 0); buf[o:o + 32] = b"ab\x7fc".ljust(32, b"\0"); return bytes(buf), "bad_label"
    if name == "label_bad_utf8":
        o = entry_offset(n, 0); buf[o:o + 32] = b"ab\xffc".ljust(32, b"\0"); return bytes(buf), "bad_label"
    if name == "label_overlong":
        o = entry_offset(n, 0); buf[o:o + 32] = b"ab\xc0\xafc".ljust(32, b"\0"); return bytes(buf), "bad_label"
    if name == "label_surrogate":
        o = entry_offset(n, 0); buf[o:o + 32] = b"ab\xed\xa0\x80c".ljust(32, b"\0"); return bytes(buf), "bad_label"
    if name == "label_truncated_seq":
        o = entry_offset(n, 0); buf[o:o + 32] = b"ab\xe2\x82".ljust(32, b"\0"); return bytes(buf), "bad_label"
    out, status = CASES[name](bytes(buf), n)
    return bytes(out), status


ALL = list(CASES) + ["dup_id", "nan_sample", "inf_sample", "unnormalized", "nan_range", "neg_hz", "nan_hz", "huge_hz",
                     "label_no_nul", "label_empty", "label_control", "label_del", "label_bad_utf8", "label_overlong",
                     "label_surrogate", "label_truncated_seq"]


@pytest.mark.parametrize("name", ALL)
def test_malformed_rejected_identically(tmp_path, tf, host_tools, name):
    good, n = good_v3(tf, tmp_path)
    data, expected = _mutate(name, good, n)
    p = tmp_path / f"{name}.bin"
    p.write_bytes(data)
    rc, rep = run_json(host_tools["scft_check"], p)
    assert rc == 2, rep
    assert rep["valid"] is False and rep["status"] == expected, rep
    with pytest.raises(tf.TemplateFormatError) as e:
        tf.parse_templates(data)
    assert e.value.status == expected
    assert tf.validate_file(str(p))["status"] == expected


def test_good_file_sanity(tmp_path, tf, host_tools):
    good, n = good_v3(tf, tmp_path)
    p = tmp_path / "g.bin"
    p.write_bytes(good)
    rc, rep = run_json(host_tools["scft_check"], p)
    assert rc == 0 and rep["valid"]


def test_size_cap_rejected_before_parse(tmp_path, tf, host_tools):
    p = tmp_path / "big.bin"
    p.write_bytes(b"SCFT" + b"\0" * ((4 << 20) + 100))
    rc, rep = run_json(host_tools["scft_check"], p)
    assert rc == 2 and rep["status"] == "bad_size"
    assert tf.validate_file(str(p))["status"] == "bad_size"


def test_v1_file_from_old_writer_still_loads(tmp_path, tf, host_tools):
    """A hand-built version-1 image (the pre-ranges layout) loads with
    has_gate false and no ranges, exactly as before."""
    n = 8
    v = tf.normalize([1, 2, 3, 4, 5, 6, 7, 9])
    buf = b"SCFT" + struct.pack("<III", 1, 1, n) + b"legacy".ljust(32, b"\0") + struct.pack("<I", 42) + struct.pack(f"<{n}f", *v)
    p = tmp_path / "v1.bin"
    p.write_bytes(buf)
    rc, rep = run_json(host_tools["scft_check"], p)
    assert rc == 0 and rep["version"] == 1 and rep["templates"][0]["has_gate"] is False and "ranges" not in rep["templates"][0]
    assert rep["templates"][0]["id"] == 42 and rep["templates"][0]["hz_per_bin"] == 0
