"""Session helper: GET/PATCH the maia-httpd API of the sensor and log every
settings change made by the operator (this script) with wall time."""
import json, sys, time, urllib.request

HOST = "192.168.50.25"
LOG = "/home/cvalentine/tezuka-live-audit/rf-session-20261109/operator_actions.jsonl"


def api():
    return json.load(urllib.request.urlopen(f"http://{HOST}/api", timeout=10))


def patch(section, body, why):
    t0 = time.time()
    req = urllib.request.Request(f"http://{HOST}/api/{section}", data=json.dumps(body).encode(),
                                 method="PATCH", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        status, text = r.status, r.read().decode(errors="replace")
    after = api()
    rec = {"t_unix_s": round(t0, 3), "t_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)), "section": section,
           "body": body, "why": why, "http_status": status, "response": text[:500],
           "after": {k: after.get(k) for k in ("ad9361", "ddc", "spectrometer", "recorder")}}
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def tune(lo, fs, bw, gain, mode="Manual", why=""):
    body = {"sampling_frequency": fs, "rx_rf_bandwidth": bw, "rx_lo_frequency": lo, "rx_gain_mode": mode}
    if mode == "Manual":
        body["rx_gain"] = gain
    return patch("ad9361", body, why)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "get":
        a = api(); print(json.dumps({k: a[k] for k in ("ad9361", "spectrometer", "ddc", "recorder")}, indent=1))
    elif cmd == "tune":
        lo, fs, bw, gain, mode = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), float(sys.argv[5]), sys.argv[6]
        r = tune(lo, fs, bw, gain, mode, why=" ".join(sys.argv[7:]))
        print(json.dumps(r, indent=1))
