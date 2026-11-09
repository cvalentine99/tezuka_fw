"""Summarise live classifier observations logged by mqtt_logger.py inside a time
window, and test each accepted matched window against an intended channel.
usage: obs_window.py <mqtt_session.jsonl> <t0_unix> <t1_unix> <chan_lo_hz> <chan_hi_hz> [label]
Frequencies use the observation's own grid: usable bin j centred at
f_c + (j + 1 - fft/2) * span / fft; the window [off, off+bw) runs from half a bin
below its first centre to half a bin above its last."""
import json, sys, collections

path, t0, t1, lo, hi = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])
want = sys.argv[6] if len(sys.argv) > 6 else None
rows = []
for line in open(path):
    r = json.loads(line)
    if r.get("topic") != "state/classifier/observation" or r.get("retained") or not r.get("json"):
        continue
    if not (t0 <= r["t"] <= t1):
        continue
    rows.append(r["json"])
out = {"observations": len(rows), "seq_first": rows[0]["seq"] if rows else None, "seq_last": rows[-1]["seq"] if rows else None}
labels = collections.Counter(o["decision"]["label"] for o in rows)
out["labels"] = dict(labels)
out["template_sha256"] = sorted({o["templates"]["sha256"] for o in rows})
out["config_generations"] = sorted({o["config"]["generation"] for o in rows})
out["learned_false_cycles"] = sum(1 for o in rows if not o["background"]["learned"])
mb = [o["background"].get("masked_bins") for o in rows]
vb = [o["background"].get("valid_bins") for o in rows]
out["masked_bins"] = {"min": min(mb), "max": max(mb), "last": mb[-1]} if mb else None
out["valid_bins"] = {"min": min(vb), "max": max(vb), "last": vb[-1]} if vb else None
out["spur_bins_last"] = rows[-1]["background"].get("spur_bins") if rows else None
acc, overl, contains_centre, peak_in, centres, scores = 0, 0, 0, 0, [], []
peak_hz = []
for o in rows:
    d, c, i, f = o["decision"], o["config"], o["input"], o.get("features") or {}
    fft, fc, span = i["fft_size"], c["frequency_hz"], c["span_hz"]
    hzb = span / fft
    cen = lambda j: fc + (j + 1 - fft / 2) * hzb
    if f.get("valid") and f.get("peak_bin") is not None:
        peak_hz.append(cen(f["peak_bin"]))
    if not d.get("accepted"):
        continue
    if want and d["label"] != want:
        continue
    acc += 1
    s = cen(d["bin_offset"]) - hzb / 2
    e = s + d["bandwidth_bins"] * hzb
    centres.append((s + e) / 2)
    scores.append(d["score"])
    if s < hi and e > lo:
        overl += 1
    if s <= (lo + hi) / 2 <= e:
        contains_centre += 1
    if f.get("valid") and d["bin_offset"] <= f["peak_bin"] < d["bin_offset"] + d["bandwidth_bins"]:
        peak_in += 1
    last_window = (round(s), round(e))
out.update({"accepted" + (f"_{want}" if want else ""): acc, "window_overlaps_channel": overl,
            "window_contains_channel_centre": contains_centre, "whole_span_peak_inside_window": peak_in,
            "window_centre_hz": {"min": round(min(centres)), "max": round(max(centres)), "mean": round(sum(centres) / len(centres))} if centres else None,
            "window_last_hz": last_window if acc else None,
            "score": {"min": min(scores), "max": max(scores)} if scores else None,
            "whole_span_peak_hz": {"min": round(min(peak_hz)), "max": round(max(peak_hz))} if peak_hz else None,
            "channel_hz": [lo, hi]})
print(json.dumps(out, indent=1))
