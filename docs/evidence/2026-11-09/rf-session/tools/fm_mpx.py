"""SigMF ci16_le IQ -> FM-demodulated MPX (S16LE mono) for one station.
usage: fm_mpx.py <sigmf-data> <sample_rate> <capture_center_hz> <station_hz> <out.raw>
Prints the station's measured pilot (19 kHz) and RDS (57 kHz) levels as evidence
that this is an FM stereo broadcast with an RDS subcarrier. No audio is kept."""
import sys, json
import numpy as np
from scipy import signal

data, fs, fc, fst, out = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]), sys.argv[5]
raw = np.fromfile(data, dtype="<i2").astype(np.float32)
x = raw[0::2] + 1j * raw[1::2]
clip = float(np.mean(np.abs(raw) >= 2047))  # 12-bit samples carried in int16
off = fst - fc
n = np.arange(x.size)
x = x * np.exp(-2j * np.pi * off / fs * n)
dec = 10
taps = signal.firwin(255, 110e3, fs=fs)
y = signal.lfilter(taps, 1.0, x)[::dec]
fs2 = fs / dec
d = np.angle(y[1:] * np.conj(y[:-1]))          # radians per sample
mpx = d * fs2 / (2 * np.pi)                      # instantaneous frequency deviation, Hz
f, p = signal.welch(mpx, fs=fs2, nperseg=8192)
def band(lo, hi): return float(10 * np.log10(np.mean(p[(f >= lo) & (f <= hi)])))
rep = {"samples": int(x.size), "duration_s": x.size / fs, "offset_hz": off, "adc_full_scale_fraction": clip,
       "peak_deviation_hz_p999": float(np.percentile(np.abs(mpx), 99.9)),
       "pilot_19k_db": band(18.9e3, 19.1e3), "pilot_floor_db": band(17.0e3, 18.0e3),
       "rds_57k_db": band(55.5e3, 58.5e3), "rds_floor_db": band(70e3, 75e3)}
print(json.dumps(rep, indent=1))
scale = 32767 / 100e3
np.clip(mpx * scale, -32767, 32767).astype("<i2").tofile(out)
