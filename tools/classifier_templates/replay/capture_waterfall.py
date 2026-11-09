"""capture_waterfall.py - read-only capture of a sensor's /waterfall feed,
with the receiver settings logged throughout.

Writes the raw float32 websocket messages (step word included) back to back:
exactly the sequence the classifier daemon replays with -r, and the layout
rf_compare.py --recorded expects. Nothing on the sensor is changed: the tool
only subscribes to the websocket and GETs /api.

Next to the raw file it writes, also when the capture is cut short:

  <out>.meta.json   receiver settings (/api) before and after, capture
                    boundaries (wall-clock start/end, first/last frame),
                    measured frame rate and inter-arrival statistics, the
                    file's SHA-256, and a settings log: /api is polled every
                    --poll seconds and every change of the watched receiver
                    or spectrometer settings is recorded with its wall time,
                    the number of frames received so far and the replay time
                    that corresponds to it (frames / frame rate). If the
                    capture is aborted (connection lost, no frame for
                    --timeout seconds, a failing /api call) the meta file
                    says so in "aborted" and keeps everything seen so far.
  <out>.events      the same changes as classifier replay events
                    ('<t_seconds> <topic> <payload>', the daemon's -E), with
                    the initial frequency and span at t = 0, so a capture
                    during which the operator retuned replays with the
                    retunes the daemon would have heard over MQTT. Each
                    event is placed half a frame before the first frame
                    received after the change was seen ((frames - 0.5) /
                    frame rate), so the daemon delivers it before that frame
                    whatever the rounding of the replay rate.

Limits, stated so a reader of the files knows them:

* The settings log is built from /api snapshots. A change is seen up to
  --poll seconds after it happened, so an event may be late by up to that
  many frames, and a change that is undone before the next poll (a retune
  and an immediate revert) leaves no trace at all, although a daemon
  subscribed to MQTT would have seen both. Keep retunes deliberate and at
  least a few poll intervals apart; the operator's own notes of the retune
  times are kept alongside the log.
* If the spectrometer frame rate changes during a capture (a sampling-rate
  change), the events after it are placed with the initial rate and the meta
  file flags it; replay such a capture in pieces.
* The frame rate the daemon replays with (-F) must be the one in the meta
  file (fps_api_start) for the event times to fall where they were seen.

    python3 capture_waterfall.py <host> <frames> <out.raw> [--poll 1.0] [--port 80] [--timeout 60] [--note TEXT]
"""
import argparse
import asyncio
import hashlib
import json
import statistics
import sys
import time
import traceback
import urllib.request

import websockets

WATCH_RX = ("rx_lo_frequency", "sampling_frequency", "rx_rf_bandwidth", "rx_gain", "rx_gain_mode")
WATCH_SPEC = ("fft_size", "number_integrations", "mode", "output_sampling_frequency", "input")
# replay event topics for the receiver settings the daemon watches
EVENT_TOPIC = {"rx_lo_frequency": "state/rx/frequency", "sampling_frequency": "state/rx/span",
               "rx_rf_bandwidth": "state/rx/bandwidth", "rx_gain": "state/rx/gain", "rx_gain_mode": "state/rx/gain_mode"}


def get_api(base):
    with urllib.request.urlopen(f"{base}/api", timeout=10) as r:
        return json.load(r)


def watched(api):
    rx = api.get("ad9361") or {}
    sp = api.get("spectrometer") or {}
    return {**{k: rx.get(k) for k in WATCH_RX}, **{"spectrometer." + k: sp.get(k) for k in WATCH_SPEC}}


def fmt_payload(v):
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def event_time(frames_received, fps):
    """Replay time of an event seen after FRAMES_RECEIVED frames: half a
    frame before frame index FRAMES_RECEIVED, so the daemon (which delivers
    an event before frame k when t <= k / F) delivers it before that frame
    even if F is given with fewer digits than the API reports."""
    return (frames_received - 0.5) / fps


async def capture(args, meta, base):
    got = 0
    first_t = last_t = None
    gaps = []
    stop = asyncio.Event()
    state = {"got": 0}

    async def poll():
        prev = watched(meta["api"])
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=args.poll)
                break
            except asyncio.TimeoutError:
                pass
            try:
                api = await asyncio.get_running_loop().run_in_executor(None, get_api, base)
            except Exception as e:                       # the sensor may be busy; keep capturing
                meta["poll_errors"].append({"t_unix_s": time.time(), "error": str(e)})
                continue
            cur = watched(api)
            changed = {k: [prev.get(k), cur.get(k)] for k in cur if cur.get(k) != prev.get(k)}
            if changed:
                fps = meta["fps_api_start"]
                meta["settings_log"].append({"t_unix_s": time.time(), "t_rel_s": time.time() - meta["started_unix_s"],
                                             "frames_received": state["got"],
                                             "t_replay_s": state["got"] / fps if fps else None,
                                             "t_event_s": event_time(state["got"], fps) if fps and state["got"] > 0 else None,
                                             "changed": changed, "ad9361": api.get("ad9361"), "spectrometer": api.get("spectrometer")})
                prev = cur

    poller = asyncio.create_task(poll())
    try:
        with open(args.out, "wb") as f:
            async with websockets.connect(f"ws://{args.host}:{args.port}/waterfall", max_size=None) as ws:
                while got < args.frames:
                    msg = await asyncio.wait_for(ws.recv(), timeout=args.timeout)
                    if not isinstance(msg, (bytes, bytearray)):
                        continue
                    now = time.time()
                    f.write(msg)
                    got += 1
                    state["got"] = got
                    if first_t is None:
                        first_t = now
                    else:
                        gaps.append(now - last_t)
                    last_t = now
                    meta["frame_sizes"][str(len(msg))] = meta["frame_sizes"].get(str(len(msg)), 0) + 1
    except asyncio.TimeoutError:
        meta["aborted"] = f"no frame received for {args.timeout} s after {got} frames"
    except Exception as e:
        meta["aborted"] = f"{type(e).__name__}: {e} after {got} frames"
    finally:
        stop.set()
        try:
            await poller
        except Exception as e:                           # never lose the capture over the poller
            meta["poll_errors"].append({"t_unix_s": time.time(), "error": f"poller: {type(e).__name__}: {e}"})
    meta["frames"] = got
    meta["first_frame_unix_s"] = first_t
    meta["last_frame_unix_s"] = last_t
    if got >= 2:
        meta["measured_fps"] = (got - 1) / (last_t - first_t)
        meta["frame_gap_s"] = {"min": min(gaps), "median": statistics.median(gaps), "max": max(gaps)}


def write_outputs(args, meta, api):
    """The meta and events files, written whatever happened to the capture."""
    meta["ended_unix_s"] = time.time()
    try:
        with open(args.out, "rb") as f:
            meta["sha256"] = hashlib.sha256(f.read()).hexdigest()
    except OSError as e:
        meta["sha256"] = None
        meta.setdefault("errors", []).append(f"hash: {e}")

    rx0 = (api or {}).get("ad9361") or {}
    lines = [f"# replay events for {args.out}: generated by capture_waterfall.py from the /api settings log",
             f"# host {args.host}, frame rate at start {meta['fps_api_start']} frames/s; "
             f"event time = (frames received - 0.5) / that rate, i.e. just before the first frame after the change was seen",
             f"# a change is seen up to {args.poll} s after it happened (poll interval); a change undone within one poll interval is not seen"]
    if meta.get("aborted"):
        lines.append(f"# capture aborted: {meta['aborted']}")
    if rx0.get("rx_lo_frequency") is not None:
        lines.append(f"0 state/rx/frequency {fmt_payload(rx0['rx_lo_frequency'])}")
    if rx0.get("sampling_frequency") is not None:
        lines.append(f"0 state/rx/span {fmt_payload(rx0['sampling_frequency'])}")
    for e in meta["settings_log"]:
        if e.get("t_event_s") is None:
            lines.append(f"# change after {e['frames_received']} frames not placed: no frame rate known ({e['changed']})")
            continue
        for key, (old, new) in e["changed"].items():
            if key in EVENT_TOPIC and new is not None:
                lines.append(f"# frames_received {e['frames_received']}, wall t+{e['t_rel_s']:.1f} s, {key} {old} -> {new}")
                lines.append(f"{e['t_event_s']:.6f} {EVENT_TOPIC[key]} {fmt_payload(new)}")
    with open(args.out + ".events", "w") as f:
        f.write("\n".join(lines) + "\n")
    meta["events_file"] = args.out + ".events"
    with open(args.out + ".meta.json", "w") as f:
        json.dump(meta, f, indent=1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("host")
    ap.add_argument("frames", type=int)
    ap.add_argument("out")
    ap.add_argument("--port", type=int, default=80, help="HTTP and websocket port of maia-httpd (default 80)")
    ap.add_argument("--poll", type=float, default=1.0, help="seconds between /api polls for the settings log (default 1.0)")
    ap.add_argument("--timeout", type=float, default=60.0, help="abort when no frame arrives for this many seconds (default 60)")
    ap.add_argument("--note", default="", help="free text stored in the meta file (segment name, what is on air, operator)")
    args = ap.parse_args(argv)
    base = f"http://{args.host}:{args.port}"

    api = get_api(base)                                  # no sensor, no capture: fail before creating files
    sp = api.get("spectrometer") or {}
    meta = {"host": args.host, "port": args.port, "note": args.note, "captured_unix_s": time.time(), "started_unix_s": time.time(),
            "api": {k: api.get(k) for k in ("ad9361", "spectrometer", "ddc", "versions")},
            "fps_api_start": sp.get("output_sampling_frequency"),
            "poll_s": args.poll, "timeout_s": args.timeout, "frames_requested": args.frames,
            "frames": 0, "frame_sizes": {}, "settings_log": [], "poll_errors": [], "aborted": None}
    try:
        asyncio.run(capture(args, meta, base))
    except Exception as e:                               # setup failure (connect refused, bad host): still record it
        meta["aborted"] = meta.get("aborted") or f"{type(e).__name__}: {e}"
        meta["traceback"] = traceback.format_exc()
    try:
        after = get_api(base)
        meta["api_after"] = after.get("ad9361")
        meta["api_after_full"] = {k: after.get(k) for k in ("ad9361", "spectrometer", "ddc")}
        meta["settings_changed_during_capture"] = bool(meta["settings_log"]) or watched(api) != watched(after)
    except Exception as e:
        meta["api_after"] = None
        meta["api_after_full"] = None
        meta["settings_changed_during_capture"] = bool(meta["settings_log"]) or None
        meta.setdefault("errors", []).append(f"api after: {type(e).__name__}: {e}")
    meta["fps_changed_during_capture"] = any("spectrometer.output_sampling_frequency" in e["changed"] for e in meta["settings_log"])
    write_outputs(args, meta, api)
    print(json.dumps(meta))
    if meta.get("aborted"):
        print(f"capture_waterfall: aborted: {meta['aborted']} (meta and events written)", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
