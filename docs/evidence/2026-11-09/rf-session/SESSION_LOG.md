# RF capture session 2026-11-09 (written before the session runs)

Session: 2026-11-09, from about 13:06 UTC (07:06 CST), operator: Claude Code
acting on the owner's written instruction of 2026-11-09 (owner Chase Valentine,
not present during the session). Observer: none.
Sensor: 192.168.50.25 (signalsdrpro.local), firmware tezuka-v0.3.24-34-g6e3a,
maia-httpd 0.6.0 (v0.3.24-21-g49403b9), maia-hdl 0.6.1, boot mode SD.
Idle: the owner could not confirm; pluto_stream and zynqrx (DATV) run as
always and did not change any RX setting during P0 (empty settings log); sweep
off; no cmd/# traffic seen. Classifier: deployed f87a3c46...; templates
cb6f4580360c... (3 templates, unchanged); thresholds and gates unchanged.
Capture host: radiomancer, tool capture_waterfall.py at the branch head.
Spectrometer: the owner's current setting is kept for every segment (Average,
4096 points, 610 integrations); it is never changed in this session.
Settings before the session (owner): LO 70 MHz, 2.5 MS/s, RF bw 2.5 MHz,
manual 41 dB, rfinput 2 (api_owner_before.json). They are restored at the end.
The reboot recorded on 2026-11-09 at 05:47 CST restored the 2.4 GHz defaults;
the earlier FM demonstration settings (96.45 MHz) were not restored, and the
owner then set 70 MHz.

## Segments (plan section 3)

| Segment | Setting | Length | On air / ground truth |
|---|---|---|---|
| S0 | LO 92674998, 2.5 MS/s, bw 2.5 MHz, manual 71 dB | 10 frames | tool check, discarded |
| S1 | same as S0 (KIGL 93.3 MHz at +625 kHz) | 10 min | KIGL 93.3, verified (section below); 10 s IQ mid-segment for RDS |
| S1b | LO 93.3 MHz (KIGL on the DC spur), same rest | 5 min | KIGL 93.3; 10 s IQ for RDS |
| S2 | LO 433 MHz, 2.5 MS/s, bw 2.5 MHz, manual 70 dB (the 433 MHz setting of the 2026-11-08 captures) | 10 min | unknown ISM traffic; no ground truth |
| S3 | LO 70 MHz, 2.5 MS/s, bw 2.5 MHz, manual 41 dB (the owner's setting today) | 10 min | unknown; no ground truth |
| S4 | LO 2.4 GHz, 30.72 MS/s, bw 18 MHz, SlowAttack AGC (the boot defaults, as operated after the 05:47 CST reboot) | 10 min | unknown 2.4 GHz ISM; no ground truth; doubles as the AGC segment S6 |
| S5 | start at the S2 setting; LO only: 70 MHz at +3 min, 2.4 GHz at +6 min, 433 MHz at +9 min; gain stays manual 70 dB | 12 min | retune sequence for the mask mechanism |

## Trusted positive

KIGL 93.3 MHz, "93.3 The Eagle", classic rock, FCC facility 35014, licensed
to Seligman MO, 100 kW ERP, transmitter about 36.468 N 94.174 W (about 5 km
from locator EM26wk). Identified in this session by RDS decoded from the
sensor's own IQ (PI 0x25C7 back-calculates to KIGL; PS 93.3 The Eagle; PTY
classic rock; 110 of 110 groups in 10 s at 12:53:24 UTC) and by the
published listing. Limits: the RDS decode uses the same antenna and front end
(IQ demodulation of the station's content, independent of the spectrum shape
and of the classifier) and is not a second receiver, which the plan prefers.

## Assignment (fixed now, before any result of S0-S5 is read)

* P0 (the short positive check, already read): development.
* S0: discarded (tool check).
* S1, S1b, S2, S3, S4, S5: evaluation. Not used to choose, fit or tune anything.

## Addendum written 2026-11-09 ~13:13 UTC, during S1, before any S1-S5 result was read

Finding from P0 (development data): at manual 71 dB on the KIGL setting the
ADC reaches full scale intermittently (0.14 % of the P0 IQ samples at |2047|
or more; the IQ floor in 0.1 s slices varies by about 14 dB), the waterfall
floor jumps by 4 to 12 dB in about one frame in ten, and the IQ recorded at
the same time shows the same jumps, so they are in the samples, not a
waterfall artifact. state/rx/overload stayed 0 (it is polled every 2 s).

Added segment, declared now: S1c, the S1 setting with manual gain lowered to
the highest value at which a 10 s IQ recording shows no full-scale sample
(gain chosen from IQ recordings only, never from classifier output), 10 min,
evaluation. It runs after S5. S1 and S1b stay as operated (71 dB, the owner's
FM demonstration gain) and are reported with the clipping noted.
