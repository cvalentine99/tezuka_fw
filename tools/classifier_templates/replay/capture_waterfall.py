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
                    file's size and SHA-256, and a settings log: /api is
                    polled every --poll seconds and every change of the
                    watched receiver or spectrometer settings is recorded
                    with its wall time, the number of frames received so far
                    and the replay time that corresponds to it (frames /
                    frame rate). If the capture is aborted (connection lost,
                    no frame for --timeout seconds, Ctrl+C) the meta file
                    says so in "aborted" and keeps everything seen so far. A
                    failed /api poll is recorded in "poll_errors" and the
                    capture continues; a failed final /api request is
                    recorded in "errors" (api_after is then null). If the
                    first /api request fails nothing is captured and no file
                    is written (exit status 2).
  <out>.events      the same changes as classifier replay events
                    ('<t_seconds> <topic> <payload>', the daemon's -E), so a
                    capture during which the operator retuned replays with
                    the retunes the daemon would have heard over MQTT. The
                    initial value of every receiver setting that /api
                    exposes and the daemon watches (EVENT_TOPIC: frequency,
                    sampling rate, span, RF bandwidth, gain, gain mode) is
                    written at t = 0: the daemon takes the first value of a
                    topic as initialisation and only a later different value
                    as a change, so an unseeded setting's first change would
                    be silently ignored in replay. A change seen before the
                    first frame is folded into the t = 0 value. Each later
                    change is placed half a frame before the first frame
                    received after it was seen ((frames - 0.5) / frame
                    rate), so the daemon delivers it before that frame
                    whatever the rounding of the replay rate.

The span event (state/rx/span) is the span of the frames: the
spectrometer's input rate, which is the AD9361 rate in single-band mode and
the DDC output rate when the sensor runs a span below 2.4 MHz through the
DDC (the AD9361 then samples at least 4 times faster than the frames cover:
decimation floor(2.4 MHz / span), minimum 4, no upper clamp, so 24x at a
100 kHz span).
state/rx/sampling carries the AD9361 rate, as on the sensor. A capture taken
with the spectrometer fed by the DDC is flagged in the meta file
("spectrometer_input") and on stderr.

Payloads are the /api representation (e.g. gain mode 'Manual'). For gain,
gain mode and bandwidth the daemon only compares a topic's payload with its
previous one, so their spelling does not matter as long as it is consistent
within a file; span, sampling rate and frequency are also parsed as numbers
(the span sets the hertz per bin, frequency and sampling rate are reported
in the observation), so they are written as plain decimal hertz.

Stopping early: Ctrl+C (SIGINT) ends the capture. The frames received so
far stay in the raw file, the meta and events files are written with
"aborted": "interrupted (SIGINT) after N frames", the raw file is hashed,
and the exit status is 130. The sidecars appear a few seconds after Ctrl+C
even when the sensor no longer answers: the websocket close is bounded by
1 s (about 3 s with the legacy websockets client, versions before 13,
which applies the bound to each close step), an /api poll already in
flight by 2 s, and the final /api request by 2 s; measured 3.7 s against a
frozen sensor with websockets 17, 5.8 s with websockets 10. From the
moment the sidecars are written until the process exits a further SIGINT
is ignored, so they are never half written and the exit status stays as
documented. A Ctrl+C that arrives after the last requested frame is on
disk (during the final /api request) does not make the capture aborted:
the meta file then says "interrupted": true with "aborted": null and the
exit status is 0. A SIGINT disposition the process inherited as ignored
(a background job of a script, nohup) is left alone: such a capture is
not interruptible by Ctrl+C, as before. Exit status 2 means aborted for
another reason (connection lost, no frame for --timeout seconds); 0 means
complete, also when /api polls or the final /api request failed (see the
meta file's poll_errors and errors). The timeouts do not cover name
resolution: give the sensor by IP address (as the session plan does) so a
vanished sensor cannot add the resolver's own timeout (about 5 s per
lookup of an mDNS .local name) before the sidecars appear.

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
  file flags it; replay such a capture in pieces. A rate change seen before
  the first frame is not that case: no frame was produced at the old rate,
  so the tool switches to the new rate (fps_api_start; the rate read before
  the capture is kept as fps_api_initial).
* Every logged change of frequency, sampling rate, span, bandwidth, gain or
  gain mode makes the daemon relearn its background in replay, as the
  corresponding MQTT message does on the sensor. Under AGC the gain steps
  seen by the polls therefore become relearns; that is the sensor's
  behaviour, not an artefact of the tool, and the events file reproduces it.
* The daemon also resets its background on state/rx/rfinput,
  state/rx/sweep/activate and state/rx/sweep/engaged (RX_WATCH in
  classifier.c); these are not in /api, so the tool neither seeds nor logs
  them. The session plan forbids changing them during a segment.
* The frame rate the daemon replays with (-F) must be the one in the meta
  file (fps_api_start) for the event times to fall where they were seen.

    python3 capture_waterfall.py <host> <frames> <out.raw> [--poll 1.0] [--port 80] [--timeout 60] [--note TEXT]
"""
import argparse
import asyncio
import hashlib
import json
import os
import signal
import statistics
import sys
import time
import traceback
import urllib.request

import websockets

WATCH_RX = ("rx_lo_frequency", "sampling_frequency", "rx_rf_bandwidth", "rx_gain", "rx_gain_mode")
WATCH_SPEC = ("fft_size", "number_integrations", "mode", "output_sampling_frequency", "input", "input_sampling_frequency")
# replay event topics for the receiver settings the daemon watches and /api
# exposes; the t = 0 lines and the change lines are written in this order
# ("span" is derived, see frame_span_hz()). api_controller.sh itself publishes
# rx/sampling, rx/bandwidth, then rx/span after a rate change; the daemon's
# result does not depend on the order (each topic resets independently)
EVENT_TOPIC = {"rx_lo_frequency": "state/rx/frequency", "sampling_frequency": "state/rx/sampling", "span": "state/rx/span",
               "rx_rf_bandwidth": "state/rx/bandwidth", "rx_gain": "state/rx/gain", "rx_gain_mode": "state/rx/gain_mode"}
CLOSE_TIMEOUT_S = 1.0           # websocket close bound (whole handshake with websockets >= 13, each close step before)
API_TIMEOUT_S = 10.0            # first and final /api request
API_TIMEOUT_INTERRUPTED_S = 2.0  # final /api request after Ctrl+C
POLL_TIMEOUT_S = 2.0            # an /api poll; a sensor answers in milliseconds, and a poll in flight at Ctrl+C is waited for


def get_api(base, timeout=API_TIMEOUT_S):
    with urllib.request.urlopen(f"{base}/api", timeout=timeout) as r:
        return json.load(r)


def frame_span_hz(api):
    """Span covered by the frames: the spectrometer's input rate (the DDC
    output rate when the spectrometer input is the DDC, the AD9361 rate
    otherwise); the AD9361 rate when the spectrometer does not report it."""
    sp = (api or {}).get("spectrometer") or {}
    rx = (api or {}).get("ad9361") or {}
    v = sp.get("input_sampling_frequency")
    return rx.get("sampling_frequency") if v is None else v


def watched(api):
    rx = api.get("ad9361") or {}
    sp = api.get("spectrometer") or {}
    return {**{k: rx.get(k) for k in WATCH_RX}, "span": frame_span_hz(api),
            **{"spectrometer." + k: sp.get(k) for k in WATCH_SPEC}}


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
    """Receive frames until ARGS.frames, the connection drops, the feed
    stops, or SIGINT. Every Ctrl+C cancels this task (own signal handler,
    so the behaviour does not depend on asyncio.run's, which Python 3.10
    lacks). Whatever ends it, the frame count, boundaries and gap statistics
    are finalised in the finally clause before any await, so the meta file
    is right for a partial capture too."""
    got = 0
    first_t = last_t = None
    gaps = []
    stop = asyncio.Event()
    state = {"got": 0}
    interrupted = False

    async def poll():
        prev = watched(meta["api"])
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=args.poll)
                break
            except asyncio.TimeoutError:
                pass
            try:
                api = await asyncio.get_running_loop().run_in_executor(None, get_api, base, POLL_TIMEOUT_S)
            except Exception as e:                       # the sensor may be busy; keep capturing
                meta["poll_errors"].append({"t_unix_s": time.time(), "error": str(e)})
                continue
            cur = watched(api)
            changed = {k: [prev.get(k), cur.get(k)] for k in cur if cur.get(k) != prev.get(k)}
            if changed:
                if state["got"] == 0 and changed.get("spectrometer.output_sampling_frequency", [None, None])[1]:
                    # no frame was produced at the old rate: the capture runs at the new one
                    meta.setdefault("fps_api_initial", meta["fps_api_start"])
                    meta["fps_api_start"] = changed["spectrometer.output_sampling_frequency"][1]
                fps = meta["fps_api_start"]
                meta["settings_log"].append({"t_unix_s": time.time(), "t_rel_s": time.time() - meta["started_unix_s"],
                                             "frames_received": state["got"],
                                             "t_replay_s": state["got"] / fps if fps else None,
                                             "t_event_s": event_time(state["got"], fps) if fps and state["got"] > 0 else None,
                                             "changed": changed, "ad9361": api.get("ad9361"), "spectrometer": api.get("spectrometer")})
                prev = cur

    loop = asyncio.get_running_loop()
    me = asyncio.current_task()
    own_handler = False
    if signal.getsignal(signal.SIGINT) is not signal.SIG_IGN:  # a background job was handed SIGINT ignored: keep it so
        try:
            loop.add_signal_handler(signal.SIGINT, me.cancel)
            own_handler = True
        except (NotImplementedError, RuntimeError, ValueError):  # Windows, or not the main thread: asyncio.run's handler
            pass
    poller = asyncio.create_task(poll())
    try:
        with open(args.out, "wb") as f:
            async with websockets.connect(f"ws://{args.host}:{args.port}/waterfall", max_size=None,
                                          close_timeout=CLOSE_TIMEOUT_S) as ws:
                while got < args.frames:
                    msg = await asyncio.wait_for(ws.recv(), timeout=args.timeout)
                    if not isinstance(msg, (bytes, bytearray)):
                        continue
                    now = time.time()
                    f.write(msg)
                    f.flush()                            # the file length always counts whole frames
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
    except (asyncio.CancelledError, KeyboardInterrupt):  # Ctrl+C
        interrupted = True
        if got < args.frames:
            meta["aborted"] = f"interrupted (SIGINT) after {got} frames"
        else:
            print("capture_waterfall: SIGINT after the last frame; the capture is complete", file=sys.stderr)
    except Exception as e:
        meta["aborted"] = f"{type(e).__name__}: {e} after {got} frames"
    finally:
        # cancellation-safe finalisation: no await before this point
        meta["frames"] = got
        meta["interrupted"] = interrupted
        meta["first_frame_unix_s"] = first_t
        meta["last_frame_unix_s"] = last_t
        if got >= 2:
            meta["measured_fps"] = (got - 1) / (last_t - first_t)
            meta["frame_gap_s"] = {"min": min(gaps), "median": statistics.median(gaps), "max": max(gaps)}
        meta["finalised"] = True
        stop.set()
        if interrupted:
            poller.cancel()                              # the poll task; a request already in flight ends with poll_timeout
        try:
            await poller
        except asyncio.CancelledError:                   # a Ctrl+C while waiting for the last poll
            if not interrupted:
                interrupted = meta["interrupted"] = True
                print("capture_waterfall: SIGINT after the last frame; the capture is complete, last poll skipped", file=sys.stderr)
        except Exception as e:                           # never lose the capture over the poller
            meta["poll_errors"].append({"t_unix_s": time.time(), "error": f"poller: {type(e).__name__}: {e}"})
        if own_handler:
            loop.remove_signal_handler(signal.SIGINT)


def initial_settings(api, settings_log):
    """The receiver settings at the first frame: the initial /api state,
    with every change the poller saw before the first frame folded in.
    Returns ({key: value} over EVENT_TOPIC, [comment lines])."""
    cur = {k: v for k, v in watched(api).items() if k in EVENT_TOPIC}
    notes = []
    for e in settings_log:
        if e["frames_received"] != 0:
            continue
        for key, (old, new) in e["changed"].items():
            if key in EVENT_TOPIC:
                cur[key] = new
                notes.append(f"# change seen before the first frame (wall t+{e['t_rel_s']:.1f} s), folded into the t = 0 value: {key} {old} -> {new}")
            else:
                notes.append(f"# change seen before the first frame (wall t+{e['t_rel_s']:.1f} s), not a replay topic: {key} {old} -> {new}")
    return cur, notes


def initial_events(api, settings_log=()):
    """The t = 0 lines: one per watched receiver setting with a value, in
    EVENT_TOPIC order."""
    cur, notes = initial_settings(api, settings_log)
    return notes + [f"0 {topic} {fmt_payload(cur[key])}" for key, topic in EVENT_TOPIC.items() if cur.get(key) is not None]


def write_outputs(args, meta, api):
    """The meta and events files, written whatever happened to the capture."""
    meta["ended_unix_s"] = time.time()
    try:
        with open(args.out, "rb") as f:
            data = f.read()
        meta["raw_bytes"] = len(data)
        meta["sha256"] = hashlib.sha256(data).hexdigest()
    except OSError as e:
        meta["raw_bytes"] = None
        meta["sha256"] = None
        meta.setdefault("errors", []).append(f"hash: {e}")
    if not meta.get("finalised") and meta.get("raw_bytes") is not None and len(meta["frame_sizes"]) == 1:
        # the capture task never reached its finally clause: count the frames from the file
        size = int(next(iter(meta["frame_sizes"])))
        meta["frames"] = meta["raw_bytes"] // size
        meta["frame_sizes"] = {str(size): meta["frames"]}
        meta.setdefault("errors", []).append("finalisation skipped; frame count derived from the file length")

    lines = [f"# replay events for {args.out}: generated by capture_waterfall.py from the /api settings log",
             f"# host {args.host}, frame rate at start {meta['fps_api_start']} frames/s"
             + (f" (changed from {meta['fps_api_initial']} before the first frame)" if meta.get("fps_api_initial") is not None else "") + "; "
             f"event time = (frames received - 0.5) / that rate, i.e. just before the first frame after the change was seen",
             f"# a change is seen up to {args.poll} s after it happened (poll interval); a change undone within one poll interval is not seen",
             "# t = 0: initial value of every watched receiver setting (the daemon takes a topic's first value as its"
             " initial state; only a later different value is a change); span = the spectrometer's input rate, i.e. the"
             " span of the frames"]
    if meta.get("aborted"):
        lines.append(f"# capture aborted: {meta['aborted']}")
    lines.extend(initial_events(api, meta["settings_log"]))
    for e in meta["settings_log"]:
        if e["frames_received"] == 0:
            continue                                     # folded into the t = 0 lines
        if e.get("t_event_s") is None:
            lines.append(f"# change after {e['frames_received']} frames not placed: no frame rate known ({e['changed']})")
            continue
        for key, topic in EVENT_TOPIC.items():            # same order as the t = 0 lines
            if key not in e["changed"]:
                continue
            old, new = e["changed"][key]
            if new is not None:
                lines.append(f"# frames_received {e['frames_received']}, wall t+{e['t_rel_s']:.1f} s, {key} {old} -> {new}")
                lines.append(f"{e['t_event_s']:.6f} {topic} {fmt_payload(new)}")
    with open(args.out + ".events", "w") as f:
        f.write("\n".join(lines) + "\n")
    meta["events_file"] = args.out + ".events"
    with open(args.out + ".meta.json", "w") as f:
        json.dump(meta, f, indent=1)


def set_sigint(handler):
    try:
        signal.signal(signal.SIGINT, handler)
    except (ValueError, OSError):                        # not the main thread: nothing to change
        pass


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

    try:
        api = get_api(base)                              # no sensor, no capture: fail before creating files
    except Exception as e:
        print(f"capture_waterfall: cannot read {base}/api: {type(e).__name__}: {e}; nothing captured, no file written", file=sys.stderr)
        return 2
    sp = api.get("spectrometer") or {}
    meta = {"host": args.host, "port": args.port, "note": args.note, "captured_unix_s": time.time(), "started_unix_s": time.time(),
            "api": {k: api.get(k) for k in ("ad9361", "spectrometer", "ddc", "versions")},
            "fps_api_start": sp.get("output_sampling_frequency"),
            "spectrometer_input": sp.get("input"), "frame_span_hz": frame_span_hz(api),
            "poll_s": args.poll, "timeout_s": args.timeout, "frames_requested": args.frames,
            "frames": 0, "frame_sizes": {}, "settings_log": [], "poll_errors": [], "aborted": None, "interrupted": False}
    if sp.get("input") not in (None, "AD9361"):
        print(f"capture_waterfall: spectrometer input is {sp.get('input')}: the frames span {meta['frame_span_hz']} Hz while the"
              f" AD9361 samples at {(api.get('ad9361') or {}).get('sampling_frequency')} Hz; the span event uses the frame span",
              file=sys.stderr)

    inherited_sigint = signal.getsignal(signal.SIGINT) if signal.getsignal(signal.SIGINT) is signal.SIG_IGN else signal.default_int_handler

    def sigint_seen():
        meta["interrupted"] = True
        if meta["frames"] < args.frames:                 # a complete capture is not aborted by a Ctrl+C in the housekeeping
            meta["aborted"] = meta.get("aborted") or f"interrupted (SIGINT) after {meta['frames']} frames"

    try:
        try:
            asyncio.run(capture(args, meta, base))
        except KeyboardInterrupt:                        # a Ctrl+C before the task installed its handler
            sigint_seen()
        except Exception as e:                           # setup failure (connect refused, bad host): still record it
            meta["aborted"] = meta.get("aborted") or f"{type(e).__name__}: {e}"
            meta["traceback"] = traceback.format_exc()
        try:
            after = get_api(base, API_TIMEOUT_INTERRUPTED_S if meta.get("interrupted") else API_TIMEOUT_S)
            meta["api_after"] = after.get("ad9361")
            meta["api_after_full"] = {k: after.get(k) for k in ("ad9361", "spectrometer", "ddc")}
            meta["settings_changed_during_capture"] = bool(meta["settings_log"]) or watched(api) != watched(after)
        except Exception as e:                           # the evidence does not depend on this request
            meta["api_after"] = None
            meta["api_after_full"] = None
            meta["settings_changed_during_capture"] = bool(meta["settings_log"]) or None
            meta.setdefault("errors", []).append(f"api after: {type(e).__name__}: {e}")
        meta["fps_changed_during_capture"] = fps_changed(meta)
    except KeyboardInterrupt:                            # a Ctrl+C anywhere in the housekeeping above
        set_sigint(signal.SIG_IGN)                       # a further Ctrl+C must not skip the sidecars
        sigint_seen()
        meta.setdefault("api_after", None)
        meta.setdefault("api_after_full", None)
        meta.setdefault("settings_changed_during_capture", bool(meta["settings_log"]) or None)
        meta.setdefault("fps_changed_during_capture", fps_changed(meta))
        meta.setdefault("errors", []).append("api after: interrupted (SIGINT)")
    set_sigint(signal.SIG_IGN)                           # the sidecars are written whole, the exit status stays as documented
    write_outputs(args, meta, api)
    print(json.dumps(meta))
    rc = 0
    if meta.get("aborted"):
        print(f"capture_waterfall: aborted: {meta['aborted']} (meta and events written)", file=sys.stderr)
        rc = 130 if meta.get("interrupted") else 2
    set_sigint(inherited_sigint)
    return rc


def fps_changed(meta):
    """A frame-rate change inside the capture (one seen before the first
    frame only switched the rate the capture runs at)."""
    return any("spectrometer.output_sampling_frequency" in e["changed"] and e["frames_received"] > 0 for e in meta["settings_log"])


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)                                       # do not wait for a poll thread still timing out against a hung sensor
