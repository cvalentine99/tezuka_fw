"""snapshot_fpga_waterfall: collect a bounded number of /waterfall frames,
check they describe one coherent context, and summarize them.

The spectrometer's context (input, FFT size, mode, integrations, ADC
rate, LO) is read from maia-httpd's GET /api before and after the
collection; if anything changed in between, or the step word in float 0
changed between frames (sweep stepping), the collection is rejected as
mixed-context rather than averaged into nonsense. Averaging is done in
the power domain across FRAMES only — never across bins or steps.

Levels are the producer's linear power unit converted with 10*log10;
they are relative to that unit, not dBm (no calibration is applied or
implied). The "noise floor" is a declared estimator: the median of the
frame-averaged bin levels, in dB, with the DC bin and the hidden edge bin
excluded. Segments are contiguous runs of bins at least
threshold_db above that floor, closed at the final bin if still open.
"""
import asyncio
import json
import logging
import math
import statistics
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from . import SCHEMA_WATERFALL
from .frame import Frame, decode_frame, mean_power, power_to_db, bins_index_to_hz

log = logging.getLogger("tezuka_mcp.waterfall")


def fetch_api(host: str, port: int, timeout_s: float = 5.0) -> Dict[str, Any]:
    url = f"http://{host}:{port}/api"
    with urllib.request.urlopen(url, timeout=timeout_s) as r:   # noqa: S310 - operator-configured sensor address
        return json.loads(r.read().decode("utf-8"))


def context_from_api(api: Dict[str, Any]) -> Dict[str, Any]:
    spec = api.get("spectrometer") or {}
    ad = api.get("ad9361") or {}
    ddc = api.get("ddc") or {}
    return {
        "input": spec.get("input"),
        "fft_size": spec.get("fft_size"),
        "mode": spec.get("mode"),
        "number_integrations": spec.get("number_integrations"),
        "input_sampling_frequency": spec.get("input_sampling_frequency"),
        "adc_sampling_frequency": ad.get("sampling_frequency"),
        "rx_lo_frequency": ad.get("rx_lo_frequency"),
        "rx_rf_bandwidth": ad.get("rx_rf_bandwidth"),
        "rx_gain": ad.get("rx_gain"),
        "rx_gain_mode": ad.get("rx_gain_mode"),
        "ddc_enabled": ddc.get("enabled"),
        "ddc_frequency": ddc.get("frequency"),
        "ddc_output_sampling_frequency": ddc.get("output_sampling_frequency"),
        "firmware_version": (api.get("versions") or {}).get("firmware_version"),
        "maia_httpd_git": (api.get("versions") or {}).get("maia_httpd_git"),
    }


def support_check(ctx: Dict[str, Any], sweep_active: Optional[bool], sweep_reason: Optional[str] = None) -> Dict[str, Any]:
    """Which spectrometer configurations this tool interprets. Anything
    else is reported as unsupported with the reason, never guessed at.
    An unknown sweep state (None) is unsupported too: the free-running
    step word cannot tell sweep stepping from a fixed centre, and the
    /api context alone does not carry the sweep flags."""
    reasons = []
    if ctx.get("input") != "AD9361":
        reasons.append(f"spectrometer input is {ctx.get('input')!r}; only the direct AD9361 (ADC) input is interpreted, the DDC path's span and centre differ")
    if ctx.get("ddc_enabled"):
        reasons.append("DDC is enabled")
    if sweep_active:
        reasons.append("sweep mode is active: each frame is one step of a wider sweep and the step grid is not interpreted here")
    elif sweep_active is None:
        reasons.append("sweep state could not be established" + (f" ({sweep_reason})" if sweep_reason else "")
                       + ": without a current, live sweep-off reading the frames cannot be read as one ADC-centred span")
    if ctx.get("mode") not in ("Average", "PeakDetect"):
        reasons.append(f"unknown spectrometer mode {ctx.get('mode')!r}")
    return {"supported": not reasons, "reasons": reasons}


async def collect_frames(url: str, max_frames: int, timeout_s: float) -> List[bytes]:
    import websockets
    deadline = time.monotonic() + timeout_s
    out: List[bytes] = []
    async with websockets.connect(url, max_size=None, open_timeout=min(10.0, timeout_s)) as ws:
        while len(out) < max_frames:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            if isinstance(msg, (bytes, bytearray)):
                out.append(bytes(msg))
    return out


def summarize(frames: List[Frame], center_hz: Optional[float], span_hz: Optional[float],
              summary_bins: int = 128, threshold_db: float = 10.0, max_segments: int = 32) -> Dict[str, Any]:
    """Bounded summary of a coherent set of frames (same fft_size, same
    step word, same API context — the caller has checked that)."""
    n = frames[0].fft_size
    avg = mean_power(frames)               # len n-1, power domain
    db = [power_to_db(v) for v in avg]
    dc = frames[0].dc_index
    excl = {dc - 1, dc, dc + 1}
    floor_pool = [db[j] for j in range(len(db)) if j not in excl]
    noise_floor_db = statistics.median(floor_pool)
    peak_j = max(range(len(db)), key=lambda j: db[j])

    # Decimated PSD: max within each bucket (keeps narrow carriers visible)
    # and mean of the bucket's power (for the level), both in dB.
    usable = len(db)
    buckets = max(1, min(summary_bins, usable))
    psd_max_db, psd_mean_db = [], []
    for b in range(buckets):
        j0 = b * usable // buckets
        j1 = max(j0 + 1, (b + 1) * usable // buckets)
        seg = avg[j0:j1]
        psd_max_db.append(round(max(db[j0:j1]), 2))
        psd_mean_db.append(round(power_to_db(sum(seg) / len(seg)), 2))

    # Segments above the floor; close at the final bin.
    gate = noise_floor_db + threshold_db
    segments: List[Dict[str, Any]] = []
    start = None
    truncated = False

    def close(a: int, b: int) -> None:
        nonlocal truncated
        if len(segments) >= max_segments:
            truncated = True
            return
        pk = max(range(a, b + 1), key=lambda j: db[j])
        seg: Dict[str, Any] = {
            "start_bin": a, "end_bin": b, "width_bins": b - a + 1,
            "peak_bin": pk, "peak_db": round(db[pk], 2), "peak_over_floor_db": round(db[pk] - noise_floor_db, 2),
            "contains_dc": a <= dc <= b,
        }
        if center_hz is not None and span_hz is not None:
            seg["start_hz"] = round(bins_index_to_hz(a - 0.5, n, center_hz, span_hz), 1)
            seg["end_hz"] = round(bins_index_to_hz(b + 0.5, n, center_hz, span_hz), 1)
            seg["peak_hz"] = round(bins_index_to_hz(pk, n, center_hz, span_hz), 1)
            seg["width_hz"] = round((b - a + 1) * span_hz / n, 1)
        segments.append(seg)

    for j in range(usable):
        above = db[j] >= gate
        if above and start is None:
            start = j
        elif not above and start is not None:
            close(start, j - 1)
            start = None
    if start is not None:
        close(start, usable - 1)   # the band runs off the top edge: still a detection

    out: Dict[str, Any] = {
        "frames_averaged": len(frames),
        "fft_size": n,
        "bins_used": usable,
        "bin0_offset": 1,
        "dc_index": dc,
        "step_word": frames[0].step_word,
        "level_unit": "dB relative to the producer's linear power scale (10*log10 of integrated |X|^2 * 4e6/(nint*fs)); NOT dBm, no calibration applied",
        "averaging": "power-domain mean across frames per bin, then 10*log10; no averaging across bins or sweep steps",
        "noise_floor": {
            "db": round(noise_floor_db, 2),
            "estimator": "median of frame-averaged bin levels in dB, excluding the DC bin and its two neighbours",
        },
        "peak": {"bin": peak_j, "db": round(db[peak_j], 2), "over_floor_db": round(db[peak_j] - noise_floor_db, 2),
                 "hz": None if center_hz is None or span_hz is None else round(bins_index_to_hz(peak_j, n, center_hz, span_hz), 1)},
        "psd_summary": {"buckets": buckets, "bins_per_bucket": usable / buckets, "max_db": psd_max_db, "mean_db": psd_mean_db,
                        "bucket_start_hz": None if center_hz is None or span_hz is None else
                        [round(bins_index_to_hz(b * usable // buckets - 0.5, n, center_hz, span_hz), 1) for b in range(buckets)]},
        "segments": {"threshold_over_floor_db": threshold_db, "count": len(segments), "truncated": truncated, "items": segments},
        "min_db": round(min(db), 2), "max_db": round(max(db), 2),
    }
    if center_hz is not None and span_hz is not None:
        out["grid"] = {"center_hz": center_hz, "span_hz": span_hz, "hz_per_bin": span_hz / n,
                       "first_usable_bin_hz": round(bins_index_to_hz(0, n, center_hz, span_hz), 1),
                       "last_usable_bin_hz": round(bins_index_to_hz(usable - 1, n, center_hz, span_hz), 1),
                       "rule": "bins[j] centred on center + (j + 1 - fft_size/2) * span / fft_size"}
    else:
        out["grid"] = {"center_hz": None, "span_hz": None, "hz_per_bin": None, "reason": "span or centre unknown"}
    return out


def check_coherent(payloads: List[bytes], api_before: Dict[str, Any], api_after: Dict[str, Any],
                   sweep_active: Optional[bool] = None) -> Dict[str, Any]:
    """Decode and verify one context. Returns {'ok': True, 'frames': [...],
    'context': ..., 'step_words': [...]} or {'ok': False, 'reason': ...}.

    The step word in float 0 needs the sweep state to be interpreted: the
    deployed firmware cycles it 0..7 on every frame even when sweep mode
    is off (observed on the SignalSDR Pro; the Dashboard notes the same),
    so a varying step word only means "different context" when a sweep
    is engaged. With sweep known off the frames are one context and the
    cycling word is recorded; with sweep state unknown (no MQTT) a varying
    word cannot be told apart from real stepping and is rejected."""
    frames: List[Frame] = []
    for i, p in enumerate(payloads):
        try:
            frames.append(decode_frame(p))
        except ValueError as e:
            return {"ok": False, "reason": f"frame {i}: {e}"}
    sizes = {f.fft_size for f in frames}
    if len(sizes) > 1:
        return {"ok": False, "reason": f"mixed FFT sizes within the collection: {sorted(sizes)}"}
    steps = sorted({f.step_word for f in frames})
    if len(steps) > 1:
        if sweep_active is True:
            return {"ok": False, "reason": f"step word changed within the collection ({steps}) while sweep is engaged: frames are different sweep steps, not one context"}
        if sweep_active is None:
            return {"ok": False, "reason": f"step word changed within the collection ({steps}) and the sweep state is unknown (no MQTT state/rx/sweep/*); cannot tell sweep stepping from the firmware's free-running fastlock counter, so the collection is rejected"}
    cb, ca = context_from_api(api_before), context_from_api(api_after)
    diff = {k: (cb.get(k), ca.get(k)) for k in cb if cb.get(k) != ca.get(k)}
    if diff:
        return {"ok": False, "reason": f"spectrometer/receiver context changed during the collection: {diff}"}
    if frames and cb.get("fft_size") not in (None, frames[0].fft_size):
        return {"ok": False, "reason": f"/api says fft_size {cb.get('fft_size')} but frames carry {frames[0].fft_size} floats"}
    return {"ok": True, "frames": frames, "context": cb, "step_words": steps}


def build_waterfall_snapshot(host: str, port: int, path: str, max_frames: int, timeout_s: float,
                             sweep_active: Optional[bool], threshold_db: float = 10.0,
                             sweep_state_fn: Optional[Callable[[], Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Synchronous driver used by the MCP tool (runs its own event loop for
    the WebSocket collection).

    The sweep state is read through `sweep_state_fn` (see
    Service.sweep_state: {"active": bool|None, "reason": str|None,
    "generation": int, "epoch": int, ...}) BEFORE the collection, to
    decide support, and AGAIN AFTER it: a change of the flag, of the
    sweep topics' value-change generation (off -> on -> off included) or
    of the MQTT connection epoch (a reconnect) in between means the
    frames may straddle a mode change, and the collection is rejected.
    Unchanged republishes of the flags do not move the generation. A bare `sweep_active` is accepted for
    callers (tests) that have no live source; it is then treated as
    constant."""
    if sweep_state_fn is None:
        def sweep_state_fn() -> Dict[str, Any]:   # noqa: E306 - static stand-in
            return {"active": sweep_active, "reason": None if sweep_active is not None else "no sweep state source given",
                    "generation": 0, "epoch": 0}
    t0 = time.time()
    url = f"ws://{host}:{port}{path}"
    out: Dict[str, Any] = {"schema": SCHEMA_WATERFALL, "provenance": {
        "waterfall_url": url, "api_url": f"http://{host}:{port}/api", "requested_frames": max_frames,
        "timeout_s": timeout_s, "started_unix_s": round(t0, 3)}}
    try:
        api_before = fetch_api(host, port)
    except Exception as e:
        out.update({"ok": False, "error": f"GET /api failed before collection: {e}", "context": None})
        return out
    ctx = context_from_api(api_before)
    sweep_before = sweep_state_fn()
    sweep_active = sweep_before.get("active")
    sup = support_check(ctx, sweep_active, sweep_before.get("reason"))
    out["context"] = ctx
    out["context"]["sweep_active_per_mqtt"] = sweep_active
    out["context"]["sweep_state"] = {"before": sweep_before, "after": None}
    out["support"] = sup
    if not sup["supported"]:
        out.update({"ok": False, "error": "unsupported spectrometer configuration; no spectrum summary produced: " + "; ".join(sup["reasons"])})
        return out
    try:
        payloads = asyncio.run(collect_frames(url, max_frames, timeout_s))
    except Exception as e:
        out.update({"ok": False, "error": f"waterfall collection failed: {type(e).__name__}: {e}"})
        return out
    try:
        api_after = fetch_api(host, port)
    except Exception as e:
        out.update({"ok": False, "error": f"GET /api failed after collection: {e}"})
        return out
    sweep_after = sweep_state_fn()
    out["context"]["sweep_state"]["after"] = sweep_after
    out["provenance"]["frames_received"] = len(payloads)
    out["provenance"]["elapsed_s"] = round(time.time() - t0, 3)
    if (sweep_after.get("active") != sweep_before.get("active") or sweep_after.get("generation") != sweep_before.get("generation")
            or sweep_after.get("epoch") != sweep_before.get("epoch")):
        out.update({"ok": False, "error": ("sweep state changed during the collection "
                                            f"(active {sweep_before.get('active')!r} -> {sweep_after.get('active')!r}, "
                                            f"sweep value changes {sweep_before.get('generation')} -> {sweep_after.get('generation')}, "
                                            f"MQTT connection epoch {sweep_before.get('epoch')} -> {sweep_after.get('epoch')}"
                                            + (f"; {sweep_after.get('reason')}" if sweep_after.get("reason") else "")
                                            + "); the frames may straddle a mode change and are not interpreted")})
        return out
    if not payloads:
        out.update({"ok": False, "error": "no frames received within the timeout"})
        return out
    coh = check_coherent(payloads, api_before, api_after, sweep_active)
    if not coh["ok"]:
        out.update({"ok": False, "error": "mixed context rejected: " + coh["reason"]})
        return out
    center = ctx.get("rx_lo_frequency")
    span = ctx.get("adc_sampling_frequency")
    out["summary"] = summarize(coh["frames"], center, span, threshold_db=threshold_db)
    out["summary"]["step_words_seen"] = coh["step_words"]
    out["summary"]["step_word_note"] = ("sweep off per MQTT: the fastlock/step word in float 0 free-runs 0..7 on this firmware and does not change the context"
                                        if len(coh["step_words"]) > 1 else "constant step word")
    out["ok"] = True
    return out
