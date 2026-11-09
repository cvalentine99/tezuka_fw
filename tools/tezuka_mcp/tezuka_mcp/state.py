"""Persistent MQTT subscriber and the sensor snapshot built from it.

Freshness is decided per topic from how the message arrived, not from
its content, and it belongs to the payload it arrived with:

  * A message delivered with the RETAIN flag set is the broker's stored
    copy, replayed at subscribe time (and again after every reconnect).
    It tells us the last known value but nothing about whether the
    producer is alive now, so it never refreshes `live_at`; when it
    carries a different payload than the last live message it also
    REPLACES that payload, so the live freshness no longer applies and
    the entry drops back to "retained". A replayed copy of the very same
    payload keeps the live evidence we already had for that payload.
  * A message without RETAIN came from the producer during this
    connection: it sets `live_at` and source="live".
  * For the classifier observation the record is (payload, producer
    identity, seq, freshness) as one unit. It is replaced only by a valid
    message that is newer within the same producer session, or by any
    valid message from a different session (a restarted daemon starts
    its seq again at 1). A repeated or older seq within a session is
    counted and otherwise ignored, and a message that fails schema
    validation is counted and otherwise ignored: the previous record
    keeps its own age.

A snapshot therefore reports, per section, one of: fresh (live and
younger than the configured max age), stale (live but older), retained
(only the replayed copy is held) or unavailable (never seen). It never
invents a frequency, rate or template hash: a missing value is None with
a reason.
"""
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from . import SCHEMA_SNAPSHOT
from .frame import bins_index_to_hz

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

# The observation message contract (docs/classifier_telemetry.md).
OBSERVATION_SCHEMA_PREFIX = "tezuka.classifier.observation/"
OBSERVATION_SCHEMA_VERSIONS = (1,)
# Sections that, when present, must be JSON objects (or null); the snapshot
# reads into them with .get().
OBSERVATION_OBJECT_FIELDS = ("time", "sensor", "input", "config", "templates", "decision",
                             "features", "temporal", "background", "health")
UNAVAILABLE = {"value": None, "status": "unavailable", "age_s": None}


def validate_observation(obj: Any) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    """Return (error, producer identity, seq). error is None when the
    message is a usable observation. Nothing is defaulted: a message that
    lacks the schema, the sequence or the producer identity is rejected
    rather than presented as a fresh observation of unknown origin."""
    if not isinstance(obj, dict):
        return "not a JSON object", None, None
    schema = obj.get("schema")
    if not isinstance(schema, str) or not schema.startswith(OBSERVATION_SCHEMA_PREFIX):
        return f"missing or foreign schema {schema!r}", None, None
    try:
        version = int(schema[len(OBSERVATION_SCHEMA_PREFIX):])
    except ValueError:
        return f"malformed schema version in {schema!r}", None, None
    if version not in OBSERVATION_SCHEMA_VERSIONS:
        return f"unsupported schema version {version} (supported: {list(OBSERVATION_SCHEMA_VERSIONS)})", None, None
    seq = obj.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        return f"missing or invalid seq {seq!r}", None, None
    for name in OBSERVATION_OBJECT_FIELDS:
        v = obj.get(name)
        if v is not None and not isinstance(v, dict):
            return f"field {name!r} is {type(v).__name__}, not an object", None, None
    sensor = obj.get("sensor")
    if not isinstance(sensor, dict):
        return "missing sensor identity object", None, None
    ident = sensor.get("session_id")
    if not isinstance(ident, str) or not ident:
        ident = sensor.get("boot_id")
    if not isinstance(ident, str) or not ident:
        return "missing producer identity (sensor.session_id or sensor.boot_id)", None, None
    return None, ident, seq


@dataclass
class Entry:
    value: str
    wall_at: float          # time.time() of the last message of any kind
    mono_at: float          # time.monotonic() of the last message of any kind
    live_at: Optional[float] = None   # monotonic of the live message that carried `value`
    retained_seen: bool = False
    live_count: int = 0
    live_epoch: Optional[int] = None  # connection epoch of that live message
    change_count: int = 0             # messages whose payload differed from the one before

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
    """The accepted observation: payload, producer identity, sequence and
    the freshness of THAT payload, replaced only as one unit."""
    obj: Dict[str, Any]
    identity: str
    seq: int
    mono_at: float
    live_at: Optional[float]
    live_epoch: Optional[int] = None


@dataclass
class ObservationStats:
    """Counters that outlive individual records."""
    retained_seen: bool = False
    live_count: int = 0
    accepted: int = 0
    seq_repeats: int = 0            # same session, same seq (content equal or not)
    seq_repeat_content_changed: int = 0
    out_of_order: int = 0           # same session, older seq
    sessions_seen: int = 0          # distinct producer identities accepted
    validation_errors: int = 0
    last_validation_error: Optional[str] = None


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
        self._obs_stats = ObservationStats()
        self.connected = False
        self.connected_at_mono: Optional[float] = None
        self.connect_attempts = 0
        # Bumped on every successful (re)connect. Retained copies are
        # replayed at the start of each epoch; a live message's epoch says
        # which connection delivered it.
        self.connection_epoch = 0
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
        self.mark_connected(bool(ok), None if ok else f"connect refused: {reason_code}")
        if ok:
            client.subscribe("state/#", qos=0)
            if not self.classifier_prefix.startswith("state/"):
                client.subscribe(self.classifier_prefix + "#", qos=0)
            log.info("connected to %s:%d, subscribed state/# (classifier prefix %s)", self.host, self.port, self.classifier_prefix)
        else:
            log.warning("MQTT connect failed: %s", reason_code)

    def _on_disconnect(self, client, userdata, flags, reason_code=None, properties=None):
        self.mark_connected(False, f"disconnected: {reason_code}")
        log.warning("MQTT disconnected: %s", reason_code)

    def mark_connected(self, ok: bool, error: Optional[str], now_mono: Optional[float] = None) -> None:
        """Connection bookkeeping (also driven directly by the tests)."""
        now_mono = time.monotonic() if now_mono is None else now_mono
        with self._lock:
            self.connected = ok
            self.connected_at_mono = now_mono if ok else None
            self.last_error = error
            if ok:
                self.connection_epoch += 1

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
            if payload != e.value:
                e.change_count += 1
            if retained:
                e.retained_seen = True
                if payload != e.value:
                    # The broker's stored copy differs from the payload our
                    # live evidence was about: that evidence does not carry
                    # over to this value.
                    e.live_at, e.live_epoch = None, None
            else:
                e.live_at, e.live_epoch = now_mono, self.connection_epoch
                e.live_count += 1
            e.value, e.wall_at, e.mono_at = payload, now_wall, now_mono
            if topic == self.observation_topic:
                self._ingest_observation(payload, retained, now_mono)

    def change_generation(self, *topics: str) -> int:
        """Value changes seen on these topics (a message carrying the same
        payload as the one before does not count, so api_controller.sh's
        2 s republish of unchanged flags leaves it alone): a generation a
        caller compares across two points in time to know whether any of
        the values moved in between, even if they moved back."""
        with self._lock:
            return sum(self._entries[t].change_count for t in topics if t in self._entries)

    def sweep_evidence(self, topics: Tuple[str, ...], max_age_s: float, now_mono: Optional[float] = None) -> Dict[str, Any]:
        """Everything Service.sweep_state needs, read under ONE acquisition
        of the lock so the connection state, the epoch, the generation and
        the per-topic evidence describe the same instant (snapshot() plus
        change_generation() would be two readings with a gap between).

        Per topic: value, status (fresh/stale/retained/unavailable), age_s,
        live_epoch (the connection epoch of the live message that carried
        the current value, None when none did), live_messages and
        retained_replay_seen. Entry semantics are unchanged: a retained
        replay of the same payload keeps the earlier live_at/live_epoch,
        and that provenance is exposed here as is - the caller decides
        whether live evidence from a previous connection epoch is good
        enough (sweep_state does not: after a reconnect the broker replays
        the stored flags whether or not api_controller.sh still runs)."""
        now_mono = time.monotonic() if now_mono is None else now_mono
        with self._lock:
            flags: Dict[str, Dict[str, Any]] = {}
            for t in topics:
                e = self._entries.get(t)
                if e is None:
                    flags[t] = {"value": None, "status": "unavailable", "age_s": None, "live_epoch": None,
                                "live_messages": 0, "retained_replay_seen": False}
                else:
                    d = e.describe(now_mono, max_age_s)
                    d["live_epoch"] = e.live_epoch
                    flags[t] = d
            return {
                "subscriber_connected": self.connected,
                "connection_epoch": self.connection_epoch,
                "generation": sum(self._entries[t].change_count for t in topics if t in self._entries),
                "flags": flags,
            }

    def _ingest_observation(self, payload: str, retained: bool, now_mono: float) -> None:
        st = self._obs_stats
        try:
            obj = json.loads(payload)
        except ValueError as ex:
            obj, err = None, f"not JSON ({ex})"
        else:
            err, ident, seq = validate_observation(obj)
        if err is not None:
            st.validation_errors += 1
            st.last_validation_error = err
            log.warning("observation rejected (%s); previous observation kept", err)
            return
        if retained:
            st.retained_seen = True
        prev = self._observation
        if prev is not None and prev.identity == ident:
            if seq == prev.seq:
                st.seq_repeats += 1          # a replay of the same cycle is not fresh evidence
                if obj != prev.obj:
                    st.seq_repeat_content_changed += 1
                    log.warning("observation seq %d repeated with different content; first copy kept", seq)
                return
            if seq < prev.seq:
                st.out_of_order += 1         # older cycle of the same session (replay, reorder)
                return
        elif prev is None or prev.identity != ident:
            st.sessions_seen += 1
        st.accepted += 1
        if retained:
            # The stored copy replaces the record but inherits no freshness.
            self._observation = ObservationEntry(obj=obj, identity=ident, seq=seq, mono_at=now_mono, live_at=None)
        else:
            st.live_count += 1
            self._observation = ObservationEntry(obj=obj, identity=ident, seq=seq, mono_at=now_mono,
                                                 live_at=now_mono, live_epoch=self.connection_epoch)

    # --- snapshot ---------------------------------------------------------
    def snapshot(self, observation_max_age_s: float, state_max_age_s: float, now_mono: Optional[float] = None) -> Dict[str, Any]:
        now_mono = time.monotonic() if now_mono is None else now_mono
        with self._lock:
            def section(topics, strip):
                return {t.split(strip, 1)[1]: self._entries[t].describe(now_mono, state_max_age_s) if t in self._entries
                        else dict(UNAVAILABLE) for t in topics}
            out: Dict[str, Any] = {
                "schema": SCHEMA_SNAPSHOT,
                "generated_at_unix_s": round(time.time(), 3),
                "provenance": {
                    "source": f"mqtt://{self.host}:{self.port} state/#",
                    "classifier_prefix": self.classifier_prefix,
                    "subscriber_connected": self.connected,
                    "connection_epoch": self.connection_epoch,
                    "subscriber_uptime_s": round(now_mono - self.started_mono, 1),
                    "connected_for_s": None if self.connected_at_mono is None else round(now_mono - self.connected_at_mono, 1),
                    "last_error": self.last_error,
                    "freshness_rule": ("fresh = a non-retained message within max_age; retained = only the broker's replayed copy is held "
                                       "for this value; stale = live but older; unavailable = never seen. Freshness belongs to the payload "
                                       "it arrived with: a retained copy with a different payload replaces the value as retained."),
                    "observation_max_age_s": observation_max_age_s,
                    "state_max_age_s": state_max_age_s,
                },
                "receiver": section(RX_TOPICS, "state/"),
                "sensor": section(MAIN_TOPICS, "state/"),
                "classifier_side": section(CLASSIFIER_SIDE_TOPICS, "state/classifier/"),
            }
            out["classifier"] = self._classifier_section(now_mono, observation_max_age_s)
            return out

    FREQUENCY_VIEW_NOTE = (
        "Two different things, deliberately kept apart. matched_window is the frequency range of the template "
        "window the daemon scored and accepted (decision.bin_offset / bandwidth_bins): the matched emission, in the "
        "daemon's own words. whole_span_peak is where the strongest bin of the residual sits across the ENTIRE span "
        "(features.peak_bin), whatever it is. They need not coincide: the window can match a weaker emission while the "
        "span's strongest bin belongs to something else, and the window is only reported on an accepted match. "
        "Neither value is a scoring input; both are presentation derived from the observation with the grid rule."
    )

    @staticmethod
    def _frequency_view(o: Dict[str, Any]) -> Dict[str, Any]:
        """Derive matched-window and whole-span-peak frequencies from one
        observation. Presentation only: the daemon's decision is untouched.
        usable bin j (bin0_offset=1 hides the step word) is centred at
        center + (j + 1 - fft_size/2) * span / fft_size; a window covering
        usable bins [offset, offset+bw) runs from the lower edge of its first
        bin to the upper edge of its last."""
        inp = o.get("input") or {}
        cfg = o.get("config") or {}
        dec = o.get("decision") or {}
        feat = o.get("features") or {}
        fft = inp.get("fft_size")
        center = cfg.get("frequency_hz")
        span = cfg.get("span_hz")
        view: Dict[str, Any] = {
            "note": StateCache.FREQUENCY_VIEW_NOTE,
            "grid_rule": "usable bin j centred at center_hz + (j + 1 - fft_size/2) * span_hz / fft_size; bin0_offset hides the step word",
            "derived_from": {"center_hz": center, "span_hz": span, "fft_size": fft,
                             "bin0_offset": inp.get("bin0_offset"), "config_bound": cfg.get("bound")},
        }
        missing = [k for k, v in (("input.fft_size", fft), ("config.frequency_hz", center), ("config.span_hz", span))
                   if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0]
        if missing:
            reason = "grid not derivable: " + ", ".join(missing) + " missing or non-positive in the observation"
            view["matched_window"] = {"available": False, "reason": reason}
            view["whole_span_peak"] = {"available": False, "reason": reason}
            view["peak_inside_matched_window"] = None
            return view
        hz_per_bin = span / fft

        def centre(j: float) -> float:
            return bins_index_to_hz(j, fft, center, span)

        off, bw = dec.get("bin_offset"), dec.get("bandwidth_bins")
        if dec.get("accepted") is True and isinstance(off, int) and isinstance(bw, int) and not isinstance(off, bool) and bw > 0:
            lo = centre(off) - hz_per_bin / 2
            view["matched_window"] = {
                "available": True, "label": dec.get("label"), "template_id": dec.get("template_id"),
                "bin_offset": off, "bandwidth_bins": bw,
                "start_hz": round(lo, 1), "center_hz": round(centre(off + bw / 2 - 0.5), 1),
                "end_hz": round(lo + bw * hz_per_bin, 1), "width_hz": round(bw * hz_per_bin, 1),
                "meaning": "frequency range of the template window the daemon scored and accepted",
            }
        else:
            view["matched_window"] = {
                "available": False,
                "reason": ("no accepted match this cycle: the daemon reports bin_offset/bandwidth_bins only for an accepted match"
                           if dec.get("accepted") is not True else "decision.bin_offset/bandwidth_bins missing or invalid"),
                "label": dec.get("label"),
            }
        pk = feat.get("peak_bin")
        if feat.get("valid") is True and isinstance(pk, int) and not isinstance(pk, bool) and pk >= 0:
            view["whole_span_peak"] = {
                "available": True, "bin": pk, "hz": round(centre(pk), 1),
                "meaning": "centre of the strongest residual bin over the whole span (features.peak_bin); independent of any match",
            }
        else:
            view["whole_span_peak"] = {"available": False,
                                       "reason": feat.get("invalid_reason") or "features.peak_bin missing or features invalid this cycle"}
        mw, wp = view["matched_window"], view["whole_span_peak"]
        if mw.get("available") and wp.get("available"):
            view["peak_inside_matched_window"] = mw["bin_offset"] <= wp["bin"] < mw["bin_offset"] + mw["bandwidth_bins"]
        else:
            view["peak_inside_matched_window"] = None
        return view

    def _classifier_section(self, now_mono: float, max_age_s: float) -> Dict[str, Any]:
        ob = self._observation
        st = self._obs_stats
        validation = {"rejected_messages": st.validation_errors, "last_rejection": st.last_validation_error}
        if ob is not None:
            if ob.live_at is None:
                status, age = "retained", None
            else:
                age = now_mono - ob.live_at
                status = "fresh" if age <= max_age_s else "stale"
            o = ob.obj
            sensor = o.get("sensor") or {}
            templates = o.get("templates") or {}
            return {
                "source": self.observation_topic,
                "best_effort": False,
                "status": status,
                "age_s": None if age is None else round(age, 3),
                "retained_replay_seen": st.retained_seen,
                "live_messages": st.live_count,
                "live_from_current_connection": None if ob.live_epoch is None else ob.live_epoch == self.connection_epoch,
                "seq_repeats_ignored": st.seq_repeats,
                "seq_repeats_with_changed_content": st.seq_repeat_content_changed,
                "out_of_order_ignored": st.out_of_order,
                "producer_sessions_seen": st.sessions_seen,
                "validation": validation,
                "schema": o.get("schema"),
                "seq": ob.seq,
                "producer_identity": ob.identity,
                "producer_time": o.get("time"),
                "sensor": sensor,
                "boot_id": sensor.get("boot_id"),
                "session_id": sensor.get("session_id"),
                "input": o.get("input"),
                "config": o.get("config"),
                "templates": templates,
                "template_sha256": templates.get("sha256"),
                "feature_semantics": templates.get("feature_semantics"),
                "decision": o.get("decision"),
                "features": o.get("features"),
                "frequency_view": self._frequency_view(o),
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
                    "reason": f"neither {self.observation_topic} nor {self.result_topic} seen",
                    "validation": validation}
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
            "validation": validation,
            "schema": None, "seq": None, "boot_id": None, "session_id": None, "template_sha256": None, "feature_semantics": None,
            "missing_reason": "sequence, boot id, template hash and feature semantics are only carried by the observation message",
            "decision": res,
            "frequency_view": {"matched_window": {"available": False, "reason": "legacy result message carries no grid (fft_size, centre, span)"},
                               "whole_span_peak": {"available": False, "reason": "legacy result message carries no features.peak_bin"},
                               "peak_inside_matched_window": None, "note": self.FREQUENCY_VIEW_NOTE},
            "online": res.get("online") if isinstance(res, dict) else None,
        }
