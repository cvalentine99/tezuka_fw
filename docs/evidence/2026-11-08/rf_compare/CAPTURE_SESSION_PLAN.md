# Owner-run RF capture session: protocol

Prepared for the next assignment in `TEZUKA_A9E7115_REVIEW_CLOSURE.md`
(items 3 to 5). This is the plan for a session the sensor's owner runs; it
has not been executed. Its purpose is trustworthy RF evaluation data: a
trusted positive, long stationary captures at the settings the sensor is
normally operated at, and one retune sequence for the mask mechanism, with
every tuning time, setting, frame rate, gain and capture boundary recorded.
Nothing in it changes templates, thresholds, gates or masks, and nothing is
deployed.

## 1. Ground rules

* The owner operates the sensor. The capture tooling is read-only
  (`/waterfall` subscription, `/api` GETs); it never writes a setting.
* Retunes happen only inside the retune segment (S5) and only within the
  owner's designated session window. During a stationary segment nothing is
  changed: no frequency, span, bandwidth, gain, gain mode, spectrometer or
  DDC change. The capture tool logs any change it sees, so a violated segment
  is detected, not guessed; a violated stationary segment is discarded.
* A label is established only by evidence independent of the spectrum shape
  (section 4). Spectrum shape, burst pattern or a bandplan allocation alone
  never establish a label; they can mark an accept as *suspected*.
* Every capture is assigned to **development** or **evaluation** at freeze
  time (section 6) and the assignment is never changed afterwards. Evaluation
  captures are not used to choose, fit or tune anything.
* The production classifier, its templates and the upload handler on the
  sensor stay as they are. No reboot, reflash, FPGA change or transmission is
  part of this session; a positive is received, never transmitted.

## 2. Before the session

Record in the session log (section 7):

* sensor address (DHCP may have changed it; `iio_info -S` lists it), the
  `/api` `versions` block (firmware, maia-httpd, maia-hdl), the boot mode as
  the owner knows it (the test sensor boots from SD), and the owner's
  confirmation that the sensor is otherwise idle (no sweep, no DATV, no other
  consumer changing settings);
* the LO settings the sensor is normally operated at (the owner states them;
  433 MHz with a 2.5 MHz span is the one seen so far) and, for each, the gain
  mode and gain, bandwidth, spectrometer mode and integration count;
* the trusted positive candidates (section 4) and how each will be
  verified;
* the host that runs the capture tool, with Python 3 and the `websockets`
  package, and enough disk: a frame is 16384 bytes; at 0.5966 frames/s ten
  minutes is 358 frames (5.9 MB), at 7.33 frames/s ten minutes is 4400
  frames (72 MB). Budget the raw size: `xz -9` only brought the two captures
  so far to 73 % and 79 % of their raw size (averaged linear-power floats do
  not compress well).

Check the tool against the sensor once with a short capture (10 frames) and
read back the meta file: the settings in `api` must be the ones the owner
set, `fps_api_start` must be the spectrometer's output rate.

## 3. Segments

Each segment is one `capture_waterfall.py` run; the frame count is the
segment length times the frame rate reported by `/api`
(`spectrometer.output_sampling_frequency`). Use `--note` for the segment
name and what is on air.

```bash
# host, frames, output, poll interval for the settings log, note
python3 tools/classifier_templates/replay/capture_waterfall.py <sensor-ip> <frames> <segment>.raw \
    --poll 1.0 --note "<segment>: <LO>, <span>, <gain mode/gain>; on air: <what>; operator <who>"
```

| Segment | Content | Length | Stationary | Purpose |
|---|---|---|---|---|
| S0 | check capture at the first LO setting | 10 frames | yes | tool and settings check, discarded afterwards |
| S1 | trusted positive: a real broadcast emission received by this sensor (section 4), LO set so the emission is off-centre by about a quarter of the span and clear of the DC spur, manual gain. For an FM station a 2.5 MHz span does this (400 kHz emission, 625 kHz off centre); for a DAB+ ensemble the template is 2.5 MHz wide and the ensemble 1.536 MHz, so use a span of 8 MHz or more and set the LO so the whole ensemble lies on one side of DC | 10 min | yes | the first positive recorded by this receiver; evaluation data |
| S1b | the same emission with the LO on it (emission centred on the DC spur) | 5 min | yes | companion to `fm_near_lo`: a legitimate signal at the LO must stay recognisable |
| S2, S3, S4 | the sensor's usual LO settings, one per segment, as operated (same gain mode, gain, span, integration count the owner uses) | 10 min each | yes | the negative/unknown band at the settings that matter; cold-start-to-settled on real data beyond 100 s |
| S5 | retune sequence: start at the S2 setting, retune to the S3 setting after about 3 min, to the S4 setting after another 3 min, back to S2 after another 3 min; retune by LO only, nothing else | about 12 min, one capture | no (by design) | the two-frequency spur criterion on frames that differ per frequency, with the retune times logged and the events file generated |
| S6 (optional) | S2 setting again, AGC (slow attack) instead of manual gain, if the owner ever operates with AGC | 10 min | yes | gain behaviour under AGC on a real band |

Between segments, wait for the spectrometer to show the new setting (a few
frames) before starting the next capture, so a settings change never lands
inside a stationary capture.

For S5 the owner makes each retune through the Dashboard or the HTTP API and
notes the wall-clock time; the tool's settings log records when the change
was seen (up to `--poll` seconds later) with the number of frames received
at that moment, and writes `<segment>.raw.events` with the retunes placed on
the replay clock (just before the first frame after the change was seen).
The owner's noted times and the log are both kept; a difference larger than
the poll interval is an anomaly to record. Retunes must be deliberate and
minutes apart: the log is built from `/api` polls, so a retune undone within
one poll interval is not seen at all, and a mis-set LO corrected within a
second would make the capture differ from what a daemon on the sensor heard.
If that happens, note it and redo the segment. If the websocket drops or
the feed stops (`--timeout`, default 60 s), the tool still writes the meta
and events files with an `aborted` marker; the segment is then redone, and
the aborted files are kept with the log.

## 4. Establishing the trusted positive

A positive is accepted into the evaluation set only with a label that does
not come from this receiver's spectrum. Acceptable methods, in order of
preference:

1. **Independent reception and demodulation** of the same emission at the
   same time: a second receiver (a car or kitchen FM radio, an RTL-SDR with an
   FM or DAB+ demodulator, a DAB+ receiver) tuned to the same frequency
   during S1, with the station identified by its audio, its RDS PI code or
   programme service name, or the DAB+ ensemble and service label. Record
   the method, the identification and the time.
2. **A known source**: a published station list for the location giving the
   frequency, station and transmitter, combined with reception of that
   station on an ordinary receiver on the day.

Not acceptable as the label: the classifier's own output, the spectrum shape
in the waterfall, or a bandplan allocation on its own. An allocation can be
recorded as supporting information.

Candidate positives for this receiver (2R2T AD9363 front end, 70 MHz to
6 GHz): a strong local FM broadcast station (the stock template set has an
FM Broadcast template, 400 kHz) or a DAB+ ensemble in Band III (DAB+
template, 2.5 MHz). CW / Beacon has no broadcast equivalent; a known beacon
with a published frequency and identification received independently would
qualify, otherwise that class stays without a trusted positive and the
report says so.

## 5. What is recorded, and by what

| Item | Recorded by |
|---|---|
| receiver settings before and after each segment (LO, sampling rate, RF bandwidth, gain, gain mode), spectrometer settings (FFT size, integrations, mode, output rate), DDC, versions | `capture_waterfall.py` meta: `api`, `api_after_full` |
| every change of those settings during the segment, with wall time, seconds since start, frames received and replay time | meta: `settings_log` |
| capture boundaries: tool start, first frame, last frame, tool end (unix seconds); measured frame rate and inter-arrival min/median/max | meta: `started_unix_s`, `first_frame_unix_s`, `last_frame_unix_s`, `ended_unix_s`, `measured_fps`, `frame_gap_s` |
| frame count, frame sizes, SHA-256 of the raw file | meta: `frames`, `frame_sizes`, `sha256` |
| retunes as replay events | `<segment>.raw.events` |
| what was on air, the positive's identification and method, the owner's noted retune times, anything unusual (AGC movement, a reboot, a dropped connection) | session log, by hand (section 7) |

Gain behaviour: with manual gain the log stays empty for a stationary
segment; with AGC (S6) `rx_gain` changes are logged as they are seen, which
gives the gain trajectory at the poll resolution. The classifier does not
act on gain events in replay; they are kept for interpretation.

## 6. After the session: freezing

1. Hash every `.raw`, `.meta.json` and `.events` file; compress the raw files
   with `xz -9`; copy them with their meta and events files into
   `docs/evidence/<date>/rf_compare/raw/` (or hash-only if a file is too
   large for the repository, stating where it is kept).
2. Assign each capture to development or evaluation and write the
   assignment into the report before any result is looked at. Suggested:
   S1, S1b, S2, S3, S4 and S5 are evaluation; a development set, if one is
   wanted for a later tuning pass, is captured separately on another day or
   at least as separate segments, never by splitting an evaluation capture.
3. Run the comparison with the captures as recorded fixtures, declaring the
   positive and the dispositions:

```bash
python3 tools/classifier_templates/replay/rf_compare.py --out /tmp/rf \
  --fixtures tools/tezuka_mcp/tests/fixtures \
  --templates board/tezuka/common/overlay_tezuka/etc/classifier-default-templates.bin \
  --sysroot $SYSROOT --bin fixed=app/classifier/build/arm/classifier \
  --recorded S1_fm_positive=raw/S1.raw:<fps> --expect "S1_fm_positive=FM Broadcast" \
  --recorded S2_433=raw/S2.raw:<fps> \
  --recorded S5_retune=raw/S5.raw:<fps> --events S5_retune=raw/S5.raw.events \
  --accept "S2_433=confirmed_false=FM Broadcast::<basis>" \
  --only S1_fm_positive,S2_433,S5_retune
```

   `--expect` makes every decision of that capture count as recognised or
   missed against the declared label. `--accept` records, per unexpected
   label, whether it is confirmed false (independent evidence), suspected
   false or unverified, with the basis; what has no `--accept` stays
   unverified.
4. Report per capture: decisions, recognised and missed (positives),
   confirmed / suspected / unverified unexpected accepts with scores, the
   mask coverage series for S5, and the settings log. Keep the three
   disposition counts separate everywhere, including the machine-readable
   results.

Only after this is a measured classifier correction in scope: developed on
development captures, evaluated once on the evaluation set, with dual-RX
capability and the production services untouched until a separate
deployment decision.

## 7. Session log template

```
Session: <date>, <start>-<end> local time, operator <owner>, observer <who>
Sensor: <ip>, firmware <versions block>, boot mode <sd/qspi>, idle confirmed: <yes/no>
Capture host: <host>, tool commit <sha>

Segment | start unix | end unix | LO Hz | span Hz | bw Hz | gain mode / dB | fps (api) | frames | file | sha256 | on air / notes
S0      |            |          |       |         |       |                |           |        |      |        | check only
S1      |            |          |       |         |       |                |           |        |      |        | positive: <station>, <freq>, verified by <method> at <time>
S1b     |            |          |       |         |       |                |           |        |      |        |
S2      |            |          |       |         |       |                |           |        |      |        |
S3      |            |          |       |         |       |                |           |        |      |        |
S4      |            |          |       |         |       |                |           |        |      |        |
S5      |            |          | seq   |         |       |                |           |        |      |        | retunes at <unix> -> <Hz>, <unix> -> <Hz>, <unix> -> <Hz> (owner's notes)
S6      |            |          |       |         |       |                |           |        |      |        | AGC

Anomalies: <AGC movement, reconnects, reboots, settings seen changing in a stationary segment, differences between noted and logged retune times>
Assignment: <segment> = evaluation / development (fixed before results are read)
```
