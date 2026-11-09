#!/bin/sh
# One-shot live install on the radio (run once over ssh after the files were
# staged into /mnt/jffs2/deploy-0b423eb and hash-checked). Same actions as
# autorun.sh, with the process identities printed for the deployment record.
D=/mnt/jffs2/deploy-0b423eb
cd "$D" || exit 1
sha256sum -c SHA256SUMS || { echo "STAGED FILES FAIL HASH CHECK, ABORTING"; exit 1; }
echo "== old processes"
pgrep -fl '^/usr/bin/classifier'; pgrep -fl '^/bin/bash /root/api_controller.sh'; pgrep -fl '^/usr/bin/mosquitto_sub -v -i tezuka_sub'; pgrep -fl '^/usr/bin/mqtt_pubd'
echo "== installing files"
cp classifier /usr/bin/classifier.new && chmod 755 /usr/bin/classifier.new && mv -f /usr/bin/classifier.new /usr/bin/classifier && echo "classifier installed"
cp api_controller.sh /root/api_controller.sh.new && chmod 755 /root/api_controller.sh.new && mv -f /root/api_controller.sh.new /root/api_controller.sh && echo "api_controller.sh installed"
xz -dc index.html.xz > /root/index.html.new && chmod 644 /root/index.html.new && mv -f /root/index.html.new /root/index.html && echo "index.html installed"
sha256sum /usr/bin/classifier /root/api_controller.sh /root/index.html
echo "== restarting classifier"
/etc/init.d/S61classifier restart
sleep 1
echo "== restarting api controller"
pids=$(pgrep -f '^/bin/bash /root/api_controller.sh'); subs=$(pgrep -f '^/usr/bin/mosquitto_sub -v -i tezuka_sub'); pubs=$(pgrep -f '^/usr/bin/mqtt_pubd -i tezuka_pub')
echo "killing: $pids $subs $pubs"
[ -n "$pids$subs$pubs" ] && kill $pids $subs $pubs 2>/dev/null
n=0; while [ $n -lt 5 ] && pgrep -f '^/bin/bash /root/api_controller.sh' >/dev/null; do sleep 1; n=$((n + 1)); done
echo "remaining controller pids after ${n}s: $(pgrep -f '^/bin/bash /root/api_controller.sh' | tr '\n' ' ')"
setsid /root/api_controller.sh </dev/null >/dev/null 2>&1 &
sleep 3
echo "== new processes"
for p in $(pgrep -f '^/usr/bin/classifier'); do echo "classifier pid $p start $(cut -d' ' -f22 /proc/$p/stat) exe $(sha256sum /proc/$p/exe | cut -c1-16) cmdline: $(tr '\0' ' ' < /proc/$p/cmdline)"; done
echo "pidfile: $(cat /var/run/classifier.pid)"
pgrep -fl '^/bin/bash /root/api_controller.sh'; pgrep -fl '^/usr/bin/mosquitto_sub -v -i tezuka_sub'; pgrep -fl '^/usr/bin/mqtt_pubd'
echo "controller main pids: $(pgrep -f '^/bin/bash /root/api_controller.sh' | wc -l) bash, $(pgrep -f '^/usr/bin/mosquitto_sub -v -i tezuka_sub' | wc -l) cmd subscriber(s), $(pgrep -f '^/usr/bin/mqtt_pubd' | wc -l) publisher(s)"
