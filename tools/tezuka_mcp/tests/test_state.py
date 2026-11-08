"""Freshness semantics of the MQTT state cache."""
import json

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
