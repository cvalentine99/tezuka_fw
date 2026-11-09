# /mnt/jffs2/autorun.sh -- sourced by /etc/init.d/S98autostart at every boot.
#
# Sensor console deployment of 2026-11-09, tezuka_fw revision e38b340f20ec56de4e4cf65e0349a7cff2be086b
# (branch worktree-classifier-mcp-foundation): the classifier daemon, the
# MQTT API controller and the dashboard were built on the host and installed
# into the ramdisk root, which comes back stock at every boot. This
# re-installs them from /mnt/jffs2/deploy-0b423eb once per boot and restarts
# the two affected services once each. Nothing else is touched: templates,
# bandplan, boot artifacts and the radio settings stay as they are. The
# earlier owner logic in autorun.sh.disabled (Valentine RF dashboard copy)
# stays disabled and untouched.
#
# Rollback: delete this file and /mnt/jffs2/deploy-0b423eb; the stock files
# return at the next boot. The host keeps the originals and restore.sh.
(
  D=/mnt/jffs2/deploy-0b423eb
  [ -d "$D" ] || exit 0
  cd "$D" || exit 0
  if ! sha256sum -c -s SHA256SUMS 2>/dev/null; then
    echo "autorun: $D failed its hash check, stock files left in place"
    exit 0
  fi
  rc=0
  rk=0
  if ! cmp -s classifier /usr/bin/classifier; then
    cp classifier /usr/bin/classifier.new && chmod 755 /usr/bin/classifier.new \
      && mv -f /usr/bin/classifier.new /usr/bin/classifier && rc=1
  fi
  if ! cmp -s api_controller.sh /root/api_controller.sh; then
    cp api_controller.sh /root/api_controller.sh.new && chmod 755 /root/api_controller.sh.new \
      && mv -f /root/api_controller.sh.new /root/api_controller.sh && rk=1
  fi
  if xz -dc index.html.xz > /root/index.html.new 2>/dev/null; then
    chmod 644 /root/index.html.new && mv -f /root/index.html.new /root/index.html
  else
    rm -f /root/index.html.new
  fi
  if [ "$rc" = 1 ]; then
    /etc/init.d/S61classifier restart
  fi
  if [ "$rk" = 1 ]; then
    pids=$(pgrep -f '^/bin/bash /root/api_controller.sh')
    subs=$(pgrep -f '^/usr/bin/mosquitto_sub -v -i tezuka_sub')
    pubs=$(pgrep -f '^/usr/bin/mqtt_pubd -i tezuka_pub')
    [ -n "$pids$subs$pubs" ] && kill $pids $subs $pubs 2>/dev/null
    n=0
    while [ $n -lt 5 ] && pgrep -f '^/bin/bash /root/api_controller.sh' >/dev/null; do
      sleep 1; n=$((n + 1))
    done
    setsid /root/api_controller.sh </dev/null >/dev/null 2>&1 &
  fi
  echo "autorun: deployment in $D applied (classifier restarted=$rc, controller restarted=$rk)"
)
