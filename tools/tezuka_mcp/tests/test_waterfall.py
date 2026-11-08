"""Waterfall summary on recorded frames, context checks, and an end-to-end
collection against a local fake sensor (HTTP /api + WebSocket /waterfall)."""
import asyncio
import copy
import json
import struct
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tezuka_mcp.frame import decode_frame
from tezuka_mcp import waterfall as WF


def test_summary_of_recorded_frames(fixture_frames, fixture_meta):
    frames = [decode_frame(r) for r in fixture_frames]
    s = WF.summarize(frames, 1.09e9, 2.5e6)
    assert s["fft_size"] == 4096 and s["bins_used"] == 4095 and s["dc_index"] == 2047 and s["bin0_offset"] == 1
    assert s["frames_averaged"] == 3 and s["step_word"] == 0.0
    # mean bin level of the fixture is ~850 (meta mean_tail) -> noise floor near 29 dB,
    # the DC spur ~190 000 -> ~53 dB
    assert 27.0 < s["noise_floor"]["db"] < 31.0
    assert s["peak"]["bin"] == 2047 and 52.0 < s["peak"]["db"] < 54.0
    assert s["peak"]["hz"] == pytest.approx(1.09e9, abs=1.0)
    assert "NOT dBm" in s["level_unit"]
    assert "median" in s["noise_floor"]["estimator"]
    dc_segs = [g for g in s["segments"]["items"] if g["contains_dc"]]
    assert len(dc_segs) == 1 and dc_segs[0]["peak_bin"] == 2047
    assert s["grid"]["hz_per_bin"] == pytest.approx(2.5e6 / 4096)
    assert len(s["psd_summary"]["max_db"]) == 128 == len(s["psd_summary"]["mean_db"])
    assert all(a >= b for a, b in zip(s["psd_summary"]["max_db"], s["psd_summary"]["mean_db"]))


def make_frame(n=4096, floor=1.0, hot=(), step=0.0, level=1000.0):
    vals = [floor] * n
    vals[0] = step
    for j in hot:
        vals[j + 1] = level   # j is a usable-bins index
    return struct.pack(f"<{n}f", *vals)


def test_segment_closes_at_final_bin():
    hot = list(range(4080, 4095))      # runs off the top edge of the usable bins
    fr = decode_frame(make_frame(hot=hot))
    s = WF.summarize([fr], 100e6, 1e6, threshold_db=10)
    last = s["segments"]["items"][-1]
    assert last["start_bin"] == 4080 and last["end_bin"] == 4094
    assert last["width_hz"] == pytest.approx(15 * 1e6 / 4096, abs=0.1)
    assert s["segments"]["count"] == 1


def test_segment_hz_bounds_follow_grid():
    fr = decode_frame(make_frame(hot=[2047]))     # the DC bin only
    s = WF.summarize([fr], 100e6, 1e6, threshold_db=10)
    seg = s["segments"]["items"][0]
    assert seg["contains_dc"] and seg["peak_hz"] == pytest.approx(100e6)
    assert seg["start_hz"] == pytest.approx(100e6 - 0.5 * 1e6 / 4096)
    assert seg["end_hz"] == pytest.approx(100e6 + 0.5 * 1e6 / 4096)


def test_step_word_handling_depends_on_sweep_state(fixture_frames, fixture_meta):
    api = fixture_meta["api_before"]
    # the recorded frames carry 0,1,2: the firmware's free-running counter with sweep OFF
    r = WF.check_coherent(fixture_frames, api, api, sweep_active=False)
    assert r["ok"] is True and r["step_words"] == [0.0, 1.0, 2.0]
    r = WF.check_coherent(fixture_frames, api, api, sweep_active=True)
    assert r["ok"] is False and "sweep is engaged" in r["reason"]
    r = WF.check_coherent(fixture_frames, api, api, sweep_active=None)
    assert r["ok"] is False and "unknown" in r["reason"]
    one = [fixture_frames[0]]
    assert WF.check_coherent(one, api, api, sweep_active=None)["ok"] is True


def test_context_change_is_rejected(fixture_frames, fixture_meta):
    before = fixture_meta["api_before"]
    after = copy.deepcopy(before)
    after["ad9361"]["rx_lo_frequency"] = 1.1e9
    r = WF.check_coherent([fixture_frames[0]], before, after)
    assert r["ok"] is False and "rx_lo_frequency" in r["reason"]


def test_support_check():
    ctx = WF.context_from_api({"spectrometer": {"input": "AD9361", "mode": "Average"}, "ddc": {"enabled": False}})
    assert WF.support_check(ctx, False)["supported"] is True
    assert WF.support_check(ctx, True)["supported"] is False
    ctx_ddc = WF.context_from_api({"spectrometer": {"input": "DDC", "mode": "Average"}, "ddc": {"enabled": True}})
    r = WF.support_check(ctx_ddc, None)
    assert r["supported"] is False and len(r["reasons"]) == 2


# --- end-to-end against a fake sensor -------------------------------------

class _Api(BaseHTTPRequestHandler):
    payload = b"{}"

    def do_GET(self):
        body = self.payload if self.path == "/api" else b"{}"
        self.send_response(200 if self.path == "/api" else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # quiet
        pass


class FakeSensor:
    """HTTP /api and WebSocket /waterfall on the same port is what the real
    maia-httpd does; here they are two ports, the tool takes host/port for
    http and derives the ws URL from the same pair, so the fake serves both
    from one asyncio server that speaks HTTP for /api and WS for /waterfall."""

    def __init__(self, api_doc, frames):
        self.api_doc = api_doc
        self.frames = frames
        self.port = None
        self._ready = threading.Event()
        self._stop = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        import websockets
        from websockets.http11 import Response
        from websockets.datastructures import Headers

        async def handler(ws):
            for f in self.frames:
                await ws.send(f)
                await asyncio.sleep(0.02)
            await asyncio.sleep(2.0)

        def process_request(connection, request):
            if request.path == "/api":
                body = json.dumps(self.api_doc).encode()
                return Response(200, "OK", Headers([("Content-Type", "application/json"), ("Content-Length", str(len(body)))]), body)
            return None

        async def main():
            self._stop = asyncio.get_running_loop().create_future()
            async with websockets.serve(handler, "127.0.0.1", 0, process_request=process_request) as server:
                self.port = server.sockets[0].getsockname()[1]
                self._ready.set()
                await self._stop

        asyncio.run(main())

    def start(self):
        self._thread.start()
        assert self._ready.wait(5)
        return self

    def stop(self):
        if self._stop is not None:
            self._stop.get_loop().call_soon_threadsafe(self._stop.set_result, None)
        self._thread.join(5)


def test_end_to_end_collection(fixture_frames, fixture_meta):
    # Serve three copies of frame 0 so the step word is constant.
    srv = FakeSensor(fixture_meta["api_before"], [fixture_frames[0]] * 3).start()
    try:
        res = WF.build_waterfall_snapshot("127.0.0.1", srv.port, "/waterfall", max_frames=3, timeout_s=10, sweep_active=False)
    finally:
        srv.stop()
    assert res["ok"] is True, res
    assert res["provenance"]["frames_received"] == 3
    assert res["context"]["rx_lo_frequency"] == 1090000000 and res["context"]["adc_sampling_frequency"] == 2500000
    assert res["summary"]["grid"]["center_hz"] == 1090000000 and res["summary"]["grid"]["span_hz"] == 2500000
    assert res["summary"]["peak"]["bin"] == 2047


def test_end_to_end_cycling_steps_accepted_when_sweep_off(fixture_frames, fixture_meta):
    srv = FakeSensor(fixture_meta["api_before"], fixture_frames).start()
    try:
        res = WF.build_waterfall_snapshot("127.0.0.1", srv.port, "/waterfall", max_frames=3, timeout_s=10, sweep_active=False)
    finally:
        srv.stop()
    assert res["ok"] is True and res["summary"]["step_words_seen"] == [0.0, 1.0, 2.0]
    assert res["summary"]["frames_averaged"] == 3


def test_end_to_end_cycling_steps_rejected_when_sweep_unknown(fixture_frames, fixture_meta):
    srv = FakeSensor(fixture_meta["api_before"], fixture_frames).start()
    try:
        res = WF.build_waterfall_snapshot("127.0.0.1", srv.port, "/waterfall", max_frames=3, timeout_s=10, sweep_active=None)
    finally:
        srv.stop()
    assert res["ok"] is False and "step word" in res["error"] and "unknown" in res["error"]


def test_end_to_end_unsupported_ddc(fixture_frames, fixture_meta):
    api = copy.deepcopy(fixture_meta["api_before"])
    api["ddc"]["enabled"] = True
    api["spectrometer"]["input"] = "DDC"
    srv = FakeSensor(api, fixture_frames).start()
    try:
        res = WF.build_waterfall_snapshot("127.0.0.1", srv.port, "/waterfall", max_frames=1, timeout_s=5, sweep_active=None)
    finally:
        srv.stop()
    assert res["ok"] is False and res["support"]["supported"] is False and "summary" not in res


def test_unreachable_sensor_is_reported_not_guessed():
    res = WF.build_waterfall_snapshot("127.0.0.1", 1, "/waterfall", max_frames=1, timeout_s=2, sweep_active=None)
    assert res["ok"] is False and "/api failed" in res["error"] and res["context"] is None
