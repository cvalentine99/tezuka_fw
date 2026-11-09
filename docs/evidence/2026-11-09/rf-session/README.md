# RF session 2026-11-09: positive check, captures, replay

Owner's instruction (2026-11-09): make the matched frequency window and the
whole-span feature peak clearly distinguishable in the console and in agent
explanations without changing scoring; run a short independently verified
positive check confirming that the matched window overlaps the intended
station; then run the longer capture session and retune sequence, keeping
settings events, template hashes, mask coverage and ground-truth notes; and
replay the recordings before changing the classifier. The reboot recorded
on 2026-11-09 at 05:47 CST (11:47 UTC) restored the receiver's 2.4 GHz
defaults and not the earlier FM demonstration settings; the owner then set
70 MHz, which was the starting point of this session.

Operator: Claude Code on the owner's written instruction, owner not present.
Sensor: SignalSDR Pro 192.168.50.25, firmware tezuka-v0.3.24-34-g6e3a,
classifier f87a3c46..., templates cb6f4580... (3 templates, unchanged all
session), thresholds and gates unchanged (0.6 / 0.15 / 6 dB / 4 dB). No
transmission, no reboot, no template, threshold, gate, bandplan or FPGA
change. Times are UTC.

## 1. Matched window and residual peak, kept apart

The observation carries two different frequencies: the matched window
(`decision.bin_offset` / `bandwidth_bins`, the template window the daemon
scored and accepted) and the whole-span residual peak (`features.peak_bin`,
the strongest bin of the spectrum over the learned background, anywhere in
the span). Commits 1cc4cc5, a05324c, 8d61d18 and 4b969b1:

* MCP `get_sensor_snapshot` gains `classifier.frequency_view`:
  `matched_window` (start, centre, end, width in Hz; accepted matches only),
  `whole_span_peak` (Hz) and `peak_inside_matched_window`, derived from the
  observation's own grid; missing values carry a reason. The server
  instructions tell the agent never to present one as the other, and that a
  peak outside the window does not by itself mean the window is wrong.
* Console Decision panel: "Matched window" and "Whole-span residual peak"
  as separate labelled fields plus "Peak inside window"; on the spectrum the
  window is the blue bracket under the label chip and the residual peak a
  dashed amber line labelled at the bottom.
* Found and fixed while verifying: the first wording called the peak the
  span's strongest emission (it is the strongest residual bin); both
  markers were drawn off the canvas when the trace clipped; and the spectrum
  handler repainted from the first render's state (stale chip). Screens in
  `positive_check/`. Each kept console read (13:03:43-13:03:52) shows
  exactly the window and residual peak recomputed from the observation
  with the same sequence number, and the MCP snapshot (13:00:48, seq 2703)
  shows 93.1019-93.5017 MHz from that observation's bins
  (`positive_check/console_vs_observation.txt`).

Scoring is unchanged: the daemon binary was not touched.

## 2. Positive check (P0, development)

Intended station: **KIGL 93.3 MHz** ("93.3 The Eagle", classic rock, FCC
facility 35014, 100 kW, transmitter about 5 km from locator EM26wk), the
strongest carrier in a 30.72 MHz survey of the FM band. LO 92 674 998 Hz,
2.5 MS/s, 2.5 MHz RF bandwidth, manual 71 dB: KIGL at +625 kHz.

Independent identification (not from the classifier or the spectrum
shape): a 10 s IQ recording taken by the sensor's own recorder during the
capture, FM-demodulated on the host and RDS-decoded with redsea: PI 0x25C7
(which back-calculates to KIGL) and PTY classic rock in 110 of 110 groups,
and the scrolling programme service name "93.3 The" / "Eagle - " in the 0A
groups; 19 kHz pilot 40 dB and the 57 kHz RDS subcarrier 15 dB over their
surroundings, 99.9th-percentile deviation 71 kHz. PI 0x25C7 was decoded
again in all groups of the S1, S1b and S1c recordings (108, 111 and 110). Limit: same
antenna and front end, not a second receiver.

| Check (live daemon, 88 cycles, 12:52-12:55) | Result |
|---|---|
| Cycles labelled FM Broadcast | 84 |
| ... whose matched window overlaps KIGL's channel (93.2-93.4 MHz) | 84 of 84 |
| ... whose window contains 93.3 MHz | 84 of 84 |
| Window centre | 93.300 to 93.305 MHz (within 5.5 kHz of the station) |
| Whole-span residual peak inside the window | 80 of 84 |
| Other cycles | 2 unknown, 2 CW / Beacon on a narrow line at 92.10 MHz |

The matched window overlaps the intended station. P0 is development data.

## 3. Capture session

Split fixed before any result was read (`SESSION_LOG.md`, written at 13:05
when its hash ef1a7066... was printed but not kept in a file; the addendum
at 13:13 gives the version kept here, 999b0932...): P0 development; S0
discarded; S1, S1b, S1c, S2, S3, S4, S5 evaluation. Spectrometer unchanged
throughout (Average, 4096 points, 610 integrations).

| Segment | Start-end | Setting | Frames (fps) | raw sha256 | On air / ground truth |
|---|---|---|---|---|---|
| S0 | 13:05:59-13:06:08 | as S1 | 10 (1.0) | a293d263... | tool check, discarded |
| S1 | 13:06:17-13:16:16 | LO 92.675 MHz, 2.5 MS/s, bw 2.5 MHz, manual 71 dB | 600 (1.0) | 7ff7c18b... | KIGL at +625 kHz, RDS mid-segment |
| S1b | 13:16:33-13:21:32 | LO 93.3 MHz (KIGL on the DC spur), rest as S1 | 300 (1.0) | 4222950c... | KIGL; KAMO 94.3 also identified by RDS (PI 0x1146 = KAMO, PS 94.3 Nash Icon, PTY country) |
| S2 | 13:21:49-13:31:48 | LO 433 MHz, 2.5 MS/s, bw 2.5 MHz, manual 70 dB | 600 (1.0) | d93224ea... | unknown ISM, no ground truth |
| S3 | 13:31:56-13:41:55 | LO 70 MHz, 2.5 MS/s, bw 2.5 MHz, manual 41 dB (owner's setting) | 600 (1.0) | 521bd743... | unknown, no ground truth |
| S4 | 13:42:03-13:52:04 | LO 2.4 GHz, 30.72 MS/s, bw 18 MHz, SlowAttack (boot defaults) | 7377 (12.3) | 04dacf8a... | unknown 2.4 GHz ISM; also the AGC segment |
| S5 | 13:52:12-14:04:11 | start as S2; LO only: 70 MHz, 2.4 GHz, 433 MHz | 720 (1.0) | 716f2e6e... | retune sequence |
| S1c | 14:06:51-14:16:50 | as S1, manual 65 dB (first of 65/60/55/50/45 dB with no full-scale IQ sample) | 600 (1.0) | 7a566fde... | KIGL, RDS mid-segment |

* **Settings events.** Every operator change is in `operator_actions.jsonl`
  (wall time, request, `/api` after); every change the capture tool saw is
  in each `raw/*.meta.json` `settings_log` and replay `.events` file.
  Stationary segments S0-S3 and S1c logged no change. S5's retunes were
  made at 13:55:12, 13:58:12 and 14:01:12 and logged at +180.6 s, +360.0 s
  and +540.6 s (within the 1 s poll). S4 logged 93 AGC gain changes: 45
  dips from 71 dB, most of 2 to 6 dB, the deepest 18 dB, each lasting 1 to
  2 s.
* **Template hashes.** cb6f4580... with status ok in every observation of
  every segment (`live/*.live.json`).
* **Mask coverage (live, masked bins of 4095).** Before the session, at
  the owner's 70 MHz setting: 98. The survey retune (96.45 MHz, 30.72 MS/s)
  raised it to 188 and the P0 retune to 286. P0, S1: 286; S1b: 608 after its
  retune; S2: 1271; S3: 1271; S4: grew from 1459 to 1536 during the segment,
  and 0 in the 202 cycles spent relearning after AGC steps; S5: 1541 to
  1564 (the last value after the third retune), 0 in 24 relearning cycles;
  S1c: 1589. The spur map persists across retunes at one FFT size and only
  grows; the mask applied in a cycle is empty while the background
  relearns.
* **Ground-truth notes.** RDS decodes and reports in `iq/`; IQ recordings
  (100 MB each) are kept on the host with their hashes in
  `iq/iq_recordings_sha256.txt` and their SigMF metadata in `iq/`.
* Every live observation and receiver-state change of the session (JSONL,
  10 MB uncompressed) is in `live/mqtt_session.jsonl.xz`.

## 4. Results, window by window

A label counts as right only when its window sits on the emission it names
(KIGL 93.2-93.4 MHz, KAMO 94.2-94.4 MHz). "Replay" is the deployed binary
(f87a3c46...) under qemu, cold start, with the capture's own events file.
Counts are all published cycles, including the learning ones; the
disposition record in `replay/final/` counts decisions after the learn, so
its totals are a few cycles lower.

| Segment | Live daemon | Replay |
|---|---|---|
| S1 (KIGL, 71 dB) | 367 cycles: FM on KIGL 279, FM at 91.84-92.26 MHz 10; CW on KIGL's carrier 15, CW on narrow lines 57; unknown 6 | 301: FM on KIGL 155, FM at 91.84-92.24 MHz 1; CW on KIGL 12, on narrow lines 123; unknown 10 |
| S1b (KIGL on the LO) | 186: FM 2 (both on KAMO); CW on KIGL 143, on KAMO 30, elsewhere 3; unknown 8 | 151: FM on KIGL 70, on KAMO 74; CW on KAMO 1; unknown 6 |
| S1c (KIGL, 65 dB, no clipping) | 365: FM 0; CW 362 (350 on a line at 92.165 MHz, 5 at 92.10 MHz, 7 at 93.638 MHz); unknown 3 | 301: FM on KIGL 131; CW 161 (128 on the 92.165 MHz line, 25 at 92.10 MHz, 6 at 91.70 MHz, 2 on KIGL); unknown 9 |
| S2 (433 MHz) | 367: FM Broadcast at 433.64-434.07 MHz 251; CW 5; unknown 111 | 301: DAB+ 23, CW 1; unknown 277 |
| S3 (70 MHz) | 367: unknown 367 | 301: unknown 301 |
| S4 (2.4 GHz, AGC) | 568: DAB+ 57 (in Wi-Fi channel 1), FM 41, CW 30; unknown 440 | 569: DAB+ 10, FM 6, CW 8; unknown 545 |
| S5 (retunes) | 440: FM at 433.03-433.46 MHz 130, plus 1 tagged with the previous 2.4 GHz config just after the last retune; CW 7; unknown 302 | 361: DAB+ 26, CW 47; unknown 288 |

Chained replay (P0, S0, S1, S1b, S2, S3 joined as one daemon run, retunes
in order, `replay/chain/`): S2 gives FM Broadcast at 433.62-434.02 MHz in
260 of 300 cycles with 865 spur bins, reproducing the live false label that
the cold replay does not show. Dispositions are in `replay/final/` (`rf_compare.py`
with `--expect` and evidence-based `--accept`; confirmed, suspected and
unverified kept separate). One accept is `confirmed_false`: the single CW /
Beacon window of the S1b replay lies inside KAMO's channel, identified by
RDS during that segment. The S1 and S1c CW sets mix windows on KIGL with
windows on unidentified lines, and the S2, S4 and S5 labels rest on band
allocation and on IQ taken after the segment, so they are `suspected_false`;
CW labels away from the FM band stay `unverified`.

## 5. What the session shows

1. **The matched window lands on the station when the mask is clean.**
   Every FM Broadcast window in P0 (84) and in the S1c replay (131)
   overlaps KIGL, centres within 5.5 kHz. In S1, 279 of 289 live FM windows
   are on KIGL (centres within 7.3 kHz) and 10 sit at 91.84-92.26 MHz, on
   an unidentified feature near KIGL's mirror frequency (item 8).
2. **The growing spur map is the dominant failure.** The live mask went
   from 98 bins at the owner's setting to 1589 (39 % of the span) over the
   session's retunes, the survey retune included. With it, the live daemon
   recognised KIGL in 0 of 365 cycles in S1c, while a cold replay of the
   same frames recognised it in 131. The chained replay shows the same
   mechanism producing the 433 MHz FM Broadcast false label. A cold replay
   therefore does not stand in for the live daemon unless the mask history
   is replayed too; the 98 bins the daemon held before the session came
   from earlier activity no capture contains.
3. **One decision per span.** The daemon reports one label per cycle for
   the whole span, so KIGL and a narrow line compete for one slot. Hysteresis
   can hold CW / Beacon while FM scores higher (P0 seq 2457: CW held at 0.95
   with FM at 0.988); this explains 23 of the 78 non-FM cycles in S1. It
   does not explain S1b or S1c: there the best-scoring template itself was
   CW in nearly every cycle, which follows from item 2.
4. **A station on the LO fails live.** In S1b the live daemon split KIGL
   into CW windows on either side of the masked DC region; the cold replay
   recognised it (70 cycles, and KAMO in 74). The capture plan's
   requirement that a legitimate signal at the LO stays recognisable holds
   only with a clean mask.
5. **False labels away from broadcast bands.** FM Broadcast at 433 MHz (S2,
   S5) and DAB+ and FM Broadcast at 2.4 GHz (S4) are implausible by
   allocation and marked suspected false. At 433 MHz, IQ taken 1 to 34
   minutes after the segments puts both FM windows' band median within
   2 dB of the span noise median at two LO settings: nothing 400 kHz wide
   was there; the only narrow carrier that stays put at both LOs is at
   433.329 MHz. The CW labels at 2.4 GHz include a recurring line at
   2.392 GHz that may be a real carrier and stay unverified.
6. **AGC makes the daemon relearn constantly.** S4: 93 gain changes, 38
   configuration generations, the background unlearned in 202 of 568
   cycles.
7. **71 dB clips at this site.** In the P0 IQ 0.14 % of the I and Q values
   (0.28 % of complex samples) reach full scale, the floor jumps by 4 to
   12 dB in about one frame in ten, and the same jumps are in the IQ, so
   they are in the samples. The 2 s `state/rx/overload` poll never showed
   it. 65 dB was clean in all its recordings.
8. **Unresolved, reported as such.** A pair of narrow lines 337 kHz either
   side of KIGL (92.963 and 93.638 MHz, the latter the residual peak seen
   during the check); lines at 92.165 and 92.10 MHz; a feature at 92.05 MHz
   (41 dB under KIGL at its mirror frequency, shape not clearly mirrored).
   Of the 17 narrow lines found at LO 92.675 MHz, the 11 that could be
   tested both ways appeared at LO 93.3 MHz neither at the same frequency
   nor at the same LO offset, and the 6 testable one way did not match
   either (`analysis/S1_S1b_line_origin.json`): they are neither steady
   on-air carriers nor LO-locked receiver spurs.

## 6. What changed on the sensor, and its state now

* Receiver settings changed only by this session's operator actions; the
  owner's settings were restored at 14:17:07 and `/api` matches
  `api_owner_before.json` exactly (receiver, spectrometer, DDC, recorder,
  recording metadata). The recorder buffer now holds the last session
  recording instead of the owner's 3 KB stub (a copy of the stub is kept).
* At 14:27:38 the classifier service was restarted once to clear the spur
  map the session's retunes had built (1589 masked bins at 70 MHz before,
  5 after; same binary f87a3c46..., single instance, templates ok, still
  enabled). `classifier_restart.log`.
* Console page redeployed four times (deployment record section 14).

## 7. Reproduce

```bash
# narrow-line origin and the 433 MHz test (IQ files on the host)
python3 tools/line_origin.py <S1 iq> 92674998 <S1b iq> 93300000 S1_S1b_line_origin.json
python3 tools/t433_check.py <iq LO 433.0> <iq LO 433.3> T433_two_lo.json
# cold replay of every evaluation capture, with dispositions
tools/run_final_replay.sh
# the chained replay
python3 tools/chain_replay.py CHAIN_P0_S3.raw 1.0005763319672132 P0_positive_check S0_check S1_kigl_offset S1b_kigl_on_lo S2_433 S3_70
# window-by-window tables
python3 tools/final_analysis.py
```

The scripts in `tools/` were written for this session and carry its host
paths; the raw captures are `raw/*.raw.xz` (S4, 121 MB raw, is kept on the
host at `~/tezuka-live-audit/rf-session-20261109/raw/S4_2400_agc.raw`, hash
in `raw/raw_sha256.txt`).

## 8. Next, before any classifier change

Per the capture plan, a correction is developed on development captures
and evaluated once on these. Only P0 is development data, so a development
session comes first. The candidates the evidence points at, none of them
implemented: a spur map that is scoped to the frequency it was learned at
or decays, instead of growing for the life of the daemon; more than one
decision per span (per-emitter results); a manual-gain guideline that keeps
the ADC out of full scale; and AGC handling that does not relearn on every
gain step.
