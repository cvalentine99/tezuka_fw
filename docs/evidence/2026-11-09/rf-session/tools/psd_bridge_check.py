"""IQ -> sensor PSD bridge check. Recompute maia-httpd /waterfall frames
(4096-point FFT, fftshift, |X|^2 integrated over nint FFTs, Average mode) from a
maia SigMF IQ recording, align them in time with the waterfall frames the
sensor's own spectrometer produced, and report how well shape and scale agree
for several window choices. The DC bin and its neighbours are excluded.
usage: psd_bridge_check.py <sigmf-data> <capture.raw> <nint> <first_frame_index_to_search> <last>"""
import json, sys
import numpy as np
from scipy import signal

data, rawf, nint, k0, k1 = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
N = 4096
r = np.fromfile(data, dtype="<i2").astype(np.float32)
x = r[0::2] + 1j * r[1::2]
nfr = x.size // (N * nint)
frames = np.fromfile(rawf, dtype="<f4").reshape(-1, N)
usable = slice(1, N)
dc = N // 2  # fftshifted DC index in the full 4096 vector
mask = np.ones(N - 1, bool); mask[dc - 1 - 3: dc - 1 + 4] = False  # usable index = full index - 1
wins = {"rect": np.ones(N), "hann": signal.windows.hann(N, sym=False),
        "blackman": signal.windows.blackman(N, sym=False), "blackmanharris": signal.windows.blackmanharris(N, sym=False)}
out = {"iq_frames": nfr, "nint": nint}
best_all = None
for name, w in wins.items():
    psd = []
    for i in range(nfr):
        b = x[i * N * nint:(i + 1) * N * nint].reshape(nint, N) * w
        X = np.fft.fftshift(np.fft.fft(b, axis=1), axes=1)
        psd.append((np.abs(X) ** 2).sum(axis=0)[usable])
    psd = np.array(psd)
    best = None
    for off in range(k0, k1 - nfr + 1):
        cs, rs = [], []
        for i in range(nfr):
            a = 10 * np.log10(psd[i][mask]); b = 10 * np.log10(frames[off + i][usable][mask])
            cs.append(np.corrcoef(a, b)[0, 1]); rs.append(np.median(b - a))
        sc = float(np.mean(cs))
        if best is None or sc > best["mean_corr_db"]:
            d = [10 * np.log10(frames[off + i][usable][mask]) - 10 * np.log10(psd[i][mask]) for i in range(nfr)]
            d = np.concatenate(d)
            best = {"window": name, "frame_offset": off, "mean_corr_db": round(sc, 5), "min_corr_db": round(float(np.min(cs)), 5),
                    "scale_db_median": round(float(np.median(d)), 3), "residual_db_std": round(float(np.std(d - np.median(d))), 3),
                    "residual_db_p95_abs": round(float(np.percentile(np.abs(d - np.median(d)), 95)), 3)}
    out[name] = best
    if best_all is None or best["mean_corr_db"] > best_all["mean_corr_db"]:
        best_all = best
out["best"] = best_all
print(json.dumps(out, indent=1))
