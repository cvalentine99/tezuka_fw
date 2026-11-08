/* scft.c — see scft.h. */
#define _GNU_SOURCE
#include "scft.h"
#include "sha256.h"

#include <errno.h>
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

static void seterr(char *err, size_t errlen, const char *fmt, ...)
{
    if (!err || errlen == 0) return;
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(err, errlen, fmt, ap);
    va_end(ap);
}

const char *scft_status_name(enum scft_status s)
{
    switch (s) {
    case SCFT_OK:           return "ok";
    case SCFT_E_IO:         return "io";
    case SCFT_E_MAGIC:      return "bad_magic";
    case SCFT_E_VERSION:    return "unsupported_version";
    case SCFT_E_HEADER:     return "bad_header";
    case SCFT_E_SIZE:       return "bad_size";
    case SCFT_E_LABEL:      return "bad_label";
    case SCFT_E_ID:         return "duplicate_id";
    case SCFT_E_DATA:       return "non_finite_data";
    case SCFT_E_NORM:       return "not_normalized";
    case SCFT_E_RANGE:      return "bad_range";
    case SCFT_E_RESOLUTION: return "bad_resolution";
    case SCFT_E_NOMEM:      return "out_of_memory";
    }
    return "unknown";
}

bool scft_utf8_valid(const unsigned char *s, size_t n)
{
    size_t i = 0;
    while (i < n) {
        unsigned char c = s[i];
        if (c < 0x80) { i++; continue; }
        size_t need;
        unsigned char min2;
        if ((c & 0xe0) == 0xc0)      { need = 1; min2 = 0x80; if (c < 0xc2) return false; }   /* overlong 2-byte */
        else if ((c & 0xf0) == 0xe0) { need = 2; min2 = (c == 0xe0) ? 0xa0 : 0x80; }
        else if ((c & 0xf8) == 0xf0) { need = 3; min2 = (c == 0xf0) ? 0x90 : 0x80; if (c > 0xf4) return false; }
        else return false;
        if (i + need >= n) return false;   /* continuation bytes must fit */
        for (size_t k = 1; k <= need; k++) {
            unsigned char cc = s[i + k];
            if ((cc & 0xc0) != 0x80) return false;
            if (k == 1 && cc < min2) return false;                 /* overlong 3/4-byte */
            if (k == 1 && c == 0xed && cc >= 0xa0) return false;   /* UTF-16 surrogates */
            if (k == 1 && c == 0xf4 && cc >= 0x90) return false;   /* > U+10FFFF */
        }
        i += need + 1;
    }
    return true;
}

int scft_label_check(const char label[SCFT_LABEL_LEN])
{
    size_t len = 0;
    while (len < SCFT_LABEL_LEN && label[len] != '\0') len++;
    if (len == SCFT_LABEL_LEN) return -1;   /* no NUL terminator */
    if (len == 0) return -1;                /* empty */
    for (size_t i = 0; i < len; i++) {
        unsigned char c = (unsigned char)label[i];
        if (c < 0x20 || c == 0x7f) return -1;
    }
    if (!scft_utf8_valid((const unsigned char *)label, len)) return -1;
    return (int)len;
}

static uint32_t rd_u32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static float rd_f32(const uint8_t *p)
{
    uint32_t u = rd_u32(p);
    float f;
    memcpy(&f, &u, sizeof(f));
    return f;
}

void scft_free(struct scft_file *f)
{
    if (!f) return;
    if (f->items) {
        for (uint32_t i = 0; i < f->count; i++) free(f->items[i].data);
        free(f->items);
    }
    memset(f, 0, sizeof(*f));
}

void scft_sha256_hex(const struct scft_file *f, char out[65])
{
    static const char hx[] = "0123456789abcdef";
    for (int i = 0; i < 32; i++) {
        out[2 * i]     = hx[f->sha256[i] >> 4];
        out[2 * i + 1] = hx[f->sha256[i] & 0xf];
    }
    out[64] = '\0';
}

enum scft_status scft_parse(const uint8_t *buf, size_t len, struct scft_file *out, char *err, size_t errlen)
{
    memset(out, 0, sizeof(*out));
    if (len > SCFT_MAX_FILE_BYTES) {
        seterr(err, errlen, "file is %zu bytes, cap is %u", len, SCFT_MAX_FILE_BYTES);
        return SCFT_E_SIZE;
    }
    if (len < 16) { seterr(err, errlen, "file is %zu bytes, header needs 16", len); return SCFT_E_SIZE; }
    if (memcmp(buf, SCFT_MAGIC, 4) != 0) { seterr(err, errlen, "bad magic"); return SCFT_E_MAGIC; }
    uint32_t version = rd_u32(buf + 4), count = rd_u32(buf + 8), canonical_n = rd_u32(buf + 12);
    if (version < 1 || version > 3) { seterr(err, errlen, "unsupported version %u", version); return SCFT_E_VERSION; }
    if (count == 0 || count > SCFT_MAX_COUNT) {
        seterr(err, errlen, "count %u out of [1, %u]", count, SCFT_MAX_COUNT);
        return SCFT_E_HEADER;
    }
    if (canonical_n < SCFT_MIN_CANONICAL_N || canonical_n > SCFT_MAX_CANONICAL_N) {
        seterr(err, errlen, "canonical_n %u out of [%u, %u]", canonical_n, SCFT_MIN_CANONICAL_N, SCFT_MAX_CANONICAL_N);
        return SCFT_E_HEADER;
    }
    /* Exact expected size, computed in 64 bits so nothing can wrap. */
    uint64_t entry = (uint64_t)SCFT_LABEL_LEN + 4 + 4ull * canonical_n;
    if (version >= 2) entry += 8 * 4;
    if (version >= 3) entry += 4;
    uint64_t expected = 16 + entry * count;
    if (expected != (uint64_t)len) {
        seterr(err, errlen, "size %zu does not match header (expected %llu for %u x %u, v%u)",
               len, (unsigned long long)expected, count, canonical_n, version);
        return SCFT_E_SIZE;
    }

    struct scft_template *items = calloc(count, sizeof(*items));
    if (!items) { seterr(err, errlen, "out of memory"); return SCFT_E_NOMEM; }
    out->items = items; out->count = count; out->canonical_n = canonical_n; out->version = version;
    out->size_bytes = len;
    sha256(buf, len, out->sha256);

    const uint8_t *p = buf + 16;
    enum scft_status st = SCFT_OK;
    for (uint32_t i = 0; i < count && st == SCFT_OK; i++) {
        struct scft_template *t = &items[i];
        memcpy(t->label, p, SCFT_LABEL_LEN); p += SCFT_LABEL_LEN;
        if (scft_label_check(t->label) < 0) {
            seterr(err, errlen, "entry %u: label violates the contract (NUL-terminated, non-empty, UTF-8, no control bytes)", i);
            st = SCFT_E_LABEL; break;
        }
        t->id = rd_u32(p); p += 4;
        for (uint32_t j = 0; j < i; j++) {
            if (items[j].id == t->id) {
                seterr(err, errlen, "entry %u: duplicate template_id %u (also entry %u)", i, t->id, j);
                st = SCFT_E_ID; break;
            }
        }
        if (st != SCFT_OK) break;
        t->data = malloc((size_t)canonical_n * sizeof(float));
        if (!t->data) { seterr(err, errlen, "out of memory"); st = SCFT_E_NOMEM; break; }
        double sum = 0.0, sumsq = 0.0;
        for (uint32_t k = 0; k < canonical_n; k++) {
            float v = rd_f32(p); p += 4;
            if (!isfinite(v)) { seterr(err, errlen, "entry %u: sample %u is not finite", i, k); st = SCFT_E_DATA; break; }
            t->data[k] = v;
            sum += v; sumsq += (double)v * v;
        }
        if (st != SCFT_OK) break;
        double mean = sum / canonical_n, norm = sqrt(sumsq);
        if (fabs(mean) > SCFT_NORM_TOL || fabs(norm - 1.0) > SCFT_NORM_TOL) {
            seterr(err, errlen, "entry %u: not normalized (mean %.3g, L2 %.6g; tolerance %g)", i, mean, norm, SCFT_NORM_TOL);
            st = SCFT_E_NORM; break;
        }
        if (version >= 2) {
            float r[8];
            for (int k = 0; k < 8; k++) {
                r[k] = rd_f32(p); p += 4;
                if (!isfinite(r[k])) {
                    seterr(err, errlen, "entry %u: feature range value %d is not finite", i, k);
                    st = SCFT_E_RANGE; break;
                }
            }
            if (st != SCFT_OK) break;
            t->has_gate = true;
            t->bandwidth_frac = (struct scft_range){ r[0], r[1] };
            t->papr_db        = (struct scft_range){ r[2], r[3] };
            t->flatness       = (struct scft_range){ r[4], r[5] };
            t->peak_count     = (struct scft_range){ r[6], r[7] };
        }
        if (version >= 3) {
            float h = rd_f32(p); p += 4;
            if (!isfinite(h) || h < 0.0f || h > SCFT_MAX_HZ_PER_BIN) {
                seterr(err, errlen, "entry %u: hz_per_bin %g invalid (finite, 0..%g)", i, (double)h, (double)SCFT_MAX_HZ_PER_BIN);
                st = SCFT_E_RESOLUTION; break;
            }
            t->hz_per_bin = h;
        }
    }
    if (st != SCFT_OK) { scft_free(out); return st; }
    return SCFT_OK;
}

enum scft_status scft_load(const char *path, struct scft_file *out, char *err, size_t errlen)
{
    memset(out, 0, sizeof(*out));
    FILE *f = fopen(path, "rb");
    if (!f) { seterr(err, errlen, "cannot open %s: %s", path, strerror(errno)); return SCFT_E_IO; }
    struct stat sb;
    if (fstat(fileno(f), &sb) != 0) { seterr(err, errlen, "cannot stat %s: %s", path, strerror(errno)); fclose(f); return SCFT_E_IO; }
    if (!S_ISREG(sb.st_mode)) { seterr(err, errlen, "%s is not a regular file", path); fclose(f); return SCFT_E_IO; }
    if ((uint64_t)sb.st_size > SCFT_MAX_FILE_BYTES) {
        seterr(err, errlen, "%s is %llu bytes, cap is %u", path, (unsigned long long)sb.st_size, SCFT_MAX_FILE_BYTES);
        fclose(f); return SCFT_E_SIZE;
    }
    size_t len = (size_t)sb.st_size;
    uint8_t *buf = malloc(len ? len : 1);
    if (!buf) { seterr(err, errlen, "out of memory"); fclose(f); return SCFT_E_NOMEM; }
    size_t got = fread(buf, 1, len, f);
    /* Reject a file that grew/shrank between stat and read as well. */
    int extra = fgetc(f);
    fclose(f);
    if (got != len || extra != EOF) { seterr(err, errlen, "short/long read of %s", path); free(buf); return SCFT_E_IO; }
    enum scft_status st = scft_parse(buf, len, out, err, errlen);
    free(buf);
    return st;
}
