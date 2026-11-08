# Follow-up to the independent review of 2026-11-08

Reviewed revision: `fb9b86a` (branch `worktree-classifier-mcp-foundation`,
parent `7997d95`). This note records the disposition of findings R1-R6 from
`CLAUDE_TEZUKA_DELIVERY_REVIEW_2026-11-08.md`, the checks run for the
acceptance directives (`CLAUDE_MO_RF_ACCEPTANCE_DIRECTIVES.md`), and what was
not done. No board service, binary, template file, receiver setting or FPGA
image was touched; the only contact with the sensor was a read-only
`/waterfall` subscription and two `/api` GETs for the RF capture described in
`2026-11-08/rf_compare/README.md`.

Evidence layout:

* this file: dispositions, verification methods, environment, commands
* `2026-11-08/rf_compare/`: RF comparison report, frozen fixture manifests,
  machine-readable results, the recorded 433 MHz capture and one full run output
* `2026-11-08/fpga/`: FPGA resource appendix for build 80fc4042

## 1. Dispositions

| Finding | Disposition | Change | Verification method (native unless stated) |
|---|---|---|---|
| R1 (P1) Classifier page throws on render (`obs`, `tstat` undeclared) | fixed | `Dashboard/classifier.jsx`: both are bound from `d.classifierObservation` / `d.classifierTemplatesStatus` (object or null) next to the result and background bindings. | esbuild transpile + `react-dom/server` 19.2.3 render with `data.jsx` primitives stubbed: pinned commit throws `ReferenceError: obs is not defined`; fixed renders with no fields, with a populated observation and upload verdict, with partial objects and with non-object garbage. **Browser:** page served from `Dashboard/` in the built-in browser at `#classifier` without a sensor: Signal Classifier and Decision cards render, console has only the two expected 404s (`/bandplan.json` is served by the sensor, `/vendor/signals.bundle.js` is a build product), no script error. |
| R2 (P1) `jw_double` passes snprintf's would-be length to the copier (stack over-read) | fixed | `app/classifier/jsonw.c`: `put_fmt()` rejects any formatted length that is negative or not below the local buffer size, so the copier never sees it; `jw_double` falls back from `%.*f` to a bounded `%.17g` when the fixed form does not fit; `jw_int`/`jw_uint` use the same guard. Every formatter checks the length. | `jsonw_check --double VALUE DECIMALS [CAP]` builds the output in a heap buffer of exactly CAP bytes with a canary byte after it (exit 70 = writer overflow flag, 71 = canary overwritten), under `-fsanitize=address,undefined`. `tests/test_utf8_jsonw.py`: DBL_MAX and -DBL_MAX at 17 decimals, 1e300, 1e40, 1e22, ordinary RF values (frequencies, dB, ratios), 0.001, -42.5, 0; NaN and +-Inf emit `null`; destination buffers one byte too small fail cleanly with exit 70 and just-enough buffers succeed, for several values. The review's harness (`jw_kv_double(DBL_MAX)` into 4096 bytes) reads 309 bytes past the stack buffer on the pinned source and is clean on the fixed source. |
| R3 (P2) Observation schema not validated in `state.py` | fixed | `validate_observation()` checks the schema name and version (`tezuka.classifier.observation/1`), an integer non-bool `seq` >= 0, a producer identity (`sensor.session_id` or `boot_id`), and that every object-typed section is a dict or null before a message may replace the held record; rejections are counted with the last error text in `classifier.validation`; the previous record keeps its own age. | `tools/tezuka_mcp/tests/test_state.py`: malformed JSON, wrong type, wrong schema, wrong version, missing/float/bool/negative seq, missing identity, non-object sections, nested garbage. |
| R4 (P2) Freshness can belong to a different observation | fixed | The observation is one record `(payload, identity, seq, freshness, live_epoch)`. It is replaced only by a newer seq from the same producer session or by any valid message from a different session; a retained replacement is `retained`, never fresh; an older or repeated seq is counted and ignored (`seq_repeats_with_changed_content` is reported separately); receiver `Entry` values drop live freshness when a retained copy carries a different payload. `connection_epoch` increments on every (re)connect and `live_from_current_connection` tells whether the held record was received on the current connection. | `test_state.py`: the two reproduced cases from the review (retained replacement inheriting freshness; seq-only dedup), retained older seq, retained newer seq stays retained, same seq from a new session accepted, out-of-order ignored, repeated seq with changed content, reconnect epoch, receiver entry retained with a different value. |
| R5 (P2) Sweep guard ignores freshness and connection state; no recheck after collection | fixed | `server.py` `Service.sweep_state()` returns `{active, reason, generation, epoch, subscriber_connected, flags}` and is `active` only when the subscriber is connected, both `state/rx/sweep/activate` and `.../engaged` are present, delivered live within `TEZUKA_STATE_MAX_AGE_S`, and are `0` or `1`; anything else is unknown with a reason. `waterfall.py` treats unknown as unsupported before collecting; it re-reads the state after the collection and rejects when the active flag, the number of sweep value changes (`generation`) or the MQTT connection epoch changed. An unchanged periodic republish does not change `generation`, so long collections stay usable. | `tests/test_sweep_state.py` (never connected, disconnected with retained off, connected retained-only, live fresh off, stale, missing, malformed, either flag on, unchanged republish, off-on-off counts +2, reconnect epoch) and `tests/test_waterfall.py` (unknown state rejected before collection, change during collection, sweep turning on, stable live off accepted, off-on-off during a fake-sensor collection rejected, reconnect during collection rejected, unchanged republish during collection accepted). |
| R6 (P2) `publish_observation()` reads MQTT-thread fields without the lock | fixed | `app/classifier/classifier.c`: `struct obs_ctx` (frequency, serial, sampling, rfinput, sweep activate/engaged, config generation) is filled by `obs_ctx_copy_locked()` inside the existing locked region at the top of `handle_frame()`; `publish_observation()` serializes only that copy. `config.bound` stays `false` with its reason. | Static: `tests/test_observation_context_source.py` fails if any `a->` read of a network-thread field reappears in `publish_observation()`, if the copy leaves the locked region, or if the helper stops covering a field. **Dynamic (separate from the static check):** ThreadSanitizer harness `app/classifier/tsan/` compiles the real `classifier.c` with stub `mosquitto.h`/`libwebsockets.h`, replays 300 recorded frames through `handle_frame()` while a second thread calls the real `mqtt_on_message()` with changing serial, frequency, sampling, rfinput and sweep values. Pinned source: 9 ThreadSanitizer data-race reports (`publish_observation` reads at classifier.c lines 1400, 1407, 1410, 1412 and `write_escaped` in jsonw.c:68, all against `mqtt_on_message` writes at classifier.c:800). Fixed source: 0 reports, 300 frames processed. This is dynamic race detection on the daemon's own code with a synthetic MQTT writer, not a run against a sensor. |

## 2. Environment and versions

| Item | Value |
|---|---|
| Source | worktree of branch `worktree-classifier-mcp-foundation`, base commit `fb9b86aef7880bca41cfc5094d3a0d11eef5d0fe`; the published commit is named in the return package and in `git log` |
| Host | Ubuntu 24.04.5, Linux 7.0.0-42-generic, x86_64 (hostname radiomancer) |
| Native C | gcc 13.3.0 (Ubuntu 13.3.0-6ubuntu2~24.04.1); `HOST_CFLAGS = -O2 -Wall -Wextra -Wno-unused-parameter -fsanitize=address,undefined -fno-omit-frame-pointer`; TSan: `-O1 -g -fsanitize=thread -fno-omit-frame-pointer`, run under `setarch x86_64 -R` with `TSAN_OPTIONS=halt_on_error=0` |
| ARM | signalsdrpro Buildroot SDK `output/signalsdrpro/host`: `arm-linux-gcc` 15.1.0 (Buildroot 2021.11-18033-g83947c7bb6 wrapper), sysroot `arm-buildroot-linux-gnueabihf/sysroot`; `qemu-arm` 8.2.2 |
| Python | 3.12.3 in a venv: mcp 2.3.0, paho-mqtt 2.2.0, websockets 17.4, pytest 9.2.0, pytest-asyncio 1.4.1.post1, numpy 2.5.4 |
| Dashboard | esbuild from `Dashboard/node_modules`, React / react-dom 19.2.3; built-in browser of the Claude desktop app for the smoke test |
| FPGA | Vivado 2025.2 (Build 6299465), batch mode, read-only |
| Templates | `classifier-default-templates.bin` sha256 `cb6f4580360c75b485140af34670e1d625942cb03a5dd279c509c7063c36f263` (same bytes as on the sensor) |
| Classifier binaries (ARM) | baseline 7997d95 `29999610181d2e67eb46aaf45068e1a0e33ad336729d09103f4d95c3e8669cee`; pinned fb9b86a `e952dd4ecd3dcb335f1a7733c452fad01ef22483dcab3e57b92de87c217e07da`; fixed `5b1f7f3cd03c02299b9d9711bdaaf0a3d30a6966d8ca6fc6956535e389198ccd` |
| Recorded RF | `2026-11-08/rf_compare/raw/wf433_live.raw` sha256 `db51a261a62de5b5ec821ff92a57d9eba109ed1916400105eb0171628c5f59f4` (60 frames, 433 MHz, gain 70 dB, Average x1023) |

## 3. Checks run and results

| Check | Where | Result |
|---|---|---|
| `tools/tezuka_mcp` suite (`python -m pytest -q` in `tools/tezuka_mcp`) | native | **63 passed** (24 existing incl. the stdio handshake, 39 new for R3/R4/R5) |
| `app/classifier` suite (`make host && python -m pytest -q tests` in `app/classifier`, `TEZUKA_ARM_SYSROOT` set) | native + ARM qemu | **112 passed, 0 skipped**: 82 existing, 27 new R2 cases, 3 R6 source guards; the 2 ARM validate tests ran under qemu |
| ARM build of the daemon (`make classifier` with the SDK) | ARM | builds; `-V` validate mode and replay run under qemu (see RF report) |
| ThreadSanitizer harness (`make tsan && make tsan-run` in `app/classifier`) | native, dynamic | fixed: 0 reports, 300 frames; pinned source via `CLASSIFIER_SRC=<pinned classifier.c>`: 9 reports (details in R6 above) |
| R2 review harness (`jw_kv_double(DBL_MAX)` into 4096 bytes, ASan) | native | pinned: stack-buffer-overflow READ of size 309; fixed: clean, emits `1.7976931348623157e+308` |
| Dashboard render (react-dom/server) and browser smoke | native + browser | see R1 |
| RF comparison, 9 generated fixtures x 3 revisions (27 qemu runs) + live capture x 3 revisions x 2 rates (6 runs) | ARM qemu | all 33 runs exit 0; identical decisions and scores across revisions; the live 433 MHz spur is accepted as FM Broadcast 52/52 by every revision; positives 38/38; details and verdict in `2026-11-08/rf_compare/README.md` |
| FPGA utilization and timing, routed checkpoint of signalsdrpro build 80fc4042 | Vivado, read-only | LUT 54.94 %, FF 33.53 %, BRAM 41.07 %, DSP 58.18 %; setup met; hold met in the build's final post-route report; `2026-11-08/fpga/README.md` |

## 4. Commands

```bash
# MCP suite
cd tools/tezuka_mcp && python -m pytest -q

# native classifier suite, ARM tests included
cd app/classifier && make host && \
  TEZUKA_ARM_SYSROOT=$SDK/arm-buildroot-linux-gnueabihf/sysroot python -m pytest -q tests

# ThreadSanitizer harness (fixed source), and the pinned source for comparison
cd app/classifier && make tsan && make tsan-run
cd app/classifier && make tsan CLASSIFIER_SRC=/path/to/fb9b86a/app/classifier/classifier.c && make tsan-run

# R2 small-buffer check (exit 70 expected)
app/classifier/build/host/jsonw_check --double 1.7976931348623157e308 17 300

# ARM daemon
cd app/classifier && PATH=$SDK/bin:$PATH make classifier

# RF comparison: see docs/evidence/2026-11-08/rf_compare/README.md section 7
# FPGA reports: vivado -mode batch -source docs/evidence/2026-11-08/fpga/util.tcl -tclargs <routed.dcp> <outdir>
```

## 5. Not done, and the one next step for each

* **RF acceptance gate: open.** The fixed revision still accepts the 433 MHz
  DC spur as FM Broadcast (52/52 on recorded data), as do the baseline and the
  reviewed revision; the explaining condition is the centre-dominated stock FM
  template meeting the spur skirt that survives the DC+-2 mask (RF report
  section 5). Next step: capture longer sequences at the sensor's usual LO
  settings plus one real broadcast emission as a trusted positive, then
  evaluate any template, gate or mask change on those, not on the generated
  fixtures.
* **Production mask degeneration** (`masked 3563/4095` after retunes) is
  explained from the code path and the recorded summaries, not reproduced
  offline. Next step: let replay mode take a scripted frequency-change
  sequence so the two-frequency spur criterion can be exercised.
* **FPGA to running image link** not established; figures apply to build
  80fc4042 only. Next step: read the device's firmware build manifest (or
  bitstream identity) over SSH and compare with `be06cf18...`.
* **Nothing deployed**: the production classifier, its templates and the
  upload handler on the sensor are as the review found them; no merge into
  main; no reboot or reflash; no FPGA replacement; no transmission.
