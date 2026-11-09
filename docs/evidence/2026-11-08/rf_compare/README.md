# RF comparison: baseline 7997d95 vs reviewed fb9b86a vs fixed revision

Offline, read-only comparison requested by `CLAUDE_MO_RF_ACCEPTANCE_DIRECTIVES.md`
(task 2: explain the RF discrepancy before tuning; task 3: freeze an evaluation
set; task 4: per-fixture results for three revisions). Nothing was transmitted,
no sensor setting was changed, no service, template or FPGA image on the sensor
was touched. The sensor interactions were read-only: two `/waterfall`
websocket subscriptions with `/api` GETs before and after each (the 433 MHz
capture, then a second capture after the sensor's owner rebooted it and it
came up at its default 2.4 GHz settings), and the read-only SSH inspection
described in `../fpga/README.md`. The second capture and the scripted-retune
replays were added for the follow-up review of 8d06537 (its actions 3 and 4).

## 1. What is RF evidence here and what is not

| Input | Kind | Why |
|---|---|---|
| `raw/wf433_live.raw` (60 frames, 433 MHz, as captured) | **recorded RF** | the sensor's own spectrometer output, unmodified, sequence order kept |
| `raw/ism2400_live.raw.xz` (360 frames, 2.4 GHz, 30.72 MHz span, as captured) | **recorded RF** | same source, a different band and span; the band shows Wi-Fi-like activity (wide intermittent bursts), which is in no template, and at least one persistent narrow spike; what was on air was not identified independently, so no label from this capture is confirmed either way (5.5) |
| `spur433_live_retune` (the 433 MHz frames with scripted `state/rx/frequency` events) | recorded RF frames, **scripted retunes** | the frames are real; the retunes are replay events, so the receiver did not actually move: the fixture exercises the mask mechanism on real spur frames, it does not show what the receiver would have seen at 438 or 443 MHz |
| `spur_retune_sequence` (cycled 1.09 GHz frames with scripted retunes) | cycled recorded frames, scripted retunes | same construction on the cycled frames |
| `tools/tezuka_mcp/tests/fixtures/wf_avg4096_frame{0,1,2}.bin` (3 frames, 1.09 GHz) | recorded RF frames, but **cycled** into sequences | only three distinct frames exist; cycling them is a software construction (see 5.3 for the effect) |
| `fm_*`, `dab_*`, `cw_*`, `unknown_block_1mhz`, `overlap_fm_cw`, `quiet_no_spur` | **software fixtures** | emitters shaped like the templates (or like nothing in the set) are injected into the cycled frames; they are positive and negative controls for the pipeline, not evidence of performance on independent RF |

No independent trusted positive (a real FM, DAB+ or CW emission recorded by
this receiver) was available; none was fabricated. Nothing was tuned on these
fixtures: no threshold, template, gate or mask was changed in any revision.

Accepted labels that are not expected are reported with a **disposition**
(`rf_compare.py --accept`, recorded in `fixtures.json`): *confirmed false*
(known to be wrong: a software fixture's emitter is synthetic and known, so any
other label is wrong by construction; for a recorded capture only on evidence
independent of the spectrum shape), *suspected false* (shape and allocation
point to a wrong label, protocol ground truth pending) or *unverified*
(nothing is known about what was on air). `results.md` shows the three counts
separately; `false_accepts` in `results.json` counts confirmed ones only. The
first-pass files (`results_live433.*`) predate this and count every unexpected
accept in one column.

## 2. Frozen inputs

| Item | Value |
|---|---|
| Template bundle | `board/tezuka/common/overlay_tezuka/etc/classifier-default-templates.bin`, sha256 `cb6f4580360c75b485140af34670e1d625942cb03a5dd279c509c7063c36f263`, version 3, canonical_n 512, labels FM Broadcast (id 1, 781.25 Hz/bin, 400 kHz), DAB+ (id 2, 4882.8 Hz/bin, 2.5 MHz), CW / Beacon (id 3, 58.6 Hz/bin, 30 kHz), no feature ranges. Same bytes the sensor runs (`templates.sha256` in `live_mcp_e2e.json`). |
| Recorded 1.09 GHz frames | `wf_avg4096_frame0.bin` `1b26b04cab3406beb5f2f50f87bc3804a4d05cb46230f9c3c088f680a1c4ec4d`, `frame1` `5acadf69568e60cd611e09b1a2bb2c59c44a2d87c58307350feff3435d2b8835`, `frame2` `602c0489845d5b4d698337197c3e2c4c155af318c79cb0b0cb4dfad09419021c` (SignalSDR Pro, 2026-11-08T18:01Z, 1.09 GHz, 2.5 MHz, Average x1023, gain 30 dB Manual) |
| Live 433 MHz capture | `raw/wf433_live.raw`, sha256 `db51a261a62de5b5ec821ff92a57d9eba109ed1916400105eb0171628c5f59f4`, 60 frames of 16384 bytes, captured 2026-11-08 (unix 1794170740.7) from 192.168.50.25 with `tools/classifier_templates/replay/capture_waterfall.py`; receiver before and after: rx_lo 433000000 Hz, fs 2500000 Hz, rx_rf_bandwidth 2500000 Hz, gain 70.0 dB Manual, spectrometer Average x1023, 4096-point, 0.5966 frames/s; firmware `tezuka-v0.3.24-34-g6e3a`, maia-httpd `v0.3.24-21-g49403b9`, maia-hdl 0.6.1 (`raw/wf433_live.raw.meta.json`) |
| Live 2.4 GHz capture | `raw/ism2400_live.raw.xz` (xz -9 of the raw file; raw sha256 `85acf590914f0d1e06d33b10e813958f992147e03505537c532c586152929376`, 360 frames of 16384 bytes; `.xz` sha256 `0d7343061a4049e856f956437596163a8e330e62facec0bd129952e6b3a66c04`), captured 2026-11-08 (unix 1794172478.1) from 192.168.50.25 with the same tool after the owner's reboot; receiver before and after: rx_lo 2400000000 Hz, fs 30720000 Hz, rx_rf_bandwidth 18000000 Hz, gain 69.0 then 71.0 dB SlowAttack (AGC), spectrometer Average x1023, 4096-point, 7.3314 frames/s, so the 360 frames are 49.1 s of air time, not the 10 minutes the frame count was sized for at 0.6 frames/s (`raw/ism2400_live.raw.meta.json`). These are the sensor's power-on defaults, not a setting it is normally operated at. |
| Replay events | `raw/spur433_retune.events` sha256 `28128de0cb146f5fafa83122e334bdd64a6990d04f26b4f37a895e31b02bbbb8` (hand-written: 433 MHz and 2.5 MHz span at 0 s, 438 MHz at 30 s, 443 MHz at 60 s); `spur_retune_sequence.events` is generated by `rf_compare.py` and recorded in `fixtures_events.json` (sha256 `b86e7ed5b150680188f7390875c4921c2fb57a60de960717fa6e109a54f37080`: 1.09 GHz at 0 s, 1.095 GHz at 60 s, 1.1 GHz at 120 s, 1.09 GHz at 180 s). Events are delivered to the daemon's real `mqtt_on_message()` on the replay clock (`-E`), so they take the same code path as a sensor's MQTT retune. |
| Frame layout and grid | float32 LE x 4096 per frame; float 0 is the step word; usable bin j is float j+1; linear integrated power (not dBm); 610.3515625 Hz per bin at 2.5 MHz span, 7500 Hz per bin at 30.72 MHz; DC at usable index 2047 |
| Daemon arguments (identical for every revision) | `-n 4096 -S 2500000 -B none -c 0.6 -m 0.15 -i 1.0 -s 6 -g 4 -l 10 -a 300 -w 4e6 -x 8` plus `-F <frame rate>`: 7.5 for the generated fixtures, 0.5966 (the capture rate) for `spur433_live_realrate` and `spur433_live_retune`, 7.5 for `spur433_live_7p5fps`; for `ism2400_live_realrate` the span is the capture's, `-S 30720000`, and `-F 7.3314`; fixtures with events add `-E <events file>`. Threshold 0.6, hold margin 0.15, SNR gate 6 dB, prominence gate 4 dB, learning 10 s, background tau 300 s, baseline width 4 MHz, spur mask DC+-2 and bins hot at 2 or more frequencies. |
| Revisions | `baseline_7997d95` = parent commit 7997d959b43c8ea250cde85a935d808cd938fb5f, binary sha256 `29999610181d2e67eb46aaf45068e1a0e33ad336729d09103f4d95c3e8669cee`; `pinned_fb9b86a` = reviewed commit fb9b86aef7880bca41cfc5094d3a0d11eef5d0fe, `e952dd4ecd3dcb335f1a7733c452fad01ef22483dcab3e57b92de87c217e07da`; `fixed` = this worktree with the R1-R6 changes plus the replay events and coverage counters of the follow-up and the events-parser correction of the closure review, `f87a3c467ea2795d187f7ff9c38a16f120de31c3b001dca68abf8183c88859e8` (earlier passes used `5b1f7f3cd03c02299b9d9711bdaaf0a3d30a6966d8ca6fc6956535e389198ccd` in 8d06537 and `97665cc1be00b6c017078c974320ad2baba59259e65952e6c7c4eeaab451208d` in a9e7115; all three produce the same decisions and scores on every fixture, compare `results_live433.md` with `results.md`). The baseline and reviewed binaries were rebuilt from clean checkouts for this pass and reproduced their hashes exactly. Each built with `make classifier` in `app/classifier` from a clean checkout of that commit, with the signalsdrpro Buildroot SDK (`arm-linux-gcc` 15.1.0, Buildroot 2021.11-18033-g83947c7bb6 toolchain wrapper). |
| Execution | `qemu-arm` 8.2.2 (`qemu-arm-static -L <sysroot>`), replay mode `-r FILE`, no network (`-B none`); virtual clock advances by 1/F per frame, so results are independent of host timing |

## 3. Fixture set (`fixtures.json`, `fixtures_events.json`, `fixtures_live433.json`)

| fixture | kind | frames / s | expected while on / off | basis |
|---|---|---|---|---|
| spur_only_cold | cycled recorded | 450 / 60 | unknown | only the receiver's DC spur is in the band; cold start |
| spur_only_settled | cycled recorded | 1800 / 240 | unknown | as above after 240 s of adaptation (tau 300 s) |
| quiet_no_spur | software | 450 / 60 | unknown | DC+-40 bins replaced by neighbouring-bin samples: nothing on air |
| fm_offcenter | software | 450 / 60 | FM Broadcast from 20 s / unknown before | FM template shape, 400 kHz, at -500 kHz from the LO, 20 dB over local median |
| fm_near_lo | software | 450 / 60 | FM Broadcast / unknown | same emitter centred on the LO over the spur: a legitimate signal at the LO must stay recognisable |
| dab_fullspan | software | 450 / 60 | DAB+ / unknown | DAB+ template shape fills the span, 15 dB |
| cw_offcenter | software | 450 / 60 | CW / Beacon / unknown | 30 kHz carrier-like shape at +580 kHz, 25 dB |
| unknown_block_1mhz | software | 450 / 60 | unknown / unknown | 1 MHz flat-top block: in no template; any label is a false accept |
| overlap_fm_cw | software | 450 / 60 | FM Broadcast or CW / Beacon / unknown | both present; DAB+ would be false |
| spur433_live_realrate | **recorded RF** | 60 / 100.6 | unknown | sensor at 433 MHz with nothing on air except its own DC spur; replayed at the capture rate 0.5966 frames/s |
| spur433_live_7p5fps | recorded RF, wrong rate | 60 / 8 | unknown | same bytes at 7.5 frames/s: the 60 frames end before the 12 s settle, so it yields no post-learning decision and is kept only to document the rate effect (first pass only, `fixtures_live433.json`) |
| ism2400_live_realrate | **recorded RF** | 360 / 49.1 | no template class is expected; dispositions: DAB+ suspected false, CW / Beacon unverified (5.5) | sensor at its 2.4 GHz defaults, 30.72 MHz span, AGC; Wi-Fi-like activity and a persistent narrow spike in band, neither identified independently; replayed at the capture rate 7.3314 frames/s |
| spur_retune_sequence | cycled recorded + scripted events | 1800 / 240 | unknown | the 1.09 GHz frames throughout; LO announced at 1.09, 1.095, 1.1 and back to 1.09 GHz at 0, 60, 120 and 180 s (each step larger than the span); records the coverage series |
| spur433_live_retune | **recorded RF** + scripted events | 60 / 100.6 | unknown | the 433 MHz frames throughout; LO announced at 433 MHz (0 s), 438 MHz (30 s), 443 MHz (60 s); the retunes are scripted, the frames do not change |

Decisions are tallied from the `state/classifier/result` lines after a settle
of 12 s (learning 10 s plus one cycle); for fixtures with an emitter, the
window `[t_on, t_off)` is ON and everything else OFF. An unexpected accept is
any label other than unknown that is not in the expected set for that window;
it is counted as a false accept only with the *confirmed false* disposition
(section 1), otherwise as suspected false or unverified.

## 4. Results (three revisions, identical inputs)

Generated fixtures (`results.md`, `results.json`; 27 runs, all exit 0):

| fixture | baseline_7997d95 | pinned_fb9b86a | fixed |
|---|---|---|---|
| spur_only_cold | 0 false accepts / 45 decisions, max best 0.411 | identical | identical |
| spur_only_settled | 0 / 213, max best 0.411 | identical | identical |
| quiet_no_spur | 0 / 45, best 0 | identical | identical |
| fm_offcenter | ON 38/38 FM Broadcast (min 0.948), OFF 0/7, 0 false | identical | identical |
| fm_near_lo | ON 38/38 FM Broadcast (min 0.917), OFF 0/7, 0 false | identical | identical |
| dab_fullspan | ON 38/38 DAB+ (min 0.910), OFF 0/7, 0 false | identical | identical |
| cw_offcenter | ON 38/38 CW / Beacon (min 0.912), OFF 0/7, 0 false | identical | identical |
| unknown_block_1mhz | 0 false / 45 (ON 38 unknown, max best 0.413) | identical | identical |
| overlap_fm_cw | ON 38/38 CW / Beacon, OFF 0/7, 0 false | identical | identical |

Recorded captures (`results.md`, `results.json` for the second pass; the
first pass's `results_live433.md`, `results_live433.json` are kept; 6 + 6 runs,
all exit 0):

| fixture | baseline_7997d95 | pinned_fb9b86a | fixed |
|---|---|---|---|
| spur433_live_realrate | **52 confirmed false accepts / 52 decisions**, all FM Broadcast, confidence 0.631..0.659 (basis: the residual follows the LO, see 5.1) | **52 / 52**, 0.631..0.659 | **52 / 52**, 0.631..0.659 |
| spur433_live_7p5fps (first pass) | 0 decisions after settle (sequence too short at this rate) | same | same |
| ism2400_live_realrate | 34 decisions after settle: **1 DAB+** (0.859, suspected false), **20 CW / Beacon** (0.984..0.990, unverified), 13 unknown; 0 confirmed false | identical | identical |

Scripted retune replays (fixed revision only, since the coverage fields are
new; `results_events.md`, `results_events.json`, `fixtures_events.json`; 2
runs, exit 0). Coverage is `masked_bins / valid_bins` from the observation
messages (the applied mask; during a relearn it reads 0 because no mask is
applied until the background is learned):

| fixture | decisions after settle | mask after each learn (hot bins -> masked) | final coverage |
|---|---|---|---|
| spur_retune_sequence | 0 accepts / 213, all unknown; `best` 0.404..0.411 before the first retune, 0.523..0.578 after the mask grows | 1.09 GHz: 71 -> 5 (DC+-2); 1.095 GHz: 70 -> 70; 1.1 GHz: 70 -> 70; 1.09 GHz: 71 -> 70 | 70 / 4025 masked (1.7 %), 4 learns |
| spur433_live_retune | **10 FM Broadcast** (0.643..0.652, confirmed false: same residual as spur433_live_realrate) counted between 13.4 s and 28.5 s (12 in all from 10.1 s, two of them before the 12 s settle), then **unknown** for the remaining 42 counted decisions from 30.2 s | 433 MHz: 234 -> 5 (DC+-2); 438 MHz: 235 -> 231; 443 MHz: 238 -> 237 | 237 / 3858 masked (5.8 %), 3 learns |

The three revisions produce the same decisions, the same scores and the same
`best` values on every fixture of the three-revision passes (checked field by
field on the result lines, including both recorded captures). The R1-R6 fixes
change serialization, locking and the MCP consumer; they do not touch the
feature, baseline, mask or scoring code, and this confirms it empirically.

## 5. The discrepancy, explained from the records

The review's open RF item was: on the same 433 MHz frames, the production
daemon reports `unknown` while the test instance accepted `FM Broadcast` at
score about 0.65 (`live_mcp_e2e.json`). Three conditions explain it; none is a
revision difference.

### 5.1 The acceptance itself reproduces offline, in every revision

On `spur433_live_realrate` every revision locks to FM Broadcast at the first
post-learning decision (t = 10.1 s, 6 frames learned) and keeps it to the end
(t = 98.9 s); the 52 post-settle confidences span 0.631 to 0.659 (an earlier
version of this note gave 0.643 as the lower bound, which was the minimum of
the sampled time series, not of all 52 decisions). Explanatory record of the first accepted frame after settle
(fixed revision, `runs/spur433_live_realrate__fixed.out.gz`, seq 9, t = 13.4 s):

| field | value |
|---|---|
| matched window | template 1 (FM Broadcast), `bin_offset` 1724, 655 bins = usable bins 1724..2378, i.e. the window centred on DC (2047) |
| score / threshold | 0.650 / 0.600 (`gate_factor` 1.0, no feature ranges in the bundle, so no gating); `margin` 0.255 over the second template |
| gates | SNR 18.1 dB (gate 6), prominence 13.4 dB (gate 4): both pass |
| residual features | `peak_bin` 2050 (DC+3), `bandwidth_hz` 18310 (-10 dB), spectral PAPR 18.4 dB, flatness 0.30, 5 peaks |
| background | learned, age 13.4 s, 9 frames; `hot` 234, `masked` 5 (DC+-2 only: one frequency seen, so no bin reaches the two-frequency spur criterion) |
| hysteresis | `locked` true, `held` true, `took_over` false |

This is the same window (1724), the same residual (peak 2050, about 17 kHz)
and the same score band (0.645 on the board, 0.631 to 0.659 here) as the board record of seq 116 in
`live_mcp_e2e.json`. The board record is therefore reproduced from recorded
data, not inferred.

Why this accept is recorded as *confirmed false* rather than *suspected*: the
matched residual is not an identified waveform but the receiver's own DC spur.
It sits on the LO (peak at DC+3) with the same shape at two unrelated LO
settings, 433 MHz (this capture) and 1.09 GHz (the three recorded frames of
5.3), so it follows the LO rather than staying at a frequency, which no
external emission does; nothing else is in the 2.5 MHz band and 433 MHz has no
FM broadcast allocation. The evidence is the spur's behaviour, not the shape
of an emission, which is the distinction that keeps the 2.4 GHz DAB+ accept
(5.5) at *suspected*.

### 5.2 Why the stock FM template accepts a narrow spike

The FM Broadcast template is dominated by its centre: 53 % of its L2 energy
lies within +-10 kHz of its peak and 71 % within +-20 kHz, out of a 400 kHz
width. The cosine similarity between the template and a Gaussian spike centred
on its peak is 0.60 at 17 kHz width, 0.74 at 30 kHz and 0.86 at 60 kHz
(computed from the bundle; `tools/classifier_templates/templates_format.py`).
Any narrow, carrier-like residual centred in the DC window therefore scores at
or above the 0.6 threshold against this template. The receiver's DC spur
leaves exactly such a residual: in the 433 MHz capture the spur peaks 27.5 dB
over the median floor at bin 2047 with a skirt of 18.2, 16.9, 15.9, 15.2,
14.5, 14.0 dB at DC+3..DC+8 (symmetric on the other side). The mask removes
DC+-2 only, so the skirt from DC+3 outward survives as an 18 kHz spike with 13 dB
prominence, and the FM window centred on it scores 0.65. The CW / Beacon
template (30 kHz) scores lower on a spike this wide, and DAB+ far lower, so the
label is FM, with a 0.25 margin.

### 5.3 Why the 1.09 GHz cycled fixtures do not show it

The three 1.09 GHz frames carry the same spur shape (25.7 dB peak, skirt 16.5
to 12.3 dB at DC+3..DC+8; the 433 MHz skirt is about 1.7 dB higher at the same
offsets, consistent with the 40 dB higher gain setting changing the floor and
spur relationship only slightly). But cycling three frames gives the
lower-envelope baseline a sequence whose minimum recurs every third frame:
the baseline learns the skirt almost exactly, the residual at the spur is the
3-frame difference only (prominence 6.1 dB versus 13.4 dB live), and the best
score falls to 0.411 (template 3, CW). The cycled fixture understates the
residual a real sequence leaves; it is a software limitation of the generated
set, not evidence that the spur is handled. `spur_only_*` results must be read
with that caveat, and the live capture is the authoritative spur case.

### 5.4 Why the production daemon reports unknown on the same frames

The production daemon's background summary at the time
(`live_sensor_snapshots.json`, `live_mcp_e2e.json`) was
`hot 92, masked 3563` of 4095 bins. With a 2.5 MHz span and a 4 MHz baseline
width, the baseline opening half-width (`base_width_hz / live_hz / 2` = 3277
bins) is clamped to `live_n / 2`, so the baseline is a flat floor and most of
the band's roll-off reads as hot; after two or more retunes those bins meet the
two-frequency spur criterion and are masked. The production instance was
therefore evaluating 532 unmasked bins and reported unknown with `best 0.000`:
it was blind, not discriminating. The test instance had seen one frequency
(`masked 5`) and evaluated the full band.

The follow-up reproduces the causal chain offline with scripted retunes over
the recorded 433 MHz frames (`spur433_live_retune`, section 4). With one
frequency seen, the daemon accepts the spur as FM Broadcast for every decision
from the end of learning (10.1 s) to the first scripted retune at 30 s: 12
decisions, 10 of them after the 12 s settle the tables count, at 0.643..0.652;
the retune relearns the background, the 231 bins hot at both announced
frequencies (the spur skirt and the band's roll-off, since the frames are the
same) meet the two-frequency criterion and are masked, and every later
decision is `unknown` with `best` empty (42 counted decisions, 30.2 s to
98.9 s: the first 12 of them during the 10 s relearn, the rest from 40.2 s with
the grown mask applied); the third frequency adds 6 bins (237 masked). The chain retune -> mask growth ->
spur skirt masked -> acceptance ends, with `unknown` as the result, is
therefore reproduced in replay on real frames, in direction and in its effect
on the decision. It is not reproduced in magnitude: the production summary was
3563 masked bins, the replay reaches 237. Replaying one capture presents the
same hot-bin population at every announced frequency, so the mask converges to
that population (about 235 bins); the production unit saw different spectra at
each of its retunes (different signals in band, AGC and gain changes), and with
N frequencies seen, any bin hot at two of them is masked, which over many
retunes covers most of the band. Reproducing the magnitude needs captures that
actually differ per frequency, replayed with the matching events, which this
pass did not have.

One more observation from the same replays: on the cycled 1.09 GHz frames
(`spur_retune_sequence`) the best score rises from 0.404..0.411 with the DC+-2
mask to 0.523..0.578 once the 70 hot bins are masked, still below the 0.6
threshold but not far from it. Masking changes the residual the templates are
matched against, so a mask change is not a neutral operation on the scores and
has to be evaluated like any other tuning change.

### 5.5 The 2.4 GHz capture: one suspected false accept, twenty unverified accepts

`ism2400_live_realrate` is the only independent capture in another band. Of
the 34 decisions after settle, every revision gives:

* **1 DAB+ accept** at 23.1 s, score 0.859, window `bin_offset` 2298, 333 bins
  (2.5 MHz) at about 2403.1 MHz, SNR 24.3 dB, prominence 7.7 dB; residual
  bandwidth 5.04 MHz, 27 peaks, flatness 0.22, `peak_bin` 2446. Disposition:
  **suspected false DAB+ acceptance on unverified 2.4 GHz activity; Wi-Fi-like
  spectrum, protocol ground truth pending.** The spectrum is consistent with
  Wi-Fi in this band (wide intermittent bursts: a block about 7.3 MHz wide at
  bins 2619..3592, 2404..2412 MHz, 11..21 dB over the floor, and activity in
  the channel 1 range), and the flat 2.5 MHz DAB+ template matched a section
  of it; but no independent reception or demodulation identified the
  waveform, so Wi-Fi is an interpretation of the spectrum, not an established
  label. Recorded separately from the waveform question: DAB+
  has no allocation near 2.4 GHz, so the label is a bandplan anomaly whatever
  the emitter was. The accept lasted one decision; at 24.1 s the CW template
  took over.
* **20 CW / Beacon accepts** (0.984..0.990), all on the same window:
  `bin_offset` 975, 8 bins (60 kHz), SNR 7.8..9.0 dB, prominence 4.0..7.0 dB
  (at or just over the 4 dB gate). The capture holds a persistent narrow spike at
  usable bins 978..979, about 2391.99 MHz: median 6.3 dB over the floor, 10 dB
  over its +-30-bin neighbourhood, present in every frame. The classifier
  oscillates between CW and unknown as the prominence crosses the gate
  (accepted 15.4..16.5, 24.1..31.8, 35.1..39.4, 42.7..46.0 and 48.1 s, that is
  2 + 8 + 5 + 4 + 1 decisions; unknown in between: those are the 13 unknowns). Whether this spike is a receiver
  artefact (like the DC spur) or an emission is not decidable from this
  capture: a persistent 30 kHz carrier is what the CW / Beacon template is for,
  so if it is on air the label is right, and if it is internal it is a false
  accept of the same family as the 433 MHz one. Disposition: **unverified**,
  counted neither as a false accept nor as a trusted positive. The other
  persistent spikes (bins 1534..1536, 2396.16 MHz, 10 dB) and the DC cluster
  (bins 2039..2056) were not accepted.

So this capture contributes **0 confirmed false accepts** to the statistics,
one suspected false accept and twenty unverified accepts. The capture was
taken at the sensor's defaults after the owner's reboot (AGC, 30.72 MHz
span), so it is not a setting the sensor is normally operated at; it is kept
as an independent unknown-band case, not as a tuning set.

## 6. Verdict on the RF acceptance gate

The gate stays **open**:

* the DC spur is still accepted as FM Broadcast by the fixed revision (52/52
  decisions on recorded 433 MHz data), exactly as by the baseline and the
  reviewed revision;
* the production daemon's `unknown` on the same frames is now reproduced as
  the mask hiding the spur after retunes (5.4), so it is not evidence that the
  production configuration discriminates; the 2.4 GHz capture adds one
  suspected false accept (DAB+ at 0.859 on unverified Wi-Fi-like activity,
  protocol ground truth pending) and twenty unverified accepts; it adds no
  confirmed false accept (5.5);
* the positives are recognised (38/38 in each of fm_offcenter, fm_near_lo,
  dab_fullspan, cw_offcenter), but they are template-derived software fixtures,
  so they show the pipeline works, not that real emissions are recognised;
* the near-LO positive is recognised at 0.917 or better and the unknown block
  is correctly left unknown, so a blanket DC exclusion is not needed to reach
  these results and would not be justified by them;
* the results are reproducible bit-for-bit across revisions (section 7).

The explaining condition is in the template set and the mask, not in the
revisions compared: a centre-dominated FM template plus a spur skirt that
survives the DC+-2 mask, and a flat 2.5 MHz DAB+ template that a section of a
wider block can match (suspected, 5.5). Any change (template shape, feature ranges, a wider
or spur-shaped mask, a spike-vs-template width test) is tuning and is out of
scope for this pass; it must be evaluated on recorded RF with real emissions,
not on these fixtures.

## 7. Reproduction

```bash
# from the repository root, with the signalsdrpro Buildroot SDK and qemu-arm-static installed
SYSROOT=/path/to/output/signalsdrpro/host/arm-buildroot-linux-gnueabihf/sysroot
python3 tools/classifier_templates/replay/rf_compare.py --out /tmp/rf \
  --fixtures tools/tezuka_mcp/tests/fixtures \
  --templates board/tezuka/common/overlay_tezuka/etc/classifier-default-templates.bin \
  --sysroot $SYSROOT \
  --bin baseline_7997d95=/path/to/7997d95/app/classifier/classifier \
  --bin pinned_fb9b86a=/path/to/fb9b86a/app/classifier/classifier \
  --bin fixed=app/classifier/build/arm/classifier \
  --recorded spur433_live_realrate=docs/evidence/2026-11-08/rf_compare/raw/wf433_live.raw:0.5966 \
  --recorded spur433_live_7p5fps=docs/evidence/2026-11-08/rf_compare/raw/wf433_live.raw:7.5

# second pass (results.md / results.json as committed): the 2.4 GHz capture decompressed first
xz -dk docs/evidence/2026-11-08/rf_compare/raw/ism2400_live.raw.xz   # -> raw/ism2400_live.raw, sha256 85acf590...
python3 tools/classifier_templates/replay/rf_compare.py --out /tmp/rfA \
  --fixtures tools/tezuka_mcp/tests/fixtures \
  --templates board/tezuka/common/overlay_tezuka/etc/classifier-default-templates.bin \
  --sysroot $SYSROOT \
  --bin baseline_7997d95=/path/to/7997d95/app/classifier/classifier \
  --bin pinned_fb9b86a=/path/to/fb9b86a/app/classifier/classifier \
  --bin fixed=app/classifier/build/arm/classifier \
  --recorded spur433_live_realrate=docs/evidence/2026-11-08/rf_compare/raw/wf433_live.raw:0.5966 \
  --recorded ism2400_live_realrate=docs/evidence/2026-11-08/rf_compare/raw/ism2400_live.raw:7.3314 \
  --accept 'spur433_live_realrate=confirmed_false=FM Broadcast::<basis, see fixtures.json>' \
  --accept 'ism2400_live_realrate=suspected_false=DAB+::<basis, see fixtures.json>' \
  --accept 'ism2400_live_realrate=unverified=CW / Beacon::<basis, see fixtures.json>' \
  --only spur_only_cold,spur_only_settled,quiet_no_spur,fm_offcenter,fm_near_lo,dab_fullspan,cw_offcenter,unknown_block_1mhz,overlap_fm_cw,spur433_live_realrate,ism2400_live_realrate

# scripted retune replays (results_events.md / results_events.json / fixtures_events.json)
python3 tools/classifier_templates/replay/rf_compare.py --out /tmp/rfB \
  --fixtures tools/tezuka_mcp/tests/fixtures \
  --templates board/tezuka/common/overlay_tezuka/etc/classifier-default-templates.bin \
  --sysroot $SYSROOT \
  --bin fixed=app/classifier/build/arm/classifier \
  --recorded spur433_live_retune=docs/evidence/2026-11-08/rf_compare/raw/wf433_live.raw:0.5966 \
  --events spur433_live_retune=docs/evidence/2026-11-08/rf_compare/raw/spur433_retune.events \
  --accept 'spur433_live_retune=confirmed_false=FM Broadcast::<basis, see fixtures_events.json>' \
  --only spur_retune_sequence,spur433_live_retune
```

A single replay with events, without the driver:

```bash
qemu-arm-static -L $SYSROOT app/classifier/build/arm/classifier -r raw/wf433_live.raw \
  -E raw/spur433_retune.events -f board/tezuka/common/overlay_tezuka/etc/classifier-default-templates.bin \
  -n 4096 -F 0.5966 -S 2500000 -B none -c 0.6 -m 0.15 -i 1.0 -s 6 -g 4 -l 10 -a 300 -w 4e6 -x 8
```

`runs/` holds the full daemon output of the fixed revision for the recorded
captures and the scripted-retune replays (gzip), so the timelines quoted above
can be checked line by line. The fixed binary of this pass is the one with
the events-parser correction (sha256 in section 2); the decisions and scores
of every fixture are unchanged from the previous fixed binary, as the tables
show.

The next captures are to be taken with `capture_waterfall.py` as described
in `CAPTURE_SESSION_PLAN.md` (same directory): it now logs every receiver
setting change during the capture and writes the matching replay events
file (the initial frequency, sampling rate, frame span, bandwidth, gain
and gain mode at t = 0, then the changes), keeps the meta and events files
when the capture is interrupted with Ctrl+C, and `rf_compare.py --expect
NAME=LABEL` declares a trusted positive. The grid span of a recorded
capture is the spectrometer's input rate; for the two captures here it
equals the AD9361 rate, so nothing in these tables changes.

The generated `.raw` sequences are deterministic (seeded by fixture name) and
are not committed; their SHA-256 values in `fixtures.json` must match after
regeneration. A new capture is taken with
`python3 tools/classifier_templates/replay/capture_waterfall.py <host> <frames> <out.raw>`
(read-only; writes `<out.raw>.meta.json` with the `/api` settings and the hash).

## 8. Limitations and next step

* Two recorded RF cases exist: 433 MHz (gain 70 dB manual, 100 s) and the
  2.4 GHz defaults (AGC, 49 s, taken after the owner's reboot instead of the
  planned second 433 MHz capture, since the sensor's 433 MHz tuning was lost
  and this pass does not retune it). Neither is longer than 100 s and neither
  is at a setting the sensor is normally operated at besides the 433 MHz one.
  Next step: with the owner, capture 10 minutes at two or three LO settings the
  sensor normally uses, and keep them as evaluation data only.
* No independent trusted positive. Next step: record a real broadcast FM or
  DAB+ emission with this receiver (no transmission needed) and freeze it as a
  positive fixture before any template, threshold or mask change is evaluated.
  The 2391.99 MHz spike in the 2.4 GHz capture is not that positive: its
  origin is unknown (5.5).
* The production mask degeneration (5.4) is reproduced in mechanism and in its
  effect on the decision by the scripted-retune replay, not in magnitude (237
  masked bins against 3563). Next step: capture at two or three different LO
  settings in one session and replay them in sequence with matching events,
  so the hot-bin population differs per frequency as it did on the sensor.
* Tuning and evaluation data are separate by construction here because
  nothing was tuned: every capture in this directory is evaluation data. Any
  future template, threshold or mask change must be developed on captures not
  listed here and then evaluated on these.
