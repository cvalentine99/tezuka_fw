"""Live-observation report per captured segment: everything the sensor's own
daemon published between a segment's first and last frame (from the
mqtt_logger JSONL), next to the capture's meta. Accepted windows are converted
to Hz on each observation's own grid. usage: seg_report.py <segment> [<segment> ...]"""
import json, sys, collections
SESS = "/home/cvalentine/tezuka-live-audit/rf-session-20261109"
obs, rx = [], []
for line in open(SESS + "/mqtt_session.jsonl"):
    r = json.loads(line)
    if r.get("retained"):
        continue
    if r.get("topic") == "state/classifier/observation" and r.get("json"):
        obs.append((r["t"], r["json"]))
    elif r.get("topic", "").startswith("state/rx/"):
        rx.append((r["t"], r["topic"], r.get("payload")))
report = {}
for seg in sys.argv[1:]:
    m = json.load(open(f"{SESS}/raw/{seg}.raw.meta.json"))
    t0, t1 = m["first_frame_unix_s"], m["last_frame_unix_s"]
    rows = [o for t, o in obs if t0 <= t <= t1]
    lab = collections.Counter(o["decision"]["label"] for o in rows)
    wins = collections.Counter()
    for o in rows:
        d = o["decision"]
        if not d.get("accepted"):
            continue
        fft, fc, span = o["input"]["fft_size"], o["config"]["frequency_hz"], o["config"]["span_hz"]
        hzb = span / fft
        s = fc + (d["bin_offset"] + 1 - fft / 2) * hzb - hzb / 2
        wins[(d["label"], round(s / 1e3), round((s + d["bandwidth_bins"] * hzb) / 1e3))] += 1
    bg = [o["background"] for o in rows]
    gens = sorted({o["config"]["generation"] for o in rows})
    learns = sorted({b.get("learn_count") for b in bg})
    report[seg] = {
        "capture": {k: m.get(k) for k in ("frames", "measured_fps", "first_frame_unix_s", "last_frame_unix_s", "sha256", "aborted", "interrupted", "settings_changed_during_capture")},
        "settings_at_start": m["api"]["ad9361"], "settings_log": m["settings_log"],
        "live": {
            "observations": len(rows), "seq": [rows[0]["seq"], rows[-1]["seq"]] if rows else None,
            "labels": dict(lab), "accepted_windows_khz": [[k[0], k[1], k[2], v] for k, v in wins.most_common(12)],
            "distinct_accepted_windows": len(wins),
            "template_sha256": sorted({o["templates"]["sha256"] for o in rows}),
            "template_status": sorted({o["templates"]["status"] for o in rows}),
            "config_generations": gens, "learn_count": learns,
            "unlearned_cycles": sum(1 for b in bg if not b.get("learned")),
            "masked_bins": {"first": bg[0].get("masked_bins"), "last": bg[-1].get("masked_bins"), "min": min(b.get("masked_bins") for b in bg), "max": max(b.get("masked_bins") for b in bg)} if bg else None,
            "spur_bins": {"first": bg[0].get("spur_bins"), "last": bg[-1].get("spur_bins")} if bg else None,
            "valid_bins_last": bg[-1].get("valid_bins") if bg else None,
            "publish_errors_last": rows[-1]["health"].get("publish_errors") if rows else None,
        },
        "rx_topic_changes_during": [[round(t - t0, 1), tp, p] for t, tp, p in rx if t0 <= t <= t1],
    }
print(json.dumps(report, indent=1))
