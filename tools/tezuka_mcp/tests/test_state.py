"""Freshness semantics of the MQTT state cache."""
import json

import pytest

from tezuka_mcp.state import StateCache, OBSERVATION_TOPIC, RESULT_TOPIC


def obs(seq, **extra):
    o = {"schema": "tezuka.classifier.observation/1", "seq": seq,
         "sensor": {"boot_id": "b00t", "hostname": "sdr"},
         "templates": {"sha256": "ab" * 32, "feature_semantics": "power-v1", "count": 3},
         "decision": {"label": "unknown", "accepted": False}, "input": {"fft_size": 4096}}
    o.update(extra)
    return json.dumps(o)


def test_unseen_is_unavailable_and_nothing_is_defaulted():
    c = StateCache("127.0.0.1", 1)
    s = c.snapshot(5, 10, now_mono=100.0)
    assert s["classifier"]["status"] == "unavailable" and s["classifier"]["best_effort"] is True
    assert s["receiver"]["rx/frequency"] == {"value": None, "status": "unavailable", "age_s": None}
    assert s["sensor"]["main/serial"]["value"] is None
    assert s["provenance"]["subscriber_connected"] is False


def test_retained_replay_does_not_count_as_fresh():
    c = StateCache("127.0.0.1", 1)
    c.ingest("state/rx/frequency", "1090000000", retained=True, now_mono=10.0)
    c.ingest(OBSERVATION_TOPIC, obs(41), retained=True, now_mono=10.0)
    s = c.snapshot(5, 10, now_mono=11.0)
    assert s["receiver"]["rx/frequency"]["value"] == "1090000000"
    assert s["receiver"]["rx/frequency"]["status"] == "retained"
    assert s["classifier"]["status"] == "retained" and s["classifier"]["seq"] == 41
    assert s["classifier"]["best_effort"] is False
    assert s["classifier"]["template_sha256"] == "ab" * 32
    assert s["classifier"]["boot_id"] == "b00t"


def test_live_messages_become_fresh_then_stale():
    c = StateCache("127.0.0.1", 1)
    c.ingest(OBSERVATION_TOPIC, obs(1), retained=True, now_mono=0.0)
    c.ingest(OBSERVATION_TOPIC, obs(2), retained=False, now_mono=1.0)
    c.ingest("state/rx/frequency", "1090000000", retained=False, now_mono=1.0)
    s = c.snapshot(5, 10, now_mono=2.0)
    assert s["classifier"]["status"] == "fresh" and s["classifier"]["age_s"] == 1.0 and s["classifier"]["seq"] == 2
    assert s["classifier"]["retained_replay_seen"] is True and s["classifier"]["live_messages"] == 1
    assert s["receiver"]["rx/frequency"]["status"] == "fresh"
    s = c.snapshot(5, 10, now_mono=30.0)
    assert s["classifier"]["status"] == "stale" and s["classifier"]["age_s"] == 29.0
    assert s["receiver"]["rx/frequency"]["status"] == "stale"


def test_repeated_seq_is_not_new_evidence():
    c = StateCache("127.0.0.1", 1)
    c.ingest(OBSERVATION_TOPIC, obs(7), retained=False, now_mono=1.0)
    c.ingest(OBSERVATION_TOPIC, obs(7), retained=False, now_mono=20.0)   # a replayed copy of the same cycle
    s = c.snapshot(5, 10, now_mono=21.0)
    assert s["classifier"]["status"] == "stale" and s["classifier"]["age_s"] == 20.0
    assert s["classifier"]["seq_repeats_ignored"] == 1
    c.ingest(OBSERVATION_TOPIC, obs(8), retained=False, now_mono=22.0)
    s = c.snapshot(5, 10, now_mono=22.5)
    assert s["classifier"]["status"] == "fresh" and s["classifier"]["seq"] == 8


def test_legacy_result_adapter_is_marked_best_effort():
    c = StateCache("127.0.0.1", 1)
    c.ingest(RESULT_TOPIC, json.dumps({"label": "FM Broadcast", "confidence": 0.8, "online": True}), retained=False, now_mono=1.0)
    s = c.snapshot(5, 10, now_mono=2.0)
    cl = s["classifier"]
    assert cl["best_effort"] is True and cl["source"] == RESULT_TOPIC and cl["status"] == "fresh"
    assert cl["seq"] is None and cl["boot_id"] is None and cl["template_sha256"] is None
    assert cl["decision"]["label"] == "FM Broadcast"
    assert "observation" in cl["best_effort_reason"]


def test_observation_wins_over_legacy_result():
    c = StateCache("127.0.0.1", 1)
    c.ingest(RESULT_TOPIC, json.dumps({"label": "x"}), retained=False, now_mono=1.0)
    c.ingest(OBSERVATION_TOPIC, obs(3), retained=False, now_mono=1.0)
    s = c.snapshot(5, 10, now_mono=1.5)
    assert s["classifier"]["source"] == OBSERVATION_TOPIC and s["classifier"]["best_effort"] is False


def test_malformed_observation_is_ignored_not_trusted():
    c = StateCache("127.0.0.1", 1)
    c.ingest(OBSERVATION_TOPIC, "not json", retained=False, now_mono=1.0)
    s = c.snapshot(5, 10, now_mono=1.5)
    assert s["classifier"]["status"] == "unavailable"


# --- Independent review 2026-11-08, R3: schema validation before use ------

def test_malformed_nested_object_is_rejected_and_previous_kept():
    c = StateCache("127.0.0.1", 1)
    c.ingest(OBSERVATION_TOPIC, obs(5), retained=False, now_mono=1.0)
    c.ingest(OBSERVATION_TOPIC, json.dumps({"schema": "tezuka.classifier.observation/1", "seq": 6, "sensor": "broken"}),
             retained=False, now_mono=2.0)
    s = c.snapshot(5, 10, now_mono=2.5)     # must not raise
    cl = s["classifier"]
    assert cl["seq"] == 5 and cl["status"] == "fresh" and cl["age_s"] == 1.5
    assert cl["validation"]["rejected_messages"] == 1 and "sensor" in cl["validation"]["last_rejection"]


@pytest.mark.parametrize("payload,needle", [
    ("{}", "schema"),
    (json.dumps({"seq": 1, "sensor": {"boot_id": "b"}}), "schema"),
    (json.dumps({"schema": "tezuka.classifier.observation/2", "seq": 1, "sensor": {"boot_id": "b"}}), "unsupported"),
    (json.dumps({"schema": "tezuka.classifier.observation/x", "seq": 1, "sensor": {"boot_id": "b"}}), "malformed"),
    (json.dumps({"schema": "something.else/1", "seq": 1, "sensor": {"boot_id": "b"}}), "schema"),
    (json.dumps({"schema": "tezuka.classifier.observation/1", "sensor": {"boot_id": "b"}}), "seq"),
    (json.dumps({"schema": "tezuka.classifier.observation/1", "seq": "7", "sensor": {"boot_id": "b"}}), "seq"),
    (json.dumps({"schema": "tezuka.classifier.observation/1", "seq": True, "sensor": {"boot_id": "b"}}), "seq"),
    (json.dumps({"schema": "tezuka.classifier.observation/1", "seq": -1, "sensor": {"boot_id": "b"}}), "seq"),
    (json.dumps({"schema": "tezuka.classifier.observation/1", "seq": 1}), "identity"),
    (json.dumps({"schema": "tezuka.classifier.observation/1", "seq": 1, "sensor": {}}), "identity"),
    (json.dumps({"schema": "tezuka.classifier.observation/1", "seq": 1, "sensor": {"boot_id": "b"}, "templates": []}), "templates"),
    ("[1,2]", "object"),
    ("not json", "JSON"),
])
def test_invalid_observation_never_becomes_fresh(payload, needle):
    c = StateCache("127.0.0.1", 1)
    c.ingest(OBSERVATION_TOPIC, payload, retained=False, now_mono=1.0)
    cl = c.snapshot(5, 10, now_mono=1.5)["classifier"]
    assert cl["status"] == "unavailable" and cl["best_effort"] is True
    assert cl["validation"]["rejected_messages"] == 1
    assert needle.lower() in cl["validation"]["last_rejection"].lower()


def test_valid_after_malformed_is_accepted():
    c = StateCache("127.0.0.1", 1)
    c.ingest(OBSERVATION_TOPIC, "{}", retained=False, now_mono=1.0)
    c.ingest(OBSERVATION_TOPIC, obs(1), retained=False, now_mono=2.0)
    cl = c.snapshot(5, 10, now_mono=2.5)["classifier"]
    assert cl["status"] == "fresh" and cl["seq"] == 1 and cl["validation"]["rejected_messages"] == 1


# --- R4: freshness belongs to one (payload, identity, seq) record ---------

def test_retained_older_seq_does_not_inherit_live_freshness():
    c = StateCache("127.0.0.1", 1)
    c.ingest(OBSERVATION_TOPIC, obs(100), retained=False, now_mono=100.0)
    c.ingest(OBSERVATION_TOPIC, obs(99), retained=True, now_mono=101.0)     # broker replays an older stored copy
    cl = c.snapshot(5, 10, now_mono=102.0)["classifier"]
    assert cl["seq"] == 100 and cl["status"] == "fresh" and cl["age_s"] == 2.0
    assert cl["out_of_order_ignored"] == 1 and cl["retained_replay_seen"] is True


def test_retained_newer_seq_replaces_as_retained_not_fresh():
    c = StateCache("127.0.0.1", 1)
    c.ingest(OBSERVATION_TOPIC, obs(100), retained=False, now_mono=100.0)
    c.ingest(OBSERVATION_TOPIC, obs(101), retained=True, now_mono=101.0)    # missed while disconnected
    cl = c.snapshot(5, 10, now_mono=102.0)["classifier"]
    assert cl["seq"] == 101 and cl["status"] == "retained" and cl["age_s"] is None


def test_new_producer_session_with_repeated_seq_is_accepted():
    c = StateCache("127.0.0.1", 1)
    c.ingest(OBSERVATION_TOPIC, obs(1, sensor={"boot_id": "b00t", "session_id": "b00t-100-1.0"}), retained=False, now_mono=1.0)
    c.ingest(OBSERVATION_TOPIC, obs(1, sensor={"boot_id": "b00t", "session_id": "b00t-200-5000.0"}), retained=False, now_mono=100.0)
    cl = c.snapshot(5, 10, now_mono=101.0)["classifier"]
    assert cl["status"] == "fresh" and cl["age_s"] == 1.0 and cl["seq"] == 1
    assert cl["session_id"] == "b00t-200-5000.0" and cl["producer_sessions_seen"] == 2
    assert cl["seq_repeats_ignored"] == 0


def test_out_of_order_live_seq_in_same_session_is_ignored():
    c = StateCache("127.0.0.1", 1)
    c.ingest(OBSERVATION_TOPIC, obs(10), retained=False, now_mono=1.0)
    c.ingest(OBSERVATION_TOPIC, obs(8), retained=False, now_mono=2.0)
    cl = c.snapshot(5, 10, now_mono=3.0)["classifier"]
    assert cl["seq"] == 10 and cl["age_s"] == 2.0 and cl["out_of_order_ignored"] == 1


def test_repeated_seq_with_changed_content_keeps_first_copy():
    c = StateCache("127.0.0.1", 1)
    c.ingest(OBSERVATION_TOPIC, obs(3, decision={"label": "A"}), retained=False, now_mono=1.0)
    c.ingest(OBSERVATION_TOPIC, obs(3, decision={"label": "B"}), retained=False, now_mono=2.0)
    cl = c.snapshot(5, 10, now_mono=2.5)["classifier"]
    assert cl["decision"] == {"label": "A"} and cl["age_s"] == 1.5
    assert cl["seq_repeats_ignored"] == 1 and cl["seq_repeats_with_changed_content"] == 1


def test_reconnect_epoch_is_reported():
    c = StateCache("127.0.0.1", 1)
    c.mark_connected(True, None, now_mono=0.0)
    c.ingest(OBSERVATION_TOPIC, obs(1), retained=False, now_mono=1.0)
    assert c.snapshot(5, 10, now_mono=2.0)["classifier"]["live_from_current_connection"] is True
    c.mark_connected(False, "disconnected: 7", now_mono=3.0)
    c.mark_connected(True, None, now_mono=4.0)
    s = c.snapshot(5, 10, now_mono=4.5)
    assert s["provenance"]["connection_epoch"] == 2
    assert s["classifier"]["live_from_current_connection"] is False


def test_receiver_entry_retained_different_value_drops_live_freshness():
    c = StateCache("127.0.0.1", 1)
    c.ingest("state/rx/frequency", "433000000", retained=False, now_mono=1.0)
    c.ingest("state/rx/frequency", "100000000", retained=True, now_mono=2.0)   # a different stored value after reconnect
    e = c.snapshot(5, 10, now_mono=2.5)["receiver"]["rx/frequency"]
    assert e["value"] == "100000000" and e["status"] == "retained" and e["age_s"] is None
    c.ingest("state/rx/frequency", "100000000", retained=False, now_mono=3.0)
    c.ingest("state/rx/frequency", "100000000", retained=True, now_mono=3.5)   # same payload replayed: evidence still holds
    e = c.snapshot(5, 10, now_mono=4.0)["receiver"]["rx/frequency"]
    assert e["status"] == "fresh" and e["age_s"] == 1.0
