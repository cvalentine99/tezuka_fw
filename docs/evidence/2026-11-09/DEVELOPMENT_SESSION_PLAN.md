# Development session: data for a spur-map fix

Prepared 2026-11-09 after the RF session (`rf-session/README.md`), revised
after two independent reviews (protocol and feasibility; every blocking issue
resolved below). Not run. Purpose: collect **development** captures from
which a fix of the daemon's spur map can be designed and chosen offline, then
evaluate the choice once on pre-registered evaluation data. Nothing here
changes the production daemon, its templates, thresholds or gates.

## 1. The problem

At the end of every background learn, `background_learned()`
(`app/classifier/classifier.c`, around line 1342) marks a bin "hot" when the
learned envelope is more than 8 dB (`-x`) over the receiver baseline. A bin
hot at two RX frequencies more than `far` apart, where `far` is the
*current* span, gets `spur_count >= 2` and is masked until the daemon
restarts or the FFT size changes. On 2026-11-09 the mask grew from 98 to
1589 of 4095 bins. The live daemon then recognised the RDS-verified KIGL
93.3 MHz in 0 of 365 S1c cycles (362 CW / Beacon accepts on other narrow
lines), while a cold replay of the same frames recognised it in 131. A
chained replay reproduced the 433 MHz FM Broadcast false label from the same
mask history.

Four mechanisms, from the code and the live log:

* **M1, coincidence.** Different real emissions that occupy the same bin
  index at two frequencies count as one spur; two hits suffice and nothing
  un-masks. Dense bands make this near certain: the learns reported 20 to
  35 % of all bins hot (FM band 659-826, 433 MHz 734-1427).
* **M2, cross-span counting.** The map is indexed by bin, not by offset in
  Hz, and is kept across sample-rate changes, so hot bins at 30.72 MS/s and
  at 2.5 MS/s count together; the order of the two spans matters because
  `far` is the current span. On 2026-11-09 the step from 188 to 286 bins
  is this case (96.45 MHz at 30.72 MS/s, then 92.675 MHz at 2.5 MS/s). The
  earlier step from 98 to 188 cannot come from the 70 MHz learn (26.45 MHz
  away, inside the 30.72 MHz `far`, and only 3 hot bins); it needs history
  from before the session, possibly the boot learn at 2.4 GHz / 30.72 MS/s.
* **M3, broad hot runs.** At 2.5 MS/s the 4 MHz baseline element (`-w`) is
  wider than the span, so the "baseline" is close to a span-wide minimum and
  a passband or floor tilt of more than 8 dB counts as hot: the first 11
  frames of S2 give one hot run of 1424 bins, and only 43 bins in runs of 5
  bins or fewer.
* **M4, repeated-learn union.** Relearns at one LO (AGC steps, gain
  changes, a relearn command) give a second hit to any bin last counted
  elsewhere that happens to be hot this time: at a constant 2.4 GHz LO the
  mask rose from 1459 to 1536 over 11 learns.

The 2026-11-09 session did not collect ground truth for which bins are
receiver spurs, nor the data to separate M1-M4.

## 2. Candidate fixes (offline only; none implemented yet)

Each candidate is a compile-time variant built from the same source (e38b340;
`app/classifier` is unchanged from there to the branch head) and ARM
toolchain as F0 and run under qemu as its own `rf_compare.py --bin`.

| Id | Change | Targets |
|---|---|---|
| F0 | none: source revision e38b340 rebuilt (the deployed binary is sha256 f87a3c46...; must replay identically, T3) | reference |
| F1a | separate spur maps per (span_hz, fft_size) key, kept across key changes | M2 |
| F1b | spur map reset whenever the (span_hz, fft_size) key changes | M2 |
| F2 | hit ratio: per span key, uint16 hits and trials per bin; a *trial* is an LO cluster (LOs within one span of each other merge), counted at most once whatever the revisit order; mask when trials >= T and hits / trials >= R; bins whose ratio falls below R are un-masked; both counters halved when trials reach 1000 | M1, M4 |
| F3 | narrowness: only bins inside a hot run of at most W Hz (converted with the live bin width at learn time) count | M1, M3 |
| F4 | F1a + F2 + F3 | M1-M4 |

Parameter grid, fixed now and searched on development data only: T in
{3, 4}; R in {0.6, 0.75, 0.9}; W in {5, 10, 20} kHz. Changing the baseline
element itself (`-w` scaled to the span) would also address M3, but it
changes the residual every template is scored on; it is out of scope here
and noted for a separate study.

## 3. Tooling before the session (no RF)

* **T1, mixed-rate replay.** A capture that changes sample rate changes the
  frame rate (1.0 frame/s at 2.5 MS/s, 12.3 at 30.72 MS/s with 610
  integrations). The replay clock becomes piecewise: frame index divided by
  the `/api` frame rate in force for that stretch (from the capture's
  settings log); `capture_waterfall.py` writes event times on the same
  piecewise clock (today it uses the rate at capture start, so a later event
  in a mixed-rate capture lands far too late or after the last frame and is
  never delivered). Host arrival times are written to `<raw>.times` as a
  diagnostic only. Tests: a synthetic two-rate capture whose span event
  reaches the first frame after the change; a single-rate capture replays
  identically before and after the change. The live path is untouched (a
  test compares the live-path source to f87a3c46's).
* **T2, tools.** `rf_compare.py` and the session's `chain_replay.py` accept
  piecewise-rate captures and chains; `capture_waterfall.py` gets
  `--seconds` so a mixed-rate segment needs no precomputed frame count.
* **T3, candidates.** F0-F4 as compile-time variants with unit tests on
  synthetic frames (a fixed spur at one offset is masked under every
  candidate; two different stations sharing a bin index at two LO clusters
  are not masked under F2-F4; a broad 1000-bin run is not counted under F3).
  **Acceptance before any candidate is run:** F0 rebuilt gives
  byte-identical observation streams to the deployed f87a3c46 on every
  2026-11-09 capture and on the P0-S3 chain.
* **T4, scoring.** A repository tool that scores each accepted window
  against declared channels (so "recognised" means the right label on the
  right frequencies), computes the per-dwell metrics of section 6, and
  attributes every masked bin to the learns that counted it.

## 4. Owner decisions before the session

1. **A 50 ohm load** on the antenna connector in use (rfinput 2) for D1.
   The other RF input left open is not an acceptable substitute: it is a
   different RX path with its own spurs, not terminated, and the 100 kW
   station 5 km away couples into it.
2. **Usual operating settings**, confirmed or corrected: 70 MHz / 2.5 MS/s /
   manual 41 dB; 433 MHz / 2.5 MS/s / manual 70 dB; 2.4 GHz / 30.72 MS/s /
   AGC (boot defaults); any other band in use.
3. **A 3-hour window** in which nothing else changes the RX settings (DATV,
   other users), and the cut order of section 5 if time runs short.

## 5. Segments (all development, except D7 sealed)

Gains are fixed per band: FM band 65 dB (clean on 2026-11-09), 433 MHz
70 dB, 70 MHz 41 dB (the owner's), other bands the first of 70, 65, 60,
55 dB whose 10 s IQ has no full-scale sample, checked on the antenna before
D1 (never on the load). The only exceptions are D1's extra 71 dB FM run
(the evaluation gain) and D2's 60 and 55 dB gain series. The classifier service is restarted before D1,
before D2, before D3 and before D4 (observation recorded before and after
each), and at the end. IQ recordings (10 s, 100 MB at 2.5 MS/s) are taken
and downloaded only between captures; at 30.72 MS/s the 256 MB recording
buffer holds about 2 s, so IQ there is 1 s.

| Seg | Content | Length | What it gives |
|---|---|---|---|
| D0 | tool check: 10 frames, then a 60 s capture with one rate change (2.5 to 30.72 MS/s) | 3 min | settings, T1 event placement on real data |
| D1 | 50 ohm load: FM band at 65 and 71 dB, 433 MHz at 70 dB, 70 MHz at 41 dB, 2.4 GHz manual and AGC; each at 2.5 MS/s, FM and 2.4 GHz also at 30.72 MS/s | 90 s each, about 15 min | receiver-only hot bins per span and gain, split into narrow runs (spurs: a hot run at most 10 kHz wide) and broad runs (wider; passband shape, M3) |
| D2 | KIGL at +625 kHz (LO 92.675 MHz), KIGL on the LO (93.3 MHz), KAMO at +625 kHz (LO 93.675 MHz), 4 min each; then at LO 92.675 MHz a gain series 65, 60, 55 dB, 2 min each; IQ before and after | 20 min | clean positives; each narrow line classed as on air (fixed RF), LO-locked, carrier-locked (fixed offset from KIGL), image (moves as the mirror) or nonlinear (falls faster than KIGL with gain) |
| D3 | revisit sequence, one capture, 3 min dwells: KIGL, 433, KIGL, 70, KIGL, 150, KIGL, 300, KAMO, 900, KIGL, 2400 (2.5 MS/s), KIGL, 96.45 MHz at 30.72 MS/s, KIGL, 433.3, KIGL (MHz) | about 51 min | positive retention as the retune history grows, for two stations, with seven LO clusters at 2.5 MS/s so F2 sees enough trials, and one cross-span step before the last three dwells |
| D4 | cross-span order test, one capture, 3 min dwells: 2.4 GHz at 30.72 MS/s, 96.45 MHz at 30.72 MS/s, KIGL at 2.5 MS/s, 433 MHz, KIGL | 15 min | M2 in the 2026-11-09 order, including the suspected boot learn |
| D5 | AGC at 2.4 GHz / 30.72 MS/s, SlowAttack, then KIGL at 2.5 MS/s | 10 + 3 min | M4: repeated learns at one LO, development counterpart of S4 |
| D6 | 433 MHz at LO 433.0 and 433.3 MHz, separate captures, IQ between | 2 x 4 min | on-air versus LO-locked narrow lines at 433 MHz (ground truth only) |
| D7 | **sealed confirmatory block**: a revisit sequence in a different order, KAMO first: KAMO, 2400, KAMO, 70, KIGL, 433, KAMO, 300, KIGL (2.5 MS/s, 3 min dwells) | 27 min | hashed at capture and not opened until the candidate choice is written (section 6) |

Ground truth: RDS from a 10 s IQ before and after D2, D3 and D7 (KIGL PI
0x25C7, KAMO PI 0x1146). Every positive dwell declares all identified
channels in its span (KIGL 93.2-93.4 MHz, KAMO 94.2-94.4 MHz); an FM window
on either counts, reported per station. Every retune is noted with its wall
time and appears in the settings log and events file.

Time: about 2 h 30 min of capture (D0-D7: 3 + 15 + 20 + 51 + 15 + 13 + 8 +
27 min) plus restarts, gain checks and IQ downloads, so 3 hours. Cut order if short: D6, then D5, then the second half of D4.

Storage: captures at 1 frame/s go into the repository compressed; the 30.72
MS/s stretches (D1 parts, D3's 96.45 MHz dwell, D4, D5) make large files and
stay on the host with their hashes, as S4 did.

## 6. Analysis (pre-registered)

**Definitions.** The *D1 narrow set at a span* is the union of D1's narrow
runs over all its LOs and gains at that span (D1 does not cover every D3 LO;
a receiver spur is expected at fixed bins whatever the LO). The *D chains*
scored are D3, D4 and D5 (each one capture with retunes) and D1 followed by
D2. For a dwell: the *decisions* are cycles with state `ok`,
starting `learn_s` plus one cycle after that dwell's retune event, with the
dwell's config generation. *Recognised* = FM Broadcast with the window
overlapping a declared channel. *Retention* of a dwell = recognised fraction
in the chained replay divided by the recognised fraction in a cold replay of
the same dwell's frames under the same candidate (this cancels programme
content and narrow-line competition), with a 30 s block-bootstrap interval.
Masks are scored without the DC +/-2 bins.

**Development phase** (D0-D6 only; D7 stays sealed):

1. T3 acceptance: F0 only, a byte comparison of observation streams against
   f87a3c46 on the 2026-11-09 data, without any scoring; no candidate is run
   on 2026-11-09 data before step 5. Then replay every D capture and
   the D-chains under F0 and each grid point of F1a-F4, with the piecewise
   clock and, for single-rate captures, also with `-F`. A ranking that
   differs between the two clocks is not established.
2. **Validity check.** D3 is diagnostic only if F0's retention is at most
   0.5 in each of the two KIGL dwells after the 96.45 MHz (30.72 MS/s)
   dwell. Otherwise the choice waits for a
   further development capture with more cross-span steps.
3. **Attribution.** Every bin masked under F0 at the end of D3, D4 and D5
   is attributed to M1 (hits at different LO clusters, not in D1's narrow
   set), M2 (hits at different spans), M3 (in a broad D1 run), M4 (hits at
   one LO cluster) or a true spur (D1 narrow set or a D2 receiver artefact
   class).
4. **Criteria for the choice**, all required:
   * retention of every KIGL and KAMO dwell after the first is at least
     0.8 in D3;
   * mask over-reach: non-DC masked bins at the end of each D chain are at
     most the D1 narrow set plus the D2 receiver-artefact bins at that
     span, plus 41 bins (1 %);
   * recall of the D1 narrow set (only where it has 5 or more non-DC bins
     at that span) no lower than F0's;
   * allocation-implausible labels (FM Broadcast or DAB+ at 433 MHz and
     2.4 GHz) in the D3/D4 chains, scored by window, no more than F0's; CW /
     Beacon labels there are reported separately and stay unverified;
   * the existing suites pass (`app/classifier` tests, `rf_compare.py`
     2026-11-08 fixtures).
5. Write the chosen candidate, its parameters, the binary hash and the
   reasons into the repository and commit them **before** step 6.

**Confirmatory phase**, once, after that commit:

6. Unseal D7 and replay it under F0 and the choice: retention of every
   positive dwell after the first at least 0.8, and the same over-reach
   criterion. This is the clean test: D7 was never seen. D7 never changes
   span, so it is diagnostic only if F0's retention in at least one D7
   dwell is at most 0.5; otherwise it is a no-regression check only, and
   the report says so.
7. Replay the 2026-11-09 evaluation data under F0 and the choice. The
   primary chain is the live order P0, S1, S1b, S2, S3, S4, S5, S1c (needs
   T1 for S4), scoring only evaluation segments: KIGL retention on S1c, and
   allocation-implausible labels on S2, S4 and S5. Pass: S1c retention at
   least 0.8 under the choice; this is diagnostic only if F0's S1c retention
   is at most 0.5. The cold single-capture replays cannot separate the
   candidates (one learn, no spur counts) and serve only as a no-regression
   check. Disclosure: S1c and the S2 chain motivated the hypotheses, so this
   step confirms on data that shaped the question; step 6 is the
   independent test. The chain also starts from an empty spur map, not the
   98 bins the live daemon held, because that history was never captured.
   Labels without an `--accept` entry are unverified.
8. Report every number, both phases, window by window, whatever the result.
   A candidate that fails is not retuned on D7 or on the evaluation data.

## 7. Later, separately: live side-by-side

Not in this session (the candidate does not exist yet). After the
confirmatory phase, with the owner's approval: build the chosen variant as a
full ARM daemon (T5), check it replays identically to its replay build, and
run it from RAM as `-T state/classifier_test/` beside production. Caveats:
restart production and start the test instance in the same minute so both
begin with empty spur maps; freeze console relearns (a relearn reaches only
production); watch CPU and `/waterfall` frame gaps with an abort threshold;
afterwards clear every retained `state/classifier_test/` topic and confirm
with a subscribe. Nothing is written to JFFS2; a reboot removes it.

## 8. Rules carried over

No transmission, reboot, reflash, FPGA, template, threshold or gate change.
The owner's settings are recorded before and restored after, and `/api` is
compared field by field. The production classifier changes only through a
separate deployment decision after section 6.
