"""Source-level guard for review finding R6 (2026-11-08): the fields the
MQTT network thread writes under `lock` (serial, sampling, rfinput, sweep
flags, frequency, config_generation) must reach publish_observation() only
through the obs_ctx snapshot copied under that lock in handle_frame(), never
by reading `a->...` directly while the other thread may be writing.

This checks the source text, not a running race: the daemon needs
libwebsockets/libmosquitto and a live sensor, so no ThreadSanitizer run is
part of the suite. It still fails the moment someone reintroduces a direct
read in the serializer."""
import re

from conftest import APP

SRC = APP / "classifier.c"
SHARED = ("serial_mqtt", "sampling_mqtt", "rfinput_mqtt", "sweep_activate", "sweep_engaged", "freq_mqtt", "config_generation")


def function_body(src, signature_start):
    i = src.index(signature_start)
    j = src.index("\n}\n", i)
    return src[i:j]


def test_publish_observation_reads_only_the_locked_snapshot():
    src = SRC.read_text()
    body = function_body(src, "static void publish_observation(")
    assert "const struct obs_ctx *oc" in body
    for name in SHARED:
        assert not re.search(r"\ba->" + name + r"\b", body), f"publish_observation reads a->{name} outside the lock"


def test_handle_frame_copies_the_snapshot_inside_the_lock():
    src = SRC.read_text()
    body = function_body(src, "static void handle_frame(")
    lock = body.index("pthread_mutex_lock(&a->lock);")
    unlock = body.index("pthread_mutex_unlock(&a->lock);", lock)
    assert "obs_ctx_copy_locked(a, &oc);" in body[lock:unlock]
    # and the serializer gets that snapshot, not the live fields
    assert re.search(r"publish_observation\([^;]*&oc, (true|false)\);", body)
    for name in SHARED:
        after = body[unlock:]
        assert not re.search(r"\ba->" + name + r"\b", after), f"handle_frame reads a->{name} after unlocking"


def test_snapshot_helper_covers_every_shared_field():
    src = SRC.read_text()
    body = function_body(src, "static void obs_ctx_copy_locked(const struct app *a, struct obs_ctx *oc)\n{")
    for name in SHARED:
        assert f"a->{name}" in body, f"obs_ctx_copy_locked does not copy {name}"
