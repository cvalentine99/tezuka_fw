"""Per-cycle table of a replay output (classifier -r, observation lines
'<t> <topic>/observation <json>'), with the matched window and the residual
peak in Hz on the observation's own grid. usage: replay_obs.py <file.out> [chan_lo chan_hi]"""
import json, sys, collections
rows = []
for line in open(sys.argv[1]):
    p = line.split(" ", 2)
    if len(p) == 3 and p[1].endswith("/observation"):
        rows.append((float(p[0]), json.loads(p[2])))
lo = float(sys.argv[2]) if len(sys.argv) > 3 else None
hi = float(sys.argv[3]) if len(sys.argv) > 3 else None
c = collections.Counter(); wins = collections.Counter()
for t, o in rows:
    d, cfg, i, f = o["decision"], o["config"], o["input"], o.get("features") or {}
    c[d["label"]] += 1
    if d.get("accepted"):
        fft, fc, span = i["fft_size"], cfg["frequency_hz"], cfg["span_hz"]
        hzb = span / fft
        s = fc + (d["bin_offset"] + 1 - fft / 2) * hzb - hzb / 2
        e = s + d["bandwidth_bins"] * hzb
        key = (d["label"], round(s / 1e3), round(e / 1e3))
        wins[key] += 1
print("decisions", len(rows), dict(c))
print("accepted windows (label, start kHz, end kHz): count")
for k, v in wins.most_common(15):
    tag = ""
    if lo is not None:
        tag = " overlaps channel" if (k[1] * 1e3 < hi and k[2] * 1e3 > lo) else " outside channel"
    print("  ", k, v, tag)
mb = [o["background"].get("masked_bins") for _, o in rows]
print("masked_bins first/last/max", mb[0], mb[-1], max(mb), "learn_count last", rows[-1][1]["background"].get("learn_count"))
