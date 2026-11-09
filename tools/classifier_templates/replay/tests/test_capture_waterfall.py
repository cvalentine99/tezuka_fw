"""capture_waterfall.py against a fake sensor: one port serving /api (HTTP)
and /waterfall (websocket) like maia-httpd, whose receiver settings change
while frames are being sent. Checks the raw file, the meta file (settings
log, boundaries, measured rate, hash) and the generated replay events,
including the two capture-readiness points of the 1791089 review: every
watched receiver setting is seeded at t = 0, and Ctrl+C keeps the sidecars.

    python -m pytest -q tools/classifier_templates/replay/tests
"""
import asyncio
import hashlib
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
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
INTERRUPT_AFTER = RETUNE_AFTER + 10   # frames on disk before SIGINT (and the retune must have been polled)
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="os.kill(SIGINT) is not a Ctrl+C on Windows")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeSensor:
    def __init__(self, port, drop_after=None, silent=False, change=None, state=None, api_down_after=None,
                 change_at_open=None, spectrometer_at_open=None, first_frame_delay=0.0):
        self.port = port
        self.drop_after = drop_after          # close the websocket after this many frames
        self.silent = silent                  # accept the websocket, never send a frame
        # the settings the owner changes after RETUNE_AFTER frames
        self.change = {"rx_lo_frequency": 438000000, "rx_gain": 60.0} if change is None else change
        self.api_down_after = api_down_after  # /api answers 503 once this many frames were sent
        self.change_at_open = change_at_open  # ad9361 settings changed when the websocket opens, before the first frame
        self.spectrometer_at_open = spectrometer_at_open   # same for spectrometer settings
        self.api_down = False                 # /api answers 503 from now on (set by a test)
        self.first_frame_delay = first_frame_delay
        self.state = {
            "ad9361": {"sampling_frequency": 2500000, "rx_rf_bandwidth": 2500000, "rx_lo_frequency": 433000000,
                       "rx_gain": 70.0, "rx_gain_mode": "Manual"},
            "spectrometer": {"input": "AD9361", "input_sampling_frequency": 2500000.0, "output_sampling_frequency": FPS,
                             "number_integrations": 1023, "fft_size": N, "mode": "Average"},
            "ddc": {"enabled": False}, "versions": {"firmware_version": "fake"},
        }
        for section, values in (state or {}).items():
            self.state[section].update(values)
        self.api_hits = 0
        self.sent = 0
        self.ready = threading.Event()
        self.stop = None
        self.loop = None

    def process_request(self, a, b):
        # websockets >= 13 calls (connection, request); the legacy server of
        # older releases calls (path, request_headers) and wants a tuple back
        legacy = isinstance(a, str)
        path = a if legacy else b.path
        if path != "/api":
            return None                               # /waterfall: upgrade to websocket
        self.api_hits += 1
        if self.api_down or (self.api_down_after is not None and self.sent >= self.api_down_after):
            status, body = HTTPStatus.SERVICE_UNAVAILABLE, b"down"
        else:
            status, body = HTTPStatus.OK, json.dumps(self.state).encode()
        if legacy:
            return status, [("Content-Type", "application/json"), ("Content-Length", str(len(body)))], body
        return a.respond(status, body.decode())

    async def waterfall(self, ws):
        frame = struct.pack("<%df" % N, *([1.0] + [100.0] * (N - 1)))
        if self.silent:
            await ws.wait_closed()                    # returns when the client leaves or the server closes
            return
        if self.change_at_open:
            self.state["ad9361"].update(self.change_at_open)
        if self.spectrometer_at_open:
            self.state["spectrometer"].update(self.spectrometer_at_open)
        if self.first_frame_delay:
            await asyncio.sleep(self.first_frame_delay)
        while True:
            if self.drop_after is not None and self.sent >= self.drop_after:
                await ws.close()
                return
            await ws.send(frame)
            self.sent += 1
            if self.sent == RETUNE_AFTER:             # the owner retunes mid-capture
                self.state["ad9361"].update(self.change)
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
        assert not self.thread.is_alive(), "fake sensor did not shut down (a handler is still running)"


INITIAL_EVENTS = ["0 state/rx/frequency 433000000", "0 state/rx/sampling 2500000", "0 state/rx/span 2500000",
                  "0 state/rx/bandwidth 2500000", "0 state/rx/gain 70", "0 state/rx/gain_mode Manual"]


def events_body(path):
    return [ln for ln in Path(path).read_text().splitlines() if ln.strip() and not ln.startswith("#")]


def run_tool(sensor, frames, out, *extra):
    return subprocess.run([sys.executable, str(TOOL), "127.0.0.1", str(frames), str(out),
                           "--port", str(sensor.port), "--poll", "0.05", *extra], capture_output=True, text=True, timeout=60)


def interrupt_capture(sensor, out, frames=100000, api_down_before_interrupt=False):
    """Run the tool for FRAMES, send SIGINT once INTERRUPT_AFTER frames are
    on disk and the poller has processed an answer taken after the retune
    (two further /api hits, all answered 200: polls are sequential, so the
    second request can only go out once the post-retune answer was
    handled), optionally take /api down just before the SIGINT, return
    (returncode, stdout, stderr)."""
    cmd = [sys.executable, str(TOOL), "127.0.0.1", str(frames), str(out), "--port", str(sensor.port), "--poll", "0.05"]
    # a background job or nohup hands the child SIGINT ignored, and Python then installs no Ctrl+C handler at all
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL)) as p:
        try:
            deadline = time.time() + 30
            hits_at_retune = None
            while True:
                if hits_at_retune is None and sensor.sent >= RETUNE_AFTER:
                    hits_at_retune = sensor.api_hits
                if (hits_at_retune is not None and sensor.api_hits >= hits_at_retune + 2
                        and out.exists() and out.stat().st_size >= INTERRUPT_AFTER * N * 4):
                    break
                assert p.poll() is None, p.communicate()[1]
                assert time.time() < deadline, "fake sensor too slow"
                time.sleep(0.01)
            if api_down_before_interrupt:
                sensor.api_down = True
            os.kill(p.pid, signal.SIGINT)
            stdout, stderr = p.communicate(timeout=30)
        finally:
            if p.poll() is None:
                p.kill()                              # __exit__ then reaps it and closes the pipes
    return p.returncode, stdout, stderr


def check_interrupted(tmp_path, out, stdout, stderr):
    """The assertions common to every interrupted capture: partial raw file
    and frame count agree, hash, reason, the retune logged before Ctrl+C,
    both sidecars."""
    assert "aborted: interrupted (SIGINT)" in stderr
    raw = out.read_bytes()
    meta = json.loads((tmp_path / (out.name + ".meta.json")).read_text())
    assert meta["frames"] * N * 4 == len(raw) == meta["raw_bytes"]
    assert INTERRUPT_AFTER <= meta["frames"] < 100000 and meta["frames_requested"] == 100000
    assert meta["frame_sizes"] == {str(N * 4): meta["frames"]}
    assert meta["sha256"] == hashlib.sha256(raw).hexdigest()
    assert meta["interrupted"] is True and meta["finalised"] is True
    assert meta["aborted"] == f"interrupted (SIGINT) after {meta['frames']} frames"
    assert meta["first_frame_unix_s"] <= meta["last_frame_unix_s"] <= meta["ended_unix_s"]
    assert meta["measured_fps"] > 0 and meta["frame_gap_s"]["min"] <= meta["frame_gap_s"]["max"]
    log = meta["settings_log"]                        # the retune seen before Ctrl+C is kept
    assert len(log) == 1 and log[0]["changed"]["rx_lo_frequency"] == [433000000, 438000000]
    assert json.loads(stdout)["aborted"] == meta["aborted"]
    events = (tmp_path / (out.name + ".events")).read_text()
    assert "# capture aborted: interrupted (SIGINT)" in events
    body = events_body(tmp_path / (out.name + ".events"))
    assert body[:6] == INITIAL_EVENTS
    assert body[6] == f"{log[0]['t_event_s']:.6f} state/rx/frequency 438000000"
    assert body[7] == f"{log[0]['t_event_s']:.6f} state/rx/gain 60" and len(body) == 8
    return meta


@pytest.fixture
def sensor():
    s = FakeSensor(free_port())
    s.start()
    yield s
    s.shutdown()


def test_capture_logs_settings_and_writes_replay_events(tmp_path, sensor):
    out = tmp_path / "cap.raw"
    p = run_tool(sensor, FRAMES, out, "--note", "fake segment")
    assert p.returncode == 0, p.stderr
    raw = out.read_bytes()
    assert len(raw) == FRAMES * N * 4

    meta = json.loads((tmp_path / "cap.raw.meta.json").read_text())
    assert meta["frames"] == FRAMES and meta["frame_sizes"] == {str(N * 4): FRAMES}
    assert meta["sha256"] == hashlib.sha256(raw).hexdigest()
    assert meta["raw_bytes"] == len(raw) and meta["interrupted"] is False and meta["finalised"] is True
    assert meta["note"] == "fake segment" and meta["port"] == sensor.port
    assert meta["api"]["ad9361"]["rx_lo_frequency"] == 433000000
    assert meta["api_after"]["rx_lo_frequency"] == 438000000
    assert meta["settings_changed_during_capture"] is True
    assert meta["fps_changed_during_capture"] is False
    assert meta["fps_api_start"] == FPS
    assert meta["spectrometer_input"] == "AD9361" and meta["frame_span_hz"] == 2500000.0
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

    body = events_body(tmp_path / "cap.raw.events")
    # every watched setting is seeded at t = 0 (the daemon's first value of a
    # topic is its initial state, so an unseeded setting's first change would
    # not count as a change in replay)
    assert body[:6] == INITIAL_EVENTS
    assert body[6] == f"{e['t_event_s']:.6f} state/rx/frequency 438000000"
    assert body[7] == f"{e['t_event_s']:.6f} state/rx/gain 60"
    # the daemon delivers an event before frame k when t <= k / F: the event
    # lands before the first frame received after the change, never after it
    k = e["frames_received"]
    assert (k - 1) / FPS < float(body[6].split()[0]) < k / FPS
    assert len(body) == 8
    # the times are sorted and plain decimals, as the daemon's -E requires
    times = [float(ln.split(" ", 1)[0]) for ln in body]
    assert times == sorted(times) and all(t >= 0 for t in times)


def test_stationary_capture_has_only_the_initial_events(tmp_path, sensor):
    out = tmp_path / "still.raw"
    p = run_tool(sensor, RETUNE_AFTER - 5, out)
    assert p.returncode == 0, p.stderr
    meta = json.loads((tmp_path / "still.raw.meta.json").read_text())
    assert meta["frames"] == RETUNE_AFTER - 5
    assert meta["settings_log"] == [] and meta["settings_changed_during_capture"] is False
    assert events_body(tmp_path / "still.raw.events") == INITIAL_EVENTS


@pytest.mark.parametrize("key,topic,new,payload", [
    ("rx_gain", "state/rx/gain", 60.0, "60"),
    ("rx_rf_bandwidth", "state/rx/bandwidth", 1500000, "1500000"),
    ("rx_gain_mode", "state/rx/gain_mode", "SlowAttack", "SlowAttack"),
])
def test_single_setting_change_is_seeded_and_emitted(tmp_path, key, topic, new, payload):
    """A gain-only, bandwidth-only or gain-mode-only change: the events file
    carries that setting's initial value at t = 0 and the change later, so
    the daemon sees a change (the end-to-end replay check is in
    test_capture_replay_e2e.py)."""
    s = FakeSensor(free_port(), change={key: new})
    s.start()
    try:
        out = tmp_path / "one.raw"
        p = run_tool(s, FRAMES, out)
    finally:
        s.shutdown()
    assert p.returncode == 0, p.stderr
    meta = json.loads((tmp_path / "one.raw.meta.json").read_text())
    assert len(meta["settings_log"]) == 1 and list(meta["settings_log"][0]["changed"]) == [key]
    body = events_body(tmp_path / "one.raw.events")
    assert body[:6] == INITIAL_EVENTS
    assert body[6:] == [f"{meta['settings_log'][0]['t_event_s']:.6f} {topic} {payload}"]
    seeded = [ln for ln in body[:6] if ln.split()[1] == topic]
    assert len(seeded) == 1 and seeded[0].split()[2] != payload   # initial value differs from the change


def test_ddc_mode_span_event_is_the_frame_span(tmp_path):
    """Below 2.4 MHz the sensor samples the AD9361 at DEC x span and feeds
    the spectrometer from the DDC: the frames span the DDC output rate, not
    the AD9361 rate, and the span event must say so (the daemon derives the
    hertz per bin from it). The AD9361 rate goes to state/rx/sampling."""
    s = FakeSensor(free_port(), change={}, state={"ad9361": {"sampling_frequency": 2000000},
                                                  "spectrometer": {"input": "DDC", "input_sampling_frequency": 500000.0},
                                                  "ddc": {"enabled": True, "decimation": 4, "output_sampling_frequency": 500000.0}})
    s.start()
    try:
        out = tmp_path / "ddc.raw"
        p = run_tool(s, 10, out)
    finally:
        s.shutdown()
    assert p.returncode == 0, p.stderr
    assert "spectrometer input is DDC" in p.stderr and "500000" in p.stderr
    meta = json.loads((tmp_path / "ddc.raw.meta.json").read_text())
    assert meta["spectrometer_input"] == "DDC" and meta["frame_span_hz"] == 500000.0
    body = events_body(tmp_path / "ddc.raw.events")
    assert body[1] == "0 state/rx/sampling 2000000" and body[2] == "0 state/rx/span 500000"
    assert len(body) == 6


def test_change_before_the_first_frame_is_folded_into_the_initial_events(tmp_path):
    """A change the poller sees while no frame has arrived yet cannot be
    placed on the replay clock; the sensor's daemon held the new value when
    the first frame came, so the t = 0 line must carry it."""
    s = FakeSensor(free_port(), change={}, change_at_open={"rx_lo_frequency": 440000000}, first_frame_delay=0.4)
    s.start()
    try:
        out = tmp_path / "early.raw"
        p = run_tool(s, 10, out)
    finally:
        s.shutdown()
    assert p.returncode == 0, p.stderr
    meta = json.loads((tmp_path / "early.raw.meta.json").read_text())
    assert len(meta["settings_log"]) == 1 and meta["settings_log"][0]["frames_received"] == 0
    assert meta["settings_log"][0]["t_event_s"] is None
    events = (tmp_path / "early.raw.events").read_text()
    assert "folded into the t = 0 value: rx_lo_frequency 433000000 -> 440000000" in events
    assert "not placed" not in events
    body = events_body(tmp_path / "early.raw.events")
    assert body == ["0 state/rx/frequency 440000000"] + INITIAL_EVENTS[1:]


def test_frame_rate_change_before_the_first_frame_switches_the_rate(tmp_path):
    """No frame was produced at the rate read before the capture, so the
    events are placed with the new rate; the change leaves a comment and
    does not count as a rate change inside the capture."""
    s = FakeSensor(free_port(), change={}, spectrometer_at_open={"output_sampling_frequency": 25.0}, first_frame_delay=0.4)
    s.start()
    try:
        out = tmp_path / "rate.raw"
        p = run_tool(s, 10, out)
    finally:
        s.shutdown()
    assert p.returncode == 0, p.stderr
    meta = json.loads((tmp_path / "rate.raw.meta.json").read_text())
    assert meta["fps_api_start"] == 25.0 and meta["fps_api_initial"] == FPS
    assert meta["fps_changed_during_capture"] is False
    events = (tmp_path / "rate.raw.events").read_text()
    assert "frame rate at start 25.0 frames/s (changed from 50.0 before the first frame)" in events
    assert "not a replay topic: spectrometer.output_sampling_frequency 50.0 -> 25.0" in events
    assert events_body(tmp_path / "rate.raw.events") == INITIAL_EVENTS


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
    p = run_tool(dropping_sensor, FRAMES, out)
    assert p.returncode == 2, p.stderr
    assert "aborted" in p.stderr
    meta = json.loads((tmp_path / "cut.raw.meta.json").read_text())
    assert meta["frames"] == 10 and meta["frames_requested"] == FRAMES
    assert meta["aborted"] and "after 10 frames" in meta["aborted"]
    assert meta["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert meta["api_after"]["rx_lo_frequency"] == 433000000
    assert meta["interrupted"] is False
    events = (tmp_path / "cut.raw.events").read_text()
    assert "# capture aborted" in events
    assert events_body(tmp_path / "cut.raw.events") == INITIAL_EVENTS


@posix_only
def test_sigint_keeps_meta_and_events(tmp_path, sensor):
    """The owner stops a segment with Ctrl+C: the frames received so far,
    the meta file (hash of the partial file, frame count, settings log) and
    the events file are all kept, the abort reason names the interruption,
    and the exit status is 130."""
    out = tmp_path / "int.raw"
    rc, stdout, stderr = interrupt_capture(sensor, out)
    assert rc == 130, stderr
    meta = check_interrupted(tmp_path, out, stdout, stderr)
    assert meta["api_after"]["rx_lo_frequency"] == 438000000     # the final /api request still ran
    assert "errors" not in meta


@posix_only
def test_sigint_with_the_final_api_request_failing(tmp_path):
    """The evidence must not depend on the final /api request: with /api
    down when Ctrl+C arrives, the sidecars, hash and reason are the same
    and the failure is recorded in errors."""
    s = FakeSensor(free_port())
    s.start()
    try:
        out = tmp_path / "intdown.raw"
        rc, stdout, stderr = interrupt_capture(s, out, api_down_before_interrupt=True)
    finally:
        s.shutdown()
    assert rc == 130, stderr
    meta = check_interrupted(tmp_path, out, stdout, stderr)
    assert meta["api_after"] is None and meta["api_after_full"] is None
    assert any(e.startswith("api after:") for e in meta["errors"])
    assert meta["settings_changed_during_capture"] is True       # from the log, not from the failed request


@pytest.fixture
def silent_sensor():
    s = FakeSensor(free_port(), silent=True)
    s.start()
    yield s
    s.shutdown()


def test_no_frames_times_out(tmp_path, silent_sensor):
    out = tmp_path / "none.raw"
    p = run_tool(silent_sensor, 5, out, "--timeout", "0.5")
    assert p.returncode == 2, p.stderr
    meta = json.loads((tmp_path / "none.raw.meta.json").read_text())
    assert meta["frames"] == 0 and "no frame received for 0.5 s" in meta["aborted"]
    assert out.stat().st_size == 0 and meta["raw_bytes"] == 0 and meta["interrupted"] is False
    events = (tmp_path / "none.raw.events").read_text()
    assert "# capture aborted: no frame received for 0.5 s" in events
    assert events_body(tmp_path / "none.raw.events") == INITIAL_EVENTS


@posix_only
def test_inherited_ignored_sigint_is_left_alone(tmp_path, sensor):
    """A capture started as a background job of a script is handed SIGINT
    ignored so a Ctrl+C aimed at the foreground does not reach it; the tool
    must not override that."""
    out = tmp_path / "bg.raw"
    cmd = [sys.executable, str(TOOL), "127.0.0.1", "100000", str(out), "--port", str(sensor.port), "--poll", "0.05"]
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN)) as p:
        try:
            deadline = time.time() + 30
            while not (out.exists() and out.stat().st_size >= 5 * N * 4):
                assert p.poll() is None and time.time() < deadline
                time.sleep(0.01)
            os.kill(p.pid, signal.SIGINT)
            time.sleep(0.5)
            assert p.poll() is None, "SIGINT ended a capture that inherited SIGINT ignored"
            assert out.stat().st_size > 5 * N * 4            # still capturing
        finally:
            p.kill()
    assert not (tmp_path / "bg.raw.meta.json").exists()  # killed, not interrupted: nothing finalised


def test_unreachable_sensor_writes_nothing(tmp_path):
    out = tmp_path / "nosensor.raw"
    p = subprocess.run([sys.executable, str(TOOL), "127.0.0.1", "5", str(out), "--port", str(free_port())],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 2
    assert "cannot read http://127.0.0.1" in p.stderr and "no file written" in p.stderr
    assert not out.exists() and not (tmp_path / "nosensor.raw.meta.json").exists()
