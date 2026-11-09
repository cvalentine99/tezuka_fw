"""Join several captures taken at the same frame rate into one replay
sequence, with each later capture's t = 0 settings placed just before its
first frame (the retune as the live daemon saw it) and its own later events
shifted by the frames before it. One daemon run over the chain keeps the
background and the spur map across the retunes, as the live daemon did.
usage: chain_replay.py <out.raw> <fps> <segment> [<segment> ...]"""
import json, sys
SESS = "/home/cvalentine/tezuka-live-audit/rf-session-20261109/raw"
out, fps, segs = sys.argv[1], float(sys.argv[2]), sys.argv[3:]
N = 4096 * 4
ev, frames_before, bounds = [], 0, []
with open(out, "wb") as fo:
    for k, s in enumerate(segs):
        m = json.load(open(f"{SESS}/{s}.raw.meta.json"))
        assert abs(m["fps_api_start"] - fps) < 1e-6, (s, m["fps_api_start"])
        data = open(f"{SESS}/{s}.raw", "rb").read()
        assert len(data) % N == 0
        t_off = 0.0 if k == 0 else (frames_before - 0.5) / fps
        for line in open(f"{SESS}/{s}.raw.events"):
            if not line.strip() or line.startswith("#"):
                continue
            t, rest = line.split(" ", 1)
            t = float(t)
            ev.append((t_off if (k > 0 and t == 0.0) else t + (frames_before / fps if k > 0 else 0.0), rest.strip()))
        bounds.append({"segment": s, "first_frame": frames_before, "frames": len(data) // N, "t_start_s": round(frames_before / fps, 3)})
        fo.write(data)
        frames_before += len(data) // N
with open(out + ".events", "w") as fe:
    fe.write(f"# chained replay events for {out}: segments {' '.join(segs)} at {fps} frames/s\n")
    for t, rest in sorted(ev, key=lambda e: e[0]):
        fe.write(f"{t:.6f} {rest}\n")
json.dump({"fps": fps, "segments": bounds, "frames": frames_before}, open(out + ".chain.json", "w"), indent=1)
print(json.dumps(bounds))
