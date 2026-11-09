"""Split a chained replay's observations back into its segments (by the
replay clock) and summarise each like seg_report.py does for live data.
usage: chain_split.py <replay.out> <chain.json>"""
import json, sys, collections
rows = []
for line in open(sys.argv[1]):
    p = line.split(" ", 2)
    if len(p) == 3 and p[1].endswith("/observation"):
        rows.append((float(p[0]), json.loads(p[2])))
ch = json.load(open(sys.argv[2])); fps = ch["fps"]
out = {}
for b in ch["segments"]:
    t0, t1 = b["first_frame"] / fps, (b["first_frame"] + b["frames"]) / fps
    seg = [o for t, o in rows if t0 <= t < t1]
    lab = collections.Counter(o["decision"]["label"] for o in seg)
    wins = collections.Counter()
    for o in seg:
        d = o["decision"]
        if not d.get("accepted"): continue
        fft, fc, span = o["input"]["fft_size"], o["config"]["frequency_hz"], o["config"]["span_hz"]; hzb = span / fft
        s = fc + (d["bin_offset"] + 1 - fft / 2) * hzb - hzb / 2
        wins[(d["label"], round(s / 1e3), round((s + d["bandwidth_bins"] * hzb) / 1e3))] += 1
    bg = [o["background"] for o in seg]
    out[b["segment"]] = {"decisions": len(seg), "labels": dict(lab), "top_windows_khz": [[*k, v] for k, v in wins.most_common(6)],
                         "masked_bins_last": bg[-1].get("masked_bins") if bg else None, "spur_bins_last": bg[-1].get("spur_bins") if bg else None,
                         "learn_count_last": bg[-1].get("learn_count") if bg else None}
print(json.dumps(out, indent=1))
