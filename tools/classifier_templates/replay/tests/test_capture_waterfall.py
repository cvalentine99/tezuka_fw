"""capture_waterfall.py against a fake sensor: one port serving /api (HTTP)
and /waterfall (websocket) like maia-httpd, whose receiver settings change
while frames are being sent. Checks the raw file, the meta file (settings
log, boundaries, measured rate, hash) and the generated replay events.

    python -m pytest -q tools/classifier_templates/replay/tests
"""
import asyncio
import hashlib
import json
import socket
import struct
import subprocess
import sys
import threading
from http import HTTPStatus
from pathlib import Path

import pytest
import websockets

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent / "capture_waterfall.py"
N = 4096
FPS = 50.0                       # fast fake sensor: 40 frames in under a second
FRAMES = 40
RETUNE_AFTER = 20                # frames sent before the LO changes


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeSensor:
    def __init__(self, port, drop_after=None, silent=False):
        self.port = port
        self.drop_after = drop_after          # close the websocket after this many frames
        self.silent = silent                  # accept the websocket, never send a frame
        self.state = {
            "ad9361": {"sampling_frequency": 2500000, "rx_rf_bandwidth": 2500000, "rx_lo_frequency": 433000000,
                       "rx_gain": 70.0, "rx_gain_mode": "Manual"},
            "spectrometer": {"input": "AD9361", "output_sampling_frequency": FPS, "number_integrations": 1023,
                             "fft_size": N, "mode": "Average"},
            "ddc": {"enabled": False}, "versions": {"firmware_version": "fake"},
        }
        self.api_hits = 0
        self.sent = 0
        self.ready = threading.Event()
        self.stop = None
        self.loop = None

    def process_request(self, connection, request):
        if request.path == "/api":
            self.api_hits += 1
            return connection.respond(HTTPStatus.OK, json.dumps(self.state))
        return None                                   # /waterfall: upgrade to websocket

    async def waterfall(self, ws):
        frame = struct.pack("<%df" % N, *([1.0] + [100.0] * (N - 1)))
        if self.silent:
            await asyncio.sleep(3600)
        while True:
            if self.drop_after is not None and self.sent >= self.drop_after:
                await ws.close()
                return
            await ws.send(frame)
            self.sent += 1
            if self.sent == RETUNE_AFTER:             # the owner retunes mid-capture
                self.state["ad9361"]["rx_lo_frequency"] = 438000000
                self.state["ad9361"]["rx_gain"] = 60.0
            await asyncio.sleep(1.0 / FPS)

    async def run(self):
        self.stop = asyncio.Event()
        async with websockets.serve(self.waterfall, "127.0.0.1", self.port, process_request=self.process_request):
            self.ready.set()
            await self.stop.wait()

    def start(self):
        def target():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.run())
        self.thread = threading.Thread(target=target, daemon=True)
        self.thread.start()
        assert self.ready.wait(5)

    def shutdown(self):
        self.loop.call_soon_threadsafe(self.stop.set)
        self.thread.join(5)


@pytest.fixture
def sensor():
    s = FakeSensor(free_port())
    s.start()
    yield s
    s.shutdown()


def test_capture_logs_settings_and_writes_replay_events(tmp_path, sensor):
    out = tmp_path / "cap.raw"
    p = subprocess.run([sys.executable, str(TOOL), "127.0.0.1", str(FRAMES), str(out),
                        "--port", str(sensor.port), "--poll", "0.05", "--note", "fake segment"],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    raw = out.read_bytes()
    assert len(raw) == FRAMES * N * 4

    meta = json.loads((tmp_path / "cap.raw.meta.json").read_text())
    assert meta["frames"] == FRAMES and meta["frame_sizes"] == {str(N * 4): FRAMES}
    assert meta["sha256"] == hashlib.sha256(raw).hexdigest()
    assert meta["note"] == "fake segment" and meta["port"] == sensor.port
    assert meta["api"]["ad9361"]["rx_lo_frequency"] == 433000000
    assert meta["api_after"]["rx_lo_frequency"] == 438000000
    assert meta["settings_changed_during_capture"] is True
    assert meta["fps_changed_during_capture"] is False
    assert meta["fps_api_start"] == FPS
    assert meta["started_unix_s"] <= meta["first_frame_unix_s"] <= meta["last_frame_unix_s"] <= meta["ended_unix_s"]
    assert 0.3 * FPS < meta["measured_fps"] < 3 * FPS
    assert meta["frame_gap_s"]["min"] <= meta["frame_gap_s"]["median"] <= meta["frame_gap_s"]["max"]
    assert sensor.api_hits >= 3                       # before, polls, after

    log = meta["settings_log"]
    assert len(log) == 1, log
    e = log[0]
    assert e["changed"]["rx_lo_frequency"] == [433000000, 438000000]
    assert e["changed"]["rx_gain"] == [70.0, 60.0]
    assert RETUNE_AFTER <= e["frames_received"] <= FRAMES
    assert e["t_replay_s"] == pytest.approx(e["frames_received"] / FPS)
    assert e["t_event_s"] == pytest.approx((e["frames_received"] - 0.5) / FPS)
    assert e["ad9361"]["rx_lo_frequency"] == 438000000
    assert meta["aborted"] is None

    events = (tmp_path / "cap.raw.events").read_text().splitlines()
    body = [ln for ln in events if ln.strip() and not ln.startswith("#")]
    assert body[0] == "0 state/rx/frequency 433000000"
    assert body[1] == "0 state/rx/span 2500000"
    assert body[2] == f"{e['t_event_s']:.6f} state/rx/frequency 438000000"
    assert body[3] == f"{e['t_event_s']:.6f} state/rx/gain 60"
    # the daemon delivers an event before frame k when t <= k / F: the event
    # lands before the first frame received after the change, never after it
    k = e["frames_received"]
    assert (k - 1) / FPS < float(body[2].split()[0]) < k / FPS
    assert len(body) == 4
    # the times are sorted and plain decimals, as the daemon's -E requires
    times = [float(ln.split(" ", 1)[0]) for ln in body]
    assert times == sorted(times) and all(t >= 0 for t in times)


def test_stationary_capture_has_only_the_initial_events(tmp_path, sensor):
    out = tmp_path / "still.raw"
    p = subprocess.run([sys.executable, str(TOOL), "127.0.0.1", str(RETUNE_AFTER - 5), str(out),
                        "--port", str(sensor.port), "--poll", "0.05"], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    meta = json.loads((tmp_path / "still.raw.meta.json").read_text())
    assert meta["frames"] == RETUNE_AFTER - 5
    assert meta["settings_log"] == [] and meta["settings_changed_during_capture"] is False
    body = [ln for ln in (tmp_path / "still.raw.events").read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    assert body == ["0 state/rx/frequency 433000000", "0 state/rx/span 2500000"]


@pytest.fixture
def dropping_sensor():
    s = FakeSensor(free_port(), drop_after=10)
    s.start()
    yield s
    s.shutdown()


def test_connection_drop_keeps_meta_and_events(tmp_path, dropping_sensor):
    """A websocket closed mid-capture must not lose the settings log: the
    meta and events files are written with an 'aborted' marker."""
    out = tmp_path / "cut.raw"
    p = subprocess.run([sys.executable, str(TOOL), "127.0.0.1", str(FRAMES), str(out),
                        "--port", str(dropping_sensor.port), "--poll", "0.05"], capture_output=True, text=True, timeout=60)
    assert p.returncode == 2, p.stderr
    assert "aborted" in p.stderr
    meta = json.loads((tmp_path / "cut.raw.meta.json").read_text())
    assert meta["frames"] == 10 and meta["frames_requested"] == FRAMES
    assert meta["aborted"] and "after 10 frames" in meta["aborted"]
    assert meta["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert meta["api_after"]["rx_lo_frequency"] == 433000000
    events = (tmp_path / "cut.raw.events").read_text()
    assert "# capture aborted" in events
    assert "0 state/rx/frequency 433000000" in events


@pytest.fixture
def silent_sensor():
    s = FakeSensor(free_port(), silent=True)
    s.start()
    yield s
    s.shutdown()


def test_no_frames_times_out(tmp_path, silent_sensor):
    out = tmp_path / "none.raw"
    p = subprocess.run([sys.executable, str(TOOL), "127.0.0.1", "5", str(out),
                        "--port", str(silent_sensor.port), "--poll", "0.05", "--timeout", "0.5"],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 2, p.stderr
    meta = json.loads((tmp_path / "none.raw.meta.json").read_text())
    assert meta["frames"] == 0 and "no frame received for 0.5 s" in meta["aborted"]
    assert out.stat().st_size == 0
