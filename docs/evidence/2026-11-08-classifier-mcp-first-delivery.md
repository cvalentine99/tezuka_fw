# Classifier / MCP foundation — first delivery record (2026-11-08)

Branch `worktree-classifier-mcp-foundation`, based on `7997d95` (the
`signalsdrpro-gps-uart` fork branch that the test sensor actually runs),
not on `main`. Nothing is committed or pushed by this record; the diff is
the working tree of that worktree.

Evidence files referenced below are in `docs/evidence/2026-11-08/`
(copied from the session scratchpad; the 170 KB raw MQTT sample and the
test instance's log were kept out of git). Credentials appear nowhere in
this record, the diff or the evidence files.

## How each claim was verified

Each result is labelled with the method that produced it:

* **native** — host build and pytest on the development machine
  (gcc with ASan/UBSan for the C host tools, Python 3 venv with mcp 2.2.0,
  paho-mqtt 2.2.0, websockets 17.4, pytest 9.2).
* **cross** — cross-compiled for armv7 with the signalsdrpro Buildroot SDK
  (`arm-linux-gcc`, `-O2 -mfpu=neon -mfloat-abi=hard -Wall -Wextra`, zero warnings).
  Binary md5 `36386d00a55e71b05467f7f4b55ea232`.
* **qemu** — the cross-compiled binary run under `qemu-arm-static -L <sysroot>`.
* **replay** — the daemon fed recorded frames (`-r`) instead of a live WebSocket.
* **board** — executed on the physical SignalSDR Pro
  (hostname signalsdrpro, firmware tezuka-v0.3.24-34-g6e3a, boot_id
  `aaa681fc-b0d5-48d8-a594-080a84a8fad9`), over SSH on the LAN.

No full Buildroot firmware image was built for this delivery and no
reflash, reboot, FPGA change, TX, boot-artifact write or production binary
replacement took place (none was required by the brief).

## Test counts

| Suite | Method | Result |
|---|---|---|
| `app/classifier/tests` | native | 82 passed, 2 skipped (ARM tests skip without `TEZUKA_ARM_SYSROOT`) |
| `app/classifier/tests/test_validate_mode_arm.py` | cross + qemu | 2 passed |
| `tools/tezuka_mcp/tests` | native | 24 passed |

## Item 1 — classifier MQTT publishing without a shell

* `classifier.c` publishes through libmosquitto only (already the case for
  the result path; the remaining string handling now goes through the
  bounded JSON writer `jsonw.c`, so labels with spaces, `+`, `/`, quotes,
  backslashes and non-ASCII travel verbatim). Publish failures increment
  `health.publish_errors`, are logged for the first 5 and then every 1000th.
* Label contract documented in `scft.h` and `docs/classifier_telemetry.md`.
* **native:** `test_utf8_jsonw.py` (escaping, invalid UTF-8 → U+FFFD,
  overflow latch), `test_scft.py` labels `Quote " back\slash`,
  `Émetteur ✓ français`, `FM Broadcast`, `A+B/C D`.
* **board:** a test instance (`-T test/classifier/`) published 15 live
  `observation` messages in a 25 s window plus the per-field topics; its log
  (163 lines) contains zero publish-failure lines
  (`health.publish_errors` = 0 in `live_mcp_e2e.json`).

## Item 2 — SCFT reader/writer hardening and staged upload

* One parser (`scft.c`) for the daemon, `classifier -V FILE` and the host
  tool `scft_check`; the Python writer/reader (`templates_format.py`)
  validates the same rules and emits the same status names.
* **native:** v1/v2/v3 round trips both directions; the stock bundle parses
  identically on both sides; 28 malformed mutations (bad magic, bad version,
  count 0 / 4097, canonical_n 1 / 2^20+1, short and long files, trailing
  byte, missing NUL, empty label, control byte, overlong UTF-8, surrogate,
  duplicate id, NaN/Inf samples, un-normalized vectors, NaN range,
  non-finite hz_per_bin, ...) fail with the same status in C and Python;
  4 MiB size cap honoured; a hand-built v1 file loads.
* **cross + qemu:** `classifier -V` returns exit 0 with the JSON summary on
  the stock bundle and exit 2 with `bad_size` on a truncated copy.
* **board:** the staged-upload sequence from the new `api_controller.sh`
  handler was executed by hand in a temporary directory on the same JFFS2
  filesystem using the staged test binary: valid stage → `-V` ok →
  `mv -f` over the copy; truncated stage → exit 2, copy untouched, stage
  removed. The *production* `api_controller.sh` on the sensor was **not**
  replaced; the new handler is syntax-checked (`bash -n`) and its logic
  mirrors the manual run. Working templates md5 before/after:
  `cbfce1ab9c90c4889a8981fc154a0412`.

## Item 3 — waterfall unit/grid contract

* Verified from the pinned producer source (maia-sdr `2637b598`,
  `spectrometer.rs`) and from three raw frames recorded from the sensor
  (`tools/tezuka_mcp/tests/fixtures/wf_avg4096_frame{0,1,2}.bin` + the `/api`
  document at capture time): linear integrated power, float 0 replaced by the
  step word (values 0, 1, 2 across the three frames), DC spur at usable index
  2047, grid `span / fft_size`.
* C daemon (`live_hz`, DC mask), Python tools (`waterfall_frame.py`,
  `template_from_capture.py`, `capture_from_device.py`, `inject_emitter.py`),
  Dashboard (`wfBinToHz` family in `data.jsx`, used by `classifier.jsx` and
  `radioastro.jsx`) and MCP (`frame.py`) now share this grid.
  `pages1.jsx` `spDrawCursor` was deliberately left (sweep-stitched buffer).
* **native:** `test_features.py` proves C and Python feature extraction agree
  on the recorded frame (peak_bin 2047) and on known-ratio synthetic frames;
  `test_frame.py` checks the decode on the fixtures.
* **board:** live frames at 433 MHz / 2.5 MHz span placed the DC spur at
  `bin 2047`, `433000000.0 Hz` (`live_mcp_e2e.json`), matching the fixture.
* Dashboard changes are syntax-checked with esbuild only; they were **not**
  exercised in a browser against the board.
* Migration: v3 templates are resampled at run time; the 0.024 % width change
  needs no regeneration (details in `docs/classifier_telemetry.md`).

## Item 4 — the `observation` message

* Schema `tezuka.classifier.observation/1`, ~3 KB, 6 KiB bound, real JSON
  encoder, explicit nulls with reasons, `config.bound = false` with reason,
  history reset on RX changes.
* **replay (qemu):** `observation_sample.json` produced from the three fixture
  frames parses as JSON.
* **board:** 15 live observations in 25 s from the test instance; seq
  monotonic; `templates.sha256` `cb6f4580...`; `feature_semantics`
  `power-v1`; the test instance classified the 433 MHz spur as
  `FM Broadcast` (score ≈ 0.65) while the production daemon on the same
  frames reports unknown, which is itself a useful finding about the stock
  template set and is not hidden here.

## Item 5 — MCP tools

* `tools/tezuka_mcp` with `get_sensor_snapshot` and `snapshot_fpga_waterfall`,
  both `readOnlyHint: true`, no command tool, loopback defaults, env-var
  endpoints, optional health file, stderr diagnostics.
* **native:** 24 tests including a real stdio handshake with the installed
  SDK 2.2.0 (tool listing and annotations), freshness rules (retained replay
  never counts as fresh, repeated seq ignored), coherence/support checks,
  collection against a local fake sensor.
* **board (`live_mcp_e2e.json`):** server started over stdio by the SDK
  client against the sensor; `initialize` + `tools/list` succeeded;
  `get_sensor_snapshot` returned the classifier section `fresh` (seq 116,
  age 1.0 s, config_bound false) and `rx/frequency` 433000000 `fresh`;
  `snapshot_fpga_waterfall(max_frames=4, timeout_s=20)` returned `ok` with
  4 frames, step words [1,2,3,4] recorded (sweep off), noise floor 31.58 dB,
  peak +26.73 dB over floor at bin 2047, one segment bins 2029–2065.
  Elapsed 0.04 s and 5.6 s.
  `live_sensor_snapshots.json` shows the legacy adapter against the
  production prefix (`best_effort: true`, seq/boot_id/sha `null`).
* The first live waterfall attempt was rejected because the deployed
  firmware cycles the step word 0..7 even with sweep off; the coherence rule
  now consults the MQTT sweep state and this behaviour is documented.

## Item 6 — live test protocol and restoration

Pre-test (`pre_test_namespace.json`): `test/classifier/#` empty; production
`state/classifier/result` live; no competing daemon in the test namespace.

Test: binary staged as `/tmp/classifier.mcp-test-20261108T1900`, run with
`-f /mnt/jffs2/classifier/templates.bin -T test/classifier/ -v` alongside
production pid 1292; nothing in `/usr/bin`, `/etc` or the working templates
was touched. Upload test used a temporary directory on JFFS2 only.

Restoration (verified on the board after the run):

| Check | Result |
|---|---|
| test pid 28163 | killed; `ps` shows only pid 1292 `/usr/bin/classifier -f /mnt/jffs2/classifier/templates.bin` |
| `/tmp/classifier.mcp-test-*` | removed, none listed |
| `/usr/bin/classifier` md5 | `6aaf623c808ba3f3eb6c6e1e6723a6bd` (unchanged) |
| `/mnt/jffs2/classifier/templates.bin` md5 | `cbfce1ab9c90c4889a8981fc154a0412` (unchanged), `templates.v1.bin` present |
| temp upload dir | absent |
| retained `test/classifier/#` | 17 topics cleared, 0 remain (`cleanup_retained.json`) |
| production stream | `state/classifier/result` 4 live messages in 6 s after cleanup (`post_test_namespace.json`) |

## Completion table

| Item | Status |
|---|---|
| 1 libmosquitto publishing, label contract, failure counters | implemented and tested (native, board) |
| 2 SCFT parser/writer hardening, `-V`, cross-language tests | implemented and tested (native, cross, qemu) |
| 2 staged atomic upload in `api_controller.sh` | implemented; flow exercised by hand on the board; script not deployed on the sensor |
| 3 waterfall contract in C / Python / MCP | implemented and tested (native, board) |
| 3 Dashboard grid helpers and provenance row | implemented, syntax-checked; not browser-tested |
| 3 `pages1.jsx` sweep cursor | deferred by design (stitched sweep buffer, separate contract) |
| 4 observation message | implemented and tested (qemu replay, board) |
| 5 `get_sensor_snapshot` | implemented and tested (native, board over stdio) |
| 5 `snapshot_fpga_waterfall` | implemented and tested (native, board over stdio) |
| 6 live test with restoration | done and verified on the board |
| Full Buildroot image build with the new classifier | not done (no SDK rebuild requested; app compiled against the existing signalsdrpro SDK) |
| Deploying the new classifier/api_controller to the sensor | deferred by design: the brief excludes permanent service replacement |

## Later stages (documented, not implemented)

* **Capture arbitration** — a single owner for the receiver's tuning, span
  and IQ capture so the classifier, the Dashboard, sweep and a future MCP
  capture tool cannot retune under each other. Needs a lease topic with
  holder id, expiry and generation, and the `config.bound` tag in frames.
* **GPU forensics** — host-side offline analysis of recorded IQ or waterfall
  runs (cyclostationary features, modulation recognition) fed by a capture
  tool; out of scope until arbitration exists.
* **Direction finding** — requires multi-channel or synchronised sensors and
  calibrated phase; the `boot_id`/`session_id`/clock-source fields in the
  observation are the hooks for correlating sensors later.
* **Template promotion** — turning a repeated unknown detection into a
  candidate template: capture → `template_from_capture.py` → human review →
  `cmd/classifier/templates` upload (now validated and atomic). A review
  queue and provenance per template are not designed yet.
