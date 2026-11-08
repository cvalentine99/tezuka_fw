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
    "Levels are relative to the producer's power scale, never dBm. Neither tool changes anything on the sensor."
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

    def sweep_active(self) -> Optional[bool]:
        snap = self.cache.snapshot(self.cfg.observation_max_age_s, self.cfg.state_max_age_s)
        v = snap["receiver"].get("rx/sweep/activate", {}).get("value")
        e = snap["receiver"].get("rx/sweep/engaged", {}).get("value")
        if v is None and e is None:
            return None
        return v == "1" or e == "1"


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
            "missing fields set to null. Missing values are never defaulted."
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
            "sweep mode are reported as unsupported rather than interpreted. Read-only."
        ),
        annotations=READ_ONLY,
    )
    def snapshot_fpga_waterfall(max_frames: int = 4, timeout_s: float = 15.0, threshold_over_floor_db: float = 10.0) -> Dict[str, Any]:
        service.calls += 1
        cfg = service.cfg
        mf = max(1, min(int(max_frames), cfg.waterfall_max_frames))
        to = max(1.0, min(float(timeout_s), cfg.waterfall_max_timeout_s))
        thr = max(1.0, min(float(threshold_over_floor_db), 60.0))
        res = build_waterfall_snapshot(cfg.http_host, cfg.http_port, cfg.waterfall_path, mf, to, service.sweep_active(), thr)
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
