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
    assert st["flags"]["activate"] == {"value": "0", "status": "retained", "age_s": None}


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
    assert st["active"] is False      # same payload: the live evidence from before still holds
