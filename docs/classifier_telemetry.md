# Classifier telemetry, units and template contract

This document is the single statement of what the on-board classifier
(`app/classifier`) consumes, what it publishes, and what the host-side
MCP tools (`tools/tezuka_mcp`) report. Everything here was verified
against the producer source at the maia-sdr commit pinned in
`package/maia-httpd/maia-httpd.mk` (`2637b59891bdfe38d8c5bf52c84984b3da68064e`)
and against frames recorded from a SignalSDR Pro
(`tools/tezuka_mcp/tests/fixtures/wf_avg4096_*`).

## 1. The `/waterfall` frame

Producer: maia-httpd, `maia-httpd/src/spectrometer.rs`
(`buffer_u64fp_to_f32`) fed by the HDL `SpectrumIntegrator`.

| Property | Value | Evidence |
|---|---|---|
| Transport | one binary WebSocket message per integration | websocket.rs |
| Layout | `float32[fft_size]`, little-endian, fftshifted (index 0 = -fs/2 edge, DC at `fft_size/2`) | spectrometer.rs; fixture argmax at float index 2048 for 4096 |
| Value | **linear power**: integrated \|X\|² × 4e6/(nint·fs) in `Average` mode, peak \|X\|² × 4e6/fs in `PeakDetect` mode | spectrometer.rs `scale` |
| Level reference | the producer's own unit scale; **not dBm**, no calibration. dB figures are `10*log10(power)` | — |
| Float 0 | **replaced** by the fastlock profile / sweep step word (`fastlock_profile as f32`). Nothing is prepended; the -fs/2 edge bin is lost | spectrometer.rs; fixture `f[0]` = 0, 1, 2 on consecutive frames |
| Usable bins | `f[1..]`, `fft_size - 1` of them | — |
| Grid | `hz_per_bin = span / fft_size` (NOT `span / (fft_size - 1)`) | FFT has `fft_size` bins over the span |
| Bin frequency | usable index `j` is centred on `f_c + (j + 1 - fft_size/2) * span / fft_size`; DC at `j = fft_size/2 - 1` (2047 for 4096) | fixture DC spur at `j = 2047` |
| Step word | free-runs 0..7 on every frame on the deployed firmware even with sweep off (observed live and in the fixture); it only identifies a sweep step when `state/rx/sweep/engaged` is 1 | live capture 2026-11-08 |

Rates and spans are distinct quantities and are named as such:

* **ADC rate** (`state/rx/sampling`, `/api ad9361.sampling_frequency`): the AD9361 output rate.
* **DDC rate** (`/api ddc.output_sampling_frequency`): the span when the spectrometer input is the DDC. Not interpreted by the classifier or the MCP tools (reported as unsupported).
* **Span** (`state/rx/span`): the analysis span of one frame; equals the ADC rate when the spectrometer input is the ADC and sweep is off.
* **Sweep step**: in sweep mode each frame is one step; neither the classifier nor the MCP tools average across steps.

Averaging is always done in the power domain across frames, then
converted to dB; never across bins or steps.

The Dashboard (`Dashboard/data.jsx` `wfBinToHz` etc.), the C daemon
(`live_hz = span / n_floats`, DC mask at `(n+1)/2 - 1`), the template tools
(`tools/classifier_templates/waterfall_frame.py`) and the MCP server
(`tezuka_mcp/frame.py`) all implement this grid. Earlier revisions used
`span / 4095` and a DC index of `4095/2`, half a bin off at the edges; the
Spectrum page's cursor in `pages1.jsx` still uses the pixel-fraction
formula because it also serves the stitched sweep buffer, and is out of
scope here.

### Migration of stored templates

Version-3 templates record `hz_per_bin` and are resampled onto the live
grid at run time, so the grid correction changes their live width by one
part in 4096 (0.024 %), far below the 8-bin minimum template width: no
stored template needs regenerating. Templates produced by the old
`template_from_capture.py` were cut around a centre index off by one bin
and a width off by 0.024 %; recutting them is optional. Version 1/2
templates are matched bin-for-bin and are unaffected.

## 2. Feature semantics (`power-v1`)

`app/classifier/features.c` and `tools/classifier_templates/features.py`
compute the same four numbers from a frame's linear-power residual (live
minus the learned receiver baseline, spur bins masked). The host test
`app/classifier/tests/test_features.py` proves they agree on a recorded
frame and on synthetic frames with known ratios.

| Feature | Definition | Unit / scope |
|---|---|---|
| `bandwidth_frac` | width of the run of bins ≥ peak/10 around the global peak | fraction of `bins_used`; `bandwidth_hz = bandwidth_frac * bins_used * hz_per_bin` |
| `spectral_papr_db` (`papr_db`) | `10*log10(peak bin power / mean bin power)` | dB; a **spectral** peak-to-mean ratio of this frame, **not** time-domain IQ PAPR |
| `flatness` | geometric mean / arithmetic mean of bin power | 0..1 |
| `peak_count` | strict local maxima ≥ peak × 10^(-1.5), deduplicated within `n/64` bins | count; a heuristic, not a prominence-based detector |
| `drift_bins_per_s` | slope of the peak position over the last ≤16 cycles | bins/s; null until ≥4 samples spanning ≥3 s |
| `occupancy_pct` | share of the last ≤16 cycles whose residual `papr_db` ≥ 14 dB | %; a **sampled heuristic** at ~1 cycle/s, not a measured duty cycle |

The SNR and prominence **gates describe the matched window of the frame**
(smoothed-peak power over the baseline inside the window; window peak over
the mean level of the flanks), not properties of the emitter.

The string `FEATURE_SEMANTICS` (`power-v1`) is published in every
observation; it must be bumped if any of the above changes.

## 3. SCFT template file (`app/classifier/scft.h`)

Little-endian; versions 1, 2, 3 as documented in `scft.h` and
`tools/classifier_templates/templates_format.py`. One parser (`scft.c`)
serves the daemon, `classifier -V FILE` and the host tests. Validation
order: size cap (4 MiB) → magic → version → `count` in [1, 4096] and
`canonical_n` in [2, 2²⁰] → exact file size computed in 64 bits (trailing
bytes rejected) → per entry: label contract, duplicate id, finite
samples, `|mean| ≤ 1e-3` and `|L2 − 1| ≤ 1e-3`, finite ranges (v2+;
`hi < lo` still means unbounded), `hz_per_bin` finite in [0, 1e9] (v3).

**Label contract.** The bytes before the first NUL (which must occur
within the 32 bytes) are non-empty, valid UTF-8 (RFC 3629: no overlongs,
surrogates or code points above U+10FFFF) and contain no C0 control byte
or DEL. Everything else — spaces, `+`, `/`, quotes, backslashes, accents,
emoji — is legal and travels verbatim; JSON payloads escape it through
`jsonw.c`. The Python writer truncates to 31 bytes on a character
boundary and refuses control characters. Status names are identical on
both sides: `ok, io, bad_magic, unsupported_version, bad_header, bad_size,
bad_label, duplicate_id, non_finite_data, not_normalized, bad_range,
bad_resolution, out_of_memory`.

**Upload path** (`cmd/classifier/templates`, `api_controller.sh`): payload
bounded (base64 of ≤ 4 MiB), decoded to `/mnt/jffs2/classifier/.templates.bin.staged.$$`
(same filesystem as the working file), validated with `classifier -V`,
`mv -f` over the working file only on success, staging file removed on
failure, verdict published retained on `state/classifier/templates_status`.

## 4. MQTT topics published by the daemon

Default prefix `state/classifier/` (`-T PREFIX` for a test instance; a
non-default prefix also moves the command topic to `PREFIX cmd/background`
and derives the client id `tezuka_classifier:<prefix>`). All messages are
QoS 0, retained. Publish failures are counted (`health.publish_errors`)
and logged with bounded frequency.

| Topic | Payload |
|---|---|
| `observation` | one JSON object per classify cycle (below) |
| `result` | legacy per-cycle JSON (kept for the Dashboard) |
| `background` | `{frequency, bins, fft_size, hz_per_bin, hot, masked}` after each learn |
| `template_shape`, `window` | normalized shapes for the Dashboard's overlay |
| `label`, `confidence`, `freq_offset`, `bandwidth_bins`, `frame_bins`, `template_id`, `feature_*` | legacy per-field topics |

### `observation` — schema `tezuka.classifier.observation/1`

Bounded (6 KiB buffer; a document that would not fit is not published and
counted as a publish error). Unknown values are `null` with a sibling
`*_reason`. Fields:

* `schema`, `seq` (cycles since daemon start, 1-based).
* `time`: `unix_s` (CLOCK_REALTIME), `mono_s`, `source: "CLOCK_REALTIME"`,
  `disciplined: null` (the daemon cannot see chrony; check `chronyc tracking`
  on the sensor), `replay` (true in `-r` mode).
* `sensor`: `hostname`, `serial` (from `state/main/serial`), `boot_id`
  (`/proc/sys/kernel/random/boot_id`), `session_id` (`boot_id[:8]-pid-start`), `pid`.
* `input`: `source`, `units: "linear_power_integrated"`, `units_note`,
  `fft_size`, `bins_used`, `bin0_offset: 1`, `first_float` (the step word),
  `dc_index`, `hz_per_bin` (null when the span is unknown).
* `config`: **`bound: false`** with `bound_reason` — RX settings come from
  retained `state/rx/*` topics polled every 2 s by `api_controller.sh`; the
  frame carries no tag binding it to them, so a frame near a retune may
  describe the previous settings. `generation` increments on every observed
  change of a reset-worthy RX topic. `frequency_hz`, `span_hz`, `sampling_hz`,
  `rfinput`, `sweep{activate, engaged}`.
* `templates`: `path`, `status` (loader status name), `sha256` of the file,
  `version`, `count`, `canonical_n`, `feature_semantics`.
* `decision`: `state` (`learning`/`idle`/`ok`), `classified`, `label`,
  `accepted`, `template_id`, `score`, `snr_db`, `prom_db`, `bin_offset`,
  `bandwidth_bins`, `gate_factor`, `threshold`, `hold_margin`, `margin`,
  `best{template_id, score, snr_db, prom_db}` (best gated candidate even when
  rejected), `hysteresis{locked, locked_template_id, held, took_over}`,
  `gates{snr_min_db, prom_min_db, scope}`.
* `features`: the four frame features with `valid`, `bandwidth_hz`, `scope`
  and `units` text; nulls with `invalid_reason` when the residual is empty.
* `temporal`: `history_samples`, `drift_bins_per_s`, `occupancy_pct`, each
  null with a reason until the history is long enough; `occupancy_note`.
* `background`: `learned`, `age_s`, `frames`, plus the mask coverage:
  `masked_bins` (bins currently masked, DC and spurs; 0 while relearning),
  `valid_bins` (`bins_used - masked_bins`, what the decision could look at),
  `spur_bins` (bins flagged by the two-frequency spur rule; they persist
  across retunes), `hot_last_learn` (hot bins at the end of the last learn,
  0 before the first), `learn_count` (learns since daemon start). The spur
  map is kept across retunes for as long as the frame size is unchanged (a
  change of `fft_size` discards it), so `masked_bins` can only grow across
  retunes at one frame size; `-r FILE -E EVENTS` replays that offline.
* `health`: `publish_errors`, `publish_ok`, `waterfall_connected`.

History (drift/occupancy) and the learned background are reset whenever any
of `state/rx/{frequency, sampling, span, bandwidth, gain, gain_mode, rfinput,
sweep/activate, sweep/engaged}` changes, or the frame size changes.

## 5. MCP tools (`tools/tezuka_mcp`)

Both tools are annotated `readOnlyHint: true`, `destructiveHint: false`.
There is no command/publish tool. Endpoints come from `TEZUKA_MQTT_HOST`,
`TEZUKA_HTTP_HOST` and friends (loopback by default).

### `get_sensor_snapshot` → `tezuka.mcp.sensor_snapshot/1`

Cached state from a persistent `state/#` subscription. Each receiver and
sensor field carries `{value, status, age_s, retained_replay_seen,
live_messages}` where `status` is `fresh` (a non-retained message within
`TEZUKA_STATE_MAX_AGE_S`), `stale`, `retained` (only the broker's replayed
copy is held for this value — the producer may be down) or `unavailable`.
Freshness belongs to the payload it arrived with: a retained copy that
carries a *different* payload than the last live message replaces the
value and the entry drops back to `retained`; a replayed copy of the same
payload keeps the live evidence. `provenance.connection_epoch` counts
successful (re)connects, each of which replays the retained tree.

The `classifier` section is the accepted `observation`, held as one record
`(payload, producer identity, seq, freshness)`:

* A message is accepted only if it validates: `schema`
  `tezuka.classifier.observation/1`, an integer `seq ≥ 0`, a `sensor`
  object with `session_id` (or `boot_id`) as the producer identity, and
  every section (`time`, `sensor`, `input`, `config`, `templates`,
  `decision`, `features`, `temporal`, `background`, `health`) an object or
  null. Anything else is counted in `validation.rejected_messages` with
  `validation.last_rejection` and otherwise ignored: the previous record
  keeps its own age, and no invalid or empty message is ever shown as
  fresh.
* Within one producer session a repeated `seq` (`seq_repeats_ignored`,
  and `seq_repeats_with_changed_content` if the payload differs) or an
  older `seq` (`out_of_order_ignored`) does not replace the record.
* A message from a *different* producer session (`producer_sessions_seen`)
  is accepted even if its `seq` repeats the previous session's number: a
  restarted daemon starts at 1 again.
* A retained message that does replace the record is `retained`
  (`age_s: null`); it never inherits the previous payload's live
  freshness. `live_from_current_connection` says whether the live message
  came in on the current connection epoch.

The section's `status` follows the same rule with
`TEZUKA_OBSERVATION_MAX_AGE_S`, and the section is `best_effort: true` with
explicit nulls when the sensor only publishes the legacy `result`.
Nothing is defaulted: an unseen frequency is `null`.

`classifier.frequency_view` keeps two frequencies apart that are easy to
confuse. Both are derived on the host from the observation's own grid
(`config.frequency_hz`, `config.span_hz`, `input.fft_size`; usable bin `j`
is centred at `center + (j + 1 - fft_size/2) * span / fft_size`) and
neither is a scoring input:

* `matched_window` — the frequency range of the template window the daemon
  scored and **accepted**: `start_hz`, `center_hz`, `end_hz`, `width_hz`
  from `decision.bin_offset` / `bandwidth_bins` (the window covers usable
  bins `[offset, offset + bw)`, edges half a bin outside the first and last
  bin centres). `available: false` with a `reason` when there is no
  accepted match this cycle (the daemon does not report a window for a
  rejected candidate) or the grid is not derivable.
* `whole_span_peak` — the centre frequency of the strongest residual bin
  (spectrum over the learned background) over the **entire span** (`features.peak_bin`), independent of any match;
  `available: false` with the daemon's `invalid_reason` when the features
  are invalid.
* `peak_inside_matched_window` — `true`/`false` when both are available,
  else `null`. `false` means the strongest residual bin lies outside the
  window: either the window matched an emission other than the one with the
  strongest residual, or the residual's strongest bin is not the strongest
  emission at all (for example something that changed since the background
  was learned). It does not by itself mean the window is wrong; the
  2026-11-09 positive check saw both cases with the window correctly on
  the station.

The console's Decision panel shows the same two values under their own
labels (blue bracket = matched window, amber dashed marker = whole-span
residual peak on the spectrum).

### `snapshot_fpga_waterfall` → `tezuka.mcp.waterfall_snapshot/1`

Collects ≤ `max_frames` (≤ `TEZUKA_WATERFALL_MAX_FRAMES`) frames within
`timeout_s` (≤ `TEZUKA_WATERFALL_MAX_TIMEOUT_S`). Reads `/api` before and
after; rejects the collection if the spectrometer/receiver context changed,
if FFT sizes differ, or if the step word varied while sweep is engaged.

The sweep state is evidence, not a cached flag: `sweep_active` is known
(`true`/`false`) only while the MQTT subscriber is connected and *both*
`state/rx/sweep/activate` and `state/rx/sweep/engaged` were delivered live
within `TEZUKA_STATE_MAX_AGE_S` (api_controller.sh republishes them every
2 s) with a value of `0` or `1`, *by the current MQTT connection*. After a
reconnect the broker replays the stored flags whether or not the producer
still runs; the cache keeps the earlier live provenance for an unchanged
payload, so each flag in `context.sweep_state.*.flags` carries
`live_epoch` and `from_current_connection`, and a flag whose live
evidence is from a previous connection epoch makes the state unknown
(reason: "live evidence is from a previous connection (epoch N vs current
M)") until that flag arrives live again. Disconnected, only-retained,
stale, missing or malformed flags make it unknown too, with the reason in
`context.sweep_state.before.reason`, and an unknown sweep state is
*unsupported*: an old "off" is not evidence that the frames are one
ADC-centred span, and the free-running step word cannot tell. The state
is read again after the collection (`context.sweep_state.after`); if the
flag, the sweep topics' value-change `generation` or the connection
`epoch` changed in between, the collection is rejected as straddling a
mode change. With sweep known off, the free-running 0..7 step word is
recorded and does not split the context.

Reports `support.supported: false` with reasons for DDC input, DDC
enabled, sweep active, sweep state unknown or an unknown mode, and
produces no summary in that case. Output: `context` from `/api`, `summary{grid,
frames_averaged, step_words_seen, noise_floor{db, estimator}, peak,
psd_summary (128 buckets, max and mean dB), segments{threshold_over_floor_db,
items[start/end bin and Hz, peak, width], truncated}, level_unit,
averaging}` and `provenance`. The noise floor is the median of
frame-averaged bin levels in dB excluding the DC bin and its neighbours; a
segment still open at the last usable bin is closed there and reported.

## 6. Limits and non-goals

* The daemon does not interpret sweep or DDC frames; it labels what the
  ADC-input spectrometer shows at one span.
* Levels are never dBm. No absolute calibration exists in this path.
* `config.bound` is false by construction until the producer tags frames
  with a configuration generation.
* Later stages (capture arbitration, GPU forensics, direction finding,
  template promotion) are documented as future work in
  `docs/evidence/2026-11-08-classifier-mcp-first-delivery.md` and are not
  registered as tools.
