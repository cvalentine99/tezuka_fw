#!/bin/bash
# Consolidated replay of all 2026-11-09 evaluation captures through the
# deployed classifier (f87a3c46...) with expectations and evidence-based
# dispositions. Each capture is replayed cold with its own events file.
set -euo pipefail
W=/home/cvalentine/tezuka_fw/.claude/worktrees/classifier-mcp-foundation
SESS=/home/cvalentine/tezuka-live-audit/rf-session-20261109
SYS=/home/cvalentine/tezuka-live-audit/tezuka_fw/output/signalsdrpro/host/arm-buildroot-linux-gnueabihf/sysroot
BIN=/home/cvalentine/tezuka-live-audit/deploy-0b423eb/artifacts/classifier
test "$(sha256sum $BIN | cut -d' ' -f1)" = f87a3c467ea2795d187f7ff9c38a16f120de31c3b001dca68abf8183c88859e8
fps() { python3 -c "import json;print(json.load(open('$SESS/raw/$1.raw.meta.json'))['fps_api_start'])"; }
R=(); for s in S1_kigl_offset S1b_kigl_on_lo S1c_kigl_lowgain S2_433 S3_70 S4_2400_agc S5_retune; do
  R+=(--recorded "$s=$SESS/raw/$s.raw:$(fps $s)" --events "$s=$SESS/raw/$s.raw.events"); done
KIGL_CW="windows on KIGL 93.3 (RDS PI 0x25C7 decoded from the segment's own mid-segment IQ) or on unidentified narrow lines in the 88-108 MHz FM broadcast allocation, where no CW beacon is published; off-channel windows not independently confirmed"
cd $W
python3 tools/classifier_templates/replay/rf_compare.py --out $SESS/replay/final --fixtures tools/tezuka_mcp/tests/fixtures \
  --templates board/tezuka/common/overlay_tezuka/etc/classifier-default-templates.bin --sysroot $SYS --bin deployed=$BIN --jobs 7 "${R[@]}" \
  --expect "S1_kigl_offset=FM Broadcast" --expect "S1b_kigl_on_lo=FM Broadcast" --expect "S1c_kigl_lowgain=FM Broadcast" \
  --accept "S1_kigl_offset=suspected_false=CW / Beacon::$KIGL_CW" \
  --accept "S1c_kigl_lowgain=suspected_false=CW / Beacon::$KIGL_CW" \
  --accept "S1b_kigl_on_lo=confirmed_false=CW / Beacon::the only CW / Beacon window of this replay (94.284-94.314 MHz) lies inside the channel of KAMO 94.3, an FM broadcast identified by RDS (PI 0x1146, PS 94.3 Nash Icon) in the S1b IQ recorded during the segment at 13:19 UTC" \
  --accept "S2_433=suspected_false=FM Broadcast::FM broadcast is allocated 88-108 MHz; a 10 s a 10 s IQ at this setting 34 min after S2 puts the window median within 0.1 dB of the span noise median (no 400 kHz emission, analysis/T433_two_lo.json), and the chained replay reproduces the accept only with the accumulated spur mask" \
  --accept "S2_433=suspected_false=DAB+::DAB+ is allocated 174-240 MHz (Band III); allocation only" \
  --accept "S5_retune=suspected_false=FM Broadcast::FM broadcast is allocated 88-108 MHz; a 10 s IQ at 433 MHz 74 s after S5 puts the live window median within 2 dB of the span noise median, its only feature a narrow on-air carrier at 433.329 MHz (analysis/T433_two_lo.json)" \
  --accept "S5_retune=suspected_false=DAB+::DAB+ is allocated 174-240 MHz (Band III); allocation only" \
  --accept "S4_2400_agc=suspected_false=DAB+::DAB+ is allocated 174-240 MHz; the replay windows (2.4007-2.4109 GHz) and the live one (2.4128-2.4153 GHz) lie in Wi-Fi channel 1 (2.401-2.423 GHz); allocation only, no IQ taken" \
  --accept "S4_2400_agc=suspected_false=FM Broadcast::FM broadcast is allocated 88-108 MHz; 2.4 GHz ISM band; no IQ taken" \
  --only S1_kigl_offset,S1b_kigl_on_lo,S1c_kigl_lowgain,S2_433,S3_70,S4_2400_agc,S5_retune
