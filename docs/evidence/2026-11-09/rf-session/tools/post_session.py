"""After S5 (declared in SESSION_LOG.md): (1) 433 MHz two-LO IQ test for the
FM Broadcast accept at 433.66-434.06 MHz; (2) S1c gain chosen from IQ only:
the first of 65, 60, 55, 50, 45 dB whose 10 s IQ has no sample at full scale;
(3) S1c, 10 min, with a mid-segment IQ for RDS; (4) owner settings restored."""
import json, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import radio
from session_lib import note, settle, capture, record_iq, download_iq, seg, RAW


def full_scale_fraction(name):
    d = f"{RAW}/{name}_x"
    os.makedirs(d, exist_ok=True)
    subprocess.run(["tar", "xf", f"{RAW}/{name}.sigmf", "-C", d], check=True)
    data = [os.path.join(r, f) for r, _, fs in os.walk(d) for f in fs if f.endswith(".sigmf-data")][0]
    a = np.fromfile(data, dtype="<i2")
    return float(np.mean(np.abs(a.astype(np.int32)) >= 2047)), int(np.abs(a.astype(np.int32)).max())


note(event="post_session_start")
# (1) 433 MHz two-LO test
for lo in (433000000, 433300000):
    r = radio.tune(lo, 2500000, 2500000, 70.0, "Manual", why=f"433 two-LO IQ test at LO {lo}")
    settle({"rx_lo_frequency": lo, "sampling_frequency": 2500000, "rx_rf_bandwidth": 2500000})
    name = f"T433_lo{lo // 1000}k_iq"
    record_iq(name, f"433 MHz two-LO test, LO {lo} Hz, manual 70 dB")
    download_iq(name)
# (2) S1c gain from IQ only
chosen = None
for g in (65.0, 60.0, 55.0, 50.0, 45.0):
    radio.tune(92674998, 2500000, 2500000, g, "Manual", why=f"S1c gain search {g} dB (IQ only)")
    settle({"rx_lo_frequency": 92674998, "sampling_frequency": 2500000, "rx_rf_bandwidth": 2500000})
    name = f"S1c_gain{int(g)}_iq"
    record_iq(name, f"S1c gain search at {g} dB")
    download_iq(name)
    frac, peak = full_scale_fraction(name)
    note(event="gain_test", gain_db=g, full_scale_fraction=frac, peak_abs=peak)
    if frac == 0.0 and peak < 2047:
        chosen = g
        break
note(event="s1c_gain_chosen", gain_db=chosen)
if chosen is not None:
    KIGL = "on air: KIGL 93.3 MHz FM (verified: RDS PI 0x25C7 = KIGL, PS 93.3 The Eagle, FCC facility 35014)"
    seg("S1c_kigl_lowgain", (92674998, 2500000, 2500000, chosen, "Manual"), 600,
        f"S1c: LO 92674998 (KIGL at +625 kHz), 2.5 MS/s, bw 2.5 MHz, manual {chosen} dB (highest of 65/60/55/50/45 with no full-scale IQ sample); {KIGL}; evaluation; operator Claude Code for the owner Chase Valentine (owner instructed, not present)",
        iq=(300, "S1c_kigl_iq", f"S1c mid-segment, KIGL at +625 kHz, manual {chosen} dB"))
# (4) restore the owner's settings
own = json.load(open("/home/cvalentine/tezuka-live-audit/rf-session-20261109/api_owner_before.json"))
a = own["ad9361"]
r = radio.tune(a["rx_lo_frequency"], a["sampling_frequency"], a["rx_rf_bandwidth"], a["rx_gain"], a["rx_gain_mode"], why="restore the owner's settings")
settle({"rx_lo_frequency": a["rx_lo_frequency"], "sampling_frequency": a["sampling_frequency"], "rx_rf_bandwidth": a["rx_rf_bandwidth"]})
m = own["recording_metadata"]
radio.patch("recording/metadata", {"filename": m["filename"], "description": m["description"], "author": m["author"]}, "restore the owner's recording metadata")
after = radio.api()
note(event="owner_settings_restored", ad9361=after["ad9361"], recorder=after["recorder"], recording_metadata=after["recording_metadata"],
     matches={k: after["ad9361"][k] == v for k, v in a.items()})
note(event="post_session_end")
