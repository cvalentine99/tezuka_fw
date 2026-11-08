/* Native (non-TSan) replay build of classifier.c: the daemon source is
 * compiled against the stub mosquitto/lws headers in stubs/ so the host
 * can run -r / -E replays with -B none (no network is ever touched; the
 * stubs fail every network call safely and replay mode never makes one).
 * Built as build/host/classifier_replay by `make host-tools` with
 * AddressSanitizer + UBSan, and used by tests/test_replay_events.py.
 *
 * Same pattern as harness.c: the daemon's main() is renamed so this file
 * can own the entry point; CLASSIFIER_SRC comes from the Makefile. */
#define main classifier_main
#include CLASSIFIER_SRC
#undef main

int main(int argc, char **argv)
{
    return classifier_main(argc, argv);
}
