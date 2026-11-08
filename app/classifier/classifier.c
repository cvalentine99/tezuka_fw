/*
 * classifier.c — onboard PSD spectral-shape signal classifier
 *
 * Connects as a WebSocket *client* to maia-httpd's existing /waterfall
 * feed (ws://localhost/waterfall) — the same magnitude-only FFT stream
 * Dashboard/radioastro.jsx already consumes in the browser — normalizes
 * each frame, correlates it against a set of pre-computed templates
 * loaded from disk, and publishes the best match over MQTT.
 *
 * Deliberately does NOT touch iio_ws_proxy or the raw IQ path: this is
 * a magnitude/PSD spectral-shape matcher, not a true (phase-sensitive)
 * cyclostationary SCF classifier — see the plan doc for why. It adds no
 * new consumer of the AD9361 ADC, since maia-httpd is already running
 * and already computing this exact FFT for the Dashboard's own Spectrum
 * and Radio Astronomy pages.
 *
 * Frame format (maia-httpd /waterfall, verified against the pinned
 * producer source — maia-httpd/src/spectrometer.rs buffer_u64fp_to_f32 —
 * and a recorded frame, see docs/classifier_telemetry.md): one binary WS
 * message = float32[fft_size], fftshifted (lowest frequency first, DC at
 * float index fft_size/2). Each value is LINEAR POWER: the HDL's
 * integrated |X|^2 scaled by 4e6/(nint*fs) (Average mode) or the peak
 * |X|^2 scaled by 4e6/fs (Peak-detect mode); an arbitrary unitless level,
 * NOT dBm. The producer overwrites float index 0 (the -fs/2 edge bin)
 * with the fastlock profile / sweep step word, so f[1..] are the
 * fft_size-1 usable bins and hz_per_bin = span / fft_size (NOT span /
 * (fft_size-1)). Bin j of f[1..] sits at
 *   f_c + ((j + 1) - fft_size/2) * span / fft_size,
 * so DC is bins-array index fft_size/2 - 1 (2047 for 4096). All dB
 * figures here are 10*log10 of power ratios.
 *
 * Template file format: see scft.h (shared parser scft.c, also used by
 * `classifier -V FILE` and the host test tool). Versions 1-3; labels are
 * UTF-8 under the LABEL CONTRACT in scft.h. The loader validates sizes,
 * finiteness, normalization, duplicate ids and the label contract before
 * anything is matched; the on-board upload path runs the same loader on
 * the staged file before it replaces the working one.
 *
 * Beyond the shape-correlation score, each frame's scalar features
 * (occupied bandwidth, peak-to-average ratio, spectral flatness, local
 * peak count — see extract_frame_features()) are published every cycle
 * regardless of match outcome, plus two cross-cycle features tracked in a
 * short rolling history (see history_push()/history_compute()): peak-bin
 * drift (catches a genuinely sweeping/chirping carrier, which a single
 * static frame can't show) and duty cycle (catches bursty/pulsed
 * emitters). A version-2 template can also declare expected ranges for
 * the four per-frame features; classify() then applies those as a soft
 * penalty on top of the shape score (see template_gate_factor()), so two
 * classes with a similar curve but different real bandwidth/peakiness
 * can still be told apart.
 *
 * Build (cross-compile for Pluto):
 * $(CC) -O2 -o classifier classifier.c -lwebsockets -lmosquitto -lm
 *
 * Usage:
 * classifier [OPTIONS]
 * -H HOST   maia-httpd host          (default: localhost)
 * -p PORT   maia-httpd port          (default: 80)
 * -P PATH   waterfall WS path        (default: /waterfall)
 * -f FILE   templates file           (default: /mnt/jffs2/classifier/templates.bin)
 * -B HOST   MQTT broker host         (default: localhost)
 * -q PORT   MQTT broker port         (default: 1883)
 * -c THRESH match confidence floor   (default: 0.6, below this publishes "unknown")
 * -m MARGIN hysteresis hold margin   (default: 0.15 — once locked onto a
 *           template, only drops back to "unknown" below THRESH - MARGIN,
 *           so a borderline score doesn't flip found/unknown every cycle)
 * -i SEC    min seconds between correlate+publish cycles (default: 1.0)
 * -S HZ     span of the /waterfall frame in Hz when MQTT cannot supply it (replay)
 * -w HZ     width of the receiver-baseline structuring element (default: 4e6)
 * -x DB     persistent narrow structure above the baseline counts as a spur
 *           candidate when it is seen on two different RX frequencies (default: 8)
 * -s DB     minimum window SNR over the background to accept a match (default: 6)
 * -g DB     minimum prominence of the matched window over its flanks (default: 4)
 * -l SEC    background learning time after start/retune (default: 10)
 * -a SEC    background adaptation time constant after learning (default: 300; 0 = frozen)
 * -r FILE   replay raw float32 frames from FILE instead of /waterfall (offline test)
 * -n N      floats per replayed frame (default: 4096)
 * -F FPS    virtual frame rate for replay timing (default: 7.5)
 * -T PREFIX MQTT topic prefix for the published state (default:
 *           state/classifier/). A non-default prefix is for a side-by-side
 *           test instance: it also moves the command topic to
 *           PREFIX + "cmd/background" and derives a distinct MQTT client id,
 *           so a test daemon never collides with the production one.
 * -V FILE   validate FILE as an SCFT template set with the production
 *           loader, print a one-line JSON report and exit 0 (valid) / 2
 *           (invalid). No network, no other side effect.
 * -v        verbose (log every publish to stderr too)
 *
 * Telemetry: besides the per-field topics and the `result` JSON, every
 * classify cycle publishes one bounded JSON `observation` (schema
 * "tezuka.classifier.observation/1", see docs/classifier_telemetry.md)
 * that carries the sensor identity, producer sequence and clock, the
 * frame/grid context, the RX configuration as last seen on MQTT with an
 * explicit config_bound=false (the daemon has no way to bind a frame to
 * the RX settings in force when it was integrated), the template-set
 * hash and feature-semantics version, the decision evidence and the frame
 * and temporal features with units and validity. All JSON goes through
 * jsonw.c, which escapes labels and refuses to emit a truncated document.
 *
 * Background model: the /waterfall power spectrum is dominated by the
 * receiver's own response (the AD9361 RF filter passband inside the
 * sample-rate span, gain-dependent noise floor, DC/LO spurs). Correlating
 * templates straight against it finds those shapes: on a SignalSDRPro at
 * 2.4 GHz / 30.72 MS/s / 18 MHz RF bandwidth, the seed templates scored
 * 0.65-0.95 on an empty band, all of it the filter edge. So the daemon
 * (1) tracks a per-bin LOWER ENVELOPE of the live spectrum over time
 * (bins move the estimate down quickly and up slowly, so bursty traffic
 * does not pull it up; settles over the first -l seconds after start or
 * any RX retune, then rises with time constant -a), and (2) derives the
 * RECEIVER BASELINE from that envelope by a morphological opening with
 * a -w Hz wide structuring element: the passband curve and the noise
 * floor are smooth on that scale and survive, anything narrower (a
 * broadcast station that is always on, a spur) is removed from the
 * baseline and therefore stays in the RESIDUAL, live minus baseline,
 * that the templates are matched against. Emitters wider than -w are
 * absorbed into the baseline and cannot be classified.
 *
 * Spurs: the receiver's own narrow lines (DC, clock harmonics) also
 * survive in the residual and would be labeled. The centre bins are
 * always masked; other persistent narrow lines (-x dB over the baseline
 * at the end of learning) are remembered per bin, and a bin that is hot
 * on two different RX frequencies is a receiver spur, masked from then
 * on. A station is only hot while the LO sits near it.
 *
 * A match must sit on a window whose power exceeds the baseline by -s dB
 * (measured on a 16-bin smoothed peak inside the matched window, so a
 * narrow carrier in a wide window still counts) and must stand -g dB
 * above the live level on either side of the window: a broadband floor
 * rise (the AD9361 desenses by ~9 dB across the whole span when a nearby
 * Wi-Fi burst hits it) clears the SNR gate everywhere but has no
 * prominence anywhere. cmd/classifier/background "learn" forces a relearn.
 *
 * Template resolution: a version-3 template records its hertz per bin.
 * At each frame size or span change it is resampled onto the live grid
 * (canonical_n * hz_per_bin / live_hz_per_bin bins, at least 8), so a
 * 300 kHz FM shape is matched over 300 kHz whatever the span. Version
 * 1/2 templates have no resolution and are matched bin-for-bin as before.
 */

#define _GNU_SOURCE
#include <libwebsockets.h>
#include <mosquitto.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>
#include <getopt.h>
#include <math.h>
#include <time.h>
#include <stdbool.h>
#include <stdint.h>
#include <pthread.h>
#include <fcntl.h>
#ifdef __ARM_NEON
#include <arm_neon.h>
#endif

#include "scft.h"
#include "jsonw.h"
#include "features.h"

/* ------------------------------------------------------------------ */
/* Templates                                                           */
/* ------------------------------------------------------------------ */

/* A range with hi < lo means "unbounded" (no gate on this feature) — the
 * default for every field on a version-1 template. (struct scft_range) */

struct template {
    char     label[32];
    uint32_t id;
    float   *data;   /* canonical_n floats, pre-normalized (zero-mean, unit L2 norm) */
    float    hz_per_bin;  /* version 3; 0 = no resolution, match bin-for-bin */

    /* data resampled onto the live grid and re-normalized, see
     * templates_prepare(); live_n == canonical_n and live == data when
     * hz_per_bin is 0 and the frame is at least canonical_n wide. */
    float   *live;
    uint32_t live_n;

    /* version 2 only; has_gate is false (all ranges unbounded) for
     * version-1 files, which then behave exactly as before. */
    bool     has_gate;
    struct scft_range bandwidth_frac, papr_db, flatness, peak_count;
};

struct templates {
    uint32_t          canonical_n;
    uint32_t          count;
    struct template   *items;
    double            prepared_hz;      /* live hz/bin the items[].live grids were built for */
    uint32_t          prepared_live_n;

    /* Provenance for the observation message: the loaded file's format
     * version and SHA-256 (all 0 / "" when nothing is loaded), and the
     * loader's verdict so a subscriber can tell "no file yet" from
     * "file rejected" without reading the daemon's stderr. */
    uint32_t          version;
    char              sha256_hex[65];
    const char       *load_status;      /* scft_status_name() */
    char              load_error[160];
};

static void templates_free(struct templates *t)
{
    if (!t->items) return;
    for (uint32_t i = 0; i < t->count; i++) {
        if (t->items[i].live != t->items[i].data) free(t->items[i].live);
        free(t->items[i].data);
    }
    free(t->items);
    t->items = NULL;
    t->count = 0;
}

/* Returns 0 on success (t populated), -1 on failure (t left with only the
 * load_status/load_error fields set — caller should treat this as "no
 * templates loaded", not a fatal error, since the file legitimately may
 * not exist yet on a fresh device). The whole parse/validate lives in
 * scft.c, shared with the -V validate mode and the host test tool; this
 * only moves the validated entries into the daemon's own struct. */
static int templates_load(const char *path, struct templates *t)
{
    memset(t, 0, sizeof(*t));
    struct scft_file sf;
    enum scft_status st = scft_load(path, &sf, t->load_error, sizeof(t->load_error));
    t->load_status = scft_status_name(st);
    if (st != SCFT_OK) {
        fprintf(stderr, "templates: %s rejected (%s): %s\n", path, t->load_status, t->load_error);
        return -1;
    }

    struct template *items = calloc(sf.count, sizeof(*items));
    if (!items) {
        scft_free(&sf);
        t->load_status = scft_status_name(SCFT_E_NOMEM);
        snprintf(t->load_error, sizeof(t->load_error), "out of memory");
        return -1;
    }
    for (uint32_t i = 0; i < sf.count; i++) {
        struct scft_template *s = &sf.items[i];
        memcpy(items[i].label, s->label, sizeof(items[i].label));
        items[i].id = s->id;
        items[i].data = s->data; s->data = NULL;   /* take ownership */
        items[i].hz_per_bin = s->hz_per_bin;
        items[i].has_gate = s->has_gate;
        items[i].bandwidth_frac = s->bandwidth_frac;
        items[i].papr_db = s->papr_db;
        items[i].flatness = s->flatness;
        items[i].peak_count = s->peak_count;
        items[i].live = items[i].data;
        items[i].live_n = sf.canonical_n;
    }
    t->canonical_n = sf.canonical_n;
    t->count       = sf.count;
    t->items       = items;
    t->version     = sf.version;
    scft_sha256_hex(&sf, t->sha256_hex);
    t->load_error[0] = '\0';
    scft_free(&sf);
    fprintf(stderr, "templates: loaded %u templates (%u bins each, v%u, sha256 %.12s...) from %s\n",
            t->count, t->canonical_n, t->version, t->sha256_hex, path);
    return 0;
}

/* -V FILE: run the production loader on FILE and report, one JSON line on
 * stdout. Exit 0 when valid, 2 when not. Used by api_controller.sh to
 * validate a staged upload before it replaces the working file, and by
 * the host tests through the same code path. */
static int validate_mode(const char *path)
{
    struct scft_file sf;
    char err[160], buf[4096];
    enum scft_status st = scft_load(path, &sf, err, sizeof(err));
    struct jw w;
    jw_init(&w, buf, sizeof(buf));
    jw_obj_open(&w);
    jw_kv_bool(&w, "valid", st == SCFT_OK);
    jw_kv_str(&w, "status", scft_status_name(st));
    jw_kv_str(&w, "path", path);
    if (st != SCFT_OK) {
        jw_kv_str(&w, "error", err);
    } else {
        char hex[65];
        scft_sha256_hex(&sf, hex);
        jw_kv_uint(&w, "version", sf.version);
        jw_kv_uint(&w, "count", sf.count);
        jw_kv_uint(&w, "canonical_n", sf.canonical_n);
        jw_kv_uint(&w, "size_bytes", sf.size_bytes);
        jw_kv_str(&w, "sha256", hex);
        jw_key(&w, "labels");
        jw_arr_open(&w);
        for (uint32_t i = 0; i < sf.count && i < 64; i++) jw_str(&w, sf.items[i].label);
        jw_arr_close(&w);
        jw_kv_bool(&w, "labels_truncated", sf.count > 64);
        scft_free(&sf);
    }
    jw_obj_close(&w);
    if (!jw_ok(&w)) {
        printf("{\"valid\":false,\"status\":\"report_overflow\"}\n");
        return 2;
    }
    printf("%s\n", buf);
    return st == SCFT_OK ? 0 : 2;
}

/* ------------------------------------------------------------------ */
/* Signal processing                                                   */
/* ------------------------------------------------------------------ */

/* Linear-interpolate src[0..src_n) into dst[0..dst_n), in place resample.
 * If src_n == dst_n this degenerates to a copy. */
static void resample_linear(const float *src, uint32_t src_n, float *dst, uint32_t dst_n)
{
    if (src_n == dst_n) { memcpy(dst, src, dst_n * sizeof(float)); return; }
    if (src_n < 2 || dst_n == 0) { for (uint32_t i = 0; i < dst_n; i++) dst[i] = 0.0f; return; }

    double scale = (double)(src_n - 1) / (double)(dst_n > 1 ? dst_n - 1 : 1);
    for (uint32_t i = 0; i < dst_n; i++) {
        double pos = i * scale;
        uint32_t lo = (uint32_t)pos;
        uint32_t hi = lo + 1 < src_n ? lo + 1 : lo;
        double frac = pos - lo;
        dst[i] = (float)(src[lo] * (1.0 - frac) + src[hi] * frac);
    }
}

/* Zero-mean, unit-L2-norm normalize in place. Returns false (vector left
 * as all-zero) if the input has ~no energy, so callers can skip it. */
static bool normalize(float *v, uint32_t n)
{
    if (n == 0) return false;
    double mean = 0.0;
    for (uint32_t i = 0; i < n; i++) mean += v[i];
    mean /= n;

    double energy = 0.0;
    for (uint32_t i = 0; i < n; i++) {
        v[i] = (float)(v[i] - mean);
        energy += (double)v[i] * v[i];
    }
    if (energy < 1e-12) return false;

    double inv_norm = 1.0 / sqrt(energy);
    for (uint32_t i = 0; i < n; i++) v[i] = (float)(v[i] * inv_norm);
    return true;
}

/* Build each template's live-grid copy for the current frame (live_n bins
 * spanning live_hz hertz per bin). A template with a resolution is
 * stretched or squeezed to canonical_n * hz_per_bin / live_hz bins
 * (clamped to [8, live_n]); one without is used as is, unless the frame
 * is narrower than it, in which case it is squeezed to the frame. No-op
 * when nothing changed since the last call. */
#define TEMPLATE_MIN_LIVE_BINS 8
static void templates_prepare(struct templates *t, double live_hz, uint32_t live_n)
{
    if (!t->items || live_n == 0) return;
    if (t->prepared_hz == live_hz && t->prepared_live_n == live_n) return;
    for (uint32_t i = 0; i < t->count; i++) {
        struct template *tpl = &t->items[i];
        uint32_t want = t->canonical_n;
        if (tpl->hz_per_bin > 0.0f && live_hz > 0.0)
            want = (uint32_t)(t->canonical_n * (double)tpl->hz_per_bin / live_hz + 0.5);
        if (want < TEMPLATE_MIN_LIVE_BINS) want = TEMPLATE_MIN_LIVE_BINS;
        if (want > live_n) want = live_n;
        if (tpl->live != tpl->data) { free(tpl->live); tpl->live = tpl->data; }
        tpl->live_n = t->canonical_n;
        if (want == t->canonical_n) continue;
        float *r = malloc(want * sizeof(float));
        if (!r) continue;
        resample_linear(tpl->data, t->canonical_n, r, want);
        if (!normalize(r, want)) { free(r); continue; }
        tpl->live = r;
        tpl->live_n = want;
    }
    t->prepared_hz = live_hz;
    t->prepared_live_n = live_n;
}

/* ------------------------------------------------------------------ */
/* Frame features — scalar descriptors of a single live frame, computed
 * once per cycle from the linear POWER bins (the /waterfall feed is
 * integrated |X|^2; before normalize() mean-centers them, which would
 * make these power ratios meaningless). The math lives in features.c
 * (struct frame_features / extract_frame_features(), shared with the
 * host test tool that proves it agrees with the Python port).
 * Published every cycle regardless of match outcome (see
 * publish_features()) and, for a version-2 template, also used to gate
 * the shape score (see template_gate_factor()). */
/* ------------------------------------------------------------------ */

/* Cross-cycle features from history_compute() — see the rolling
 * `history[]` ring in struct app. Both fields are "n/a" (valid=false)
 * until the ring has enough samples spanning enough real time; no
 * spurious estimates right after the daemon (re)starts. */
struct temporal_features {
    bool     drift_valid;
    double   drift_bins_per_s; /* + = peak moving toward higher bins over time */
    bool     duty_valid;
    double   duty_cycle_pct;   /* % of recent cycles with a standout peak present */
};

/* Soft penalty in (0, 1] for how well `ff` matches a version-2 template's
 * declared expected ranges. A hard reject at the boundary would be
 * brittle — real-world noise sits right there — so this steps down a
 * bounded amount per out-of-range feature instead of rejecting outright,
 * keeping the ranking well-behaved while still letting a badly-mismatched
 * feature (wrong bandwidth by a wide margin, say) push a shape-similar-
 * but-wrong template below a better-gated competitor. Templates without a
 * gate (v1, or a v2 entry with every range left unbounded) always return
 * 1.0 — no effect on their score. */
/* Four gated features (bandwidth/PAPR/flatness/peak_count) at 0.15 each
 * means missing all four lands exactly at the floor (1.0 - 4*0.15 = 0.4) —
 * chosen together so the floor is an actual reachable bound, not a dead
 * constant a smaller per-feature penalty would never hit. */
#define GATE_PENALTY_PER_FEATURE 0.15
#define GATE_PENALTY_FLOOR       0.4

static double range_miss(double v, struct scft_range r)
{
    if (r.hi < r.lo) return 0.0; /* unbounded: no penalty */
    return (v >= r.lo && v <= r.hi) ? 0.0 : 1.0;
}

static double template_gate_factor(const struct template *tpl, const struct frame_features *ff)
{
    if (!tpl->has_gate || !ff->valid) return 1.0;
    double misses = range_miss(ff->bandwidth_frac, tpl->bandwidth_frac)
                  + range_miss(ff->papr_db,        tpl->papr_db)
                  + range_miss(ff->flatness,       tpl->flatness)
                  + range_miss(ff->peak_count,     tpl->peak_count);
    double factor = 1.0 - misses * GATE_PENALTY_PER_FEATURE;
    return factor < GATE_PENALTY_FLOOR ? GATE_PENALTY_FLOOR : factor;
}

struct match_result {
    bool     found;
    uint32_t template_idx;
    double   score;          /* clamped to [0, 1] */
    int32_t  bin_offset;     /* position (in live-frame bins) where the best window started */
    uint32_t bandwidth_bins; /* width of the matched window, in live-frame bins */
    double   snr_db;         /* window power over the background, dB */
    double   prom_db;        /* window peak over the flanking live level, dB */
};

/* Plain float32 dot product, NEON when available. Templates are zero-mean,
 * so dot(window, tpl) == dot(window - mean, tpl): no per-window centering
 * is needed, and with the window's sum and sum-of-squares from prefix
 * sums each sliding position costs exactly this one dot product. The
 * previous implementation centered, normalized and dotted every window
 * in double precision with a serial accumulator: ~41% of a Cortex-A9
 * core for three templates. */
static inline float dot_f32(const float *a, const float *b, uint32_t n)
{
    uint32_t i = 0;
    float s;
#ifdef __ARM_NEON
    float32x4_t acc0 = vdupq_n_f32(0.0f), acc1 = vdupq_n_f32(0.0f);
    for (; i + 8 <= n; i += 8) {
        acc0 = vmlaq_f32(acc0, vld1q_f32(a + i),     vld1q_f32(b + i));
        acc1 = vmlaq_f32(acc1, vld1q_f32(a + i + 4), vld1q_f32(b + i + 4));
    }
    float32x4_t acc = vaddq_f32(acc0, acc1);
    float32x2_t h = vadd_f32(vget_low_f32(acc), vget_high_f32(acc));
    s = vget_lane_f32(vpadd_f32(h, h), 0);
#else
    float s0 = 0, s1 = 0, s2 = 0, s3 = 0;
    for (; i + 4 <= n; i += 4) {
        s0 += a[i] * b[i]; s1 += a[i + 1] * b[i + 1];
        s2 += a[i + 2] * b[i + 2]; s3 += a[i + 3] * b[i + 3];
    }
    s = s0 + s1 + s2 + s3;
#endif
    for (; i < n; i++) s += a[i] * b[i];
    return s;
}

/* Window SNR: the largest SNR_SMOOTH_BINS-bin average of live/background
 * inside [start, start+n), in dB. Window-mean SNR would hide a narrow
 * carrier inside a wide template window; the smoothed peak does not. */
#define SNR_SMOOTH_BINS 16
static double window_snr_db(const double *pl, const double *pb, uint32_t start, uint32_t n)
{
    uint32_t w = n < SNR_SMOOTH_BINS ? n : SNR_SMOOTH_BINS;
    double best = 0.0;
    for (uint32_t i = start; i + w <= start + n; i++) {
        double lw = pl[i + w] - pl[i], bw = pb[i + w] - pb[i];
        if (lw > 0 && bw > 0) { double r = lw / bw; if (r > best) best = r; }
    }
    return best > 0 ? 10.0 * log10(best) : 0.0;
}

/* Prominence: the window's smoothed live peak against the mean live level
 * of up to n/2 bins on each side of it (whatever the frame has). */
static double window_prominence_db(const double *pl, uint32_t live_n, uint32_t start, uint32_t n)
{
    uint32_t w = n < SNR_SMOOTH_BINS ? n : SNR_SMOOTH_BINS, half = n / 2;
    double peak = 0.0;
    for (uint32_t i = start; i + w <= start + n; i++) {
        double lw = (pl[i + w] - pl[i]) / w;
        if (lw > peak) peak = lw;
    }
    uint32_t l0 = start > half ? start - half : 0, l1 = start;
    uint32_t r0 = start + n, r1 = r0 + half < live_n ? r0 + half : live_n;
    double sum = (pl[l1] - pl[l0]) + (pl[r1] - pl[r0]);
    uint32_t cnt = (l1 - l0) + (r1 - r0);
    if (cnt == 0 || sum <= 0 || peak <= 0) return 0.0;
    return 10.0 * log10(peak / (sum / cnt));
}

/* Per-template best hit for one frame. `resid` is the baseline-subtracted
 * frame scaled so max(resid) == 1 (cosine similarity is scale invariant;
 * the scaling keeps the double prefix sums well conditioned). `live` and
 * `base` are the (spur-masked) live and baseline power spectra, for the
 * window SNR and prominence. Templates must have been prepared for this
 * frame (templates_prepare()). */
static void classify_all(const float *resid, const float *live, const float *base, uint32_t live_n,
                         const struct templates *t, const struct frame_features *ff,
                         double snr_min_db, struct match_result *hits)
{
    double snr_lin = pow(10.0, snr_min_db / 10.0);
    /* prefix sums: resid, resid^2, live, base */
    double *ps  = malloc((live_n + 1) * sizeof(double));
    double *ps2 = malloc((live_n + 1) * sizeof(double));
    double *pl  = malloc((live_n + 1) * sizeof(double));
    double *pb  = malloc((live_n + 1) * sizeof(double));
    if (!ps || !ps2 || !pl || !pb) { free(ps); free(ps2); free(pl); free(pb); return; }
    ps[0] = ps2[0] = pl[0] = pb[0] = 0.0;
    for (uint32_t i = 0; i < live_n; i++) {
        ps[i + 1]  = ps[i]  + resid[i];
        ps2[i + 1] = ps2[i] + (double)resid[i] * resid[i];
        pl[i + 1]  = pl[i]  + live[i];
        pb[i + 1]  = pb[i]  + base[i];
    }

    for (uint32_t ti = 0; ti < t->count; ti++) {
        const struct template *tpl = &t->items[ti];
        struct match_result best = { .found = false, .score = -2.0 };
        double gate = template_gate_factor(tpl, ff);
        uint32_t tn = tpl->live_n;
        if (tn > live_n) tn = live_n;

        /* Only windows whose mean power clears the SNR gate are candidates:
         * otherwise a narrow template wins the cosine race on a noise spike
         * (0.99 against an 8-bin blip) and the real emitter under a wider
         * template is never considered. */
        uint32_t best_start = 0; double best_cos = -2.0;
        for (uint32_t start = 0; start + tn <= live_n; start++) {
            double bw = pb[start + tn] - pb[start];
            if (bw <= 0 || pl[start + tn] - pl[start] < bw * snr_lin) continue;
            double S = ps[start + tn] - ps[start];
            double E = ps2[start + tn] - ps2[start];
            double var = E - S * S / (double)tn;      /* sum of squares after mean removal */
            if (var < 1e-12) continue;                /* flat window: nothing to match */
            double c = (double)dot_f32(resid + start, tpl->live, tn) / sqrt(var);
            if (c > best_cos) { best_cos = c; best_start = start; }
        }
        if (best_cos > -2.0) {
            best.found = true;
            best.score = best_cos * gate;
            best.bin_offset = (int32_t)best_start;
            best.bandwidth_bins = tn;
            best.snr_db = window_snr_db(pl, pb, best_start, tn);
            best.prom_db = tn < live_n ? window_prominence_db(pl, live_n, best_start, tn) : 0.0;
        }
        if (best.score < 0.0) best.score = 0.0;
        if (best.score > 1.0) best.score = 1.0;
        best.template_idx = ti;
        hits[ti] = best;
    }
    free(ps); free(ps2); free(pl); free(pb);
}

/* ------------------------------------------------------------------ */
/* App state (moved ahead of publish_result below, which needs the full
 * definition for its hysteresis bookkeeping — the WebSocket client code
 * further down that owns the rest of this struct's fields follows later) */
/* ------------------------------------------------------------------ */
struct app {
    struct templates templates;
    const char *templates_path;
    double      threshold;
    double      hold_margin;
    double      min_interval_s;
    bool        verbose;

    /* Hysteresis state: without this, a score that hovers right at
     * `threshold` flips found/unknown on essentially every ~1s publish
     * cycle (each cycle is evaluated from scratch, with no memory of the
     * last one) — visible on the Dashboard as the label chip and the
     * reference-match panel blinking in and out once a second. Once
     * locked onto a template, we only drop back to "unknown" if the score
     * falls below (threshold - hold_margin), not merely below threshold. */
    bool        locked;
    uint32_t    locked_template_id;
    double      snr_min_db;      /* -s */
    uint32_t    shape_pub_id;    /* publish_shapes() bookkeeping */
    double      shape_pub_hz;
    uint32_t    shape_pub_live_n;
    double      prom_min_db;     /* -P; only applied to sliding (sub-span) windows */

    /* Background model (see header comment). bg is the temporal lower
     * envelope, base the receiver baseline derived from it, mask marks
     * spur bins; all bg_n bins. bg_frames counts frames folded in since
     * the last reset. */
    float      *bg;
    float      *base;
    uint8_t    *mask;
    uint32_t    bg_n;
    bool        bg_learned;      /* learning period finished, spur bookkeeping done */
    double      base_width_hz;   /* -w */
    double      spur_db;         /* -x */
    double      span_hz;         /* -S, or state/rx/span / sampling from MQTT */
    double      live_hz;         /* span_hz / fft_size, 0 when unknown */
    uint32_t    fft_size;        /* floats per frame (bins + the step word) */
    float       first_float;     /* f[0] of the last frame: fastlock profile / sweep step */

    /* Observation bookkeeping (see publish_observation()). */
    uint64_t    seq;             /* classify cycles since start, 1-based in the message */
    uint32_t    config_generation; /* bumped on every RX-settings change seen on MQTT */
    char        hostname[64];
    char        boot_id[40];     /* /proc/sys/kernel/random/boot_id, "" if unreadable */
    char        serial_mqtt[80]; /* latest state/main/serial, "" if never seen */
    char        sweep_activate[8], sweep_engaged[8]; /* latest state/rx/sweep/{activate,engaged}, "" if never seen */
    char        rfinput_mqtt[8];
    char        sampling_mqtt[32];
    double      mono_at_start;   /* CLOCK_MONOTONIC at start, for the session id */

    /* Spur map: per bin, how many distinct RX frequencies had it hot at
     * the end of learning (>= 2 means receiver spur) and the frequency
     * it was last counted at; a bin only counts again once the LO has
     * moved by more than a span, so revisiting a band or nudging the
     * tuning does not turn a station into a spur. */
    uint8_t    *spur_count;
    double     *spur_last_hz;
    uint32_t    spur_n;
    uint32_t    bg_frames;
    double      bg_t0;           /* now_s() at last reset */
    double      bg_last_t;       /* now_s() at last update */
    double      learn_s;         /* -l */
    double      bg_tau_s;        /* -a; 0 = frozen after learning */

    /* Set from libmosquitto's network thread (see mqtt_on_message), read
     * and cleared on the frame thread under `lock`. */
    pthread_mutex_t lock;
    bool        tune_changed;    /* an RX setting we subscribe to changed */
    bool        relearn;         /* cmd/classifier/background "learn" */
    double      span_hz_mqtt;    /* latest state/rx/span (or sampling) */
    char        freq_mqtt[64];   /* latest state/rx/frequency */

    /* Replay mode (-r): frames come from a file at a virtual clock. */
    const char *replay_path;
    uint32_t    replay_floats;
    double      replay_fps;
    double      replay_t;        /* virtual seconds */

    /* Rolling history of recent peak positions, for the two cross-cycle
     * features a single frame can't show on its own (see history_push() /
     * history_compute()): peak-bin drift (a genuinely sweeping/chirping
     * carrier) and duty cycle (a bursty/pulsed emitter). A fixed-size ring
     * covering FEATURE_HISTORY cycles at the ~1/s throttle rate below —
     * about 16s of history by default, plenty for both without unbounded
     * growth. */
    struct {
        double   t;             /* seconds, now_s() clock */
        double   peak_bin_frac; /* 0..1, scale-invariant across frame-size changes */
        bool     occupied;
    } history[16];
    uint32_t history_next;
    uint32_t history_filled;
    double   start_t;

    /* Per-connection message reassembly buffer (WS fragments can arrive
     * across multiple RECEIVE callbacks; browsers hide this, raw lws
     * doesn't) */
    uint8_t  *msg_buf;
    size_t    msg_len;
    size_t    msg_cap;

    double    last_run_t;        /* now_s() of the last classify cycle, 0 = never */
    volatile bool running;
    volatile bool connected;   /* set true on ESTABLISHED, false on CLOSED/ERROR */
};

#define FEATURE_HISTORY ((uint32_t)(sizeof(((struct app *)0)->history) / sizeof(((struct app *)0)->history[0])))

static struct app A;

/* Monotonic seconds, or the virtual replay clock in -r mode so a recorded
 * capture processes at full speed with the same throttle/learning/
 * adaptation timing it would have had live. */
static double now_s(void)
{
    if (A.replay_path) return A.replay_t;
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

/* ------------------------------------------------------------------ */
/* MQTT publish — one persistent libmosquitto connection for the whole
 * daemon lifetime (same library zynqrx and mqtt_pubd already link).
 * Earlier revisions forked a mosquitto_pub process per field, i.e. about
 * a dozen fork+exec+TCP handshakes per cycle: measured at ~22% of the
 * Zynq's CPU and ~13 forks/s on a SignalSDRPro, far more than the
 * correlation itself costs. It also passed the template label through a
 * shell. Now: QoS 0, retained (matching api_controller.sh's state/
 * convention, so a late-connecting Dashboard gets the last result
 * immediately), published from the lws thread while libmosquitto's own
 * network thread (mosquitto_loop_start) handles I/O and reconnects. */
/* ------------------------------------------------------------------ */
static struct mosquitto *g_mosq;
static bool g_mqtt_verbose;

/* -T PREFIX. The default is the Dashboard's state/ namespace; a test
 * instance uses its own prefix and gets its own command topic and client
 * id (see mqtt_init) so it can run next to the production daemon. */
#define TOPIC_PREFIX_DEFAULT "state/classifier/"
#define BACKGROUND_CMD_DEFAULT "cmd/classifier/background"
static const char *g_topic_prefix = TOPIC_PREFIX_DEFAULT;
static char g_background_cmd[160] = BACKGROUND_CMD_DEFAULT;

/* Publish failures are counted (and reported in the observation) rather
 * than silently dropped. Logging is bounded: the first few, then one in
 * every PUB_ERR_LOG_EVERY, so a dead broker cannot flood the console. */
static uint32_t g_pub_errors, g_pub_ok;
#define PUB_ERR_LOG_FIRST 5
#define PUB_ERR_LOG_EVERY 1000

static void mqtt_pub(const char *topic, const char *value)
{
    if (!g_mosq) { if (A.replay_path) printf("%.1f %s%s %s\n", A.replay_t, g_topic_prefix, topic, value); return; }
    char full[256];
    int fl = snprintf(full, sizeof(full), "%s%s", g_topic_prefix, topic);
    if (fl < 0 || (size_t)fl >= sizeof(full)) { g_pub_errors++; return; }
    int rc = mosquitto_publish(g_mosq, NULL, full, (int)strlen(value), value, 0, true);
    if (rc == MOSQ_ERR_SUCCESS) { g_pub_ok++; return; }
    g_pub_errors++;
    if (g_mqtt_verbose || g_pub_errors <= PUB_ERR_LOG_FIRST || g_pub_errors % PUB_ERR_LOG_EVERY == 0)
        fprintf(stderr, "mqtt: publish %s failed (%u so far): %s\n", full, g_pub_errors, mosquitto_strerror(rc));
}

/* Everything publish_observation() reports from the fields the MQTT
 * thread writes (mqtt_on_message, under `lock`), copied in ONE locked
 * region at the start of a frame so the observation describes a single
 * instant of that state: the generation and the settings it counts are
 * never mixed from two different updates, and the frame thread never
 * reads those strings while the network thread is rewriting them. */
struct obs_ctx {
    char     freq[64];           /* state/rx/frequency, "" if never seen */
    char     serial[80];
    char     sampling[32];
    char     rfinput[8];
    char     sweep_activate[8], sweep_engaged[8];
    uint32_t config_generation;
};

/* Caller holds a->lock. */
static void obs_ctx_copy_locked(const struct app *a, struct obs_ctx *oc);

/* RX settings whose change invalidates the learned background (reset=true),
 * plus identity/mode topics that are only recorded for the observation.
 * These are api_controller.sh's retained state/ topics; the retained value
 * arrives right after subscribing and seeds the table, only a later
 * *different* value counts as a change. A change of any reset=true topic
 * also bumps config_generation. */
static struct { const char *topic; char value[80]; bool have; bool reset; } RX_WATCH[] = {
    { "state/rx/frequency",      "", false, true },
    { "state/rx/sampling",       "", false, true },
    { "state/rx/span",           "", false, true },
    { "state/rx/bandwidth",      "", false, true },
    { "state/rx/gain",           "", false, true },
    { "state/rx/gain_mode",      "", false, true },
    { "state/rx/rfinput",        "", false, true },
    { "state/rx/sweep/activate", "", false, true },
    { "state/rx/sweep/engaged",  "", false, true },
    { "state/main/serial",       "", false, false },
};
#define RX_WATCH_N (sizeof(RX_WATCH) / sizeof(RX_WATCH[0]))

/* Bounded copy: truncates silently, always NUL-terminates. */
static void set_str(char *dst, size_t cap, const char *src)
{
    size_t n = strlen(src);
    if (n >= cap) n = cap - 1;
    memcpy(dst, src, n);
    dst[n] = '\0';
}

static void obs_ctx_copy_locked(const struct app *a, struct obs_ctx *oc)
{
    set_str(oc->freq, sizeof(oc->freq), a->freq_mqtt);
    set_str(oc->serial, sizeof(oc->serial), a->serial_mqtt);
    set_str(oc->sampling, sizeof(oc->sampling), a->sampling_mqtt);
    set_str(oc->rfinput, sizeof(oc->rfinput), a->rfinput_mqtt);
    set_str(oc->sweep_activate, sizeof(oc->sweep_activate), a->sweep_activate);
    set_str(oc->sweep_engaged, sizeof(oc->sweep_engaged), a->sweep_engaged);
    oc->config_generation = a->config_generation;
}

static void mqtt_on_connect(struct mosquitto *m, void *ud, int rc)
{
    (void)ud;
    if (rc != 0) { fprintf(stderr, "classifier: MQTT connect refused: %s\n", mosquitto_connack_string(rc)); return; }
    fprintf(stderr, "classifier: connected to MQTT broker\n");
    for (size_t i = 0; i < RX_WATCH_N; i++) mosquitto_subscribe(m, NULL, RX_WATCH[i].topic, 0);
    mosquitto_subscribe(m, NULL, g_background_cmd, 0);
}

static void mqtt_on_message(struct mosquitto *m, void *ud, const struct mosquitto_message *msg)
{
    (void)m; (void)ud;
    if (!msg->topic) return;
    char val[80];
    int n = msg->payloadlen < (int)sizeof(val) - 1 ? msg->payloadlen : (int)sizeof(val) - 1;
    if (n < 0) n = 0;
    memcpy(val, msg->payload ? msg->payload : "", n); val[n] = '\0';

    pthread_mutex_lock(&A.lock);
    if (strcmp(msg->topic, g_background_cmd) == 0) {
        if (strcmp(val, "learn") == 0) A.relearn = true;
    } else {
        for (size_t i = 0; i < RX_WATCH_N; i++) {
            if (strcmp(msg->topic, RX_WATCH[i].topic) != 0) continue;
            if (RX_WATCH[i].have && strcmp(RX_WATCH[i].value, val) != 0 && RX_WATCH[i].reset) {
                A.tune_changed = true;
                A.config_generation++;
            }
            set_str(RX_WATCH[i].value, sizeof(RX_WATCH[i].value), val);
            RX_WATCH[i].have = true;
            if (strcmp(msg->topic, "state/rx/span") == 0 ||
                (strcmp(msg->topic, "state/rx/sampling") == 0 && !RX_WATCH[2].have)) {
                double v = atof(val);
                if (v > 0) A.span_hz_mqtt = v;
            }
            if (strcmp(msg->topic, "state/rx/frequency") == 0)
                set_str(A.freq_mqtt, sizeof(A.freq_mqtt), val);
            else if (strcmp(msg->topic, "state/rx/sampling") == 0)
                set_str(A.sampling_mqtt, sizeof(A.sampling_mqtt), val);
            else if (strcmp(msg->topic, "state/rx/rfinput") == 0)
                set_str(A.rfinput_mqtt, sizeof(A.rfinput_mqtt), val);
            else if (strcmp(msg->topic, "state/rx/sweep/activate") == 0)
                set_str(A.sweep_activate, sizeof(A.sweep_activate), val);
            else if (strcmp(msg->topic, "state/rx/sweep/engaged") == 0)
                set_str(A.sweep_engaged, sizeof(A.sweep_engaged), val);
            else if (strcmp(msg->topic, "state/main/serial") == 0)
                set_str(A.serial_mqtt, sizeof(A.serial_mqtt), val);
            break;
        }
    }
    pthread_mutex_unlock(&A.lock);
}

/* Published as the broker's last-will if the daemon dies without a clean
 * shutdown, and explicitly by main() on SIGTERM — otherwise the retained
 * label would outlive the daemon and the Dashboard would show a stale
 * match forever. */
#define OFFLINE_RESULT "{\"label\":\"unknown\",\"confidence\":0,\"online\":false}"

static int mqtt_init(const char *host, int port, bool verbose)
{
    g_mqtt_verbose = verbose;
    mosquitto_lib_init();
    /* Client id: fixed for the production namespace (so a restart takes
     * over the previous session), derived from the prefix otherwise. */
    char client_id[96] = "tezuka_classifier";
    if (strcmp(g_topic_prefix, TOPIC_PREFIX_DEFAULT) != 0) {
        size_t k = strlen(client_id);
        client_id[k++] = ':';
        for (const char *p = g_topic_prefix; *p && k < sizeof(client_id) - 1; p++)
            client_id[k++] = (*p == '/' || *p == '#' || *p == '+') ? '.' : *p;
        client_id[k] = '\0';
    }
    g_mosq = mosquitto_new(client_id, true, NULL);
    if (!g_mosq) { fprintf(stderr, "classifier: mosquitto_new failed\n"); return -1; }
    mosquitto_connect_callback_set(g_mosq, mqtt_on_connect);
    mosquitto_message_callback_set(g_mosq, mqtt_on_message);
    mosquitto_reconnect_delay_set(g_mosq, 1, 10, true);
    char will_topic[256];
    snprintf(will_topic, sizeof(will_topic), "%sresult", g_topic_prefix);
    mosquitto_will_set(g_mosq, will_topic, (int)strlen(OFFLINE_RESULT), OFFLINE_RESULT, 0, true);
    int rc = mosquitto_connect_async(g_mosq, host, port, 60);
    if (rc != MOSQ_ERR_SUCCESS)
        fprintf(stderr, "classifier: MQTT %s:%d not reachable yet (%s), will keep retrying\n",
                host, port, mosquitto_strerror(rc));
    if (mosquitto_loop_start(g_mosq) != MOSQ_ERR_SUCCESS) {
        fprintf(stderr, "classifier: mosquitto_loop_start failed\n");
        mosquitto_destroy(g_mosq); g_mosq = NULL; mosquitto_lib_cleanup();
        return -1;
    }
    return 0;
}

static void mqtt_shutdown(void)
{
    if (!g_mosq) return;
    mqtt_pub("label", "unknown");
    mqtt_pub("confidence", "0.0");
    mqtt_pub("result", OFFLINE_RESULT);
    /* give the network thread a moment to flush the three retained messages */
    usleep(200 * 1000);
    mosquitto_disconnect(g_mosq);
    mosquitto_loop_stop(g_mosq, false);
    mosquitto_destroy(g_mosq);
    g_mosq = NULL;
    mosquitto_lib_cleanup();
}

/* Labels come from templates.bin, which cmd/classifier/templates lets any
 * MQTT client replace. The loader (scft.c) enforces the LABEL CONTRACT —
 * NUL-terminated, non-empty, valid UTF-8, no control bytes — so a label
 * is arbitrary Unicode text and is carried verbatim; every JSON payload
 * goes through jsonw.c, which escapes it, so nothing is mangled on the way
 * out (earlier revisions replaced quotes and backslashes with '?'). */

/* live_n is published alongside bin_offset/bandwidth_bins rather than
 * assumed shared knowledge: it's what those two are measured in units
 * of, and publishing it removes any need for a subscriber (the
 * Dashboard) to assume its own /waterfall connection saw the exact same
 * frame size the daemon did at this instant.
 *
 * Decision: a hit is acceptable when its score clears `threshold` AND its
 * window sits at least snr_min_db above the background. Hysteresis
 * (a->locked / a->locked_template_id): the held template keeps the label
 * while it stays above (threshold - hold_margin); a *different* template
 * only takes over when it beats the held one's score by hold_margin, so
 * two templates trading 0.72/0.75 no longer flip the label every cycle. */
/* Publish a finished jsonw document, or count it as a publish error when
 * the writer overflowed / is unbalanced (nothing truncated ever leaves). */
static void mqtt_pub_json(const char *topic, const struct jw *w)
{
    if (!jw_ok(w)) {
        g_pub_errors++;
        if (g_pub_errors <= PUB_ERR_LOG_FIRST || g_pub_errors % PUB_ERR_LOG_EVERY == 0)
            fprintf(stderr, "mqtt: %s%s not published: JSON buffer too small (%zu)\n", g_topic_prefix, topic, w->cap);
        return;
    }
    mqtt_pub(topic, w->buf);
}

/* The matched template on the live grid (republished when the match or
 * the grid changes) and the normalized live window it matched (every
 * match cycle), so the Dashboard's reference panel can overlay exactly
 * what the daemon compared. Both zero-mean / unit-L2-norm. */
#define SHAPE_MAX_BINS 1024
static void publish_shapes(struct app *a, const struct template *tpl, const struct templates *t,
                           const float *resid, const struct match_result *m)
{
    uint32_t tn = tpl->live_n;
    if (tn > SHAPE_MAX_BINS) return;   /* a whole-span v1 template: too big for the live channel */
    size_t cap = (size_t)tn * 12 + 256;
    char *buf = malloc(cap);
    if (!buf) return;
    struct jw w;
    if (a->shape_pub_id != tpl->id || a->shape_pub_hz != t->prepared_hz || a->shape_pub_live_n != t->prepared_live_n) {
        jw_init(&w, buf, cap);
        jw_obj_open(&w);
        jw_kv_uint(&w, "id", tpl->id);
        jw_kv_str(&w, "label", tpl->label);
        jw_kv_uint(&w, "bins", tn);
        jw_kv_double(&w, "hz_per_bin", tpl->hz_per_bin, 1);
        jw_key(&w, "data"); jw_arr_open(&w);
        for (uint32_t i = 0; i < tn; i++) jw_double(&w, tpl->live[i], 4);
        jw_arr_close(&w);
        jw_obj_close(&w);
        mqtt_pub_json("template_shape", &w);
        a->shape_pub_id = tpl->id; a->shape_pub_hz = t->prepared_hz; a->shape_pub_live_n = t->prepared_live_n;
    }
    if (resid && m->bin_offset >= 0) {
        float *win = malloc(tn * sizeof(float));
        if (win) {
            memcpy(win, resid + m->bin_offset, tn * sizeof(float));
            if (normalize(win, tn)) {
                jw_init(&w, buf, cap);
                jw_obj_open(&w);
                jw_kv_uint(&w, "id", tpl->id);
                jw_kv_uint(&w, "bins", tn);
                jw_key(&w, "data"); jw_arr_open(&w);
                for (uint32_t i = 0; i < tn; i++) jw_double(&w, win[i], 4);
                jw_arr_close(&w);
                jw_obj_close(&w);
                mqtt_pub_json("window", &w);
            }
            free(win);
        }
    }
    free(buf);
}

/* What publish_result() decided, kept for the observation message. */
struct decision {
    const struct template     *tpl;      /* accepted template, NULL = unknown */
    const struct match_result *chosen;   /* its hit */
    const struct match_result *best;     /* best gated candidate, may be NULL */
    double margin;                       /* best - second best (or best alone) */
    bool   held;                         /* the hysteresis hold kept the previous lock */
    bool   took_over;                    /* a different template displaced the held one */
    double gate_factor;                  /* template_gate_factor() of the accepted template */
};

static void publish_result(struct app *a, const struct match_result *hits, const struct templates *t,
                           uint32_t live_n, const char *state, double bg_age_s, const float *resid,
                           const struct frame_features *ff, struct decision *dec)
{
    char buf[512];
    struct jw w;
    double thr = a->threshold, m = a->hold_margin;
    memset(dec, 0, sizeof(*dec));
    dec->gate_factor = 1.0;

    /* Rank only the hits that clear the SNR and prominence gates; a
     * template whose best window is a gated-out blip must not shadow
     * another template's genuine hit. */
    const struct match_result *best = NULL, *second = NULL, *held = NULL;
    for (uint32_t i = 0; i < t->count; i++) {
        const struct match_result *h = &hits[i];
        if (!h->found) continue;
        if (h->snr_db < a->snr_min_db) continue;
        if (h->bandwidth_bins < live_n && h->prom_db < a->prom_min_db) continue;
        if (!best || h->score > best->score) { second = best; best = h; }
        else if (!second || h->score > second->score) second = h;
        if (a->locked && t->items[i].id == a->locked_template_id) held = h;
    }
    double margin = (best && second) ? best->score - second->score : (best ? best->score : 0.0);

    const struct match_result *chosen = NULL;
    bool held_ok = held && held->score >= thr - m;
    bool best_ok = best && best->score >= thr;
    if (held_ok) {
        chosen = held;
        dec->held = true;
        if (best_ok && best != held && best->score >= held->score + m) { chosen = best; dec->took_over = true; dec->held = false; }
    } else if (best_ok) {
        chosen = best;
    }
    dec->best = best;
    dec->margin = margin;

    if (!chosen) {
        a->locked = false;
        mqtt_pub("label", "unknown");
        mqtt_pub("confidence", "0.0");
        jw_init(&w, buf, sizeof(buf));
        jw_obj_open(&w);
        jw_kv_str(&w, "label", "unknown");
        jw_kv_int(&w, "confidence", 0);
        jw_kv_double(&w, "best", best ? best->score : 0.0, 3);
        jw_kv_double(&w, "best_snr_db", best ? best->snr_db : 0.0, 1);
        jw_kv_double(&w, "best_prom_db", best ? best->prom_db : 0.0, 1);
        jw_kv_double(&w, "margin", margin, 3);
        jw_kv_str(&w, "state", state);
        jw_kv_double(&w, "bg_age_s", bg_age_s, 0);
        jw_kv_bool(&w, "online", true);
        jw_obj_close(&w);
        mqtt_pub_json("result", &w);
        if (a->verbose) fprintf(stderr, "classify: unknown (best=%.3f snr=%.1f dB prom=%.1f dB %s)\n",
                                best ? best->score : 0.0, best ? best->snr_db : 0.0, best ? best->prom_db : 0.0, state);
        return;
    }

    const struct template *tpl = &t->items[chosen->template_idx];
    dec->tpl = tpl;
    dec->chosen = chosen;
    dec->gate_factor = ff ? template_gate_factor(tpl, ff) : 1.0;
    a->locked = true;
    a->locked_template_id = tpl->id;
    mqtt_pub("label", tpl->label);
    snprintf(buf, sizeof(buf), "%.3f", chosen->score);
    mqtt_pub("confidence", buf);
    snprintf(buf, sizeof(buf), "%d", chosen->bin_offset);
    mqtt_pub("freq_offset", buf);
    snprintf(buf, sizeof(buf), "%u", chosen->bandwidth_bins);
    mqtt_pub("bandwidth_bins", buf);
    snprintf(buf, sizeof(buf), "%u", live_n);
    mqtt_pub("frame_bins", buf);
    snprintf(buf, sizeof(buf), "%u", tpl->id);
    mqtt_pub("template_id", buf);

    /* The same fields as one message, so a subscriber sees a result
     * atomically instead of reassembling a burst (the Dashboard currently
     * debounces the per-field topics for exactly that reason). */
    jw_init(&w, buf, sizeof(buf));
    jw_obj_open(&w);
    jw_kv_str(&w, "label", tpl->label);
    jw_kv_double(&w, "confidence", chosen->score, 3);
    jw_kv_int(&w, "freq_offset", chosen->bin_offset);
    jw_kv_uint(&w, "bandwidth_bins", chosen->bandwidth_bins);
    jw_kv_uint(&w, "frame_bins", live_n);
    jw_kv_uint(&w, "template_id", tpl->id);
    jw_kv_double(&w, "snr_db", chosen->snr_db, 1);
    jw_kv_double(&w, "prom_db", chosen->prom_db, 1);
    jw_kv_double(&w, "margin", margin, 3);
    jw_kv_double(&w, "hz_per_bin", a->live_hz, 1);
    jw_kv_str(&w, "state", state);
    jw_kv_double(&w, "bg_age_s", bg_age_s, 0);
    jw_kv_bool(&w, "online", true);
    jw_obj_close(&w);
    mqtt_pub_json("result", &w);
    publish_shapes(a, tpl, t, resid, chosen);

    if (a->verbose)
        fprintf(stderr, "classify: %s (id=%u) conf=%.3f snr=%.1f dB prom=%.1f dB margin=%.3f bin_offset=%d\n",
                tpl->label, tpl->id, chosen->score, chosen->snr_db, chosen->prom_db, margin, chosen->bin_offset);
}

/* Published every cycle regardless of match outcome — these describe the
 * live signal itself, not a match against a template, so they're useful
 * even when nothing matched ("unknown, but here's what we can tell about
 * it"). bandwidth is reported in bins (matching the existing
 * bandwidth_bins convention for an actual match) so the Dashboard can
 * convert it to Hz with the same math it already has. */
static void publish_features(const struct frame_features *ff, uint32_t live_n)
{
    char buf[64];
    if (!ff->valid) {
        mqtt_pub("feature_bandwidth_bins", "0");
        mqtt_pub("feature_papr_db", "0.0");
        mqtt_pub("feature_flatness", "0.0");
        mqtt_pub("feature_peak_count", "0");
        return;
    }
    snprintf(buf, sizeof(buf), "%u", (uint32_t)(ff->bandwidth_frac * live_n + 0.5));
    mqtt_pub("feature_bandwidth_bins", buf);
    snprintf(buf, sizeof(buf), "%.2f", ff->papr_db);
    mqtt_pub("feature_papr_db", buf);
    snprintf(buf, sizeof(buf), "%.4f", ff->flatness);
    mqtt_pub("feature_flatness", buf);
    snprintf(buf, sizeof(buf), "%u", (uint32_t)ff->peak_count);
    mqtt_pub("feature_peak_count", buf);
}

/* "n/a" (not a number the Dashboard should try to parse) until the history
 * ring has enough samples — see history_compute(). */
static void publish_temporal(const struct temporal_features *tf)
{
    char buf[64];
    if (tf->duty_valid) {
        snprintf(buf, sizeof(buf), "%.1f", tf->duty_cycle_pct);
        mqtt_pub("feature_duty_cycle_pct", buf);
    } else {
        mqtt_pub("feature_duty_cycle_pct", "n/a");
    }
    if (tf->drift_valid) {
        snprintf(buf, sizeof(buf), "%.3f", tf->drift_bins_per_s);
        mqtt_pub("feature_drift_bins_s", buf);
    } else {
        mqtt_pub("feature_drift_bins_s", "n/a");
    }
}

/* ------------------------------------------------------------------ */
/* WebSocket client                                                    */
/* ------------------------------------------------------------------ */

static void ensure_msg_cap(struct app *a, size_t need)
{
    if (a->msg_cap >= need) return;
    size_t newcap = a->msg_cap ? a->msg_cap * 2 : 65536;
    while (newcap < need) newcap *= 2;
    a->msg_buf = realloc(a->msg_buf, newcap);
    a->msg_cap = newcap;
}


/* A frame counts as "occupied" (something standing out from the noise
 * floor, not just thermal noise) once the residual's peak-to-average
 * ratio clears this floor. Measured on real captures with power units:
 * the residual of an empty band sits at 9.5-10.5 dB (clipping at zero
 * makes the mean small), BLE/Wi-Fi fragments at 15-20 dB, a broadcast
 * band at 24 dB. The old value of 6 dB, set when the ratio was being
 * computed in doubled dB, was below the noise and marked every frame
 * occupied. */
#define OCCUPIED_PAPR_DB 14.0

/* Minimum ring-buffer occupancy/time-span before history_compute() will
 * report an actual number instead of "n/a" — avoids a spurious slope
 * estimate from 2-3 samples right after (re)start. */
#define HISTORY_MIN_SAMPLES 4
#define HISTORY_MIN_SPAN_S  3.0

static void history_push(struct app *a, double t, double peak_bin_frac, bool occupied)
{
    uint32_t idx = a->history_next % FEATURE_HISTORY;
    a->history[idx].t             = t;
    a->history[idx].peak_bin_frac = peak_bin_frac;
    a->history[idx].occupied      = occupied;
    a->history_next++;
    if (a->history_filled < FEATURE_HISTORY) a->history_filled++;
}

/* Regression runs in fractional-bin space (0..1), not raw bin indices —
 * that stays valid even if live_n happened to change mid-history (e.g. a
 * /waterfall FFT-size change), then gets scaled to bins/s using the
 * *current* live_n only at the end, for the reader. */
static struct temporal_features history_compute(const struct app *a, uint32_t live_n)
{
    struct temporal_features out;
    memset(&out, 0, sizeof(out));
    uint32_t n = a->history_filled;
    if (n < HISTORY_MIN_SAMPLES) return out;

    double t0 = a->history[0].t;
    double sum_t = 0, sum_y = 0, sum_tt = 0, sum_ty = 0;
    double t_min = 1e300, t_max = -1e300;
    uint32_t occupied_count = 0;
    for (uint32_t i = 0; i < n; i++) {
        double t = a->history[i].t - t0;
        double y = a->history[i].peak_bin_frac;
        sum_t += t; sum_y += y; sum_tt += t * t; sum_ty += t * y;
        if (t < t_min) t_min = t;
        if (t > t_max) t_max = t;
        if (a->history[i].occupied) occupied_count++;
    }

    out.duty_valid     = true;
    out.duty_cycle_pct = 100.0 * occupied_count / n;

    double span = t_max - t_min;
    if (span >= HISTORY_MIN_SPAN_S) {
        double denom = (double)n * sum_tt - sum_t * sum_t;
        if (fabs(denom) > 1e-9) {
            double slope_frac_per_s = ((double)n * sum_ty - sum_t * sum_y) / denom;
            out.drift_valid       = true;
            out.drift_bins_per_s  = slope_frac_per_s * (double)live_n;
        }
    }
    return out;
}

static void background_reset(struct app *a, uint32_t live_n, const char *why)
{
    free(a->bg); free(a->base); free(a->mask);
    a->bg   = calloc(live_n, sizeof(float));
    a->base = calloc(live_n, sizeof(float));
    a->mask = calloc(live_n, sizeof(uint8_t));
    a->bg_n = live_n;
    a->bg_learned = false;
    a->bg_frames = 0;
    a->bg_t0 = a->bg_last_t = now_s();
    a->locked = false;
    a->history_filled = 0; a->history_next = 0;
    if (a->verbose) fprintf(stderr, "background: reset (%s), learning for %.0f s\n", why, a->learn_s);
}

/* Fold one raw frame into the background lower-envelope tracker. A bin
 * below the estimate pulls it down with BG_ALPHA_DOWN per frame; a bin
 * above it pushes it up slowly: during learning at a rate that settles
 * within learn_s, afterwards with time constant bg_tau_s (0 = never).
 * Because noise is chi-square distributed around its mean, the envelope
 * sits a little below the noise mean, which is why the SNR gate, not a
 * zero residual, is what rejects an empty band. */
#define BG_ALPHA_DOWN 0.3
static void background_update(struct app *a, const float *live, uint32_t live_n)
{
    double t = now_s();
    if (!a->bg || a->bg_n != live_n) background_reset(a, live_n, "frame size");
    if (!a->bg) return;
    double dt = t - a->bg_last_t;
    double up;
    if (a->bg_frames == 0) { memcpy(a->bg, live, live_n * sizeof(float)); a->bg_frames = 1; a->bg_last_t = t; return; }
    if (t - a->bg_t0 < a->learn_s) up = dt / (a->learn_s / 4.0);           /* settles in ~learn_s */
    else if (a->bg_tau_s > 0)       up = dt / a->bg_tau_s;
    else                            up = 0.0;
    if (up > 1.0) up = 1.0;
    float fu = (float)up, fd = (float)BG_ALPHA_DOWN;
    for (uint32_t i = 0; i < live_n; i++) {
        float d = live[i] - a->bg[i];
        a->bg[i] += (d < 0.0f ? fd : fu) * d;
    }
    a->bg_frames++;
    a->bg_last_t = t;
}

/* Receiver baseline: morphological opening of the envelope (rolling
 * minimum then rolling mean, both over 2*half+1 bins). Smooth structure
 * wider than the element survives, narrower lines are removed. */
static void baseline_compute(const float *env, float *base, uint32_t n, uint32_t half)
{
    float *mn = malloc(n * sizeof(float));
    if (!mn) { memcpy(base, env, n * sizeof(float)); return; }
    for (uint32_t i = 0; i < n; i++) {
        uint32_t lo = i > half ? i - half : 0, hi = i + half < n ? i + half : n - 1;
        float m = env[lo];
        for (uint32_t j = lo + 1; j <= hi; j++) if (env[j] < m) m = env[j];
        mn[i] = m;
    }
    double *pre = malloc((n + 1) * sizeof(double));
    if (!pre) { memcpy(base, mn, n * sizeof(float)); free(mn); return; }
    pre[0] = 0.0;
    for (uint32_t i = 0; i < n; i++) pre[i + 1] = pre[i] + mn[i];
    for (uint32_t i = 0; i < n; i++) {
        uint32_t lo = i > half ? i - half : 0, hi = i + half + 1 < n ? i + half + 1 : n;
        base[i] = (float)((pre[hi] - pre[lo]) / (hi - lo));
    }
    free(pre); free(mn);
}

/* Called once when the learning period ends: build the spur mask. The
 * centre bins are always masked (DC); other bins hot in the envelope are
 * counted against the RX frequency, and bins hot on >= 2 frequencies are
 * masked as receiver spurs. */
static void background_learned(struct app *a, const char *freq)
{
    uint32_t n = a->bg_n;
    if (a->spur_count && a->spur_n != n) {
        free(a->spur_count); free(a->spur_last_hz);
        a->spur_count = NULL; a->spur_last_hz = NULL;
    }
    if (!a->spur_count) {
        a->spur_count = calloc(n, sizeof(uint8_t));
        a->spur_last_hz = calloc(n, sizeof(double));
        a->spur_n = n;
    }
    double fhz = freq[0] ? atof(freq) : 0.0;
    double far = a->span_hz > 0 ? a->span_hz : 1e12;   /* unknown span: never recount */
    double thr = pow(10.0, a->spur_db / 10.0);
    uint32_t hot = 0, masked = 0;
    for (uint32_t i = 0; i < n; i++) {
        bool is_hot = a->base[i] > 0 && a->bg[i] > a->base[i] * thr;
        if (is_hot) hot++;
        if (is_hot && fhz > 0 && a->spur_count && a->spur_count[i] < 255 &&
            (a->spur_last_hz[i] == 0.0 || fabs(fhz - a->spur_last_hz[i]) > far)) {
            a->spur_count[i]++;
            a->spur_last_hz[i] = fhz;
        }
        a->mask[i] = (a->spur_count && a->spur_count[i] >= 2) ? 1 : 0;
        /* DC: the fftshifted frame has DC at float index fft_size/2, i.e.
         * bins-array index fft_size/2 - 1 (the step word took float 0). */
        uint32_t dc = (n + 1) / 2 - 1;
        if (i + 2 >= dc && i <= dc + 2) a->mask[i] = 1;
        if (a->mask[i]) masked++;
    }
    a->bg_learned = true;
    char buf[256];
    struct jw w;
    jw_init(&w, buf, sizeof(buf));
    jw_obj_open(&w);
    jw_kv_str(&w, "frequency", freq[0] ? freq : "");
    jw_kv_uint(&w, "bins", n);
    jw_kv_uint(&w, "fft_size", n + 1);
    jw_kv_double(&w, "hz_per_bin", a->live_hz, 1);
    jw_kv_uint(&w, "hot", hot);
    jw_kv_uint(&w, "masked", masked);
    jw_obj_close(&w);
    mqtt_pub_json("background", &w);
    if (a->verbose) fprintf(stderr, "background: learned (%s), %u hot bins, %u masked\n",
                            freq[0] ? freq : "no frequency", hot, masked);
}

/* ------------------------------------------------------------------ */
/* Observation — one bounded JSON message per classify cycle, matched or
 * not, carrying everything a consumer needs to interpret the result
 * without guessing: who produced it, when (and by which clock), what
 * frame/grid it describes, which RX configuration the daemon believes
 * was in force (and that this belief is NOT bound to the frame), which
 * template set and feature semantics were used, the decision evidence,
 * and the features with units, scope and validity. Fields that cannot be
 * known are explicit nulls with a reason, never defaults.
 *
 * Schema: OBSERVATION_SCHEMA. Bump the trailing number on any change that
 * is not purely additive. Documented in docs/classifier_telemetry.md. */
/* ------------------------------------------------------------------ */
#define OBSERVATION_SCHEMA "tezuka.classifier.observation/1"
#define OBSERVATION_BUF 6144

static void publish_observation(struct app *a, uint32_t live_n, const char *state, double bg_age_s,
                                const struct frame_features *ff, const struct temporal_features *tf,
                                const struct decision *dec, const struct obs_ctx *oc, bool classified)
{
    static char buf[OBSERVATION_BUF];
    struct jw w;
    jw_init(&w, buf, sizeof(buf));
    a->seq++;

    struct timespec rt, mono;
    clock_gettime(CLOCK_REALTIME, &rt);
    clock_gettime(CLOCK_MONOTONIC, &mono);

    jw_obj_open(&w);
    jw_kv_str(&w, "schema", OBSERVATION_SCHEMA);
    jw_kv_uint(&w, "seq", a->seq);

    jw_key(&w, "time"); jw_obj_open(&w);
    jw_kv_double(&w, "unix_s", rt.tv_sec + rt.tv_nsec / 1e9, 3);
    jw_kv_double(&w, "mono_s", mono.tv_sec + mono.tv_nsec / 1e9, 3);
    jw_kv_str(&w, "source", "CLOCK_REALTIME");
    /* The daemon cannot see whether chrony/NTP/PPS currently disciplines
     * the system clock; it reports the clock it used, not its quality. */
    jw_kv_null(&w, "disciplined");
    jw_kv_str(&w, "disciplined_reason", "not observed by the daemon; check chronyc on the sensor");
    jw_kv_bool(&w, "replay", a->replay_path != NULL);
    jw_obj_close(&w);

    jw_key(&w, "sensor"); jw_obj_open(&w);
    jw_kv_str(&w, "hostname", a->hostname);
    if (oc->serial[0]) jw_kv_str(&w, "serial", oc->serial);
    else { jw_kv_null(&w, "serial"); jw_kv_str(&w, "serial_reason", "state/main/serial not seen on MQTT"); }
    if (a->boot_id[0]) jw_kv_str(&w, "boot_id", a->boot_id);
    else { jw_kv_null(&w, "boot_id"); jw_kv_str(&w, "boot_id_reason", "/proc/sys/kernel/random/boot_id unreadable"); }
    char session[96];
    snprintf(session, sizeof(session), "%.8s-%ld-%.0f", a->boot_id[0] ? a->boot_id : "noboot", (long)getpid(), a->mono_at_start);
    jw_kv_str(&w, "session_id", session);
    jw_kv_int(&w, "pid", getpid());
    jw_obj_close(&w);

    jw_key(&w, "input"); jw_obj_open(&w);
    jw_kv_str(&w, "source", "maia-httpd /waterfall");
    jw_kv_str(&w, "units", "linear_power_integrated");
    jw_kv_str(&w, "units_note", "unitless receiver level, not dBm; dB values are 10*log10 of power ratios");
    jw_kv_uint(&w, "fft_size", a->fft_size);
    jw_kv_uint(&w, "bins_used", live_n);
    jw_kv_uint(&w, "bin0_offset", 1);
    jw_kv_str(&w, "bin0_note", "float 0 is the fastlock/sweep step word replacing the -fs/2 edge bin");
    jw_kv_double(&w, "first_float", a->first_float, 0);
    jw_kv_uint(&w, "dc_index", (live_n + 1) / 2 - 1);
    if (a->live_hz > 0) jw_kv_double(&w, "hz_per_bin", a->live_hz, 3);
    else { jw_kv_null(&w, "hz_per_bin"); jw_kv_str(&w, "hz_per_bin_reason", "span unknown (no state/rx/span and no -S)"); }
    jw_obj_close(&w);

    /* RX configuration as last seen on retained MQTT. The frame carries
     * no tag that ties it to these values, so config_bound is false by
     * construction; config_generation lets a consumer at least detect
     * that a change happened between two observations. */
    jw_key(&w, "config"); jw_obj_open(&w);
    jw_kv_bool(&w, "bound", false);
    jw_kv_str(&w, "bound_reason", "RX settings come from retained state/rx/* topics polled by api_controller.sh; the /waterfall frame carries no configuration tag, so a frame near a retune may describe the previous settings");
    jw_kv_uint(&w, "generation", oc->config_generation);
    if (oc->freq[0]) jw_kv_double(&w, "frequency_hz", atof(oc->freq), 0);
    else { jw_kv_null(&w, "frequency_hz"); jw_kv_str(&w, "frequency_reason", "state/rx/frequency not seen"); }
    if (a->span_hz > 0) jw_kv_double(&w, "span_hz", a->span_hz, 0);
    else jw_kv_null(&w, "span_hz");
    if (oc->sampling[0]) jw_kv_double(&w, "sampling_hz", atof(oc->sampling), 0);
    else jw_kv_null(&w, "sampling_hz");
    if (oc->rfinput[0]) jw_kv_str(&w, "rfinput", oc->rfinput);
    else jw_kv_null(&w, "rfinput");
    jw_key(&w, "sweep"); jw_obj_open(&w);
    if (oc->sweep_activate[0]) jw_kv_bool(&w, "activate", strcmp(oc->sweep_activate, "1") == 0);
    else jw_kv_null(&w, "activate");
    if (oc->sweep_engaged[0]) jw_kv_bool(&w, "engaged", strcmp(oc->sweep_engaged, "1") == 0);
    else jw_kv_null(&w, "engaged");
    jw_kv_str(&w, "note", "sweep/DDC frames are not interpreted by this daemon; when engaged, span and hz_per_bin describe one step, not the sweep");
    jw_obj_close(&w);
    jw_obj_close(&w);

    jw_key(&w, "templates"); jw_obj_open(&w);
    jw_kv_str(&w, "path", a->templates_path);
    jw_kv_str(&w, "status", a->templates.load_status ? a->templates.load_status : "not_loaded");
    if (a->templates.items) {
        jw_kv_str(&w, "sha256", a->templates.sha256_hex);
        jw_kv_uint(&w, "version", a->templates.version);
        jw_kv_uint(&w, "count", a->templates.count);
        jw_kv_uint(&w, "canonical_n", a->templates.canonical_n);
    } else {
        jw_kv_null(&w, "sha256");
        jw_kv_str(&w, "sha256_reason", a->templates.load_error[0] ? a->templates.load_error : "no template file loaded");
        jw_kv_uint(&w, "count", 0);
    }
    jw_kv_str(&w, "feature_semantics", FEATURE_SEMANTICS);
    jw_obj_close(&w);

    jw_key(&w, "decision"); jw_obj_open(&w);
    jw_kv_str(&w, "state", state);
    jw_kv_bool(&w, "classified", classified);
    if (dec->tpl) {
        jw_kv_str(&w, "label", dec->tpl->label);
        jw_kv_bool(&w, "accepted", true);
        jw_kv_uint(&w, "template_id", dec->tpl->id);
        jw_kv_double(&w, "score", dec->chosen->score, 3);
        jw_kv_double(&w, "snr_db", dec->chosen->snr_db, 1);
        jw_kv_double(&w, "prom_db", dec->chosen->prom_db, 1);
        jw_kv_int(&w, "bin_offset", dec->chosen->bin_offset);
        jw_kv_uint(&w, "bandwidth_bins", dec->chosen->bandwidth_bins);
        jw_kv_double(&w, "gate_factor", dec->gate_factor, 3);
    } else {
        jw_kv_str(&w, "label", "unknown");
        jw_kv_bool(&w, "accepted", false);
        jw_kv_null(&w, "template_id");
    }
    jw_kv_double(&w, "threshold", a->threshold, 3);
    jw_kv_double(&w, "hold_margin", a->hold_margin, 3);
    jw_kv_double(&w, "margin", dec->margin, 3);
    jw_key(&w, "best");
    if (dec->best) {
        jw_obj_open(&w);
        jw_kv_uint(&w, "template_id", a->templates.items ? a->templates.items[dec->best->template_idx].id : 0);
        jw_kv_double(&w, "score", dec->best->score, 3);
        jw_kv_double(&w, "snr_db", dec->best->snr_db, 1);
        jw_kv_double(&w, "prom_db", dec->best->prom_db, 1);
        jw_obj_close(&w);
    } else jw_null(&w);
    jw_key(&w, "hysteresis"); jw_obj_open(&w);
    jw_kv_bool(&w, "locked", a->locked);
    if (a->locked) jw_kv_uint(&w, "locked_template_id", a->locked_template_id); else jw_kv_null(&w, "locked_template_id");
    jw_kv_bool(&w, "held", dec->held);
    jw_kv_bool(&w, "took_over", dec->took_over);
    jw_obj_close(&w);
    jw_key(&w, "gates"); jw_obj_open(&w);
    jw_kv_double(&w, "snr_min_db", a->snr_min_db, 1);
    jw_kv_double(&w, "prom_min_db", a->prom_min_db, 1);
    jw_kv_str(&w, "scope", "gates describe the matched window of this frame, not the emitter");
    jw_obj_close(&w);
    jw_obj_close(&w);

    jw_key(&w, "features"); jw_obj_open(&w);
    jw_kv_str(&w, "scope", "residual = live power minus learned receiver baseline, spur bins masked");
    jw_kv_bool(&w, "valid", ff->valid);
    if (ff->valid) {
        jw_kv_double(&w, "bandwidth_frac", ff->bandwidth_frac, 6);
        jw_kv_double(&w, "bandwidth_hz", a->live_hz > 0 ? ff->bandwidth_frac * live_n * a->live_hz : NAN, 1);
        jw_kv_double(&w, "spectral_papr_db", ff->papr_db, 2);
        jw_kv_double(&w, "flatness", ff->flatness, 4);
        jw_kv_uint(&w, "peak_count", (unsigned long long)ff->peak_count);
        jw_kv_uint(&w, "peak_bin", ff->peak_bin);
    } else {
        jw_kv_null(&w, "bandwidth_frac"); jw_kv_null(&w, "bandwidth_hz"); jw_kv_null(&w, "spectral_papr_db");
        jw_kv_null(&w, "flatness"); jw_kv_null(&w, "peak_count"); jw_kv_null(&w, "peak_bin");
        jw_kv_str(&w, "invalid_reason", "residual has no energy (empty band or still learning)");
    }
    jw_kv_str(&w, "units", "bandwidth_frac: fraction of bins_used at -10 dB; spectral_papr_db: peak bin power over mean bin power in dB (a spectral ratio, NOT time-domain IQ PAPR); flatness: geometric/arithmetic mean of power (0..1); peak_count: local maxima within -15 dB of the peak");
    jw_obj_close(&w);

    jw_key(&w, "temporal"); jw_obj_open(&w);
    jw_kv_uint(&w, "history_samples", a->history_filled);
    if (tf->drift_valid) jw_kv_double(&w, "drift_bins_per_s", tf->drift_bins_per_s, 3);
    else { jw_kv_null(&w, "drift_bins_per_s"); jw_kv_str(&w, "drift_reason", "fewer than 4 samples or under 3 s of history"); }
    if (tf->duty_valid) jw_kv_double(&w, "occupancy_pct", tf->duty_cycle_pct, 1);
    else { jw_kv_null(&w, "occupancy_pct"); jw_kv_str(&w, "occupancy_reason", "fewer than 4 history samples"); }
    jw_kv_str(&w, "occupancy_note", "sampled heuristic: share of the last cycles (about 1/s) whose residual PAPR exceeded 14 dB; not a measured duty cycle");
    jw_obj_close(&w);

    jw_key(&w, "background"); jw_obj_open(&w);
    jw_kv_bool(&w, "learned", a->bg_learned);
    jw_kv_double(&w, "age_s", bg_age_s, 1);
    jw_kv_uint(&w, "frames", a->bg_frames);
    jw_obj_close(&w);

    jw_key(&w, "health"); jw_obj_open(&w);
    jw_kv_uint(&w, "publish_errors", g_pub_errors);
    jw_kv_uint(&w, "publish_ok", g_pub_ok);
    jw_kv_bool(&w, "waterfall_connected", a->connected);
    jw_obj_close(&w);

    jw_obj_close(&w);
    mqtt_pub_json("observation", &w);
}

static void handle_frame(struct app *a, const float *f, size_t n_floats)
{
    if (n_floats < 2) return; /* need at least the step index + 1 bin */
    uint32_t live_n = (uint32_t)(n_floats - 1);
    const float *live = f + 1;
    a->fft_size = (uint32_t)n_floats;
    a->first_float = f[0];

    /* Retune or explicit relearn requested from the MQTT thread? */
    bool reset = false; const char *why = "";
    struct obs_ctx oc;
    pthread_mutex_lock(&a->lock);
    if (a->tune_changed) { reset = true; why = "RX settings changed"; a->tune_changed = false; }
    if (a->relearn)      { reset = true; why = "cmd/classifier/background learn"; a->relearn = false; }
    if (a->span_hz_mqtt > 0) a->span_hz = a->span_hz_mqtt;
    obs_ctx_copy_locked(a, &oc);
    pthread_mutex_unlock(&a->lock);
    if (reset) background_reset(a, live_n, why);
    /* The grid is span / fft_size: the producer's FFT has fft_size bins
     * over the span, and the step word only hides one of them. */
    a->live_hz = a->span_hz > 0 ? a->span_hz / (double)n_floats : 0.0;

    /* The background sees every frame (cheap: one multiply-add per bin);
     * only the correlate+publish cycle below is throttled. */
    background_update(a, live, live_n);

    double t_now = now_s();
    if (a->last_run_t != 0.0 && t_now - a->last_run_t < a->min_interval_s)
        return;
    a->last_run_t = t_now;

    double bg_age = t_now - a->bg_t0;
    bool learning = bg_age < a->learn_s;

    /* Baseline from the envelope (once per cycle), spur mask at the end
     * of learning. */
    uint32_t half = 1;
    if (a->live_hz > 0) half = (uint32_t)(a->base_width_hz / a->live_hz / 2.0 + 0.5);
    else half = live_n / 16;
    if (half < 1) half = 1;
    if (half > live_n / 2) half = live_n / 2;
    baseline_compute(a->bg, a->base, live_n, half);
    if (!learning && !a->bg_learned) background_learned(a, oc.freq);
    templates_prepare(&a->templates, a->live_hz, live_n);

    /* Masked live: spur bins pinned to the baseline so they count as
     * empty in the residual, SNR and prominence. */
    float *lv = malloc(live_n * sizeof(float));
    float *resid = malloc(live_n * sizeof(float));
    if (!lv || !resid) { free(lv); free(resid); return; }
    float peak = 0.0f;
    for (uint32_t i = 0; i < live_n; i++) {
        lv[i] = a->mask[i] ? a->base[i] : live[i];
        float r = lv[i] - a->base[i];
        resid[i] = r > 0.0f ? r : 0.0f;
        if (resid[i] > peak) peak = resid[i];
    }
    if (peak > 0.0f) for (uint32_t i = 0; i < live_n; i++) resid[i] /= peak;

    /* Features describe the residual, i.e. whatever is on air beyond the
     * learned background, not the receiver's own passband. Published
     * every cycle whether or not anything matches. */
    struct frame_features ff = extract_frame_features(resid, live_n);
    publish_features(&ff, live_n);

    double peak_bin_frac = ff.valid && live_n > 1 ? (double)ff.peak_bin / (double)(live_n - 1) : 0.0;
    bool occupied = ff.valid && ff.papr_db >= OCCUPIED_PAPR_DB;
    history_push(a, t_now, peak_bin_frac, occupied);
    struct temporal_features tf = history_compute(a, live_n);
    publish_temporal(&tf);

    struct decision dec;
    if (learning || !a->templates.items || a->templates.count == 0 || peak <= 0.0f) {
        struct match_result none = { .found = false };
        const char *state = learning ? "learning" : "idle";
        publish_result(a, &none, &(struct templates){ .count = 0 }, live_n, state, bg_age, NULL, &ff, &dec);
        publish_observation(a, live_n, state, bg_age, &ff, &tf, &dec, &oc, false);
        free(resid); free(lv);
        return;
    }

    struct match_result *hits = calloc(a->templates.count, sizeof(*hits));
    if (hits) {
        classify_all(resid, lv, a->base, live_n, &a->templates, &ff, a->snr_min_db, hits);
        publish_result(a, hits, &a->templates, live_n, "ok", bg_age, resid, &ff, &dec);
        publish_observation(a, live_n, "ok", bg_age, &ff, &tf, &dec, &oc, true);
        free(hits);
    }
    free(resid); free(lv);
}

/* -r FILE: replay raw float32 frames (the exact /waterfall payload layout,
 * replay_floats per frame) at a virtual clock of replay_fps, then exit.
 * With -B none nothing is published, results go to stdout. */
static int replay_file(struct app *a)
{
    FILE *f = fopen(a->replay_path, "rb");
    if (!f) { perror(a->replay_path); return 1; }
    size_t n = a->replay_floats;
    float *frame = malloc(n * sizeof(float));
    if (!frame) { fclose(f); return 1; }
    size_t count = 0;
    while (fread(frame, sizeof(float), n, f) == n) {
        a->replay_t = count / a->replay_fps;
        handle_frame(a, frame, n);
        count++;
    }
    fprintf(stderr, "replay: %zu frames processed\n", count);
    free(frame);
    fclose(f);
    return 0;
}

static int ws_client_cb(struct lws *wsi, enum lws_callback_reasons reason,
                         void *user, void *in, size_t len)
{
    (void)user;
    struct app *a = &A;

    switch (reason) {
    case LWS_CALLBACK_CLIENT_ESTABLISHED:
        fprintf(stderr, "classifier: connected to /waterfall\n");
        a->msg_len = 0;
        a->connected = true;
        break;

    case LWS_CALLBACK_CLIENT_RECEIVE: {
        ensure_msg_cap(a, a->msg_len + len);
        memcpy(a->msg_buf + a->msg_len, in, len);
        a->msg_len += len;

        if (lws_is_final_fragment(wsi)) {
            if (a->msg_len >= sizeof(float) * 2 && (a->msg_len % sizeof(float)) == 0)
                handle_frame(a, (const float *)a->msg_buf, a->msg_len / sizeof(float));
            a->msg_len = 0;
        }
        break;
    }

    case LWS_CALLBACK_CLIENT_CONNECTION_ERROR:
        fprintf(stderr, "classifier: connection error: %s\n", in ? (char *)in : "?");
        a->connected = false;
        break;

    case LWS_CALLBACK_CLIENT_CLOSED:
        fprintf(stderr, "classifier: /waterfall connection closed, will retry\n");
        a->connected = false;
        break;

    default:
        break;
    }
    return 0;
}

static struct lws_protocols protocols[] = {
    { "waterfall-client", ws_client_cb, 0, 0, 0, NULL, 0 },
    { NULL, NULL, 0, 0, 0, NULL, 0 }
};

static void on_signal(int s)
{
    (void)s;
    A.running = false;
}

static void usage(const char *prog)
{
    fprintf(stderr,
        "Usage: %s [OPTIONS]\n"
        "  -H HOST   maia-httpd host        (default: localhost)\n"
        "  -p PORT   maia-httpd port        (default: 80)\n"
        "  -P PATH   waterfall WS path      (default: /waterfall)\n"
        "  -f FILE   templates file         (default: /mnt/jffs2/classifier/templates.bin)\n"
        "  -B HOST   MQTT broker host       (default: localhost)\n"
        "  -q PORT   MQTT broker port       (default: 1883)\n"
        "  -c THRESH match confidence floor (default: 0.6)\n"
        "  -m MARGIN hysteresis hold margin (default: 0.15; holds a lock until\n"
        "            score < THRESH - MARGIN, so a borderline score doesn't\n"
        "            flip found/unknown every cycle)\n"
        "  -i SEC    min correlate interval (default: 1.0)\n"
        "  -S HZ     frame span in Hz when MQTT cannot supply it (replay)\n"
        "  -w HZ     receiver-baseline structuring element width (default: 4e6)\n"
        "  -x DB     spur candidate threshold over the baseline (default: 8)\n"
        "  -s DB     min window SNR over background (default: 6)\n"
        "  -g DB     min prominence over the window flanks (default: 4)\n"
        "  -l SEC    background learning time (default: 10)\n"
        "  -a SEC    background adaptation time constant (default: 300, 0 = frozen)\n"
        "  -r FILE   replay raw float32 frames from FILE (offline test)\n"
        "  -n N      floats per replayed frame (default: 4096)\n"
        "  -F FPS    virtual frame rate for replay (default: 7.5)\n"
        "  -T PREFIX MQTT topic prefix (default: state/classifier/); a non-default\n"
        "            prefix also moves the command topic to PREFIX cmd/background and\n"
        "            derives its own MQTT client id, for a side-by-side test instance\n"
        "  -V FILE   validate FILE as a template set, print a JSON report, exit 0/2\n"
        "  -v        verbose\n"
        "  -h        this help\n", prog);
}

static void read_identity(struct app *a)
{
    if (gethostname(a->hostname, sizeof(a->hostname)) != 0) snprintf(a->hostname, sizeof(a->hostname), "unknown");
    a->hostname[sizeof(a->hostname) - 1] = '\0';
    a->boot_id[0] = '\0';
    FILE *f = fopen("/proc/sys/kernel/random/boot_id", "r");
    if (f) {
        if (fgets(a->boot_id, sizeof(a->boot_id), f)) {
            size_t n = strcspn(a->boot_id, "\r\n");
            a->boot_id[n] = '\0';
        } else a->boot_id[0] = '\0';
        fclose(f);
    }
    struct timespec mono;
    clock_gettime(CLOCK_MONOTONIC, &mono);
    a->mono_at_start = mono.tv_sec + mono.tv_nsec / 1e9;
}

int main(int argc, char **argv)
{
    const char *host = "localhost";
    int port = 80;
    const char *path = "/waterfall";
    const char *mqtt_host = "localhost";
    int mqtt_port = 1883;
    A.templates_path = "/mnt/jffs2/classifier/templates.bin";
    A.threshold       = 0.6;
    A.hold_margin     = 0.15;
    A.min_interval_s  = 1.0;
    A.snr_min_db      = 6.0;
    A.base_width_hz   = 4e6;
    A.spur_db         = 8.0;
    A.prom_min_db     = 4.0;
    A.learn_s         = 10.0;
    A.bg_tau_s        = 300.0;
    A.replay_floats   = 4096;
    A.replay_fps      = 7.5;
    A.verbose         = false;
    A.locked          = false;
    pthread_mutex_init(&A.lock, NULL);

    const char *validate_path = NULL;
    int opt;
    while ((opt = getopt(argc, argv, "H:p:P:f:B:q:c:m:i:s:g:l:a:r:n:F:S:w:x:T:V:vh")) != -1) {
        switch (opt) {
        case 'T': {
            size_t l = strlen(optarg);
            if (l == 0 || l > 120 || optarg[l - 1] != '/' || strchr(optarg, '#') || strchr(optarg, '+')) {
                fprintf(stderr, "classifier: -T PREFIX must end in '/' and contain no wildcards\n");
                return 1;
            }
            g_topic_prefix = optarg;
            if (strcmp(optarg, TOPIC_PREFIX_DEFAULT) != 0)
                snprintf(g_background_cmd, sizeof(g_background_cmd), "%scmd/background", optarg);
            break;
        }
        case 'V': validate_path      = optarg;      break;
        case 'H': host              = optarg;      break;
        case 'p': port              = atoi(optarg); break;
        case 'P': path              = optarg;      break;
        case 'f': A.templates_path  = optarg;      break;
        case 'B': mqtt_host         = optarg;      break;
        case 'q': mqtt_port         = atoi(optarg); break;
        case 'c': A.threshold       = atof(optarg); break;
        case 'm': A.hold_margin     = atof(optarg); break;
        case 'i': A.min_interval_s  = atof(optarg); break;
        case 's': A.snr_min_db      = atof(optarg); break;
        case 'S': A.span_hz         = atof(optarg); break;
        case 'w': A.base_width_hz   = atof(optarg); break;
        case 'x': A.spur_db         = atof(optarg); break;
        case 'g': A.prom_min_db     = atof(optarg); break;
        case 'l': A.learn_s         = atof(optarg); break;
        case 'a': A.bg_tau_s        = atof(optarg); break;
        case 'r': A.replay_path     = optarg;      break;
        case 'n': A.replay_floats   = (uint32_t)atoi(optarg); break;
        case 'F': A.replay_fps      = atof(optarg); break;
        case 'v': A.verbose         = true;         break;
        case 'h': usage(argv[0]); return 0;
        default:  usage(argv[0]); return 1;
        }
    }

    if (validate_path) return validate_mode(validate_path);

    signal(SIGINT,  on_signal);
    signal(SIGTERM, on_signal);

    A.start_t = now_s();
    read_identity(&A);

    if (strcmp(mqtt_host, "none") != 0 && mqtt_init(mqtt_host, mqtt_port, A.verbose) != 0) return 1;

    if (templates_load(A.templates_path, &A.templates) != 0)
        fprintf(stderr, "classifier: no templates loaded (%s: %s) — will publish \"unknown\" until "
                         "cmd/classifier/templates installs a valid set and the daemon is restarted\n",
                A.templates_path, A.templates.load_status ? A.templates.load_status : "?");

    if (A.replay_path) {
        int rc = replay_file(&A);
        mqtt_shutdown();
        templates_free(&A.templates);
        free(A.bg); free(A.base); free(A.mask); free(A.spur_count); free(A.spur_last_hz);
        return rc;
    }

    struct lws_context_creation_info info;
    memset(&info, 0, sizeof(info));
    info.port      = CONTEXT_PORT_NO_LISTEN;
    info.protocols = protocols;
    info.gid       = -1;
    info.uid       = -1;

    struct lws_context *ctx = lws_create_context(&info);
    if (!ctx) { fprintf(stderr, "classifier: lws context create failed\n"); return 1; }

    A.running = true;

    while (A.running) {
        struct lws_client_connect_info ccinfo;
        memset(&ccinfo, 0, sizeof(ccinfo));
        ccinfo.context  = ctx;
        ccinfo.address  = host;
        ccinfo.port     = port;
        ccinfo.path     = path;
        ccinfo.host     = host;
        ccinfo.origin   = host;
        ccinfo.protocol = protocols[0].name;
        ccinfo.pwsi     = NULL;

        A.connected = false;
        struct lws *wsi = lws_client_connect_via_info(&ccinfo);
        if (!wsi) {
            fprintf(stderr, "classifier: connect failed, retrying in 5s\n");
            sleep(5);
            continue;
        }

        /* Pump the event loop until ESTABLISHED flips connected true, or
         * we give up and retry. Once connected, keep pumping until
         * CLOSED/CONNECTION_ERROR flips it back false — that's the
         * signal to break out and reconnect. */
        double connect_start = now_s();
        bool ever_connected = false;
        while (A.running) {
            lws_service(ctx, 200);
            if (A.connected) ever_connected = true;
            if (ever_connected && !A.connected) break;      /* dropped: reconnect */
            if (!ever_connected && now_s() - connect_start > 5.0) break; /* handshake never completed */
        }
        if (A.running && !ever_connected) sleep(5);
    }

    mqtt_shutdown();
    templates_free(&A.templates);
    free(A.bg); free(A.base); free(A.mask); free(A.spur_count); free(A.spur_last_hz);
    free(A.msg_buf);
    lws_context_destroy(ctx);
    return 0;
}
