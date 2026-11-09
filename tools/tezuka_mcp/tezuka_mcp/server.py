"""MCP server entry point: two read-only tools over stdio.

Diagnostics go to stderr (never stdout, which carries the protocol).
Nothing here publishes to MQTT, retunes, transmits, promotes templates or
restarts anything on the sensor; there is deliberately no generic
"publish MQTT command" tool.
"""
import json
import logging
import os
import sys
import time
from typing import Any, Dict, Optional

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import __version__
from .config import Config
from .state import StateCache
from .waterfall import build_waterfall_snapshot

log = logging.getLogger("tezuka_mcp")

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

INSTRUCTIONS = (
    "Read-only view of one Tezuka SDR sensor. get_sensor_snapshot returns the cached receiver and "
    "classifier state with explicit freshness per field (fresh/stale/retained/unavailable); "
    "snapshot_fpga_waterfall collects a bounded number of FPGA spectrum frames and summarizes them. "
    "Levels are relative to the producer's power scale, never dBm. Neither tool changes anything on the sensor. "
    "When explaining a classifier observation keep two frequencies apart: classifier.frequency_view.matched_window is "
    "the range of the template window the daemon scored and accepted (decision.bin_offset/bandwidth_bins), while "
    "classifier.frequency_view.whole_span_peak is the strongest bin of the residual (spectrum over the learned background) "
    "anywhere in the span (features.peak_bin). They are independent and often differ; never present one as the other, and "
    "do not treat a peak outside the window as proof the window is wrong."
)


class Service:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.cache = StateCache(cfg.mqtt_host, cfg.mqtt_port, client_id_suffix=f"_{os.getpid()}", classifier_prefix=cfg.classifier_prefix)
        self.calls = 0
        self.started = time.time()

    def start(self) -> None:
        self.cache.start()
        self.write_health("started")

    def write_health(self, event: str) -> None:
        if not self.cfg.health_path:
            return
        doc = {"version": __version__, "event": event, "time_unix_s": round(time.time(), 3),
               "uptime_s": round(time.time() - self.started, 1), "tool_calls": self.calls,
               "mqtt": {"host": self.cfg.mqtt_host, "port": self.cfg.mqtt_port, "connected": self.cache.connected,
                        "last_error": self.cache.last_error}}
        tmp = self.cfg.health_path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(doc, f)
            os.replace(tmp, self.cfg.health_path)
        except OSError as e:  # pragma: no cover
            log.warning("health file %s: %s", self.cfg.health_path, e)

    SWEEP_TOPICS = ("state/rx/sweep/activate", "state/rx/sweep/engaged")

    def sweep_state(self) -> Dict[str, Any]:
        """The receiver's sweep state as evidence, not a bare flag.

        `active` is True/False only when the MQTT subscriber is connected
        and BOTH state/rx/sweep/activate and state/rx/sweep/engaged were
        delivered live ON THE CURRENT CONNECTION (api_controller.sh
        republishes them every 2 s, so a live copy younger than
        TEZUKA_STATE_MAX_AGE_S is the normal case) with a value of "0" or
        "1". Anything else - disconnected, only the broker's retained copy,
        stale, missing, malformed, or live evidence that predates the last
        reconnect - is None with the reason: an old "off" is not evidence
        that a collection can be read as one ADC-centred span. After a
        reconnect the broker replays the stored flags whether or not the
        producer still runs; a replay of the same payload keeps the cache's
        earlier live provenance (live_epoch stays at the old connection),
        so each flag reports `live_epoch` and `from_current_connection`
        and both must come from the current epoch before `active` is
        decided. `generation` counts value CHANGES on the two topics
        (unchanged 2 s republishes do not move it) and `epoch` the MQTT
        connection epoch, so a caller comparing two readings sees a flag
        that went off -> on -> off in between, or a reconnect in between,
        even when both readings say "off". Everything is read under one
        acquisition of the cache lock."""
        ev = self.cache.sweep_evidence(self.SWEEP_TOPICS, self.cfg.state_max_age_s)
        epoch = ev["connection_epoch"]
        flags = {t.rsplit("/", 1)[1]: ev["flags"][t] for t in self.SWEEP_TOPICS}
        out: Dict[str, Any] = {"active": None, "reason": None, "generation": ev["generation"], "epoch": epoch,
                               "subscriber_connected": ev["subscriber_connected"],
                               "flags": {k: {"value": f["value"], "status": f["status"], "age_s": f["age_s"],
                                             "live_epoch": f["live_epoch"],
                                             "from_current_connection": (None if f["live_epoch"] is None or not ev["subscriber_connected"]
                                                                         else f["live_epoch"] == epoch)}
                                         for k, f in flags.items()}}
        reasons = []
        if not out["subscriber_connected"]:
            reasons.append("MQTT subscriber is not connected, so the cached sweep flags may be outdated")
        for name, f in flags.items():
            v, status, live_epoch = f["value"], f["status"], f["live_epoch"]
            if v is None:
                reasons.append(f"state/rx/sweep/{name} never seen")
            elif v not in ("0", "1"):
                reasons.append(f"state/rx/sweep/{name} has an unexpected value {v!r}")
            elif status != "fresh":
                reasons.append(f"state/rx/sweep/{name} is {status} (no live copy within {self.cfg.state_max_age_s} s)")
            elif live_epoch != epoch:
                since = ("only the broker's retained replay has been seen since the reconnect" if f.get("retained_replay_seen")
                         else "nothing has been received for it since the reconnect")
                reasons.append(f"state/rx/sweep/{name} live evidence is from a previous connection (epoch {live_epoch} vs current {epoch}); {since}")
        if reasons:
            out["reason"] = "; ".join(reasons)
            return out
        out["active"] = flags["activate"]["value"] == "1" or flags["engaged"]["value"] == "1"
        return out

    def sweep_active(self) -> Optional[bool]:
        return self.sweep_state()["active"]


def build_server(service: Service) -> MCPServer:
    server = MCPServer(name="tezuka-sensor", version=__version__, instructions=INSTRUCTIONS)

    @server.tool(
        name="get_sensor_snapshot",
        title="Sensor snapshot (cached MQTT state)",
        description=(
            "Return the sensor's receiver settings and the classifier's latest observation from a persistent "
            "MQTT subscription, with provenance and per-field freshness. 'retained' means only the broker's "
            "replayed copy was seen since this server connected, so the producer may be down; 'stale' means the "
            "last live message is older than the configured maximum age. The classifier section is taken from "
            "state/classifier/observation (atomic, with producer sequence, boot id, template hash and feature-semantics "
            "version); when a sensor only publishes the legacy result topic the section is marked best_effort with the "
            "missing fields set to null. Missing values are never defaulted. classifier.frequency_view derives, from the "
            "observation's own grid (center, span, fft_size), the matched window as a frequency range (start/center/end/width, "
            "accepted matches only) and, separately, the whole-span residual peak frequency; peak_inside_matched_window says "
            "whether they coincide. These are presentation only, not scoring inputs."
        ),
        annotations=READ_ONLY,
    )
    def get_sensor_snapshot() -> Dict[str, Any]:
        service.calls += 1
        snap = service.cache.snapshot(service.cfg.observation_max_age_s, service.cfg.state_max_age_s)
        service.write_health("get_sensor_snapshot")
        return snap

    @server.tool(
        name="snapshot_fpga_waterfall",
        title="FPGA spectrum snapshot (/waterfall)",
        description=(
            "Collect up to max_frames binary frames from maia-httpd's /waterfall WebSocket within timeout_s "
            "(both bounded by the server configuration), verify they describe one spectrometer context (GET /api "
            "before and after, constant FFT size and step word), average them in the power domain and return the "
            "frequency grid, a decimated PSD summary, a declared noise-floor estimate, above-floor segments and "
            "provenance. Levels are dB relative to the producer's unit scale, not dBm. DDC input, non-ADC input and "
            "sweep mode are reported as unsupported rather than interpreted; so is a sweep state that cannot be "
            "established from live MQTT on the current connection (subscriber disconnected, flags missing, stale, only "
            "retained, or live only before the last reconnect), and a collection during which the sweep state "
            "changed is rejected. Read-only."
        ),
        annotations=READ_ONLY,
    )
    def snapshot_fpga_waterfall(max_frames: int = 4, timeout_s: float = 15.0, threshold_over_floor_db: float = 10.0) -> Dict[str, Any]:
        service.calls += 1
        cfg = service.cfg
        mf = max(1, min(int(max_frames), cfg.waterfall_max_frames))
        to = max(1.0, min(float(timeout_s), cfg.waterfall_max_timeout_s))
        thr = max(1.0, min(float(threshold_over_floor_db), 60.0))
        res = build_waterfall_snapshot(cfg.http_host, cfg.http_port, cfg.waterfall_path, mf, to, None, thr,
                                       sweep_state_fn=service.sweep_state)
        res["provenance"]["bounds_applied"] = {"max_frames": mf, "timeout_s": to, "threshold_over_floor_db": thr}
        service.write_health("snapshot_fpga_waterfall")
        return res

    return server


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=os.environ.get("TEZUKA_MCP_LOG", "INFO"),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = Config.from_env()
    service = Service(cfg)
    service.start()
    log.info("tezuka-mcp %s: mqtt %s:%d, http %s:%d, stdio transport", __version__, cfg.mqtt_host, cfg.mqtt_port, cfg.http_host, cfg.http_port)
    try:
        build_server(service).run(transport="stdio")
    finally:
        service.cache.stop()


if __name__ == "__main__":
    main()
