"""Replay events (-E FILE) and the observation's background coverage
fields, run natively through build/host/classifier_replay (classifier.c
compiled against tsan/stubs with ASan+UBSan, -B none: no network).

The recorded SignalSDR Pro frames (tests/fixtures/wf_avg4096_frame{0,1,2}.bin,
1.09 GHz, 2.5 MHz span, DC spur at usable bin 2047) are cycled into a
sequence and scripted state/rx/frequency retunes are delivered on the
daemon's virtual clock. Every retune resets the background; at the end of
each relearn the bins that are hot at a second distinct RX frequency
become receiver spurs and stay masked, so masked_bins can only grow with
retunes — the offline reproduction of the 3563-of-4095 mask seen on a
sensor. The daemon prints the virtual time in the first column of every
line it would have published."""
import json
import subprocess

import pytest

from conftest import APP, HERE, STOCK_BUNDLE

REPLAY = APP / "build" / "host" / "classifier_replay"
FIXTURES = HERE / "fixtures"
N = 4096
BINS = N - 1
FPS = 7.5
LEARN_S = 10.0
DC_MASK_BINS = 5          # DC +-2 is always masked, spur or not


def make_raw(path, seconds):
    frames = [(FIXTURES / f"wf_avg4096_frame{i}.bin").read_bytes() for i in range(3)]
    assert all(len(f) == N * 4 for f in frames)
    n = int(round(seconds * FPS))
    with open(path, "wb") as f:
        for i in range(n):
            f.write(frames[i % 3])
    return n


def write_events(path, lines):
    path.write_text("\n".join(lines) + "\n")
    return path


def run(raw, events=None, extra=(), timeout=300):
    cmd = [str(REPLAY), "-r", str(raw), "-n", str(N), "-F", str(FPS), "-B", "none", "-S", "2500000",
           "-l", str(int(LEARN_S)), "-i", "1.0", "-f", str(STOCK_BUNDLE)]
    if events is not None:
        cmd += ["-E", str(events)]
    cmd += list(extra)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def parse(stdout):
    """{topic suffix: [(t, payload)]}; JSON payloads decoded."""
    rows = {}
    for line in stdout.splitlines():
        parts = line.split(" ", 2)
        if len(parts) < 3:
            continue
        t, topic, payload = parts
        key = topic.rsplit("/", 1)[-1]
        if payload[:1] in "{[":
            payload = json.loads(payload)
        rows.setdefault(key, []).append((float(t), payload))
    return rows


@pytest.fixture(scope="module")
def seq90(tmp_path_factory):
    p = tmp_path_factory.mktemp("replay") / "seq90.raw"
    make_raw(p, 90)
    return p


# --- (a) events file validation ---------------------------------------

def test_unsorted_events_rejected(tmp_path, seq90):
    ev = write_events(tmp_path / "unsorted.txt", ["0 state/rx/frequency 1090000000",
                                                   "30 state/rx/frequency 1100000000",
                                                   "20 state/rx/frequency 1110000000"])
    p = run(seq90, ev)
    assert p.returncode != 0
    assert "line 3" in p.stderr and "sorted" in p.stderr
    assert p.stdout == ""            # nothing replayed


def test_malformed_events_rejected(tmp_path, seq90):
    ev = write_events(tmp_path / "bad_time.txt", ["abc state/rx/frequency 1090000000"])
    p = run(seq90, ev)
    assert p.returncode != 0 and "line 1" in p.stderr and p.stdout == ""

    ev = write_events(tmp_path / "no_topic.txt", ["0 state/rx/frequency 1090000000", "10"])
    p = run(seq90, ev)
    assert p.returncode != 0 and "line 2" in p.stderr and "topic" in p.stderr

    ev = write_events(tmp_path / "negative.txt", ["-1 state/rx/frequency 1090000000"])
    p = run(seq90, ev)
    assert p.returncode != 0 and "line 1" in p.stderr


def test_missing_events_file_rejected(tmp_path, seq90):
    p = run(seq90, tmp_path / "does_not_exist.txt")
    assert p.returncode != 0 and "does_not_exist.txt" in p.stderr and p.stdout == ""


def test_events_require_replay_mode(tmp_path):
    ev = write_events(tmp_path / "ev.txt", ["0 state/rx/frequency 1090000000"])
    p = subprocess.run([str(REPLAY), "-E", str(ev), "-B", "none", "-f", str(STOCK_BUNDLE)],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode != 0 and "-E" in p.stderr and "-r" in p.stderr


def test_comments_blanks_and_late_events(tmp_path):
    raw = tmp_path / "seq5.raw"
    make_raw(raw, 5)
    ev = write_events(tmp_path / "ev.txt", ["# a comment", "", "   ", "0 state/rx/frequency 1090000000",
                                             "  # indented comment", "1 state/rx/span 2500000",
                                             "2 state/main/serial SN with spaces in it",
                                             "60 state/rx/frequency 1100000000",   # after the last frame
                                             "61 state/rx/frequency 1110000000"])
    p = run(raw, ev)
    assert p.returncode == 0, p.stderr
    assert "5 events loaded" in p.stderr
    assert "3 of 5 events delivered" in p.stderr and "2 not delivered" in p.stderr
    rows = parse(p.stdout)
    # the serial (payload with spaces) reached the observation verbatim
    assert rows["observation"][-1][1]["sensor"]["serial"] == "SN with spaces in it"
    # the undelivered retunes never bumped the generation
    assert all(o["config"]["generation"] == 0 for _, o in rows["observation"])


# --- (b) retune sequence: the spur mask grows, decisions keep flowing ---

@pytest.fixture(scope="module")
def retune_run(seq90, tmp_path_factory):
    ev = write_events(tmp_path_factory.mktemp("events") / "retunes.txt", [
        "# cold start at 1.09 GHz, 2.5 MHz span",
        "0 state/rx/frequency 1090000000",
        "0 state/rx/span 2500000",
        "30 state/rx/frequency 1100000000",
        "60 state/rx/frequency 1110000000",
    ])
    p = run(seq90, ev)
    assert p.returncode == 0, p.stderr
    assert "4 of 4 events delivered" in p.stderr
    return parse(p.stdout)


def obs_between(rows, t0, t1):
    return [(t, o) for t, o in rows["observation"] if t0 <= t < t1]


def test_generation_increments_at_each_retune(retune_run):
    gens = [(t, o["config"]["generation"]) for t, o in retune_run["observation"]]
    assert gens, "no observations"
    assert all(g == 0 for t, g in gens if t < 30)
    assert all(g == 1 for t, g in gens if 30 <= t < 60)
    assert all(g == 2 for t, g in gens if t >= 60)
    assert [g for _, g in gens] == sorted(g for _, g in gens)
    freqs = {o["config"]["frequency_hz"] for _, o in retune_run["observation"] if o["time"]["replay"]}
    assert freqs == {1090000000.0, 1100000000.0, 1110000000.0}


def test_background_published_after_each_learn(retune_run):
    bgs = retune_run["background"]
    assert len(bgs) == 3
    assert [b["frequency"] for _, b in bgs] == ["1090000000", "1100000000", "1110000000"]
    assert all(b["bins"] == BINS and b["fft_size"] == N for _, b in bgs)
    # each learn ends LEARN_S after its (re)start at 0 / 30 / 60 s
    for (t, _), start in zip(bgs, (0.0, 30.0, 60.0)):
        assert start + LEARN_S <= t < start + LEARN_S + 1.5


def test_spur_mask_grows_with_the_second_frequency(retune_run):
    bgs = retune_run["background"]
    (t1, b1), (t2, b2), (t3, b3) = bgs
    # first learn: hot bins seen at one frequency only -> DC mask alone
    assert b1["hot"] > DC_MASK_BINS
    assert b1["masked"] == DC_MASK_BINS
    # second frequency: the same bins are hot again -> receiver spurs
    assert b2["masked"] > b1["masked"]
    assert b3["masked"] >= b2["masked"]

    after1 = obs_between(retune_run, t1, 30.0)
    after2 = obs_between(retune_run, t2, 60.0)
    after3 = obs_between(retune_run, t3, 1e9)
    assert after1 and after2 and after3
    for _, o in after1:
        bg = o["background"]
        assert bg["learned"] is True and bg["learn_count"] == 1
        assert bg["masked_bins"] == b1["masked"] and bg["spur_bins"] == 0
        assert bg["hot_last_learn"] == b1["hot"]
    for _, o in after2:
        bg = o["background"]
        assert bg["learned"] is True and bg["learn_count"] == 2
        assert bg["masked_bins"] == b2["masked"] and bg["masked_bins"] > b1["masked"]
        assert 0 < bg["spur_bins"] <= bg["masked_bins"]
        assert bg["hot_last_learn"] == b2["hot"]
    for _, o in after3:
        bg = o["background"]
        assert bg["learn_count"] == 3 and bg["masked_bins"] == b3["masked"] and bg["spur_bins"] > 0


def test_mask_cleared_while_relearning_spurs_kept(retune_run):
    relearn = obs_between(retune_run, 30.0, 30.0 + LEARN_S)
    assert relearn
    for _, o in relearn:
        bg = o["background"]
        assert bg["learned"] is False and bg["masked_bins"] == 0 and bg["valid_bins"] == BINS
        assert o["decision"]["state"] == "learning"
    # the spur map survives the third reset: it is what the next learn re-applies
    relearn3 = obs_between(retune_run, 60.0, 60.0 + LEARN_S)
    assert relearn3 and all(o["background"]["spur_bins"] > 0 for _, o in relearn3)


def test_valid_bins_is_always_the_complement(retune_run):
    for _, o in retune_run["observation"]:
        bg = o["background"]
        assert o["input"]["bins_used"] == BINS
        assert bg["valid_bins"] == BINS - bg["masked_bins"]
        assert bg["spur_bins"] <= bg["masked_bins"] or not bg["learned"]
        assert o["schema"] == "tezuka.classifier.observation/1"
        assert set(bg) >= {"learned", "age_s", "frames", "masked_bins", "valid_bins", "spur_bins", "hot_last_learn", "learn_count"}


def test_decisions_keep_flowing_after_each_learn(retune_run):
    results = retune_run["result"]
    for t_learn, _ in retune_run["background"]:
        after = [r for t, r in results if t_learn < t < t_learn + 5.0]
        assert after, f"no result in the 5 s after the learn at {t_learn}"
        assert all(r["state"] in ("ok", "idle") for r in after)
        assert all(r["online"] is True for r in after)
    # one result per observation, on the same virtual clock
    assert [t for t, _ in results] == [t for t, _ in retune_run["observation"]]


# --- (c) scripted "learn" relearn -------------------------------------

def test_scripted_learn_resets_background_age(tmp_path):
    raw = tmp_path / "seq40.raw"
    make_raw(raw, 40)
    ev = write_events(tmp_path / "learn.txt", ["0 state/rx/frequency 1090000000",
                                               "20 cmd/classifier/background learn"])
    p = run(raw, ev)
    assert p.returncode == 0, p.stderr
    rows = parse(p.stdout)
    before = [(t, o) for t, o in rows["observation"] if t < 20.0]
    after = [(t, o) for t, o in rows["observation"] if t >= 20.0]
    assert before and after
    t_b, last_before = before[-1]
    t_a, first_after = after[0]
    assert last_before["background"]["learned"] is True
    assert last_before["background"]["age_s"] > LEARN_S
    assert first_after["background"]["learned"] is False
    assert first_after["background"]["age_s"] < last_before["background"]["age_s"]
    assert first_after["background"]["age_s"] <= t_a - 20.0 + 0.2
    # the reset happened at the 20 s frame; the next throttled cycle is at most ~1 s (FPS frames) later
    assert first_after["background"]["frames"] <= FPS + 1
    # a relearn is not a retune: generation unchanged, same frequency, no new spur
    assert all(o["config"]["generation"] == 0 for _, o in rows["observation"])
    bgs = rows["background"]
    assert len(bgs) == 2 and [b["frequency"] for _, b in bgs] == ["1090000000"] * 2
    assert bgs[1][1]["masked"] == bgs[0][1]["masked"] == DC_MASK_BINS
    assert rows["observation"][-1][1]["background"]["learn_count"] == 2
    assert rows["observation"][-1][1]["background"]["spur_bins"] == 0


def test_scripted_learn_honours_T_prefix(tmp_path):
    """-T moves the command topic to PREFIX cmd/background; the stock one is then ignored."""
    raw = tmp_path / "seq25.raw"
    make_raw(raw, 25)
    ev = write_events(tmp_path / "learn.txt", ["0 state/rx/frequency 1090000000",
                                               "15 cmd/classifier/background learn",
                                               "20 test/clf/cmd/background learn"])
    p = run(raw, ev, extra=["-T", "test/clf/"])
    assert p.returncode == 0, p.stderr
    rows = parse(p.stdout)
    assert all(line.split(" ", 2)[1].startswith("test/clf/") for line in p.stdout.splitlines() if line.strip())
    ages = [(t, o["background"]["age_s"], o["background"]["learned"]) for t, o in rows["observation"]]
    assert all(learned for t, _, learned in ages if 15.0 <= t < 20.0), "stock cmd topic must be ignored under -T"
    assert any(not learned for t, _, learned in ages if t >= 20.0), "prefixed cmd topic must trigger the relearn"
