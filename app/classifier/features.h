/*
 * features.h — per-frame scalar features of a linear-POWER spectrum.
 *
 * Input is one frame's bins in linear power (what maia-httpd's /waterfall
 * carries: integrated |X|^2 scaled by 4e6 / (nint * fs) in Average mode,
 * peak |X|^2 * 4e6 / fs in Peak-detect mode — see
 * maia-httpd/src/spectrometer.rs at the pinned commit). Ratios use
 * 10*log10. Kept in lockstep with tools/classifier_templates/features.py;
 * the host test tool features_check.c proves the two agree.
 *
 * FEATURE_SEMANTICS names the contract version these numbers follow and
 * is published in every observation; bump it if the math changes.
 */
#ifndef FEATURES_H
#define FEATURES_H

#include <stdbool.h>
#include <stdint.h>

#define FEATURE_SEMANTICS "power-v1"

/* -10 dB occupied bandwidth is found by scanning outward from the peak
 * until power drops below peak/10, not a global threshold count — that
 * stays robust to a stray unrelated noise bin elsewhere in the frame. */
#define BW_GATE_RATIO         0.1    /* -10 dB */
/* A candidate peak must be within this ratio of the global peak to count
 * for peak_count — otherwise every noise wiggle would count. */
#define PEAK_PROMINENCE_RATIO 0.0316 /* -15 dB */

struct frame_features {
    bool     valid;          /* false if the frame had ~no energy at all */
    double   bandwidth_frac; /* -10 dB occupied width, as a fraction of the frame */
    double   papr_db;        /* peak-to-mean power ratio of the bins (NOT time-domain PAPR) */
    double   flatness;       /* spectral flatness (geomean/mean of power), in (0, 1] */
    double   peak_count;     /* number of locally-prominent maxima */
    uint32_t peak_bin;       /* index of the global peak */
};

struct frame_features extract_frame_features(const float *power, uint32_t n);

#endif
