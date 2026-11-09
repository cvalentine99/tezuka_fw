"""433 MHz two-LO IQ test for the FM Broadcast windows: band median and max
of each window at both LO settings, against the span's noise median.
usage: t433_check.py <iq_lo433.0> <iq_lo433.3> <out.json>"""
import json, sys, numpy as np
from scipy import signal
def psd(path, lo):
    r = np.fromfile(path, dtype="<i2").astype(np.float32); x = r[0::2] + 1j * r[1::2]
    f, p = signal.welch(x, fs=2.5e6, nperseg=8192, return_onesided=False, window="blackmanharris")
    return np.fft.fftshift(f) + lo, 10 * np.log10(np.fft.fftshift(p)), float(np.mean(np.abs(r) >= 2047))
A = psd(sys.argv[1], 433.0e6); B = psd(sys.argv[2], 433.3e6)
def band(F, a, b):
    f, p, _ = F; m = (f >= a) & (f <= b); return {"median_db": round(float(np.median(p[m])), 2), "max_db": round(float(p[m].max()), 2)}
out = {"span_median_db": {"lo_433.0": round(float(np.median(A[1])), 2), "lo_433.3": round(float(np.median(B[1])), 2)},
       "full_scale_fraction": {"lo_433.0": A[2], "lo_433.3": B[2]}, "windows": {}}
for name, (a, b) in {"S2 live 433.638-434.068": (433.638e6, 434.068e6), "S5 live 433.031-433.463": (433.031e6, 433.463e6)}.items():
    out["windows"][name] = {"lo_433.0_same_band": band(A, a, b), "lo_433.3_same_band": band(B, a, b)}
json.dump(out, open(sys.argv[3], "w"), indent=1); print(json.dumps(out, indent=1))
