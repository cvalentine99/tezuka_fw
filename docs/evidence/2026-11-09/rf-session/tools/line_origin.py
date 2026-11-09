"""Narrow-line origin test between two IQ recordings at two LO settings:
a line at the same absolute frequency at both is on air; at the same offset
from the LO at both, it travels with the receiver. Each line gets both
booleans and whether each comparison was possible (position inside the other
recording's usable span). usage: line_origin.py <iqA> <loA> <iqB> <loB> <out.json>"""
import json, sys, numpy as np
from scipy import signal
FS, EDGE = 2.5e6, 30e3
def lines(path, lo):
    r = np.fromfile(path, dtype="<i2").astype(np.float32); x = r[0::2] + 1j * r[1::2]
    f, p = signal.welch(x, fs=FS, nperseg=8192, return_onesided=False, window="blackmanharris")
    f = np.fft.fftshift(f); p = 10 * np.log10(np.fft.fftshift(p)); med = signal.medfilt(p, 101)
    pk, _ = signal.find_peaks(p - med, height=8, distance=8)
    return [(float(f[i]), float(f[i] + lo), float(p[i] - med[i])) for i in pk]
a_path, lo_a, b_path, lo_b, out = sys.argv[1], float(sys.argv[2]), sys.argv[3], float(sys.argv[4]), sys.argv[5]
A, B = lines(a_path, lo_a), lines(b_path, lo_b)
inside_b = lambda absf: lo_b - FS / 2 + EDGE <= absf <= lo_b + FS / 2 - EDGE
res = []
for off, absf, h in A:
    abs_testable = inside_b(absf); off_testable = inside_b(lo_b + off)
    same_abs = any(abs(fb - absf) <= 600 for _, fb, _ in B) if abs_testable else None
    same_off = any(abs(ob - off) <= 600 for ob, _, _ in B) if off_testable else None
    kind = ("on air" if same_abs and not same_off else "travels with the LO" if same_off and not same_abs else
            "ambiguous" if same_abs and same_off else
            "neither (both tests possible)" if (same_abs is False and same_off is False) else "one test only, negative")
    res.append({"abs_hz": round(absf), "offset_from_lo_hz": round(off), "height_db": round(h, 1),
                "same_abs_at_B": same_abs, "same_offset_at_B": same_off, "kind": kind})
json.dump({"A": {"iq": a_path, "lo_hz": lo_a}, "B": {"iq": b_path, "lo_hz": lo_b}, "lines_at_A": res}, open(out, "w"), indent=1)
for r in res: print(r)
