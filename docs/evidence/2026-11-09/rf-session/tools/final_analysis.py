"""Window-level results for every segment, live and replayed: each accepted
window is placed against the two RDS-verified FM channels (KIGL 93.2-93.4 MHz,
KAMO 94.2-94.4 MHz, US 200 kHz channels) so a label is counted as right only
when its window sits on the emission it names. Writes JSON and Markdown."""
import json, collections, os
SESS = "/home/cvalentine/tezuka-live-audit/rf-session-20261109"
CH = {"KIGL 93.3 (RDS verified)": (93.2e6, 93.4e6), "KAMO 94.3 (RDS verified)": (94.2e6, 94.4e6)}
SEGS = ["P0_positive_check", "S1_kigl_offset", "S1b_kigl_on_lo", "S1c_kigl_lowgain", "S2_433", "S3_70", "S4_2400_agc", "S5_retune"]

def win(o):
    d = o["decision"]
    if not d.get("accepted"):
        return None
    fft, fc, span = o["input"]["fft_size"], o["config"]["frequency_hz"], o["config"]["span_hz"]; hzb = span / fft
    s = fc + (d["bin_offset"] + 1 - fft / 2) * hzb - hzb / 2
    return d["label"], s, s + d["bandwidth_bins"] * hzb

def where(s, e):
    for name, (a, b) in CH.items():
        if s < b and e > a:
            return name
    return "elsewhere"

def summarise(obs):
    lab = collections.Counter(o["decision"]["label"] for o in obs)
    cells = collections.Counter()
    for o in obs:
        w = win(o)
        if w:
            cells[(w[0], where(w[1], w[2]))] += 1
    return {"decisions": len(obs), "labels": dict(lab), "accepted_by_label_and_place": {f"{k[0]} @ {k[1]}": v for k, v in sorted(cells.items(), key=lambda kv: -kv[1])}}

live_rows = []
for line in open(SESS + "/mqtt_session.jsonl"):
    r = json.loads(line)
    if r.get("topic") == "state/classifier/observation" and not r.get("retained") and r.get("json"):
        live_rows.append((r["t"], r["json"]))

def replay_rows(path):
    out = []
    if not os.path.exists(path):
        return None
    for line in open(path):
        p = line.split(" ", 2)
        if len(p) == 3 and p[1].endswith("/observation"):
            out.append(json.loads(p[2]))
    return out

res = {}
for s in SEGS:
    mp = f"{SESS}/raw/{s}.raw.meta.json"
    if not os.path.exists(mp):
        continue
    m = json.load(open(mp))
    live = [o for t, o in live_rows if m["first_frame_unix_s"] <= t <= m["last_frame_unix_s"]]
    entry = {"live": summarise(live)}
    for d in ("p0", "final"):
        rr = replay_rows(f"{SESS}/replay/{d}/runs/{s}__deployed.out") or replay_rows(f"{SESS}/replay/{d}/runs/P0_kigl__deployed.out" if s == "P0_positive_check" and d == "p0" else "/nonexistent")
        if rr:
            entry["replay_cold"] = summarise(rr)
    bg = [o["background"] for o in live]
    entry["live_mask"] = {"masked_last": bg[-1]["masked_bins"] if bg else None, "spur_last": bg[-1]["spur_bins"] if bg else None,
                          "unlearned_cycles": sum(1 for b in bg if not b["learned"]), "learns": (bg[-1]["learn_count"] - bg[0]["learn_count"]) if bg else None,
                          "template_sha256": sorted({o["templates"]["sha256"] for o in live})}
    res[s] = entry
ch = SESS + "/replay/chain_split.json"
if os.path.exists(ch):
    res["_chain_note"] = "chain replay (P0..S3 as one daemon run) per segment, see chain_split.json"
json.dump(res, open(SESS + "/final_analysis.json", "w"), indent=1)
lines = ["| Segment | Run | Decisions | Labels | Accepted windows by place |", "|---|---|---|---|---|"]
for s, e in res.items():
    if s.startswith("_"):
        continue
    for run in ("live", "replay_cold"):
        if run in e:
            x = e[run]
            lines.append(f"| {s} | {run.replace('_', ' ')} | {x['decisions']} | " + ", ".join(f"{k} {v}" for k, v in x["labels"].items()) +
                         " | " + "; ".join(f"{k}: {v}" for k, v in x["accepted_by_label_and_place"].items()) + " |")
open(SESS + "/final_analysis.md", "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
