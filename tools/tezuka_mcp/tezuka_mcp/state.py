"""Persistent MQTT subscriber and the sensor snapshot built from it.

Freshness is decided per topic from how the message arrived, not from
its content:

  * A message delivered with the RETAIN flag set is the broker's stored
    copy, replayed at subscribe time. It tells us the last known value
    but nothing about whether the producer is alive now, so it populates
    `value` with source="retained" and never refreshes `live_at`.
  * A message without RETAIN came from the producer during this
    connection: it sets `live_at` and source="live".
  * For the classifier observation, a repeated `seq` is also not new.

A snapshot therefore reports, per section, one of: fresh (live and
younger than the configured max age), stale (live but older), retained
(only the replayed copy was ever seen) or unavailable (never seen). It
never invents a frequency, rate or template hash: a missing value is
None with a reason.
"""
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from . import SCHEMA_SNAPSHOT

log = logging.getLogger("tezuka_mcp.state")

OBSERVATION_TOPIC = "state/classifier/observation"
RESULT_TOPIC = "state/classifier/result"
RX_TOPICS = (
    "state/rx/frequency", "state/rx/sampling", "state/rx/span", "state/rx/bandwidth",
    "state/rx/gain", "state/rx/gain_mode", "state/rx/rfinput", "state/rx/ant",
    "state/rx/sweep/activate", "state/rx/sweep/engaged", "state/rx/sweep/frequency", "state/rx/sweep/span",
)
MAIN_TOPICS = ("state/main/serial", "state/main/hw_model", "state/main/fw_version", "state/main/uptime")
CLASSIFIER_SIDE_TOPICS = ("state/classifier/enabled", "state/classifier/background", "state/classifier/templates_status")


@dataclass
class Entry:
    value: str
    wall_at: float          # time.time() of the last message of any kind
    mono_at: float          # time.monotonic() of the last message of any kind
    live_at: Optional[float] = None   # monotonic of the last NON-retained message
    retained_seen: bool = False
    live_count: int = 0

    def describe(self, now_mono: float, max_age_s: float) -> Dict[str, Any]:
        if self.live_at is None:
            status = "retained"
            age = None
        else:
            age = now_mono - self.live_at
            status = "fresh" if age <= max_age_s else "stale"
        return {"value": self.value, "status": status, "age_s": None if age is None else round(age, 3),
                "retained_replay_seen": self.retained_seen, "live_messages": self.live_count}


@dataclass
class ObservationEntry:
    obj: Dict[str, Any]
    seq: Optional[int]
    mono_at: float
    live_at: Optional[float]
    retained_seen: bool
    live_count: int = 0
    seq_repeats: int = 0


class StateCache:
    """Thread-safe cache fed by a paho-mqtt network thread."""

    def __init__(self, host: str, port: int, client_id_suffix: str = "", classifier_prefix: str = "state/classifier/"):
        self.host, self.port = host, port
        self.classifier_prefix = classifier_prefix
        self.observation_topic = classifier_prefix + "observation"
        self.result_topic = classifier_prefix + "result"
        self._lock = threading.Lock()
        self._entries: Dict[str, Entry] = {}
        self._observation: Optional[ObservationEntry] = None
        self.connected = False
        self.connected_at_mono: Optional[float] = None
        self.connect_attempts = 0
        self.last_error: Optional[str] = None
        self._client = None
        self._client_id = "tezuka_mcp" + client_id_suffix
        self.started_mono = time.monotonic()

    # --- paho plumbing ---------------------------------------------------
    def start(self) -> None:
        import paho.mqtt.client as mqtt
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self._client_id, clean_session=True)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        c.reconnect_delay_set(1, 10)
        self._client = c
        try:
            self.connect_attempts += 1
            c.connect_async(self.host, self.port, keepalive=30)
        except Exception as e:  # pragma: no cover - connect_async rarely raises
            self.last_error = str(e)
        c.loop_start()

    def stop(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        ok = (getattr(reason_code, "is_failure", None) is not None and not reason_code.is_failure) or reason_code == 0
        with self._lock:
            self.connected = bool(ok)
            self.connected_at_mono = time.monotonic() if ok else None
            self.last_error = None if ok else f"connect refused: {reason_code}"
        if ok:
            client.subscribe("state/#", qos=0)
            if not self.classifier_prefix.startswith("state/"):
                client.subscribe(self.classifier_prefix + "#", qos=0)
            log.info("connected to %s:%d, subscribed state/# (classifier prefix %s)", self.host, self.port, self.classifier_prefix)
        else:
            log.warning("MQTT connect failed: %s", reason_code)

    def _on_disconnect(self, client, userdata, flags, reason_code=None, properties=None):
        with self._lock:
            self.connected = False
            self.last_error = f"disconnected: {reason_code}"
        log.warning("MQTT disconnected: %s", reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            payload = repr(msg.payload)
        self.ingest(msg.topic, payload, bool(msg.retain))

    # --- ingestion (also used directly by the tests) --------------------
    def ingest(self, topic: str, payload: str, retained: bool, now_mono: Optional[float] = None) -> None:
        now_mono = time.monotonic() if now_mono is None else now_mono
        now_wall = time.time()
        with self._lock:
            e = self._entries.get(topic)
            if e is None:
                e = Entry(value=payload, wall_at=now_wall, mono_at=now_mono)
                self._entries[topic] = e
            e.value, e.wall_at, e.mono_at = payload, now_wall, now_mono
            if retained:
                e.retained_seen = True
            else:
                e.live_at = now_mono
                e.live_count += 1
            if topic == self.observation_topic:
                self._ingest_observation(payload, retained, now_mono)

    def _ingest_observation(self, payload: str, retained: bool, now_mono: float) -> None:
        try:
            obj = json.loads(payload)
            if not isinstance(obj, dict):
                raise ValueError("not an object")
        except ValueError as ex:
            log.warning("observation payload is not JSON (%s); ignored", ex)
            return
        seq = obj.get("seq") if isinstance(obj.get("seq"), int) else None
        prev = self._observation
        if prev is None:
            self._observation = ObservationEntry(obj=obj, seq=seq, mono_at=now_mono, live_at=None, retained_seen=retained)
            prev = self._observation
        else:
            prev.obj, prev.mono_at = obj, now_mono
        if retained:
            prev.retained_seen = True
            return
        if seq is not None and prev.seq == seq and prev.live_at is not None:
            prev.seq_repeats += 1   # a replay of the same cycle is not fresh evidence
            return
        prev.seq = seq
        prev.live_at = now_mono
        prev.live_count += 1

    # --- snapshot ---------------------------------------------------------
    def snapshot(self, observation_max_age_s: float, state_max_age_s: float, now_mono: Optional[float] = None) -> Dict[str, Any]:
        now_mono = time.monotonic() if now_mono is None else now_mono
        with self._lock:
            out: Dict[str, Any] = {
                "schema": SCHEMA_SNAPSHOT,
                "generated_at_unix_s": round(time.time(), 3),
                "provenance": {
                    "source": f"mqtt://{self.host}:{self.port} state/#",
                    "classifier_prefix": self.classifier_prefix,
                    "subscriber_connected": self.connected,
                    "subscriber_uptime_s": round(now_mono - self.started_mono, 1),
                    "connected_for_s": None if self.connected_at_mono is None else round(now_mono - self.connected_at_mono, 1),
                    "last_error": self.last_error,
                    "freshness_rule": "fresh = a non-retained message within max_age; retained = only the broker's replayed copy seen; stale = live but older; unavailable = never seen",
                    "observation_max_age_s": observation_max_age_s,
                    "state_max_age_s": state_max_age_s,
                },
                "receiver": {t.split("state/", 1)[1]: self._entries[t].describe(now_mono, state_max_age_s) if t in self._entries
                             else {"value": None, "status": "unavailable", "age_s": None} for t in RX_TOPICS},
                "sensor": {t.split("state/", 1)[1]: self._entries[t].describe(now_mono, state_max_age_s) if t in self._entries
                           else {"value": None, "status": "unavailable", "age_s": None} for t in MAIN_TOPICS},
                "classifier_side": {t.split("state/classifier/", 1)[1]: self._entries[t].describe(now_mono, state_max_age_s) if t in self._entries
                                    else {"value": None, "status": "unavailable", "age_s": None} for t in CLASSIFIER_SIDE_TOPICS},
            }
            out["classifier"] = self._classifier_section(now_mono, observation_max_age_s)
            return out

    def _classifier_section(self, now_mono: float, max_age_s: float) -> Dict[str, Any]:
        ob = self._observation
        if ob is not None:
            if ob.live_at is None:
                status, age = "retained", None
            else:
                age = now_mono - ob.live_at
                status = "fresh" if age <= max_age_s else "stale"
            o = ob.obj
            return {
                "source": self.observation_topic,
                "best_effort": False,
                "status": status,
                "age_s": None if age is None else round(age, 3),
                "retained_replay_seen": ob.retained_seen,
                "live_messages": ob.live_count,
                "seq_repeats_ignored": ob.seq_repeats,
                "schema": o.get("schema"),
                "seq": o.get("seq"),
                "producer_time": o.get("time"),
                "sensor": o.get("sensor"),
                "boot_id": (o.get("sensor") or {}).get("boot_id"),
                "input": o.get("input"),
                "config": o.get("config"),
                "templates": o.get("templates"),
                "template_sha256": (o.get("templates") or {}).get("sha256"),
                "feature_semantics": (o.get("templates") or {}).get("feature_semantics"),
                "decision": o.get("decision"),
                "features": o.get("features"),
                "temporal": o.get("temporal"),
                "background": o.get("background"),
                "health": o.get("health"),
            }
        # Legacy adapter: a daemon that predates the observation message only
        # publishes `result`. Everything it lacks is None with a reason, and
        # the whole section is flagged best_effort.
        r = self._entries.get(self.result_topic)
        if r is None:
            return {"source": None, "best_effort": True, "status": "unavailable",
                    "reason": f"neither {self.observation_topic} nor {self.result_topic} seen"}
        try:
            res = json.loads(r.value)
            if not isinstance(res, dict):
                res = {"raw": r.value}
        except ValueError:
            res = {"raw": r.value}
        d = r.describe(now_mono, max_age_s)
        return {
            "source": self.result_topic,
            "best_effort": True,
            "best_effort_reason": f"daemon does not publish {self.observation_topic}; fields below are reassembled from the legacy result message and the retained per-field topics",
            "status": d["status"], "age_s": d["age_s"], "retained_replay_seen": d["retained_replay_seen"], "live_messages": d["live_messages"],
            "schema": None, "seq": None, "boot_id": None, "template_sha256": None, "feature_semantics": None,
            "missing_reason": "sequence, boot id, template hash and feature semantics are only carried by the observation message",
            "decision": res,
            "online": res.get("online") if isinstance(res, dict) else None,
        }
