# RF comparison: baseline 7997d95 vs reviewed fb9b86a vs fixed revision

Offline, read-only comparison requested by `CLAUDE_MO_RF_ACCEPTANCE_DIRECTIVES.md`
(task 2: explain the RF discrepancy before tuning; task 3: freeze an evaluation
set; task 4: per-fixture results for three revisions). Nothing was transmitted,
no sensor setting was changed, no service, template or FPGA image on the sensor
was touched. The only sensor interaction was a read-only subscription to its
`/waterfall` websocket and two GET requests to `/api` (before and after) to
record the receiver settings.

## 1. What is RF evidence here and what is not

| Input | Kind | Why |
|---|---|---|
| `raw/wf433_live.raw` (60 frames, 433 MHz, as captured) | **recorded RF** | the sensor's own spectrometer output, unmodified, sequence order kept |
| `tools/tezuka_mcp/tests/fixtures/wf_avg4096_frame{0,1,2}.bin` (3 frames, 1.09 GHz) | recorded RF frames, but **cycled** into sequences | only three distinct frames exist; cycling them is a software construction (see 5.3 for the effect) |
| `fm_*`, `dab_*`, `cw_*`, `unknown_block_1mhz`, `overlap_fm_cw`, `quiet_no_spur` | **software fixtures** | emitters shaped like the templates (or like nothing in the set) are injected into the cycled frames; they are positive and negative controls for the pipeline, not evidence of performance on independent RF |

No independent trusted positive (a real FM, DAB+ or CW emission recorded by
this receiver) was available; none was fabricated. Nothing was tuned on these
fixtures: no threshold, template, gate or mask was changed in any revision.

## 2. Frozen inputs

| Item | Value |
|---|---|
| Template bundle | `board/tezuka/common/overlay_tezuka/etc/classifier-default-templates.bin`, sha256 `cb6f4580360c75b485140af34670e1d625942cb03a5dd279c509c7063c36f263`, version 3, canonical_n 512, labels FM Broadcast (id 1, 781.25 Hz/bin, 400 kHz), DAB+ (id 2, 4882.8 Hz/bin, 2.5 MHz), CW / Beacon (id 3, 58.6 Hz/bin, 30 kHz), no feature ranges. Same bytes the sensor runs (`templates.sha256` in `live_mcp_e2e.json`). |
| Recorded 1.09 GHz frames | `wf_avg4096_frame0.bin` `1b26b04cab3406beb5f2f50f87bc3804a4d05cb46230f9c3c088f680a1c4ec4d`, `frame1` `5acadf69568e60cd611e09b1a2bb2c59c44a2d87c58307350feff3435d2b8835`, `frame2` `602c0489845d5b4d698337197c3e2c4c155af318c79cb0b0cb4dfad09419021c` (SignalSDR Pro, 2026-11-08T18:01Z, 1.09 GHz, 2.5 MHz, Average x1023, gain 30 dB Manual) |
| Live 433 MHz capture | `raw/wf433_live.raw`, sha256 `db51a261a62de5b5ec821ff92a57d9eba109ed1916400105eb0171628c5f59f4`, 60 frames of 16384 bytes, captured 2026-11-08 (unix 1794170740.7) from 192.168.50.25 with `tools/classifier_templates/replay/capture_waterfall.py`; receiver before and after: rx_lo 433000000 Hz, fs 2500000 Hz, rx_rf_bandwidth 2500000 Hz, gain 70.0 dB Manual, spectrometer Average x1023, 4096-point, 0.5966 frames/s; firmware `tezuka-v0.3.24-34-g6e3a`, maia-httpd `v0.3.24-21-g49403b9`, maia-hdl 0.6.1 (`raw/wf433_live.raw.meta.json`) |
| Frame layout and grid | float32 LE x 4096 per frame; float 0 is the step word; usable bin j is float j+1; linear integrated power (not dBm); 610.3515625 Hz per bin; DC at usable index 2047 |
| Daemon arguments (identical for every revision) | `-n 4096 -S 2500000 -B none -c 0.6 -m 0.15 -i 1.0 -s 6 -g 4 -l 10 -a 300 -w 4e6 -x 8` plus `-F <frame rate>`: 7.5 for the generated fixtures, 0.5966 (the capture rate) for `spur433_live_realrate`, 7.5 for `spur433_live_7p5fps`. Threshold 0.6, hold margin 0.15, SNR gate 6 dB, prominence gate 4 dB, learning 10 s, background tau 300 s, baseline width 4 MHz, spur mask DC+-2 and bins hot at 2 or more frequencies. |
| Revisions | `baseline_7997d95` = parent commit 7997d959b43c8ea250cde85a935d808cd938fb5f, binary sha256 `29999610181d2e67eb46aaf45068e1a0e33ad336729d09103f4d95c3e8669cee`; `pinned_fb9b86a` = reviewed commit fb9b86aef7880bca41cfc5094d3a0d11eef5d0fe, `e952dd4ecd3dcb335f1a7733c452fad01ef22483dcab3e57b92de87c217e07da`; `fixed` = this worktree with the R1-R6 changes, `5b1f7f3cd03c02299b9d9711bdaaf0a3d30a6966d8ca6fc6956535e389198ccd`. Each built with `make classifier` in `app/classifier` from a clean checkout of that commit, with the signalsdrpro Buildroot SDK (`arm-linux-gcc` 15.1.0, Buildroot 2021.11-18033-g83947c7bb6 toolchain wrapper). |
| Execution | `qemu-arm` 8.2.2 (`qemu-arm-static -L <sysroot>`), replay mode `-r FILE`, no network (`-B none`); virtual clock advances by 1/F per frame, so results are independent of host timing |

## 3. Fixture set (`fixtures.json`, `fixtures_live433.json`)

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
| spur433_live_7p5fps | recorded RF, wrong rate | 60 / 8 | unknown | same bytes at 7.5 frames/s: the 60 frames end before the 12 s settle, so it yields no post-learning decision and is kept only to document the rate effect |

Decisions are tallied from the `state/classifier/result` lines after a settle
of 12 s (learning 10 s plus one cycle); for fixtures with an emitter, the
window `[t_on, t_off)` is ON and everything else OFF. A false accept is any
label other than unknown that is not in the expected set for that window.

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

Live capture (`results_live433.md`, `results_live433.json`; 6 runs, all exit 0):

| fixture | baseline_7997d95 | pinned_fb9b86a | fixed |
|---|---|---|---|
| spur433_live_realrate | **52 false accepts / 52 decisions**, all FM Broadcast, scores 0.643..0.659 | **52 / 52**, 0.643..0.659 | **52 / 52**, 0.643..0.659 |
| spur433_live_7p5fps | 0 decisions after settle (sequence too short at this rate) | same | same |

The three revisions produce the same decisions, the same scores and the same
`best` values on every fixture. The R1-R6 fixes change serialization, locking
and the MCP consumer; they do not touch the feature, baseline, mask or scoring
code, and this confirms it empirically.

## 5. The discrepancy, explained from the records

The review's open RF item was: on the same 433 MHz frames, the production
daemon reports `unknown` while the test instance accepted `FM Broadcast` at
score about 0.65 (`live_mcp_e2e.json`). Three conditions explain it; none is a
revision difference.

### 5.1 The acceptance itself reproduces offline, in every revision

On `spur433_live_realrate` every revision locks to FM Broadcast at the first
post-learning decision (t = 10.1 s, 6 frames learned) and keeps it to the end
(t = 98.9 s). Explanatory record of the first accepted frame after settle
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
and the same score band (0.645) as the board record of seq 116 in
`live_mcp_e2e.json`. The board record is therefore reproduced from recorded
data, not inferred.

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
(`masked 5`) and evaluated the full band. This is not reproducible in replay
(the mask only accumulates across MQTT frequency changes), so it rests on the
recorded summaries, which are consistent with the code path.

## 6. Verdict on the RF acceptance gate

The gate stays **open**:

* the DC spur is still accepted as FM Broadcast by the fixed revision (52/52
  decisions on recorded 433 MHz data), exactly as by the baseline and the
  reviewed revision;
* the positives are recognised (38/38 in each of fm_offcenter, fm_near_lo,
  dab_fullspan, cw_offcenter), but they are template-derived software fixtures,
  so they show the pipeline works, not that real emissions are recognised;
* the near-LO positive is recognised at 0.917 or better and the unknown block
  is correctly left unknown, so a blanket DC exclusion is not needed to reach
  these results and would not be justified by them;
* the results are reproducible bit-for-bit across revisions (section 7).

The explaining condition is in the template set and the mask, not in the
revisions compared: a centre-dominated FM template plus a spur skirt that
survives the DC+-2 mask. Any change (template shape, feature ranges, a wider
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
```

The generated `.raw` sequences are deterministic (seeded by fixture name) and
are not committed; their SHA-256 values in `fixtures.json` must match after
regeneration. A new capture is taken with
`python3 tools/classifier_templates/replay/capture_waterfall.py <host> <frames> <out.raw>`
(read-only; writes `<out.raw>.meta.json` with the `/api` settings and the hash).

## 8. Limitations and next step

* Only one recorded RF case exists (433 MHz, gain 70 dB, 100 s, cold start to
  settled-at-100 s). Cold-versus-settled on real data beyond 100 s, other LO
  frequencies and other gain settings were not captured. Next step: capture
  10 minutes at two or three LO settings the sensor normally uses, without
  changing the sensor's configuration, and add them as recorded fixtures.
* No independent trusted positive. Next step: record a real broadcast FM or
  DAB+ emission with this receiver (no transmission needed) and freeze it as a
  positive fixture before any template or threshold change is evaluated.
* The production mask degeneration (5.4) is explained from code and recorded
  summaries, not reproduced. Next step: add an MQTT frequency-change sequence
  to replay mode so the two-frequency mask criterion can be exercised offline.
