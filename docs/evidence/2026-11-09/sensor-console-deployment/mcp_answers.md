# Host AI client answers through tezuka-sensor (Claude Code 2.1.319 on radiomancer, 2026-11-09)

Each answer was produced by `claude -p` with only the two tezuka-sensor tools allowed; the tool calls listed are the ones the client actually made (extracted from the saved stream-json transcripts, kept outside the repository at `tezuka-live-audit/deploy-0b423eb/mcp-evidence/q*.stream.jsonl`).

## Question 1: What is the radio observing now, and how fresh is that information?

Tool calls made: `mcp__tezuka-sensor__get_sensor_snapshot` {}; `mcp__tezuka-sensor__snapshot_fpga_waterfall` {"max_frames": 4, "timeout_s": 15}
Turns: 4, duration 24 s, model ['claude-fable-5-1']

Answer (verbatim):

> The radio is parked on the FM broadcast band and the classifier is locked onto an FM station. Everything in the snapshot is live and fresh except two minor classifier fields, noted below.
> 
> **Receiver settings** (schema tezuka.mcp.sensor_snapshot/1, all fields status fresh, ages 1.6 to 2.0 s, 3 live messages each after the retained replay):
> 
> | Setting | Value |
> |---|---|
> | Center frequency | 96.450 MHz |
> | Sample rate / span | 30.72 MS/s, 30.72 MHz span (81.1 to 111.8 MHz) |
> | RF bandwidth | 20 MHz |
> | Gain | 71 dB, manual |
> | Input / antenna | rfinput 2, A_BALANCED |
> | Sweep | off (activate 0, engaged 0) |
> 
> The sensor reports itself as signalsdrpro, firmware tezuka-v0.3.24-34-g6e3a, uptime about 12.8 hours.
> 
> **Classifier observation** (schema tezuka.classifier.observation/1, seq 390, status fresh, age 0.73 s, 7 live messages on this connection, not best_effort, 0 rejected or out-of-order):
> - Decision: label "FM Broadcast", template 1, accepted, score 0.993 against a 0.6 threshold, SNR 12 dB, prominence 4.7 dB. Hysteresis is locked and held on template 1.
> - The matched window sits at bin offset 1734 with width 53 bins, so roughly 94.1 MHz and 400 kHz wide. The feature peak is at bin 1629, about 93.3 MHz.
> - Temporal: 16 history samples, drift near zero, occupancy heuristic 100 percent.
> - Background baseline learned 400 s ago over 7999 frames, 5 masked bins, templates file ok (version 3, 3 templates, power-v1 semantics).
> - The producer's own timestamp is 1794223932.786 from CLOCK_REALTIME. Whether that clock is disciplined was not observed by the daemon.
> 
> **Live FPGA waterfall** (schema tezuka.mcp.waterfall_snapshot/1, 4 frames in 0.25 s, context verified unchanged before and after, sweep state fresh and off, support ok). Noise floor estimate 63.5 dB on the producer's relative scale, not dBm. Four segments above the 10 dB threshold:
> 
> | Peak | Width | Over floor |
> |---|---|---|
> | 93.300 MHz | 210 kHz | 32.6 dB |
> | 101.903 MHz | 143 kHz | 17.2 dB |
> | 90.510 MHz | 23 kHz | 21.0 dB |
> | 94.290 MHz | 120 kHz | 14.2 dB |
> 
> The strongest carrier at 93.3 MHz matches the classifier's feature peak. The 90.51 MHz line is only 3 bins wide, so it looks like a spur or narrowband carrier rather than a broadcast signal.
> 
> **Freshness caveats.** The MCP subscriber had been connected only 7.9 s when the snapshot was taken, so the live message counts are small but every receiver and classifier field did arrive live, not just as a retained replay. Two items are not fresh: the classifier background summary topic is retained only (no live copy seen yet, age unknown), and templates_status is unavailable, meaning never seen on this connection. Neither affects the main observation, which carries its own background and template fields live.

## Question 2: What evidence supports the current classification, and what remains uncertain?

Tool calls made: `mcp__tezuka-sensor__get_sensor_snapshot` {}; `mcp__tezuka-sensor__snapshot_fpga_waterfall` {"max_frames": 4, "threshold_over_floor_db": 10, "timeout_s": 15}
Turns: 4, duration 56 s, model ['claude-fable-5-1']

Answer (verbatim):

> **Current call: "FM Broadcast", template 1, accepted and locked.** The observation is fresh (age 0.6 s, schema tezuka.classifier.observation/1, seq 420, 9 live messages on this connection, 0 rejected). The receiver state topics are all fresh at 1.3 to 1.8 s old. The independent waterfall capture averaged 4 frames in one spectrometer context with sweep off before and after.
> 
> **Evidence for the label**
> 
> - **Score is far above threshold.** Score 0.99 against threshold 0.6, margin 0.99, hysteresis locked and held, no takeover.
> - **Gates pass.** SNR 11.3 dB over the 6 dB gate, prominence 5.5 dB over the 4 dB gate.
> - **Features look like wideband FM.** Residual bandwidth 105 kHz at -10 dB, spectral PAPR 26.3 dB, flatness 0.0155, 2 peaks, drift 0.029 bins/s over 16 samples. A stable, continuous, ~100 to 250 kHz wide carrier in the 88 to 108 MHz band is what a broadcast FM station looks like.
> - **The waterfall corroborates the feature peak.** Feature peak bin 1628 lands within 2 bins of the capture's strongest segment: bins 1611 to 1643, peak at 93.2925 MHz, 34.7 dB over the median floor, 247.5 kHz wide. 93.3 MHz is a valid FM channel allocation.
> - **Background and templates are healthy.** Baseline learned from 8615 frames, 5 masked bins, 0 spur bins, templates file status ok, sha cb6f4580, version 3, feature semantics power-v1 on both the templates and the observation.
> 
> **What remains uncertain**
> 
> - **Which emitter the decision describes.** The decision window starts at bin offset 1734, which maps to about 94.10 MHz under the published grid rule, not to the 93.29 MHz peak the features report. Its SNR of 11.3 dB also matches the weaker 94.29 MHz segment (15.8 dB over floor, 180 kHz) far better than the 34.7 dB carrier. The payload itself warns that gates describe the matched window, not the emitter. So the label may be attached to a different station than the dominant one.
> - **Only one of several candidates is reported.** The capture shows 30 segments over 10 dB above floor, including 101.92 MHz (19.1 dB, 150 kHz), 95.33 MHz (10.8 dB, 262 kHz) and a narrow 37.5 kHz spike at 90.51 MHz that does not look like broadcast FM. The classifier gives no per-emitter breakdown and scores against only 3 templates, so 0.99 means "best of three", not "excluded everything else".
> - **Configuration is not bound to frames.** The observation reports bound false: /waterfall frames carry no settings tag, so a frame near a retune can describe old settings. No retune is visible right now, but it cannot be proven from the data.
> - **Noise floor is a span-wide median over a shaped passband.** Band edges sit near 50 dB and the middle near 70 dB because the RF bandwidth is 20 MHz inside a 30.72 MHz span. The "over floor" figures are therefore approximate. Levels are relative to the producer scale, not dBm, and gain is manual at 71 dB with no calibration.
> - **Occupancy 100% is a heuristic**, a count of cycles whose residual PAPR exceeded 14 dB, not a measured duty cycle.
> - **Side topics are thin.** The background side topic is retained only (0 live messages), templates_status is unavailable, and the producer clock discipline is unobserved, so timestamps are not verified against chrony.
> 
> Next useful check would be reading the classifier source for the bin_offset definition to settle whether the window really sits at 94.1 MHz or is expressed in a different index space.

## Question 3: Summarize the current spectrum, or explain why this mode cannot be summarized.

Tool calls made: `mcp__tezuka-sensor__get_sensor_snapshot` {}; `mcp__tezuka-sensor__snapshot_fpga_waterfall` {"max_frames": 4, "threshold_over_floor_db": 10, "timeout_s": 15}
Turns: 4, duration 56 s, model ['claude-fable-5-1']

Answer (verbatim):

> The mode is summarizable. Sweep is off, the input is the AD9361 ADC path with no DDC, and the waterfall tool reported the context as supported with no reasons listed. Both tools returned fresh data.
> 
> **Receiver state** (sensor snapshot schema tezuka.mcp.sensor_snapshot/1, all rx fields status fresh, ages 2.1 to 2.5 s, 3 live messages each, subscriber connected for 9 s on connection epoch 1):
> 
> | Setting | Value |
> |---|---|
> | Center | 96.450 MHz |
> | Span / sample rate | 30.72 MHz |
> | RF bandwidth | 20 MHz |
> | Gain | 71 dB, manual |
> | Input | rfinput 2, A_BALANCED |
> | Sweep activate / engaged | 0 / 0 |
> 
> **Spectrum** (waterfall schema tezuka.mcp.waterfall_snapshot/1, 4 of 4 frames averaged in 0.23 s, FFT 4096, Average mode with 375 integrations, sweep flags fresh and unchanged before and after collection). Levels are dB on the producer's scale, not dBm.
> 
> | Measure | Value |
> |---|---|
> | Noise floor (median) | 54.6 dB |
> | Grid | 81.10 to 111.80 MHz, 7.5 kHz per bin |
> | Strongest peak | 93.300 MHz at 96.4 dB, 41.9 dB over floor |
> 
> Three segments exceeded the 10 dB over floor threshold:
> 
> - **90.42 to 95.99 MHz, peak 93.300 MHz.** This is a 5.6 MHz wide merged segment, not one emitter. It contains the dominant FM carrier at 93.3 MHz, a second carrier near 94.1 to 94.3 MHz at about 77.5 dB, a third near 90.5 MHz at about 84 dB, and a flat pedestal around 70 dB spanning the whole range. A pedestal 15 dB above the floor centred on a 42 dB over floor carrier at 71 dB manual gain looks like receiver-side spreading from the strong 93.3 MHz station rather than real occupancy. That is my inference, not a tool output.
> - **101.82 to 101.98 MHz, peak 101.8875 MHz,** 81.9 dB, 27.3 dB over floor, 157.5 kHz wide. Width and placement are consistent with an FM broadcast channel.
> - **108.499 to 108.521 MHz, peak 108.510 MHz,** 73.2 dB, 18.7 dB over floor, 22.5 kHz wide. Narrowband, just above the FM band top. The band allocation would suggest VHF aviation navigation, but the tools do not identify it.
> 
> Several bumps near 88.1, 98.1, 99.3, 103.4 and 106.3 MHz sit 5 to 9 dB above the floor and stayed under the threshold. The band edges below about 86.5 MHz and above about 106.5 MHz read 44 to 48 dB because the 20 MHz RF filter rolls them off, so the median floor mixes in-band and filtered bins.
> 
> **Classifier** (schema tezuka.classifier.observation/1, seq 479, status fresh, age 0.48 s, 9 live messages from the current connection, 0 rejected, templates version 3 with 3 templates, sha256 starting cb6f4580): state ok, label **FM Broadcast**, template 1, score 0.994, SNR 11.6 dB, prominence 5.4 dB, lock held. Its feature peak at bin 1628 lands on 93.31 MHz by the grid rule, matching the waterfall peak. Measured bandwidth at minus 10 dB was 142.5 kHz, spectral PAPR 24.7 dB, drift 0.009 bins per second, occupancy heuristic 100 percent. Background is learned from 9830 frames, 491 s old, 5 masked bins.
> 
> **Caveats the tools flagged:**
> 
> - The classifier config is marked not bound. Frames carry no configuration tag, so a frame near a retune could describe earlier settings. No retune was in progress here.
> - The classifier background topic is retained only, with 0 live messages. The templates status topic is unavailable.
> - Producer clock discipline is null. The daemon did not observe it, and it suggests checking chronyc on the sensor.
> - The step word in float 0 cycled 2 through 5 across the frames. The tool notes this free-runs on this firmware with sweep off and does not change the context.
