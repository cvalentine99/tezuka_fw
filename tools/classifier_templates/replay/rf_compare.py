"""rf_compare.py - controlled offline comparison of classifier revisions on
a frozen fixture set (independent review 2026-11-08, RF acceptance gate).

Every revision's cross-compiled daemon is run under qemu-arm-static in
replay mode (-r, -B none: no network, no board) on the SAME frame
sequences with the SAME template bytes and the SAME arguments, and the
result/observation/background lines it prints are tallied per fixture.

Fixtures are generated deterministically (seeded) from the three recorded
/waterfall frames in tools/tezuka_mcp/tests/fixtures (SignalSDR Pro,
1.09 GHz, 2.5 MHz span, 4096-point Average spectrometer, DC spur at
usable bin 2047). Injected emitters are shaped like the templates they are
meant to be recognised as (or like nothing in the set, for the unknown
cases): they are SOFTWARE fixtures, positive controls for the pipeline,
not evidence of performance on real RF. The frame contract is the one in
waterfall_frame.py: float 0 is the step word, usable bin j is float j+1,
linear power, span / fft_size per bin, DC at usable index fft_size/2 - 1.

    python3 rf_compare.py --out DIR --fixtures tools/tezuka_mcp/tests/fixtures \
        --templates board/tezuka/common/overlay_tezuka/etc/classifier-default-templates.bin \
        --sysroot SYSROOT --bin baseline=/path/classifier --bin pinned=... --bin fixed=...

Writes DIR/fixtures.json (manifest with SHA-256 and expected disposition),
DIR/raw/*.raw (the sequences), DIR/runs/<fixture>__<rev>.out (daemon
output), DIR/results.json and DIR/results.md.

Replay events: a fixture may carry an events file (the daemon's -E FILE:
"<t_seconds> <topic> <payload>" per line, sorted by t), delivered to the
daemon's real MQTT handler on its virtual clock, so scripted retunes and
"learn" commands take the same code path as on a sensor. The generated
fixture spur_retune_sequence uses one (LO moved by more than a span at
60, 120 and 180 s over 240 s of the recorded frames: every retune relearns
the background and the bins hot at a second frequency become receiver
spurs, so the mask can only grow); --events NAME=PATH attaches a file to
any fixture (repeatable; the manifest records its SHA-256 and contents).
Runs of fixtures with events additionally record a "coverage" series
(t, masked_bins, valid_bins, spur_bins, learn_count, label) from the
observation messages, sampled at most once per 5 s, with the final mask
coverage and the peak masked count; results.md gains coverage columns
when any fixture in the run has events. A revision that predates the
coverage fields reports them as null.

Accept dispositions: an accepted label that is not in a fixture's expected
set is "unexpected". Whether it is a FALSE accept depends on what was
really on air, which the spectrum alone does not establish, so every
unexpected accept carries a disposition: "confirmed_false" (known to be
wrong: a software fixture's emitter is synthetic and known, so its
off-label accepts are confirmed by construction; for a recorded capture
only when independent evidence says so), "suspected_false" (shape and
allocation point to a wrong label, protocol ground truth pending) or
"unverified" (nothing is known either way). Recorded captures default to
"unverified"; --accept NAME=DISPOSITION=LABEL[::basis] sets one label's
disposition and records the basis in the manifest (labels are checked
against the template set). --expect NAME=LABEL declares a recorded capture a
trusted positive (the label is expected throughout; recognised and misses
are then reported); it combines with --events. results.json reports
the three counts separately and results.md shows them as
confirmed / suspected / unverified; "false_accepts" counts confirmed ones
only.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter

import numpy as np

DISPOSITIONS = ("confirmed_false", "suspected_false", "unverified")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from templates_format import read_templates  # noqa: E402

N = 4096                 # floats per frame (step word + 4095 usable bins)
SPAN_HZ = 2.5e6
CENTER_HZ = 1.09e9
FPS = 7.5
DC = N // 2 - 1          # usable-bins index of DC
LIVE_HZ = SPAN_HZ / N
LEARN_S = 10.0
# Identical arguments for every revision: the daemon defaults, spelled out.
DAEMON_ARGS = ["-n", str(N), "-F", str(FPS), "-S", str(int(SPAN_HZ)), "-B", "none",
               "-c", "0.6", "-m", "0.15", "-i", "1.0", "-s", "6", "-g", "4", "-l", str(int(LEARN_S)), "-a", "300",
               "-w", "4e6", "-x", "8"]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def events_record(path, out_dir):
    """Manifest entry for a replay events file: path, hash and the text
    itself, so the run is reproducible from the manifest alone."""
    with open(path) as f:
        text = f.read()
    return {"file": os.path.relpath(path, out_dir), "sha256": sha256(path), "contents": text,
            "format": "one event per line: <t_seconds> <topic> <payload>; '#' comments; sorted by t; passed to the daemon as -E"}


def write_events(out_raw, name, lines):
    p = os.path.join(out_raw, name + ".events")
    with open(p, "w") as f:
        f.write("\n".join(lines) + "\n")
    return events_record(p, os.path.dirname(out_raw))


def load_recorded(fixture_dir):
    frames = []
    for i in range(3):
        p = os.path.join(fixture_dir, f"wf_avg4096_frame{i}.bin")
        a = np.fromfile(p, dtype="<f4")
        assert a.size == N, p
        frames.append(a)
    return np.stack(frames)


def cycled(base, seconds):
    n = int(round(seconds * FPS))
    return base[np.arange(n) % base.shape[0]].copy()


def template_shape(tpl, canonical_n):
    """0..1 shape of a template stretched to its real width on this grid
    (the same stretch inject_emitter.py and the daemon apply)."""
    shape = np.array(tpl.data, dtype=np.float64)
    shape -= shape.min()
    shape /= shape.max()
    if tpl.hz_per_bin > 0:
        n_live = max(8, int(round(canonical_n * tpl.hz_per_bin / LIVE_HZ)))
        n_live = min(n_live, N - 1)
        shape = np.interp(np.linspace(0, canonical_n - 1, n_live), np.arange(canonical_n), shape)
    return shape


def flat_block(width_bins, rolloff=0.1):
    """A flat-top block with raised-cosine edges: like nothing in the
    template set (wider than FM, narrower than DAB+, no central peak)."""
    x = np.ones(width_bins)
    e = max(1, int(width_bins * rolloff))
    ramp = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, e))
    x[:e] = ramp
    x[-e:] = ramp[::-1]
    return x


def inject(a, shape, start, snr_db, t_on, t_off, rng):
    """Add `shape` (0..1) at usable bin `start` during [t_on, t_off) with its
    peak snr_db above the median level of that window in the first 10 s,
    with the ~25 % per-bin fluctuation (gamma k=16) an averaged steady
    signal shows. Same recipe as inject_emitter.py."""
    n = shape.size
    win = a[: int(FPS * 10), 1 + start:1 + start + n]
    noise = float(np.median(win))
    peak = noise * 10 ** (snr_db / 10)
    for i in range(a.shape[0]):
        if t_on <= i / FPS < t_off:
            a[i, 1 + start:1 + start + n] += (peak * shape * rng.gamma(16.0, 1.0 / 16.0, n)).astype("<f4")
    return {"start_bin": start, "end_bin": start + n - 1, "width_bins": n,
            "center_hz": CENTER_HZ + (start + n / 2 - 0.5 + 1 - N / 2) * LIVE_HZ, "width_hz": n * LIVE_HZ,
            "snr_db_over_local_median": snr_db, "t_on_s": t_on, "t_off_s": t_off}


def remove_spur(a, rng, half=40):
    """Replace the DC spur region with values drawn from the same frame's
    neighbouring bins (software fixture: a receiver without the spur)."""
    lo, hi = DC - half, DC + half
    for i in range(a.shape[0]):
        pool = np.concatenate([a[i, 1 + DC - 300:1 + DC - 100], a[i, 1 + DC + 100:1 + DC + 300]])
        a[i, 1 + lo:1 + hi + 1] = rng.choice(pool, hi - lo + 1).astype("<f4")
    return {"replaced_bins": [lo, hi]}


def build_fixtures(base, templates, out_raw):
    """Returns the manifest list; writes the .raw sequences."""
    canonical_n, tpls = templates
    fm, dab, cw = tpls[0], tpls[1], tpls[2]
    fm_shape, dab_shape, cw_shape = (template_shape(t, canonical_n) for t in (fm, dab, cw))
    specs = []

    def add(name, seconds, expected_on, expected_off, condition, basis, builder, t_on=None, t_off=None):
        rng = np.random.default_rng(abs(hash(name)) % (2 ** 32) if False else sum(map(ord, name)))
        a = cycled(base, seconds)
        detail = builder(a, rng) if builder else {}
        p = os.path.join(out_raw, name + ".raw")
        a.astype("<f4").tofile(p)
        specs.append({"name": name, "file": os.path.relpath(p, os.path.dirname(out_raw)), "sha256": sha256(p),
                      "frames": int(a.shape[0]), "duration_s": seconds, "fps": FPS,
                      "layout": "float32 LE x 4096 per frame; float 0 = step word; usable bin j = float j+1; linear power (not dBm)",
                      "grid": {"center_hz": CENTER_HZ, "span_hz": SPAN_HZ, "fft_size": N, "hz_per_bin": LIVE_HZ, "dc_usable_index": DC},
                      "source": "three recorded SignalSDR Pro frames (tools/tezuka_mcp/tests/fixtures, 2026-11-08T18:01Z, 1.09 GHz, 2.5 MHz, Average x1023, gain 30 dB Manual), cycled" + (", then modified as `detail` says" if detail else ""),
                      "detail": detail, "on_window_s": [t_on, t_off] if t_on is not None else None,
                      "expected_on": sorted(expected_on), "expected_off": sorted(expected_off),
                      "condition": condition, "expected_basis": basis, "kind": "recorded" if not detail else "software fixture",
                      "software_emitter": bool(detail)})

    nothing = set()
    add("spur_only_cold", 60, nothing, nothing, "cold start: learning begins on the first frame",
        "the recorded band holds only the receiver's DC spur (waterfall summary: one segment, contains_dc); no emitter class is correct", None)
    add("spur_only_settled", 240, nothing, nothing, "settled: 240 s of the same frames, background adaptation tau 300 s",
        "as spur_only_cold", None)
    add("quiet_no_spur", 60, nothing, nothing, "cold start",
        "the spur region is replaced by neighbouring-bin samples: nothing is on air", remove_spur)
    add("fm_offcenter", 60, {"FM Broadcast"}, nothing, "cold start; emitter on from 20 s",
        "an emitter with the FM template's own 400 kHz shape at -500 kHz from the LO (positive control derived from the template, not independent RF)",
        lambda a, rng: inject(a, fm_shape, 900, 20.0, 20.0, 60.0, rng), 20.0, 60.0)
    add("fm_near_lo", 60, {"FM Broadcast"}, nothing, "cold start; emitter on from 20 s, centred on the LO over the spur",
        "a legitimate signal AT the LO must stay recognisable: a blanket DC exclusion would remove it (positive control derived from the template)",
        lambda a, rng: inject(a, fm_shape, DC - fm_shape.size // 2, 20.0, 20.0, 60.0, rng), 20.0, 60.0)
    add("dab_fullspan", 60, {"DAB+"}, nothing, "cold start; emitter on from 20 s, fills the whole span",
        "the DAB+ template is 2.5 MHz wide, the whole span at this grid (positive control derived from the template)",
        lambda a, rng: inject(a, dab_shape, 0, 15.0, 20.0, 60.0, rng), 20.0, 60.0)
    add("cw_offcenter", 60, {"CW / Beacon"}, nothing, "cold start; emitter on from 20 s",
        "a 30 kHz carrier-like shape at +580 kHz (positive control derived from the template)",
        lambda a, rng: inject(a, cw_shape, 3000, 25.0, 20.0, 60.0, rng), 20.0, 60.0)
    add("unknown_block_1mhz", 60, nothing, nothing, "cold start; emitter on from 20 s",
        "a 1 MHz flat-top block is in no template: any accepted label is a false accept; 'unknown' is correct",
        lambda a, rng: inject(a, flat_block(1638), 300, 15.0, 20.0, 60.0, rng), 20.0, 60.0)
    add("overlap_fm_cw", 60, {"FM Broadcast", "CW / Beacon"}, nothing, "cold start; two emitters on from 20 s, the carrier inside the FM window",
        "both emitters are present, so either label is defensible; DAB+ would be false",
        lambda a, rng: (inject(a, fm_shape, 1000, 20.0, 20.0, 60.0, rng), inject(a, cw_shape, 1300, 25.0, 20.0, 60.0, rng))[0] | {"second": "CW at 1300"}, 20.0, 60.0)
    add("spur_retune_sequence", 240, nothing, nothing,
        "cold start at 1.09 GHz, then retuned by > span at 60 s (1.095 GHz), 120 s (1.1 GHz) and 180 s (back to 1.09 GHz) through scripted state/rx/frequency events; the same recorded frames throughout",
        "nothing is on air but the receiver's own DC spur, so no template class is expected; an accepted label is an unexpected accept (unverified unless --accept gives it a disposition, since these are recorded frames). Each retune relearns the background and the bins hot at a second frequency become receiver spurs, so masked_bins can only grow (the coverage series records how much of the span is left to classify)", None)
    specs[-1]["events"] = write_events(out_raw, "spur_retune_sequence", [
        "# spur_retune_sequence: the LO moves by more than the 2.5 MHz span at each step,",
        "# so a bin hot at two of these frequencies is counted as a receiver spur",
        "0 state/rx/frequency 1090000000",
        "60 state/rx/frequency 1095000000",
        "120 state/rx/frequency 1100000000",
        "180 state/rx/frequency 1090000000",
    ])
    specs[-1]["kind"] = "recorded frames + scripted events"
    specs[-1]["source"] += "; retuned through the events file (see `events`)"
    return specs


def attach_events(specs, spec_arg, out_raw):
    """--events NAME=PATH: deliver the replay events in PATH to fixture NAME
    (the daemon's -E). The file is copied next to the sequences and its
    hash and contents go into the manifest."""
    name, sep, path = spec_arg.partition("=")
    if not sep or not path:
        raise SystemExit(f"--events expects NAME=PATH, got {spec_arg!r}")
    spec = next((s for s in specs if s["name"] == name), None)
    if spec is None:
        raise SystemExit(f"--events: fixture {name!r} not found (have: {sorted(s['name'] for s in specs)})")
    if not os.path.isfile(path):
        raise SystemExit(f"--events: {path}: no such file")
    dst = os.path.join(out_raw, name + ".events")
    if os.path.abspath(dst) != os.path.abspath(path):
        shutil.copyfile(path, dst)
    spec["events"] = events_record(dst, os.path.dirname(out_raw))
    spec["events"]["source"] = os.path.abspath(path)
    steps = [ln.strip() for ln in spec["events"]["contents"].splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    spec["condition"] += "; scripted replay events on the virtual clock: " + ", ".join(steps)
    spec["kind"] = spec.get("kind", "fixture") + " + scripted events"


def check_label(label, known, flag):
    if known is not None and label not in known:
        raise SystemExit(f"{flag}: label {label!r} is not in the template set {sorted(known)}; the daemon can never produce it")


def attach_accept(specs, spec_arg, known_labels=None):
    """--accept NAME=DISPOSITION=LABEL[::basis]: disposition of an unexpected
    accepted LABEL on fixture NAME (see DISPOSITIONS), with the evidence it
    rests on; both go into the manifest."""
    parts = spec_arg.split("=", 2)
    if len(parts) != 3:
        raise SystemExit(f"--accept expects NAME=DISPOSITION=LABEL[::basis], got {spec_arg!r}")
    name, disp, rest = parts
    label, _, basis = rest.partition("::")
    label, basis = label.strip(), basis.strip()
    if disp not in DISPOSITIONS:
        raise SystemExit(f"--accept: disposition {disp!r} is not one of {DISPOSITIONS}")
    if not label:
        raise SystemExit(f"--accept: empty label in {spec_arg!r}")
    spec = next((s for s in specs if s["name"] == name), None)
    if spec is None:
        raise SystemExit(f"--accept: fixture {name!r} not found (have: {sorted(s['name'] for s in specs)})")
    check_label(label, known_labels, "--accept")
    spec.setdefault("accept_dispositions", {})[label] = {"disposition": disp, "basis": basis or "(no basis given)"}


def attach_expect(specs, spec_arg, known_labels=None):
    """--expect NAME=LABEL[,LABEL]: labels expected throughout a recorded
    capture (a trusted positive, on air for the whole sequence). Any of
    them counts as recognised; anything else accepted is unexpected."""
    name, sep, labels = spec_arg.partition("=")
    if not sep or not labels.strip():
        raise SystemExit(f"--expect expects NAME=LABEL[,LABEL], got {spec_arg!r}")
    spec = next((s for s in specs if s["name"] == name), None)
    if spec is None:
        raise SystemExit(f"--expect: fixture {name!r} not found (have: {sorted(s['name'] for s in specs)})")
    if not spec.get("recorded_capture"):
        raise SystemExit(f"--expect: {name!r} is not a recorded capture (--recorded)")
    spec["expected_off"] = [l.strip() for l in labels.split(",") if l.strip()]
    for l in spec["expected_off"]:
        check_label(l, known_labels, "--expect")
    spec["expected_basis"] = "trusted positive: the label was established independently of the spectrum (see the capture's notes); the emission is on air for the whole sequence"


def disposition_of(spec, label):
    """Disposition and basis of an unexpected accept of LABEL on SPEC."""
    given = (spec.get("accept_dispositions") or {}).get(label)
    if given:
        return given["disposition"], given["basis"]
    if spec.get("software_emitter"):
        return "confirmed_false", "software fixture: the injected emitter is synthetic and known, any other label is wrong by construction"
    return "unverified", "recorded frames: what was on air is not established by the spectrum alone"


def daemon_args_for(fps, span_hz=None):
    """DAEMON_ARGS with the frame rate (and, for a capture at another sample
    rate, the span) replaced: a recorded sequence is replayed at the rate and
    on the grid it was captured with unless the caller says otherwise."""
    args = list(DAEMON_ARGS)
    args[args.index("-F") + 1] = repr(float(fps))
    if span_hz:
        args[args.index("-S") + 1] = str(int(span_hz))
    return args


def add_recorded(specs, spec_arg, out_raw):
    """--recorded name=path[:fps]: a raw sequence captured from a sensor
    (capture.py / capture_from_device.py layout, step word included), used
    as it is. Expected disposition: nothing on air -> unknown; the caller
    states the band in the meta file next to it (path + '.meta.json')."""
    name, rest = spec_arg.split("=", 1)
    path, _, fps_s = rest.partition(":")
    fps = float(fps_s) if fps_s else FPS
    a = np.fromfile(path, dtype="<f4")
    if a.size == 0 or a.size % N:
        raise SystemExit(f"{path}: {a.size} floats is not a multiple of {N}")
    frames = a.size // N
    dst = os.path.join(out_raw, name + ".raw")
    shutil.copyfile(path, dst)
    meta = {}
    if os.path.exists(path + ".meta.json"):
        with open(path + ".meta.json") as f:
            meta = json.load(f)
    api = meta.get("api", {})
    rx = api.get("ad9361", {})
    sp = api.get("spectrometer", {})
    specs.append({"name": name, "file": os.path.relpath(dst, os.path.dirname(out_raw)), "sha256": sha256(dst),
                  "frames": int(frames), "duration_s": frames / fps, "fps": fps,
                  "layout": "float32 LE x 4096 per frame; float 0 = step word; usable bin j = float j+1; linear power (not dBm)",
                  "grid": {"center_hz": rx.get("rx_lo_frequency"), "span_hz": rx.get("sampling_frequency"), "fft_size": sp.get("fft_size", N),
                           "hz_per_bin": (rx.get("sampling_frequency") or SPAN_HZ) / sp.get("fft_size", N), "dc_usable_index": DC},
                  "source": f"recorded as captured from {meta.get('host', '?')} at {meta.get('captured_unix_s')} (unix s): "
                            f"rx_lo {rx.get('rx_lo_frequency')} Hz, fs {rx.get('sampling_frequency')} Hz, gain {rx.get('rx_gain')} dB {rx.get('rx_gain_mode')}, "
                            f"{sp.get('mode')} x{sp.get('number_integrations')}, {sp.get('fft_size')}-point; capture sha256 {meta.get('sha256')}",
                  "detail": {"meta": meta, "replay_fps": fps},
                  "on_window_s": None, "expected_on": [], "expected_off": [],
                  "condition": f"recorded sequence as captured, replayed at {fps:g} frames/s: cold start, learning begins on the first frame",
                  "expected_basis": meta.get("on_air") or (
                      "no emitter class of the template set is expected in this band; every accepted label is tallied "
                      "as an unexpected accept, unverified unless --accept gives it a disposition with its basis; the report "
                      "next to the capture says which of them could be correct (the meta file records the receiver "
                      "settings, not what was on air)"),
                  "kind": "recorded", "recorded_capture": True, "software_emitter": False,
                  "daemon_args": daemon_args_for(fps, rx.get("sampling_frequency"))})


def run_daemon(qemu, sysroot, binary, raw, templates, out_path, daemon_args=DAEMON_ARGS, events=None):
    cmd = [qemu, "-L", sysroot, binary, "-r", raw, "-f", templates] + list(daemon_args)
    if events:
        cmd += ["-E", events]
    with open(out_path, "w") as f:
        p = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, text=True)
    return cmd, p.returncode, p.stderr


def parse_output(path):
    rows = {"result": [], "observation": [], "background": []}
    with open(path) as f:
        for line in f:
            parts = line.rstrip("\n").split(" ", 2)
            if len(parts) < 3:
                continue
            t, topic, payload = parts
            key = topic.rsplit("/", 1)[-1]
            if key in rows:
                try:
                    rows[key].append((float(t), json.loads(payload)))
                except ValueError:
                    pass
    return rows


COVERAGE_SAMPLE_S = 5.0
COVERAGE_COLUMNS = ["t", "masked_bins", "valid_bins", "spur_bins", "learn_count", "label"]


def coverage(rows):
    """Mask coverage over the run, from the observation messages: one row
    per COVERAGE_COLUMNS sampled at most once per COVERAGE_SAMPLE_S, the
    final row, the final mask coverage (masked / bins_used) and the peak
    masked count. A revision without the coverage fields yields nulls."""
    series, last_t, max_masked, final, bins_used = [], None, None, None, None
    for t, o in rows["observation"]:
        bg = o.get("background") or {}
        row = [t, bg.get("masked_bins"), bg.get("valid_bins"), bg.get("spur_bins"), bg.get("learn_count"),
               (o.get("decision") or {}).get("label")]
        if row[1] is not None:
            max_masked = row[1] if max_masked is None else max(max_masked, row[1])
        if (o.get("input") or {}).get("bins_used") is not None:
            bins_used = o["input"]["bins_used"]
        final = row
        if last_t is None or t - last_t >= COVERAGE_SAMPLE_S:
            series.append(row)
            last_t = t
    if final is None:
        return None
    return {"columns": COVERAGE_COLUMNS, "sample_interval_s": COVERAGE_SAMPLE_S, "series": series, "final": final,
            "final_mask_coverage": (final[1] / bins_used) if final[1] is not None and bins_used else None,
            "max_masked_bins": max_masked, "bins_used": bins_used,
            "backgrounds": [{"t": t, **b} for t, b in rows["background"]]}


def coverage_cells(res):
    """results.md cells for a run: final masked/valid (max masked), spur bins, learns."""
    cov = res.get("coverage")
    if not cov:
        return ["-", "-", "-"]
    f = cov["final"]
    pct = f" = {100 * cov['final_mask_coverage']:.1f}%" if cov["final_mask_coverage"] is not None else ""
    return [f"{f[1]} / {f[2]} (max {cov['max_masked_bins']}){pct}", str(f[3]), str(f[4])]


def tally(spec, rows):
    on = spec["on_window_s"]
    settle = LEARN_S + 2.0                     # skip learning plus one cycle
    dec_on, dec_off = [], []
    for t, r in rows["result"]:
        if t < settle:
            continue
        if on and on[0] <= t < on[1]:
            dec_on.append((t, r))
        elif not on or t < on[0]:
            dec_off.append((t, r))
        else:
            dec_off.append((t, r))
    exp_on, exp_off = set(spec["expected_on"]), set(spec["expected_off"])

    def summarize(decs, allowed):
        labels = Counter(r["label"] for _, r in decs)
        accepted = [(t, r) for t, r in decs if r["label"] != "unknown"]
        unexpected = [(t, r) for t, r in accepted if r["label"] not in allowed]
        correct = [(t, r) for t, r in accepted if r["label"] in allowed]
        misses = [(t, r) for t, r in decs if allowed and r["label"] not in allowed]
        min_correct = min((r.get("confidence", 0) for _, r in correct), default=None)
        by_label = {}
        for t, r in unexpected:
            disp, basis = disposition_of(spec, r["label"])
            e = by_label.setdefault(r["label"], {"decisions": 0, "disposition": disp, "basis": basis,
                                                 "score_min": None, "score_max": None, "first_t": t})
            e["decisions"] += 1
            c = r.get("confidence", 0)
            e["score_min"] = c if e["score_min"] is None else min(e["score_min"], c)
            e["score_max"] = c if e["score_max"] is None else max(e["score_max"], c)
        by_disp = {d: [(t, r) for t, r in unexpected if by_label[r["label"]]["disposition"] == d] for d in DISPOSITIONS}
        worst = {d: max((r.get("confidence", 0) for _, r in v), default=None) for d, v in by_disp.items()}
        confirmed = by_disp["confirmed_false"]
        return {"decisions": len(decs), "labels": dict(labels),
                "unexpected_accepts": len(unexpected), "unexpected_accepts_by_label": by_label,
                "confirmed_false_accepts": len(confirmed), "suspected_false_accepts": len(by_disp["suspected_false"]),
                "unverified_accepts": len(by_disp["unverified"]),
                "worst_score_by_disposition": worst,
                # kept for readers of the first-pass results: confirmed false accepts only
                "false_accepts": len(confirmed),
                "false_accept_labels": dict(Counter(r["label"] for _, r in confirmed)),
                "worst_false_accept_score": worst["confirmed_false"],
                "recognized": len(correct) if allowed else None,
                "misses": len(misses) if allowed else None, "min_correct_score": min_correct,
                "first_false_accept": {"t": confirmed[0][0], **confirmed[0][1]} if confirmed else None,
                "first_unexpected_accept": {"t": unexpected[0][0], **unexpected[0][1]} if unexpected else None}

    out = {"on": summarize(dec_on, exp_on) if on else None, "off": summarize(dec_off, exp_off)}
    # explanatory record for the first accepted decision, from the observation
    # message when the revision publishes one (pinned/fixed), else from result
    first_acc = next(((t, r) for t, r in rows["result"] if t >= settle and r["label"] != "unknown"), None)
    if first_acc:
        t0 = first_acc[0]
        obs = next((o for t, o in rows["observation"] if abs(t - t0) < 0.01), None)
        bg = [b for t, b in rows["background"] if t <= t0]
        out["explain_first_accept"] = {"t": t0, "result": first_acc[1],
                                       "observation": {k: obs.get(k) for k in ("decision", "features", "background", "input")} if obs else None,
                                       "background_learned": bg[-1] if bg else None}
    else:
        bg = rows["background"]
        out["explain_first_accept"] = None
        out["background_learned"] = bg[-1][1] if bg else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--fixtures", required=True, help="dir with wf_avg4096_frame{0,1,2}.bin")
    ap.add_argument("--templates", required=True)
    ap.add_argument("--sysroot", required=True)
    ap.add_argument("--qemu", default=shutil.which("qemu-arm-static") or "qemu-arm-static")
    ap.add_argument("--bin", action="append", required=True, help="name=path (repeat)")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--recorded", action="append", default=[],
                    help="name=path[:fps]: add a captured raw sequence as a recorded fixture (repeat)")
    ap.add_argument("--only", default="", help="comma-separated fixture names to run (default: all)")
    ap.add_argument("--events", action="append", default=[],
                    help="NAME=PATH: deliver the replay events file PATH (daemon -E) to fixture NAME (repeat)")
    ap.add_argument("--expect", action="append", default=[],
                    help="NAME=LABEL[,LABEL]: labels expected throughout recorded capture NAME (a trusted positive; repeat)")
    ap.add_argument("--accept", action="append", default=[],
                    help="NAME=DISPOSITION=LABEL[::basis]: disposition (%s) of an unexpected accepted LABEL on fixture NAME, "
                         "with the evidence it rests on (repeat); recorded captures default to unverified" % "|".join(DISPOSITIONS))
    args = ap.parse_args()

    os.makedirs(os.path.join(args.out, "raw"), exist_ok=True)
    os.makedirs(os.path.join(args.out, "runs"), exist_ok=True)
    base = load_recorded(args.fixtures)
    templates = read_templates(args.templates)
    specs = build_fixtures(base, templates, os.path.join(args.out, "raw"))
    for rec in args.recorded:
        add_recorded(specs, rec, os.path.join(args.out, "raw"))
    for ev in args.events:
        attach_events(specs, ev, os.path.join(args.out, "raw"))
    known = {t.label for t in templates[1]}
    for ex in args.expect:
        attach_expect(specs, ex, known)
    for acc in args.accept:
        attach_accept(specs, acc, known)
    if args.only:
        wanted = set(args.only.split(","))
        missing = wanted - {s["name"] for s in specs}
        if missing:
            raise SystemExit(f"--only names not found: {sorted(missing)}")
        specs = [s for s in specs if s["name"] in wanted]
    manifest = {"recorded_frames": [{"file": f"wf_avg4096_frame{i}.bin", "sha256": sha256(os.path.join(args.fixtures, f"wf_avg4096_frame{i}.bin"))} for i in range(3)],
                "templates": {"file": args.templates, "sha256": sha256(args.templates),
                              "labels": [t.label for t in templates[1]], "canonical_n": templates[0]},
                "daemon_args": DAEMON_ARGS,
                "accept_dispositions": {d: {"confirmed_false": "known to be wrong (software fixtures by construction; recorded captures only on independent evidence)",
                                            "suspected_false": "shape and allocation point to a wrong label, protocol ground truth pending",
                                            "unverified": "nothing is known about what was on air; neither a false accept nor a positive"}[d] for d in DISPOSITIONS},
                "fixtures": specs}
    with open(os.path.join(args.out, "fixtures.json"), "w") as f:
        json.dump(manifest, f, indent=1)

    bins = dict(b.split("=", 1) for b in args.bin)
    revs = {name: {"path": path, "sha256": sha256(path)} for name, path in bins.items()}
    jobs = [(spec, name) for spec in specs for name in bins]

    def job(spec, name):
        raw = os.path.join(args.out, spec["file"])
        outp = os.path.join(args.out, "runs", f"{spec['name']}__{name}.out")
        events = os.path.join(args.out, spec["events"]["file"]) if spec.get("events") else None
        cmd, rc, err = run_daemon(args.qemu, args.sysroot, bins[name], raw, args.templates, outp,
                                  spec.get("daemon_args", DAEMON_ARGS), events)
        rows = parse_output(outp)
        res = {"command": cmd, "returncode": rc, "stderr_tail": err.strip().splitlines()[-3:],
               "result_lines": len(rows["result"]), **tally(spec, rows)}
        if events:
            res["coverage"] = coverage(rows)
        return spec["name"], name, res

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for fx, name, res in ex.map(lambda j: job(*j), jobs):
            results.setdefault(fx, {})[name] = res
    doc = {"revisions": revs, "qemu": args.qemu, "sysroot": args.sysroot, "results": results}
    with open(os.path.join(args.out, "results.json"), "w") as f:
        json.dump(doc, f, indent=1)

    # markdown table (coverage columns only when a fixture in this run has
    # events, so a run without them keeps the previous layout)
    has_events = any(s.get("events") for s in specs)
    cov_head = " coverage: final masked / valid bins (max masked) | spur bins | learns |" if has_events else ""
    lines = ["| fixture | expected (ON / OFF) | revision | ON: accepted / decisions | OFF: accepted / decisions | unexpected accepts: confirmed false / suspected false / unverified (worst score) | misses | condition |" + cov_head,
             "|---|---|---|---|---|---|---|---|" + ("---|---|---|" if has_events else "")]
    for spec in specs:
        exp = f"{' or '.join(spec['expected_on']) or 'unknown'} / {' or '.join(spec['expected_off']) or 'unknown'}"
        for name in bins:
            r = results[spec["name"]][name]
            on, off = r["on"], r["off"]
            on_s = f"{on['recognized'] if on['recognized'] is not None else sum(v for k, v in on['labels'].items() if k != 'unknown')}/{on['decisions']} {on['labels']}" if on else "-"
            off_s = f"{sum(v for k, v in off['labels'].items() if k != 'unknown')}/{off['decisions']} {off['labels']}"
            if off.get("recognized") is not None:          # a recorded trusted positive (--expect): on air throughout
                off_s += f"; recognised {off['recognized']}/{off['decisions']} (min {off['min_correct_score']})"
            cells = []
            for d in DISPOSITIONS:
                k = d.replace("_false", "_false_accepts") if d != "unverified" else "unverified_accepts"
                n_ = (on[k] if on else 0) + off[k]
                worst = max([x for x in ((on or {}).get("worst_score_by_disposition", {}).get(d), off["worst_score_by_disposition"].get(d)) if x is not None], default=None)
                cells.append(f"{n_}{f' ({worst})' if worst is not None else ''}")
            fa = " / ".join(cells)
            misses = on["misses"] if on and on["misses"] is not None else (off["misses"] if off.get("misses") is not None else "-")
            cov_s = (" " + " | ".join(coverage_cells(r)) + " |") if has_events else ""
            lines.append(f"| {spec['name']} | {exp} | {name} | {on_s} | {off_s} | {fa} | {misses} | {spec['condition']} |" + cov_s)
    with open(os.path.join(args.out, "results.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
