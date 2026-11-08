"""Service.sweep_state: the waterfall tool's sweep guard must be built from
current, live, valid MQTT evidence (independent review 2026-11-08, R5)."""
import time

from tezuka_mcp.config import Config
from tezuka_mcp.server import Service

ACT, ENG = "state/rx/sweep/activate", "state/rx/sweep/engaged"


def make_service(state_max_age_s=10.0):
    cfg = Config(mqtt_host="127.0.0.1", mqtt_port=1, http_host="127.0.0.1", http_port=1, waterfall_path="/waterfall",
                 observation_max_age_s=5.0, state_max_age_s=state_max_age_s, waterfall_max_frames=32,
                 waterfall_max_timeout_s=60.0, health_path="", classifier_prefix="state/classifier/")
    return Service(cfg)     # not started: the cache is fed directly


def test_never_connected_is_unknown():
    st = make_service().sweep_state()
    assert st["active"] is None and "not connected" in st["reason"] and "never seen" in st["reason"]
    assert st["generation"] == 0


def test_disconnected_with_retained_off_is_unknown_not_false():
    svc = make_service()
    now = time.monotonic()
    svc.cache.ingest(ACT, "0", retained=True, now_mono=now)
    svc.cache.ingest(ENG, "0", retained=True, now_mono=now)
    st = svc.sweep_state()
    assert st["active"] is None
    assert "not connected" in st["reason"] and "retained" in st["reason"]
    assert st["flags"]["activate"] == {"value": "0", "status": "retained", "age_s": None,
                                       "live_epoch": None, "from_current_connection": None}


def test_connected_but_only_retained_is_unknown():
    svc = make_service()
    now = time.monotonic()
    svc.cache.mark_connected(True, None, now_mono=now)
    svc.cache.ingest(ACT, "0", retained=True, now_mono=now)
    svc.cache.ingest(ENG, "0", retained=True, now_mono=now)
    st = svc.sweep_state()
    assert st["active"] is None and "retained" in st["reason"] and "not connected" not in st["reason"]


def test_connected_live_fresh_off_is_false():
    svc = make_service()
    now = time.monotonic()
    svc.cache.mark_connected(True, None, now_mono=now)
    svc.cache.ingest(ACT, "0", retained=False, now_mono=now)
    svc.cache.ingest(ENG, "0", retained=False, now_mono=now)
    st = svc.sweep_state()
    assert st["active"] is False and st["reason"] is None and st["generation"] == 0 and st["epoch"] == 1


def test_stale_live_off_is_unknown():
    svc = make_service(state_max_age_s=10.0)
    now = time.monotonic()
    svc.cache.mark_connected(True, None, now_mono=now - 100)
    svc.cache.ingest(ACT, "0", retained=False, now_mono=now - 60)
    svc.cache.ingest(ENG, "0", retained=False, now_mono=now - 60)
    st = svc.sweep_state()
    assert st["active"] is None and "stale" in st["reason"]


def test_one_flag_missing_or_malformed_is_unknown():
    svc = make_service()
    now = time.monotonic()
    svc.cache.mark_connected(True, None, now_mono=now)
    svc.cache.ingest(ACT, "0", retained=False, now_mono=now)
    st = svc.sweep_state()
    assert st["active"] is None and "engaged never seen" in st["reason"]
    svc.cache.ingest(ENG, "n/a", retained=False, now_mono=now)
    st = svc.sweep_state()
    assert st["active"] is None and "unexpected value 'n/a'" in st["reason"]


def test_either_flag_on_is_true_and_generation_counts_value_changes():
    svc = make_service()
    now = time.monotonic()
    svc.cache.mark_connected(True, None, now_mono=now)
    svc.cache.ingest(ACT, "0", retained=False, now_mono=now)
    svc.cache.ingest(ENG, "1", retained=False, now_mono=now)
    st = svc.sweep_state()
    assert st["active"] is True and st["generation"] == 0
    svc.cache.ingest(ENG, "0", retained=False, now_mono=now)
    st2 = svc.sweep_state()
    assert st2["active"] is False and st2["generation"] == 1
    assert svc.sweep_active() is False


def test_unchanged_republish_keeps_generation_but_refreshes():
    # api_controller.sh republishes the flags every 2 s whether or not they
    # changed: that must keep the state usable (fresh) without looking like
    # a change to a collection in progress.
    svc = make_service()
    now = time.monotonic()
    svc.cache.mark_connected(True, None, now_mono=now - 8)
    for k in range(5):
        svc.cache.ingest(ACT, "0", retained=False, now_mono=now - 8 + 2 * k)
        svc.cache.ingest(ENG, "0", retained=False, now_mono=now - 8 + 2 * k)
    st = svc.sweep_state()
    assert st["active"] is False and st["generation"] == 0 and st["flags"]["activate"]["status"] == "fresh"


def test_off_on_off_moves_generation_by_two():
    svc = make_service()
    now = time.monotonic()
    svc.cache.mark_connected(True, None, now_mono=now)
    svc.cache.ingest(ACT, "0", retained=False, now_mono=now)
    svc.cache.ingest(ENG, "0", retained=False, now_mono=now)
    g0 = svc.sweep_state()["generation"]
    svc.cache.ingest(ACT, "1", retained=False, now_mono=now)
    svc.cache.ingest(ACT, "0", retained=False, now_mono=now)
    st = svc.sweep_state()
    assert st["active"] is False and st["generation"] == g0 + 2


def test_reconnect_moves_epoch_and_retained_replay_is_not_fresh():
    svc = make_service()
    now = time.monotonic()
    svc.cache.mark_connected(True, None, now_mono=now)
    svc.cache.ingest(ACT, "0", retained=False, now_mono=now)
    svc.cache.ingest(ENG, "0", retained=False, now_mono=now)
    e0 = svc.sweep_state()["epoch"]
    svc.cache.mark_connected(False, "disconnected: 7", now_mono=now)
    svc.cache.mark_connected(True, None, now_mono=now)
    svc.cache.ingest(ACT, "0", retained=True, now_mono=now)      # broker replays the same values
    svc.cache.ingest(ENG, "0", retained=True, now_mono=now)
    st = svc.sweep_state()
    assert st["epoch"] == e0 + 1 and st["generation"] == 0
    # The cache keeps the earlier live provenance for the unchanged payload
    # (the retained replay does not overwrite it), but that evidence is from
    # the previous connection: the producer may have died while we were
    # away and the broker would replay the same "0" regardless.
    assert st["active"] is None
    assert "previous connection" in st["reason"] and f"epoch {e0} vs current {e0 + 1}" in st["reason"]
    assert "activate" in st["reason"] and "engaged" in st["reason"]
    for name in ("activate", "engaged"):
        f = st["flags"][name]
        assert f["status"] == "fresh" and f["live_epoch"] == e0 and f["from_current_connection"] is False


def _reconnect_with_retained_replay(svc, now):
    """Epoch 1: both flags live "0"; disconnect; epoch 2: the broker replays
    the same "0" values as RETAINED copies (the reviewer's R5 reproduction)."""
    svc.cache.mark_connected(True, None, now_mono=now)
    svc.cache.ingest(ACT, "0", retained=False, now_mono=now)
    svc.cache.ingest(ENG, "0", retained=False, now_mono=now)
    assert svc.sweep_state()["active"] is False
    svc.cache.mark_connected(False, "disconnected: 7", now_mono=now)
    svc.cache.mark_connected(True, None, now_mono=now)
    svc.cache.ingest(ACT, "0", retained=True, now_mono=now)
    svc.cache.ingest(ENG, "0", retained=True, now_mono=now)


def test_retained_only_reconnect_is_unknown():
    svc = make_service()
    now = time.monotonic()
    _reconnect_with_retained_replay(svc, now)
    st = svc.sweep_state()
    assert st["active"] is None and st["epoch"] == 2 and st["subscriber_connected"] is True
    assert "previous connection (epoch 1 vs current 2)" in st["reason"]
    assert st["flags"]["activate"]["from_current_connection"] is False
    assert st["flags"]["engaged"]["from_current_connection"] is False
    assert svc.sweep_active() is None


def test_only_one_flag_refreshed_live_in_new_epoch_names_the_other():
    svc = make_service()
    now = time.monotonic()
    _reconnect_with_retained_replay(svc, now)
    svc.cache.ingest(ACT, "0", retained=False, now_mono=now)     # api_controller.sh's first republish after the reconnect
    st = svc.sweep_state()
    assert st["active"] is None
    assert "state/rx/sweep/engaged live evidence is from a previous connection (epoch 1 vs current 2)" in st["reason"]
    assert "state/rx/sweep/activate" not in st["reason"]
    assert st["flags"]["activate"] == {"value": "0", "status": "fresh", "age_s": st["flags"]["activate"]["age_s"],
                                       "live_epoch": 2, "from_current_connection": True}
    assert st["flags"]["engaged"]["live_epoch"] == 1 and st["flags"]["engaged"]["from_current_connection"] is False


def test_both_flags_refreshed_live_in_new_epoch_is_false_and_generation_unchanged():
    svc = make_service()
    now = time.monotonic()
    _reconnect_with_retained_replay(svc, now)
    svc.cache.ingest(ACT, "0", retained=False, now_mono=now)
    svc.cache.ingest(ENG, "0", retained=False, now_mono=now)
    st = svc.sweep_state()
    assert st["active"] is False and st["reason"] is None
    assert st["generation"] == 0 and st["epoch"] == 2
    assert all(st["flags"][k]["from_current_connection"] is True and st["flags"][k]["live_epoch"] == 2
               for k in ("activate", "engaged"))


def test_flag_fields_carry_live_epoch_provenance():
    svc = make_service()
    st = svc.sweep_state()
    assert st["flags"]["activate"] == {"value": None, "status": "unavailable", "age_s": None,
                                       "live_epoch": None, "from_current_connection": None}
    now = time.monotonic()
    svc.cache.ingest(ACT, "0", retained=True, now_mono=now)
    st = svc.sweep_state()
    assert st["flags"]["activate"]["live_epoch"] is None and st["flags"]["activate"]["from_current_connection"] is None


def test_sweep_evidence_is_one_locked_reading():
    svc = make_service()
    now = time.monotonic()
    svc.cache.mark_connected(True, None, now_mono=now)
    svc.cache.ingest(ACT, "0", retained=False, now_mono=now)
    svc.cache.ingest(ACT, "1", retained=False, now_mono=now)
    ev = svc.cache.sweep_evidence((ACT, ENG), 10.0, now_mono=now)
    assert ev["subscriber_connected"] is True and ev["connection_epoch"] == 1
    assert ev["generation"] == 1 == svc.cache.change_generation(ACT, ENG)
    assert ev["flags"][ACT] == {"value": "1", "status": "fresh", "age_s": 0.0, "retained_replay_seen": False,
                                "live_messages": 2, "live_epoch": 1}
    assert ev["flags"][ENG] == {"value": None, "status": "unavailable", "age_s": None, "retained_replay_seen": False,
                                "live_messages": 0, "live_epoch": None}
    # the general snapshot keeps its shape (no live_epoch leak into describe())
    snap = svc.cache.snapshot(5.0, 10.0, now_mono=now)
    assert "live_epoch" not in snap["receiver"]["rx/sweep/activate"]
