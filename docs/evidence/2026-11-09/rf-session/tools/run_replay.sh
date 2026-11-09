#!/bin/bash
# Replay every evaluation capture of the 2026-11-09 session through the
# DEPLOYED classifier binary (f87a3c46..., the one running on the sensor) under
# qemu, with each capture's own events file, before any classifier change.
# Dispositions: S1/S1b/S1c are trusted positives (FM Broadcast expected);
# everything else stays unverified unless an --accept line gives evidence.
set -euo pipefail
W=/home/cvalentine/tezuka_fw/.claude/worktrees/classifier-mcp-foundation
SESS=/home/cvalentine/tezuka-live-audit/rf-session-20261109
SYS=/home/cvalentine/tezuka-live-audit/tezuka_fw/output/signalsdrpro/host/arm-buildroot-linux-gnueabihf/sysroot
BIN=/home/cvalentine/tezuka-live-audit/deploy-0b423eb/artifacts/classifier
test "$(sha256sum $BIN | cut -d' ' -f1)" = f87a3c467ea2795d187f7ff9c38a16f120de31c3b001dca68abf8183c88859e8
fps() { python3 -c "import json;print(json.load(open('$SESS/raw/$1.raw.meta.json'))['fps_api_start'])"; }
ARGS=(); ONLY=()
for s in "$@"; do
  ARGS+=(--recorded "$s=$SESS/raw/$s.raw:$(fps $s)" --events "$s=$SESS/raw/$s.raw.events")
  case $s in S1*|P0*) ARGS+=(--expect "$s=FM Broadcast");; esac
  ONLY+=("$s")
done
cd $W
python3 tools/classifier_templates/replay/rf_compare.py --out $SESS/replay/${OUT:-eval} --fixtures tools/tezuka_mcp/tests/fixtures \
  --templates board/tezuka/common/overlay_tezuka/etc/classifier-default-templates.bin --sysroot $SYS --bin deployed=$BIN \
  --jobs 6 "${ARGS[@]}" ${ACCEPT_ARGS:-} --only "$(IFS=,; echo "${ONLY[*]}")"
