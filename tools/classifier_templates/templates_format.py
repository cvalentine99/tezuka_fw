"""templates_format.py — read/write/validate the classifier's templates.bin (SCFT).

This is the dev-machine counterpart to app/classifier/scft.c, the single
parser the on-device daemon, its `classifier -V FILE` validate mode and
the host test tool all use. Keep the two in sync — the format is:

    char     magic[4]      = b"SCFT"
    uint32_t version       = 1, 2 or 3
    uint32_t count         1..MAX_COUNT
    uint32_t canonical_n   MIN_CANONICAL_N..MAX_CANONICAL_N
    then `count` entries of:
        char     label[32]   UTF-8, NUL-terminated inside the 32 bytes
        uint32_t template_id unique within the file
        float32  data[canonical_n]   (little-endian; zero-mean, unit L2)
        -- version >= 2, appended per entry --
        float32  bandwidth_frac_lo, bandwidth_frac_hi
        float32  papr_db_lo,        papr_db_hi
        float32  flatness_lo,       flatness_hi
        float32  peak_count_lo,     peak_count_hi
        -- version >= 3, appended per entry (after the ranges) --
        float32  hz_per_bin          (0 = no resolution, matched bin-for-bin)

The file ends exactly after the last entry: trailing bytes are rejected
(the C loader requires size == header-implied size before allocating).

LABEL CONTRACT (same as scft.h): the bytes before the first NUL are
valid UTF-8, contain no C0 control bytes (0x00-0x1f) or DEL (0x7f), and
are non-empty; at most 31 bytes of text. Spaces, '+', '/', quotes and
backslashes are all fine. write_templates() truncates an over-long label
on a UTF-8 character boundary (never mid-sequence) and refuses control
characters.

A version-3 template records its resolution: the on-device classifier
resamples it onto the live frame's grid (canonical_n * hz_per_bin /
live_hz_per_bin bins) before matching, so the shape covers the same
hertz at any span. Version 1/2 templates have no resolution and are
matched bin-for-bin, which only works at the span they were made for.
hz_per_bin of a live frame is span / fft_size (see waterfall_frame.py).

A version-2 range with hi < lo means "unbounded" (no gate on that
feature) — see FeatureRanges.UNBOUNDED below. Version 1 files behave as
if every range were unbounded: the on-device classifier applies no gate
to them, so old templates.bin files keep classifying exactly as before.

Templates must already be normalized (zero-mean, unit L2 norm, within
NORM_TOL) before writing — the on-device loader REJECTS a file whose
entries are not; it never re-normalizes. Use normalize() below.

Command line:
    python3 templates_format.py validate FILE   # exit 0 valid / 2 invalid
    python3 templates_format.py inspect  FILE   # JSON summary
"""
import json
import math
import struct
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

MAGIC = b"SCFT"
VERSION_1 = 1
VERSION_2 = 2
VERSION_3 = 3
LABEL_LEN = 32
MAX_COUNT = 4096
MIN_CANONICAL_N = 2
MAX_CANONICAL_N = 1 << 20
MAX_FILE_BYTES = 4 << 20
NORM_TOL = 1e-3
MAX_HZ_PER_BIN = 1e9

# hi < lo signals "no gate on this feature" to the on-device loader.
_UNBOUNDED = (1.0, -1.0)


class TemplateFormatError(ValueError):
    """A file (or a template about to be written) violates the SCFT
    contract. `status` mirrors scft_status_name() in scft.c so a Python
    test can compare its verdict with the C loader's."""

    def __init__(self, status: str, message: str):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


@dataclass
class FeatureRanges:
    """Expected (lo, hi) range per feature, for a version-2 template's soft
    match gate (see classifier.c's template_gate_factor()). Any field left
    as UNBOUNDED is not gated. Construct these via from_samples() below
    rather than by hand in normal use."""
    bandwidth_frac: tuple = _UNBOUNDED
    papr_db: tuple = _UNBOUNDED
    flatness: tuple = _UNBOUNDED
    peak_count: tuple = _UNBOUNDED

    UNBOUNDED = _UNBOUNDED

    @staticmethod
    def from_samples(bandwidth_fracs, papr_dbs, flatnesses, peak_counts, margin: float = 0.15):
        """Build a FeatureRanges from one or more observed samples of each
        feature (e.g. per-frame values from several captures, or a single
        synthesized template's own computed features), widened by `margin`
        as a fraction of the observed spread (or of the value itself, for a
        single sample) so a range built from a handful of samples isn't
        pathologically tight."""
        def widen(values):
            lo, hi = min(values), max(values)
            pad = (hi - lo) * margin if hi > lo else max(abs(lo), 1e-6) * margin
            return (lo - pad, hi + pad)

        return FeatureRanges(
            bandwidth_frac=widen(bandwidth_fracs),
            papr_db=widen(papr_dbs),
            flatness=widen(flatnesses),
            peak_count=widen(peak_counts),
        )

    def as_tuples(self) -> List[Tuple[float, float]]:
        return [tuple(self.bandwidth_frac), tuple(self.papr_db), tuple(self.flatness), tuple(self.peak_count)]


@dataclass
class Template:
    label: str
    template_id: int
    data: Sequence[float]  # length must equal canonical_n for the file
    feature_ranges: Optional[FeatureRanges] = field(default=None)
    hz_per_bin: float = 0.0  # version 3; 0 = no resolution


def normalize(data: Sequence[float]) -> list:
    """Zero-mean, unit-L2-norm — matches classifier.c's normalize() exactly.
    Raises ValueError on a (near-)zero-energy input, same as the C code
    treating that as "nothing to classify" rather than silently producing
    a divide-by-zero or NaN template."""
    n = len(data)
    if n == 0:
        raise ValueError("empty template data")
    mean = sum(data) / n
    centered = [v - mean for v in data]
    energy = sum(v * v for v in centered)
    if energy < 1e-12:
        raise ValueError("template has ~zero energy after mean removal")
    inv_norm = energy ** -0.5
    return [v * inv_norm for v in centered]


# --------------------------------------------------------------------------
# Label contract
# --------------------------------------------------------------------------

def check_label_bytes(raw: bytes) -> str:
    """Validate the text bytes of a label (what sits before the NUL) and
    return it decoded. Raises TemplateFormatError('bad_label')."""
    if len(raw) == 0:
        raise TemplateFormatError("bad_label", "label is empty")
    if len(raw) > LABEL_LEN - 1:
        raise TemplateFormatError("bad_label", f"label is {len(raw)} bytes, max {LABEL_LEN - 1}")
    for b in raw:
        if b < 0x20 or b == 0x7F:
            raise TemplateFormatError("bad_label", f"label contains control byte 0x{b:02x}")
    try:
        # Python's strict decoder rejects overlongs, surrogates and > U+10FFFF,
        # the same set scft_utf8_valid() rejects.
        return raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise TemplateFormatError("bad_label", f"label is not valid UTF-8 ({e.reason})")


def encode_label(label: str) -> bytes:
    """Encode a label for writing: UTF-8, truncated on a character boundary
    to LABEL_LEN-1 bytes, NUL-padded to LABEL_LEN. Refuses control
    characters and an empty result."""
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in label):
        raise TemplateFormatError("bad_label", f"label {label!r} contains a control character")
    try:
        raw = label.encode("utf-8")
    except UnicodeEncodeError:
        raise TemplateFormatError("bad_label", f"label {label!r} contains a lone surrogate")
    if len(raw) > LABEL_LEN - 1:
        cut = LABEL_LEN - 1
        # back up to the start of the UTF-8 sequence that straddles the cut
        while cut > 0 and (raw[cut] & 0xC0) == 0x80:
            cut -= 1
        raw = raw[:cut]
    if not raw:
        raise TemplateFormatError("bad_label", "label is empty")
    return raw.ljust(LABEL_LEN, b"\x00")


# --------------------------------------------------------------------------
# Writer
# --------------------------------------------------------------------------

def _check_vector(label: str, data: Sequence[float]) -> None:
    for i, v in enumerate(data):
        if not math.isfinite(v):
            raise TemplateFormatError("non_finite_data", f"template {label!r} sample {i} is not finite")
    n = len(data)
    mean = sum(data) / n
    l2 = math.sqrt(sum(float(v) * float(v) for v in data))
    if abs(mean) > NORM_TOL or abs(l2 - 1.0) > NORM_TOL:
        raise TemplateFormatError("not_normalized",
                                  f"template {label!r} is not normalized (mean {mean:.3g}, L2 {l2:.6g}); call normalize()")


def write_templates(path: str, canonical_n: int, templates: Sequence[Template]) -> None:
    """Write a validated SCFT file. Raises TemplateFormatError for anything
    the on-device loader would reject, so a bad file is never produced."""
    if not templates:
        raise TemplateFormatError("bad_header", "at least one template is required")
    if len(templates) > MAX_COUNT:
        raise TemplateFormatError("bad_header", f"{len(templates)} templates, max {MAX_COUNT}")
    if not (MIN_CANONICAL_N <= canonical_n <= MAX_CANONICAL_N):
        raise TemplateFormatError("bad_header", f"canonical_n {canonical_n} out of [{MIN_CANONICAL_N}, {MAX_CANONICAL_N}]")
    seen_ids = {}
    for i, t in enumerate(templates):
        if len(t.data) != canonical_n:
            raise TemplateFormatError("bad_size",
                                      f"template {t.label!r} has {len(t.data)} samples, expected canonical_n={canonical_n}")
        if not (0 <= int(t.template_id) <= 0xFFFFFFFF):
            raise TemplateFormatError("bad_header", f"template_id {t.template_id} is not a uint32")
        if t.template_id in seen_ids:
            raise TemplateFormatError("duplicate_id",
                                      f"template_id {t.template_id} used by both {seen_ids[t.template_id]!r} and {t.label!r}")
        seen_ids[t.template_id] = t.label
        _check_vector(t.label, t.data)
        if t.feature_ranges is not None:
            for lo, hi in t.feature_ranges.as_tuples():
                if not (math.isfinite(lo) and math.isfinite(hi)):
                    raise TemplateFormatError("bad_range", f"template {t.label!r} has a non-finite feature range")
        if not (math.isfinite(t.hz_per_bin) and 0.0 <= t.hz_per_bin <= MAX_HZ_PER_BIN):
            raise TemplateFormatError("bad_resolution", f"template {t.label!r} hz_per_bin {t.hz_per_bin!r} invalid")

    # Version 2 only if at least one template actually carries ranges —
    # otherwise stay on version 1 so a plain shape-only library round-trips
    # through unmodified older tooling/devices exactly as it always has.
    version = VERSION_2 if any(t.feature_ranges is not None for t in templates) else VERSION_1
    if any(t.hz_per_bin > 0 for t in templates):
        version = VERSION_3

    entry = LABEL_LEN + 4 + 4 * canonical_n + (32 if version >= VERSION_2 else 0) + (4 if version >= VERSION_3 else 0)
    if 16 + entry * len(templates) > MAX_FILE_BYTES:
        raise TemplateFormatError("bad_size", f"file would be {16 + entry * len(templates)} bytes, cap {MAX_FILE_BYTES}")

    with open(path, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<I", version))
        f.write(struct.pack("<I", len(templates)))
        f.write(struct.pack("<I", canonical_n))
        for t in templates:
            f.write(encode_label(t.label))
            f.write(struct.pack("<I", t.template_id))
            f.write(struct.pack(f"<{canonical_n}f", *t.data))
            if version >= VERSION_2:
                fr = t.feature_ranges or FeatureRanges()
                for lo, hi in fr.as_tuples():
                    f.write(struct.pack("<ff", lo, hi))
            if version >= VERSION_3:
                f.write(struct.pack("<f", float(t.hz_per_bin)))


# --------------------------------------------------------------------------
# Reader / validator
# --------------------------------------------------------------------------

def parse_templates(buf: bytes):
    """Parse and fully validate an in-memory SCFT image with the same rules
    and status names as scft.c. Returns (version, canonical_n, templates).
    Raises TemplateFormatError."""
    if len(buf) > MAX_FILE_BYTES:
        raise TemplateFormatError("bad_size", f"file is {len(buf)} bytes, cap is {MAX_FILE_BYTES}")
    if len(buf) < 16:
        raise TemplateFormatError("bad_size", f"file is {len(buf)} bytes, header needs 16")
    if buf[:4] != MAGIC:
        raise TemplateFormatError("bad_magic", f"bad magic {buf[:4]!r}")
    version, count, canonical_n = struct.unpack_from("<III", buf, 4)
    if version not in (VERSION_1, VERSION_2, VERSION_3):
        raise TemplateFormatError("unsupported_version", f"unsupported version {version}")
    if not (1 <= count <= MAX_COUNT):
        raise TemplateFormatError("bad_header", f"count {count} out of [1, {MAX_COUNT}]")
    if not (MIN_CANONICAL_N <= canonical_n <= MAX_CANONICAL_N):
        raise TemplateFormatError("bad_header", f"canonical_n {canonical_n} out of [{MIN_CANONICAL_N}, {MAX_CANONICAL_N}]")
    entry = LABEL_LEN + 4 + 4 * canonical_n + (32 if version >= VERSION_2 else 0) + (4 if version >= VERSION_3 else 0)
    expected = 16 + entry * count
    if expected != len(buf):
        raise TemplateFormatError("bad_size",
                                  f"size {len(buf)} does not match header (expected {expected} for {count} x {canonical_n}, v{version})")

    templates = []
    ids = {}
    off = 16
    for i in range(count):
        label_bytes = buf[off:off + LABEL_LEN]
        off += LABEL_LEN
        nul = label_bytes.find(b"\x00")
        if nul < 0:
            raise TemplateFormatError("bad_label", f"entry {i}: label has no NUL terminator")
        try:
            label = check_label_bytes(label_bytes[:nul])
        except TemplateFormatError as e:
            raise TemplateFormatError(e.status, f"entry {i}: {e.message}")
        (template_id,) = struct.unpack_from("<I", buf, off)
        off += 4
        if template_id in ids:
            raise TemplateFormatError("duplicate_id", f"entry {i}: duplicate template_id {template_id} (also entry {ids[template_id]})")
        ids[template_id] = i
        data = struct.unpack_from(f"<{canonical_n}f", buf, off)
        off += 4 * canonical_n
        for k, v in enumerate(data):
            if not math.isfinite(v):
                raise TemplateFormatError("non_finite_data", f"entry {i}: sample {k} is not finite")
        mean = sum(data) / canonical_n
        l2 = math.sqrt(sum(v * v for v in data))
        if abs(mean) > NORM_TOL or abs(l2 - 1.0) > NORM_TOL:
            raise TemplateFormatError("not_normalized",
                                      f"entry {i}: not normalized (mean {mean:.3g}, L2 {l2:.6g}; tolerance {NORM_TOL})")
        feature_ranges = None
        hz_per_bin = 0.0
        if version >= VERSION_2:
            ranges = []
            for _ in range(4):
                lo, hi = struct.unpack_from("<ff", buf, off)
                off += 8
                if not (math.isfinite(lo) and math.isfinite(hi)):
                    raise TemplateFormatError("bad_range", f"entry {i}: feature range is not finite")
                ranges.append((lo, hi))
            feature_ranges = FeatureRanges(*ranges)
            if all(hi < lo for lo, hi in ranges):
                feature_ranges = None  # all unbounded: carry it as "no gate"
        if version >= VERSION_3:
            (hz_per_bin,) = struct.unpack_from("<f", buf, off)
            off += 4
            if not (math.isfinite(hz_per_bin) and 0.0 <= hz_per_bin <= MAX_HZ_PER_BIN):
                raise TemplateFormatError("bad_resolution", f"entry {i}: hz_per_bin {hz_per_bin!r} invalid")
        templates.append(Template(label=label, template_id=template_id, data=list(data),
                                  feature_ranges=feature_ranges, hz_per_bin=hz_per_bin))
    return version, canonical_n, templates


def read_templates(path: str):
    """Validating reader. Returns (canonical_n, templates) like before;
    raises TemplateFormatError (a ValueError) on any contract violation."""
    with open(path, "rb") as f:
        buf = f.read(MAX_FILE_BYTES + 1)
    _, canonical_n, templates = parse_templates(buf)
    return canonical_n, templates


def validate_file(path: str) -> dict:
    """JSON-able report in the same shape as `classifier -V FILE`."""
    import hashlib
    try:
        with open(path, "rb") as f:
            buf = f.read(MAX_FILE_BYTES + 1)
    except OSError as e:
        return {"valid": False, "status": "io", "path": path, "error": str(e)}
    try:
        version, canonical_n, templates = parse_templates(buf)
    except TemplateFormatError as e:
        return {"valid": False, "status": e.status, "path": path, "error": e.message}
    return {
        "valid": True, "status": "ok", "path": path, "version": version, "count": len(templates),
        "canonical_n": canonical_n, "size_bytes": len(buf), "sha256": hashlib.sha256(buf).hexdigest(),
        "labels": [t.label for t in templates[:64]], "labels_truncated": len(templates) > 64,
    }


def _main(argv):
    if len(argv) != 3 or argv[1] not in ("validate", "inspect"):
        print(__doc__.strip().splitlines()[-3:][0], file=sys.stderr)
        print("usage: templates_format.py validate|inspect FILE", file=sys.stderr)
        return 64
    report = validate_file(argv[2])
    if argv[1] == "inspect" and report["valid"]:
        _, _, templates = parse_templates(open(argv[2], "rb").read())
        report["templates"] = [
            {"label": t.label, "id": t.template_id, "hz_per_bin": t.hz_per_bin,
             "ranges": None if t.feature_ranges is None else
             {k: (None if hi < lo else [lo, hi]) for k, (lo, hi) in
              zip(("bandwidth_frac", "papr_db", "flatness", "peak_count"), t.feature_ranges.as_tuples())}}
            for t in templates
        ]
    print(json.dumps(report))
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
