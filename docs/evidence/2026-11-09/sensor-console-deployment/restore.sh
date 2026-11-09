#!/bin/sh
# Roll back the 2026-11-09 sensor console deployment (release 0b423eb) on the
# SignalSDR Pro, restoring the files backed up in this directory.
#
# Usage:  SSHPASS=<root password> ./restore.sh [sensor-address]
# (no scp on the radio: files travel through ssh + cat)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
HOST=${1:-192.168.50.25}
SSH="sshpass -e ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@$HOST"
cd "$HERE"
echo "verifying local backup hashes"; sha256sum -c --quiet hashes.sha256
put() { # put <local> <remote> <mode>
  cat "$1" | $SSH "cat > '$2.restore' && chmod $3 '$2.restore' && mv -f '$2.restore' '$2'"
}
echo "restoring files"
put usr/bin/classifier      /usr/bin/classifier      755
put root/api_controller.sh  /root/api_controller.sh  755
put root/index.html         /root/index.html         644
echo "removing the deployment's persistence (previous autorun.sh.disabled is left untouched)"
$SSH 'rm -f /mnt/jffs2/autorun.sh; rm -rf /mnt/jffs2/deploy-0b423eb; sync'
echo "restarting the classifier (S61classifier) and the MQTT controller"
$SSH '/etc/init.d/S61classifier restart >/dev/null 2>&1 || true
pids=$(pgrep -f "^/bin/bash /root/api_controller.sh" || true); subs=$(pgrep -f "^/usr/bin/mosquitto_sub -v -i tezuka_sub" || true)
[ -n "$pids$subs" ] && kill $pids $subs 2>/dev/null; sleep 1
setsid /root/api_controller.sh </dev/null >/dev/null 2>&1 &
sleep 2; pgrep -fl "^/bin/bash /root/api_controller.sh"; pgrep -fl "^/usr/bin/classifier"'
echo "verifying restored hashes on the radio"
$SSH 'sha256sum /usr/bin/classifier /root/api_controller.sh /root/index.html' | sed 's#  /#  ./#' | sha256sum -c --quiet - 2>/dev/null \
  && echo "restore complete" || { echo "WARNING: restored hashes differ from hashes.sha256, inspect manually"; exit 1; }
