/* features.c — see features.h. Moved verbatim out of classifier.c so the
 * host test tool can compile it without libwebsockets/mosquitto. */
#include "features.h"

#include <math.h>
#include <string.h>

struct frame_features extract_frame_features(const float *raw, uint32_t n)
{
    struct frame_features ff;
    memset(&ff, 0, sizeof(ff));
    if (n == 0) return ff;

    uint32_t peak_i = 0;
    double peak_power = raw[0] > 0 ? raw[0] : 0.0;
    double sum_power = 0.0, sum_log_power = 0.0;
    for (uint32_t i = 0; i < n; i++) {
        double power = raw[i] > 0 ? raw[i] : 0.0;
        sum_power     += power;
        sum_log_power += log(power > 1e-20 ? power : 1e-20);
        if (power > peak_power) { peak_power = power; peak_i = i; }
    }
    if (peak_power <= 0.0 || sum_power <= 0.0) return ff; /* leaves valid=false */

    double mean_power = sum_power / n;

    ff.papr_db  = 10.0 * log10(peak_power / (mean_power > 1e-20 ? mean_power : 1e-20));
    double geomean_power = exp(sum_log_power / n);
    ff.flatness = geomean_power / (mean_power > 1e-20 ? mean_power : 1e-20);
    if (ff.flatness > 1.0) ff.flatness = 1.0; /* numerical guard: should be <=1 by AM-GM */

    double bw_gate = peak_power * BW_GATE_RATIO;
    uint32_t lo = peak_i, hi = peak_i;
    while (lo > 0     && (double)raw[lo - 1] >= bw_gate) lo--;
    while (hi + 1 < n && (double)raw[hi + 1] >= bw_gate) hi++;
    ff.bandwidth_frac = (double)(hi - lo + 1) / (double)n;

    /* Local maxima above a fixed prominence floor, deduped by a minimum
     * bin separation (n/64, at least 1) — a lightweight heuristic, not a
     * proper prominence-based peak detector, but enough to tell "one
     * carrier" from "a comb of several" in a single pass. */
    double prom_gate = peak_power * PEAK_PROMINENCE_RATIO;
    uint32_t min_sep = n / 64 > 1 ? n / 64 : 1;
    uint32_t count = 0, last_peak = 0;
    bool have_last = false;
    for (uint32_t i = 1; i + 1 < n; i++) {
        double p = (double)raw[i];
        if (p < prom_gate) continue;
        if (raw[i] <= raw[i - 1] || raw[i] <= raw[i + 1]) continue;
        if (have_last && (i - last_peak) < min_sep) continue;
        count++;
        last_peak = i;
        have_last = true;
    }
    ff.peak_count = (double)count;

    ff.peak_bin = peak_i;
    ff.valid = true;
    return ff;
}
