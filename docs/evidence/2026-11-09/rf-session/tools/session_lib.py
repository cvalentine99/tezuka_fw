"""Run the 2026-11-09 capture session S0..S5 (SESSION_LOG.md). The capture tool
stays read-only; this orchestrator is the operator: it applies each segment's
setting through the maia-httpd API (logged by radio.py with wall time), waits
for the spectrometer to show it, runs capture_waterfall.py, takes the IQ
recordings for RDS mid-segment (downloaded only after the segment ends), and
for S5 makes the three LO-only retunes at +180/+360/+540 s, noting each time."""
import json, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(__file__))
import radio

SESS = "/home/cvalentine/tezuka-live-audit/rf-session-20261109"
RAW = SESS + "/raw"
CAP = "/home/cvalentine/tezuka_fw/.claude/worktrees/classifier-mcp-foundation/tools/classifier_templates/replay/capture_waterfall.py"
PY = "/home/cvalentine/.venvs/tezuka-mcp/bin/python"
RUNLOG = SESS + "/session_run.jsonl"
OP = "operator Claude Code for the owner Chase Valentine (owner instructed, not present)"


def note(**kw):
    kw["t_unix_s"] = round(time.time(), 3)
    kw["t_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(RUNLOG, "a") as f:
        f.write(json.dumps(kw) + "\n")
    print(json.dumps(kw), flush=True)


def settle(expect, extra_s=8.0, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        a = radio.api()
        ok = all(a["ad9361"].get(k) == v or (k == "rx_lo_frequency" and abs(a["ad9361"][k] - v) <= 10) for k, v in expect.items())
        if ok and a["spectrometer"]["input_sampling_frequency"] == float(a["ad9361"]["sampling_frequency"]):
            time.sleep(extra_s)
            return radio.api()
        time.sleep(1)
    raise RuntimeError(f"setting not seen within {timeout} s: {expect}")


def capture(name, seconds, note_text, background=False, fps=None):
    a = radio.api()
    fps = fps or a["spectrometer"]["output_sampling_frequency"]
    frames = max(10, int(round(seconds * fps))) if seconds else 10
    cmd = [PY, CAP, "192.168.50.25", str(frames), f"{RAW}/{name}.raw", "--poll", "1.0", "--note", note_text]
    note(event="capture_start", segment=name, frames=frames, fps_api=fps, api=a["ad9361"])
    out, err = open(f"{RAW}/{name}.stdout", "w"), open(f"{RAW}/{name}.stderr", "w")
    p = subprocess.Popen(cmd, stdout=out, stderr=err)
    if background:
        return p
    rc = p.wait()
    note(event="capture_end", segment=name, rc=rc)
    return rc


def record_iq(name, desc):
    radio.patch("recording/metadata", {"filename": name, "description": desc, "author": "rf session 2026-11-09"}, f"label IQ {name}")
    r = radio.patch("recorder", {"state_change": "Start"}, f"IQ recording {name} (owner recorder settings: IQ12bit, 10 s)")
    t = time.time()
    while time.time() - t < 25 and radio.api()["recorder"]["state"] != "Stopped":
        time.sleep(1)
    note(event="iq_recorded", name=name, started_utc=r["t_utc"], state=radio.api()["recorder"]["state"])


def download_iq(name):
    path = f"{RAW}/{name}.sigmf"
    subprocess.run(["curl", "-s", "-o", path, "http://192.168.50.25/recording"], check=True)
    h = subprocess.run(["sha256sum", path], capture_output=True, text=True).stdout.split()[0]
    note(event="iq_downloaded", name=name, bytes=os.path.getsize(path), sha256=h)


def seg(name, setting, seconds, text, iq=None):
    lo, fs, bw, gain, mode = setting
    r = radio.tune(lo, fs, bw, gain, mode, why=f"{name} setting")
    note(event="tuned", segment=name, request=r["body"], after=r["after"]["ad9361"])
    exp = {"rx_lo_frequency": lo, "sampling_frequency": fs, "rx_rf_bandwidth": bw}
    settle(exp)
    if iq is None:
        return capture(name, seconds, text)
    p = capture(name, seconds, text, background=True)
    time.sleep(iq[0])
    record_iq(iq[1], iq[2])
    rc = p.wait()
    note(event="capture_end", segment=name, rc=rc)
    download_iq(iq[1])
    return rc


