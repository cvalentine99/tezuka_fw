# Sensor console deployment record, 2026-11-09

Deployment of the classifier, the MQTT API controller and the generated
dashboard from branch `worktree-classifier-mcp-foundation` onto the SignalSDR
Pro test sensor, plus the host-side MCP server for the operator's AI client.
Brief: CLAUDE_MO_SENSOR_CONSOLE_DEPLOYMENT.md (Valentine RF, 2026-11-09).

End states, in the brief's terms:

| State | Result |
|---|---|
| Software deployed | Yes: classifier, API controller and dashboard from revision e38b340 are installed, running and hash-verified on the sensor; boot persistence verified by a reboot at 11:47 UTC (section 13) |
| Dashboard demonstrated | Yes: live console at http://192.168.50.25/#classifier, all fields populated, controls exercised, evidence below (demonstrated in a headless Chrome on the host; see the browser note) |
| MCP demonstrated | Yes: `tezuka-sensor` registered in Claude Code on host `radiomancer`, both tools discovered and invoked, three questions answered from real tool calls |
| RF acceptance | Open: no RF acceptance session has been run; the DC-spur false matches and accumulated masking of the 2026-11-08 reviews are not fixed by this deployment |

(Persistence was reboot-unverified when the record was first written; the
owner then asked for the reboot, see section 13.)

## 1. Where it runs

| Item | Value |
|---|---|
| Console URL | http://192.168.50.25/#classifier (sidebar RF, Signal Classifier) |
| Sensor | SignalSDR Pro, hostname `signalsdrpro`, `signalsdrpro.local`, eth0 192.168.50.25 (DHCP, verified by hostname, serial and MQTT identity, not assumed), MAC 00:60:88:cf:91:62 |
| Serial | spi-norspi1.0SPI-NOR-UniqueID1040007c641100050d003000c38cbf0133 |
| Firmware on the sensor | tezuka-v0.3.24-34-g6e3a, Linux 6.12.77, Buildroot 2026.05.2, maia-httpd 0.6.0 (v0.3.24-21-g49403b9), maia-hdl 0.6.1; boots from SD (`/boot` is the vfat boot partition, mounted read-only, untouched); FPGA image untouched |
| Firmware origin | the installed `/usr/bin/classifier`, `/root/api_controller.sh` and `/root/index.html` matched byte for byte the `output/signalsdrpro/target` of the local build at fork commit 7997d95, which is an ancestor of the release; the templates equal the stock default set |
| Host and client | `radiomancer`, Claude Code 2.1.319 (`/home/cvalentine/.local/bin/claude`), MCP server in user scope (`~/.claude.json`, entry `tezuka-sensor`); Claude Desktop is installed but was not configured |
| MCP venv | `/home/cvalentine/.venvs/tezuka-mcp` (`pip install -e tools/tezuka_mcp[test]` from the release checkout): tezuka-mcp 0.1.0, mcp 2.3.0, paho-mqtt 2.2.0, websockets 17.4, Python 3.12.3 |
| Release checkout | `/home/cvalentine/tezuka-live-audit/release-0b423eb`, a clean clone detached at the deployed revision (clean at build time: 0 changed files) |
| Host-side deployment folder | `/home/cvalentine/tezuka-live-audit/deploy-0b423eb/` (artifacts, backups, logs, MCP transcripts); not in the repository |

## 2. Source revision

| Revision | Role |
|---|---|
| `0b423eb71a54c8f2bad6c70aea94bae58b4663fe` | the reviewed release named in the brief; fetched on 2026-11-09 and found to be the branch head at that time (the branch had not advanced) |
| `e38b340f20ec56de4e4cf65e0349a7cff2be086b` | final source revision: 0b423eb plus one commit, "Dashboard: state the classifier page's experimental status and score meaning", which changes `Dashboard/classifier.jsx` only (operator copy required by section 3 of the brief). The classifier binary and the controller are byte-identical between the two revisions (verified by rebuilding at e38b340) |

No merge to main, no force push. The fork's branch was fast-forwarded
0b423eb..e38b340 and this record is committed on top.

## 3. Artifacts and installed hashes (SHA-256)

Built on the host from the release checkout with the sensor's own Buildroot
SDK (`output/signalsdrpro/host`, arm-linux-gcc, hard-float, glibc 2.41) and
the locked dashboard dependencies (`npm ci`, esbuild 0.28.2). The dashboard
was built with `--brand 'Valentine RF'`, the brand the sensor already showed;
`bundle.py` leaves `MQTT_DEV_HOST` as null in the shipped page, so it
connects to the host it is served from.

| File on the sensor | Deployed hash | Size | Replaced hash (backed up) |
|---|---|---|---|
| `/usr/bin/classifier` | f87a3c467ea2795d187f7ff9c38a16f120de31c3b001dca68abf8183c88859e8 | 68824 | b2ead05ea506aa340001f92e6bdb136db53759eb3efe94143b3feb339cf765ed (30556) |
| `/root/api_controller.sh` | 4ddc8927fecdf1ad128625a0b5a17a8803c758fde838db59793f23a0cba8cad5 | 55201 | 318fbca1b8fc865d672405f643d3b098ea3118e8146924eff2b5bf655f168a17 (53331) |
| `/root/index.html` | 771fa1733e848f876fc6e7bff46e7a24536a121c776906c53c0742718fe8591b | 652114 | 21c5923ceac818cb7947acbf7a8a46076e004a33b74ea9d7234a0a85e4255173 (648906) |
| `/mnt/jffs2/deploy-0b423eb/index.html.xz` | 21a5060766655430aa1652ff5209bb8cc5eb42290f2003cac8b4f9cf7b991f1d | 161984 | new |
| `/mnt/jffs2/autorun.sh` | 8842804d3c52f3408537058b823aee09064886ace13b17a695c926dcf09aa745 | 2385 | new (none existed; `autorun.sh.disabled` untouched) |
| `/etc/init.d/S61classifier` | bedf3d270ef75fa00c7b078a2847b644110dec3ea05439a2faa1db0b2632fcaa | | identical in the release, not replaced |
| `/mnt/jffs2/classifier/templates.bin` | cb6f4580360c75b485140af34670e1d625942cb03a5dd279c509c7063c36f263 | 6376 | unchanged before, during and after (equals the stock default) |

The page served at `http://192.168.50.25/` hashes to the deployed
`index.html` value (checked with curl after the install, content-length
652114). The running classifier's `/proc/<pid>/exe` hashes to the deployed
binary. Hash lists: `sensor-console-deployment/artifact_hashes.txt`
(artifacts) and `sensor-console-deployment/backup_hashes.sha256` (what was
replaced, as backed up).

Pre-install checks on the binary: ELF32 ARM, EABI5 hard-float, VFPv3,
interpreter `/lib/ld-linux-armhf.so.3`, needs `libwebsockets.so.21`,
`libmosquitto.so.1`, glibc symbols up to 2.38; the sensor has glibc 2.41,
libwebsockets.so.21 and libmosquitto.so.2.1.2 (soname .1). Under qemu-user
with the SDK sysroot, `classifier -V` accepted a copy of the installed
template set (valid, version 3, 3 templates, sha cb6f4580...) and rejected a
random file (bad_magic) and a truncated header (bad_size), exit 2 both.

## 4. What changed on the sensor, and when

| Time (UTC, 2026-11-09) | Action |
|---|---|
| 10:59 to 11:05 | backup of every file to be replaced or edited, pulled to the host with hashes (`backup-20261109T105903Z/`) and the pre-deployment settings snapshot |
| 11:24 | artifacts staged into `/mnt/jffs2/deploy-0b423eb/` and hash-checked there |
| 11:25:32 to 11:25:37 | install: atomic rename of the three files, `S61classifier restart` (classifier pid 1270 to 3877), controller tree (pids 1284, 1410, 1411, 1412 and its `mqtt_pubd` 1408) killed and `/root/api_controller.sh` relaunched detached the way `S95bgcript` starts it (new tree 3899, 3960 mqtt_pubd, 3962, 3963 mosquitto_sub `tezuka_sub`, 3964); one controller, one command subscriber, one publisher confirmed |
| 11:27 | `/mnt/jffs2/autorun.sh` written (persistence) |
| 11:29 | the retained sweep-centre topic restored (see deviation below) |
| 11:35 | two deliberately invalid template uploads (acceptance check), both rejected |
| 11:39 to 11:41 | console controls check: classifier off, on (pid 3877 to 21843), Relearn background |

Services restarted: `classifier` (twice in total: once by the install, once
by the off/on control check) and `api_controller.sh` (once). mosquitto,
maia-httpd and everything else kept running; the broker was never stopped.
Nothing was transmitted, no playback, no reboot, no boot artifact changed,
no retune, no capture session.

Radio settings before and after (from `/api`): every block identical
(`ad9361`, `ddc`, `spectrometer`, `recorder`, `recording_metadata`,
`geolocation`, `versions`). The sensor stayed at RX LO 96.45 MHz, 30.72 MS/s
sampling and span, 20 MHz RF bandwidth, gain 71 dB manual, rfinput 2, sweep
off, DDC off, spectrometer AD9361 input, 4096-point FFT, Average mode, 375
integrations, 20 frames/s. Gain mode is manual throughout, so there were no
AGC changes to explain.

Deviation observed and corrected: the retained topic `state/rx/sweep/frequency`
went from 96450000 to 280000000 when the controller restarted. The
controller initialises `/tmp/sweep_frequency` at startup from a sysfs read
that happens before the IIO device path is resolved, so it falls back to its
280 MHz default; the old instance had 96450000 only because the owner had
tuned through it. This is pre-existing controller behaviour (the same thing
happens at every boot) and not part of the deployed change; the LO itself
never moved. The file was rewritten to 96450000 at 11:29 UTC, which the
controller republished; `/tmp/sweep_span` and `/tmp/sweep_on` were unchanged.
A software restart of this controller therefore does alter one retained
sweep topic; worth fixing in the controller separately.

Side effect of the off/on control check: the controller's `cmd/classifier/enabled`
handler writes the U-Boot variable `classifier_enabled` (0 then 1). It ended
at 1, its pre-deployment value.

## 5. Persistence (configured; verified by reboot, section 13)

`/root` and `/usr` are the ramdisk root, so the stock files return at every
boot. `/etc/init.d/S98autostart` sources `/mnt/jffs2/autorun.sh` if present;
that file now installs the staged copies from `/mnt/jffs2/deploy-0b423eb/`
after a `sha256sum -c` of `SHA256SUMS`, replaces a file only when it differs
(`cmp`), decompresses the dashboard, and restarts each affected service at
most once (`S61classifier restart`; controller tree killed with anchored
`pgrep` patterns and relaunched detached). It is idempotent: run again
against the installed files it replaces nothing and restarts nothing (checked
live). The previous owner logic `autorun.sh.disabled` and
`valentine-rf-index.html` were left untouched. The milestone itself did not
reboot; the owner asked for a verification reboot afterwards, recorded in
section 13. JFFS2 after the deployment: 700 KB used, 196 KB available of
896 KB.

Copy of the script: `sensor-console-deployment/autorun.sh`. The one-shot
install script used live: `sensor-console-deployment/install_live.sh`, with
its output in `install_live.log`.

## 6. Rollback

Host backup: `/home/cvalentine/tezuka-live-audit/deploy-0b423eb/backup-20261109T105903Z/`
holds the replaced files (`usr/bin/classifier`, `root/api_controller.sh`,
`root/index.html`), the startup files read (`etc/init.d/S61classifier`,
`S95bgcript`, `S98autostart`), `mnt/jffs2/autorun.sh.disabled`,
`mnt/jffs2/valentine-rf-index.html`, the templates, `hashes.sha256`, the
before/after/final settings snapshots, and `restore.sh`. The previous
`~/.claude.json` of the host is also there (`host-claude.json.before`).

`restore.sh` (copy in `sensor-console-deployment/restore.sh`) verifies the
local hashes, pushes the three original files back over ssh+cat, removes
`/mnt/jffs2/autorun.sh` and `/mnt/jffs2/deploy-0b423eb`, restarts the
classifier and the controller, and checks the restored hashes:

    SSHPASS=<root password> ./restore.sh 192.168.50.25

Reboot-only rollback: delete `/mnt/jffs2/autorun.sh` and
`/mnt/jffs2/deploy-0b423eb`, then reboot; the ramdisk returns the firmware's
own files. The restore script was syntax-checked but not executed.

## 7. Acceptance checks (on the actual radio)

| Check | Result | Evidence |
|---|---|---|
| Console access | Pass. `http://192.168.50.25/#classifier` serves the deployed page (hash above), title "Valentine RF Dashboard", Signal Classifier page with live spectrum, Enabled control, Decision panel, Reference match graph, Classifications, Signal Features, editable Band Plan (17 entries) | `console_live.png`, `console_demo.json` |
| Live data | Pass. Spectrum websocket pill "connected" and MQTT-fed fields populated at the same time; the MQTT pill in the top bar is separate from the spectrum pill | `console_live.png` |
| Provenance | Pass. Observation #862 v1; Templates cb6f4580360c... (3 tpl, ok); Grid 4096 pt, 7.5 kHz/bin; Config binding unbound (gen 0) with the daemon's reason printed; Publish errors 0; Features power-v1. These agree with the MQTT observation read directly (`templates.sha256` cb6f4580..., `hz_per_bin` 7500.0, `health.publish_errors` 0) and with the installed template hash | `console_demo.json`, `mcp_get_sensor_snapshot.json` |
| Controls | Pass. Enabled toggle off: daemon gone (no pid, pidfile removed), `state/classifier/enabled` off, retained result `online:false`, Decision pill "offline". Toggle on: new pid 21843 running the deployed binary (exe hash f87a3c46...), `classifier_enabled`=1, observation seq restarts, background learned after 11 s. Relearn background: observation state "learning", `background.learned` false then true, `learn_count` 1 to 2. No retune was made | `console_demo.json`, `console_live.png` |
| Decision evidence | Pass. Decision panel shows best score, window SNR, prominence, margin, resolution, background age, persistent lines / masked spurs; Reference match plots the FM Broadcast template against the live window; the classifications list carries the latest decision | `console_live.png` |
| Template rejection | Pass. Two invalid uploads to `cmd/classifier/templates` (random bytes as base64; a non-base64 string) were rejected (`templates_status` accepted:false, bad_magic; bad_encoding), the staged file removed, `templates.bin` hash unchanged (cb6f4580...), classifier pid unchanged, and the console shows "Last template upload: rejected (bad_encoding: payload is not base64)". No templates were installed into production. Note: the second status line in the log is the retained copy of the first verdict; the bad_encoding verdict appears on the console and in the final snapshot | `template_rejection.log`, `console_live.png` |
| AI access | Pass. `claude mcp get tezuka-sensor`: Connected. Direct stdio client: tools `get_sensor_snapshot`, `snapshot_fpga_waterfall`; snapshot classifier status fresh, `best_effort` false, seq 371, live_from_current_connection true; waterfall ok, supported. No legacy best-effort fallback was in play (the old daemon had no observation topic; the new one does) | `mcp_direct_client.log`, `mcp_get_sensor_snapshot.json`, `mcp_snapshot_fpga_waterfall.json` |
| Freshness | Pass. Observation ages 0.5 to 0.7 s, receiver topics 0.4 to 2.5 s, sequence advancing across calls (371, 390, 420, 479, 862 later); the broker was not stopped. The snapshot also reports what is only retained (the `background` side topic) and what is unavailable until an upload happens (`templates_status`) | `mcp_answers.md` |
| Mode limitations | Pass. `snapshot_fpga_waterfall` reports `support.supported` true with no reasons for this mode (AD9361 input, Average, sweep off, DDC off); the observation carries the daemon's own note that sweep/DDC frames are not interpreted. The answer to question 3 states the mode is summarizable and why; the unsupported path was not exercisable without changing the receiver | `mcp_answers.md`, `mcp_snapshot_fpga_waterfall.json` |
| Deployment integrity | Pass. Served page hash = installed `/root/index.html` = artifact; running `/proc/<pid>/exe` = artifact; `/api` identical before and after; template hash identical; the one changed retained topic (sweep centre) explained and restored above | section 3 and 4, `install_live.log` |
| Persistence and recovery | Pass after the owner-requested reboot (section 13): new boot id, deployed hashes back in place, one classifier and one controller tree, telemetry resumed. Rollback files and script present on the host and hash-verified; restore not executed | section 5, 6 and 13 |

Browser note: the in-app browser pane of the Claude desktop app shows the
page but denies connections to the broker's websocket port 9001 (its console
logs the MQTT websocket failing while the same handshake from the host
succeeds), so in that pane the Enabled toggle reads OFF and the Decision
fields stay empty; the Chrome extension could not inject into any page at
the time. The demonstration above was therefore run in a headless
google-chrome 149 on `radiomancer` driven over the DevTools protocol
(screenshots and field readouts in `console_demo.json`); every value it read
was cross-checked on the sensor over ssh after each click. An ordinary
browser on the LAN has none of these restrictions.

## 8. MCP demonstration

Client configuration (merged by `claude mcp add --scope user`, the existing
`gr-mcp` and project-scoped entries preserved, the previous file backed up):

    tezuka-sensor: stdio /home/cvalentine/.venvs/tezuka-mcp/bin/tezuka-mcp
    env TEZUKA_MQTT_HOST=192.168.50.25 TEZUKA_HTTP_HOST=192.168.50.25

Discovery and invocation: `claude mcp get tezuka-sensor` reports Connected;
the direct stdio client (`mcp_direct_client.log`) lists both tools and calls
both with the results saved. The three questions were asked with `claude -p`
restricted to the two tools; the client called `get_sensor_snapshot` and
`snapshot_fpga_waterfall` for each and answered from them (`mcp_answers.md`,
verbatim). The full stream-json transcripts with the raw tool results are
kept on the host under `deploy-0b423eb/mcp-evidence/`.

Points the answers raised that are worth following up: the decision's
`bin_offset` (1734, about 94.1 MHz on the grid) does not coincide with the
features' peak bin (about 93.3 MHz), so which emitter the label describes
within a 5.6 MHz wide merged segment is not settled by the payload; and the
producer clock discipline is reported as not observed.

## 9. Operator copy added (commit e38b340)

On the Signal Classifier page: subtitle "Experimental spectral-template
matching; RF acceptance pending."; under the spectrum "Spectrum power is
shown relative to the producer's scale (maia-httpd /waterfall integrated
power, 10*log10), not calibrated dBm."; Decision subtitle and Best-score
tooltip "Match score is spectral similarity, not calibrated identification
confidence."; and, when the observation reports config.bound false, the
daemon's own reason is printed under the provenance row. No telemetry was
inserted and no label was forced.

## 10. Five-minute walkthrough

1. Open http://192.168.50.25/#classifier. The top-right MQTT pill and the
   card's CONNECTED pill should both be green; the spectrum updates live.
2. Decision card: read the pill (match / no match / learning), Best score,
   Window SNR, Prominence, Margin. Below them the provenance row: Observation
   number and schema, Templates hash and count, Grid, Config binding
   (unbound, with the reason), Publish errors, Features semantics. The score
   is spectral similarity, not identification confidence.
3. Reference match: the template (blue) against the live window (yellow).
4. Click Relearn background on a quiet moment: the pill shows "learning
   background (N s)" for about ten seconds, then returns.
5. Enabled toggle: off stops the daemon (pill "offline", the result's online
   flag false), on restarts it and relearns the background.
6. On the host: `claude mcp get tezuka-sensor` then ask Claude Code "What is
   the radio observing now, and how fresh is that information?"; it calls
   get_sensor_snapshot and snapshot_fpga_waterfall and quotes the freshness
   fields.

## 11. RF limits statement

Nothing in this deployment changes the matcher, thresholds, gates, spur or
background algorithm, templates, bandplan, receiver mode or FPGA image. The
issues recorded on 2026-11-08 stand: false matches on the DC spur and the
accumulation of masked bins over time are not fixed. The classifier remains
experimental until the RF acceptance session below has been run and
evaluated.

## 12. Next RF session (prepared, not run)

Follow `docs/evidence/2026-11-08/rf_compare/CAPTURE_SESSION_PLAN.md` with the
deployed revision and the frozen capture tools. All commands run on the host
from the release checkout at e38b340:

    cd /home/cvalentine/tezuka-live-audit/release-0b423eb
    git rev-parse HEAD      # e38b340f20ec56de4e4cf65e0349a7cff2be086b
    PY=/home/cvalentine/.venvs/tezuka-mcp/bin/python   # has websockets 17
    CAP=tools/classifier_templates/replay/capture_waterfall.py

Before the session (section 2 of the plan): record the address, the `/api`
versions block, the boot mode (SD) and the owner's confirmation that the
sensor is idle; the owner states the operating LO settings. Frame rate: the
sensor currently runs the spectrometer at 20 frames/s (375 integrations at
30.72 MS/s), so ten minutes is 12000 frames of 16384 bytes (about 196 MB per
segment). If the owner keeps that rate, budget the disk accordingly; if the
owner changes the integration count for the session, read the new rate from
`/api` (`spectrometer.output_sampling_frequency`) and recompute.

    # S0: tool and settings check, 10 frames, discarded afterwards
    $PY $CAP 192.168.50.25 10 S0_check.raw --poll 1.0 --note "S0: check capture at the first LO setting"

    # S1: trusted positive (FM station off-centre by about a quarter of the span, manual gain), 10 min
    FPS=$(curl -s http://192.168.50.25/api | $PY -c 'import json,sys; print(json.load(sys.stdin)["spectrometer"]["output_sampling_frequency"])')
    N10=$($PY -c "print(int(round(600*$FPS)))"); N5=$($PY -c "print(int(round(300*$FPS)))"); N12=$($PY -c "print(int(round(720*$FPS)))")
    $PY $CAP 192.168.50.25 $N10 S1_fm_positive.raw --poll 1.0 --note "S1: <LO>, <span>, manual <gain>; on air: <station, verified by independent receiver>; operator <who>"
    # S1b: same emission with the LO on it, 5 min
    $PY $CAP 192.168.50.25 $N5 S1b_fm_on_lo.raw --poll 1.0 --note "S1b: LO on the emission"
    # S2, S3, S4: the owner's usual LO settings, 10 min each
    $PY $CAP 192.168.50.25 $N10 S2.raw --poll 1.0 --note "S2: <setting as operated>"
    $PY $CAP 192.168.50.25 $N10 S3.raw --poll 1.0 --note "S3: <setting as operated>"
    $PY $CAP 192.168.50.25 $N10 S4.raw --poll 1.0 --note "S4: <setting as operated>"
    # S5: retune sequence S2 -> S3 -> S4 -> S2 by LO only, about 3 min apart, one capture of about 12 min
    $PY $CAP 192.168.50.25 $N12 S5_retune.raw --poll 1.0 --note "S5: retune sequence, LO only; owner notes each retune time"

After the session (section 6 of the plan): hash and `xz -9` every raw file,
copy raw, meta and events into `docs/evidence/<date>/rf_compare/raw/` (or
hash-only if too large), write the development/evaluation assignment before
looking at any result (S1, S1b, S2 to S5 are evaluation), then:

    SYSROOT=/home/cvalentine/tezuka-live-audit/tezuka_fw/output/signalsdrpro/host/arm-buildroot-linux-gnueabihf/sysroot
    $PY tools/classifier_templates/replay/rf_compare.py --out /tmp/rf \
      --fixtures tools/tezuka_mcp/tests/fixtures \
      --templates board/tezuka/common/overlay_tezuka/etc/classifier-default-templates.bin \
      --sysroot $SYSROOT --bin fixed=/home/cvalentine/tezuka-live-audit/deploy-0b423eb/artifacts/classifier \
      --recorded S1_fm_positive=raw/S1_fm_positive.raw:$FPS --expect "S1_fm_positive=FM Broadcast" \
      --recorded S2=raw/S2.raw:$FPS --recorded S5_retune=raw/S5_retune.raw:$FPS --events S5_retune=raw/S5_retune.raw.events \
      --accept "S2=confirmed_false=FM Broadcast::<basis>" \
      --only S1_fm_positive,S2,S5_retune

The `--bin fixed=` path is the deployed binary (sha f87a3c46...), so the
replay evaluates exactly what runs on the sensor. Keep the three disposition
counts (confirmed, suspected, unverified) separate. The investigation
workflow roadmap of the 2026-11-08 reviews (trusted positive first, then
measured corrections developed on development captures and evaluated once)
is unchanged by this deployment.

## 13. Reboot verification (owner-requested, 2026-11-09 11:47 UTC)

After the record above was written, the owner asked for a reboot to verify
the boot-time persistence. Before the reboot the store passed `sha256sum -c`
and `autorun.sh` had hash 8842804d...; `reboot` was issued at 11:47:39 UTC,
ssh answered again after about 20 s, and the checks were taken at 11:48:49
UTC (uptime 50 s). Snapshot: `sensor-console-deployment/reboot_verification_snapshot.txt`.

| Check | Result |
|---|---|
| New boot | boot_id a8837c6b-... before, fac5273b-adf5-464a-917e-7ee0ede16f89 after |
| Files after boot | `/usr/bin/classifier` f87a3c46..., `/root/api_controller.sh` 4ddc8927..., `/root/index.html` 771fa173..., templates cb6f4580...: the deployed hashes, not the firmware's ramdisk copies |
| Services | one classifier (pid 1498, `/proc/pid/exe` = deployed binary, pidfile 1498), one controller tree (1505; mqtt_pubd 1570; 1572; mosquitto_sub tezuka_sub 1573; 1574): no duplicates, both restarted once by autorun after their init-script start |
| Telemetry | observation seq 12 under the new boot id, templates sha cb6f4580..., publish_errors 0, waterfall connected; `state/classifier/enabled` on; `classifier_enabled`=1 |
| Served page | hash 771fa173... (the deployed dashboard) |
| Store | `/mnt/jffs2/deploy-0b423eb` passes its hash check; JFFS2 700 KB used, 196 KB available |

Effect of the reboot on the receiver: the firmware brought the radio up in
its boot defaults, not in the settings used for the demonstration. After the
reboot `/api` reports RX LO 2.4 GHz, RF bandwidth 18 MHz, gain mode
SlowAttack (AGC, 53 to 71 dB seen), rfinput 1, DDC frequency 0, recorder
IQ16bit; before the reboot it was 96.45 MHz, 20 MHz, manual 71 dB, rfinput 2.
This is the firmware's own behaviour (runtime receiver settings are not
persisted across boots), not something the deployment changed, and the
classifier accordingly reports label unknown at 2.4 GHz. The settings were
not restored, since that is a retune and outside this milestone; the owner
sets the operating LO, gain and input before the next session as section 12
already requires. The controller's sweep-centre default (280 MHz) is also
back, as it is at every boot.
