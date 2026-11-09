/*
 * jsonw.h — tiny bounded JSON writer for the daemon's MQTT payloads.
 *
 * Writes into a caller-provided buffer, never past its end: once a write
 * would overflow, `overflow` latches and jw_ok() returns false, so the
 * caller publishes nothing rather than a truncated document. Strings are
 * escaped per RFC 8259 (", \, control bytes as \u00XX); invalid UTF-8
 * sequences are replaced by U+FFFD so the output is always valid UTF-8
 * JSON whatever the input bytes. Non-finite numbers become null.
 */
#ifndef JSONW_H
#define JSONW_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define JW_MAX_DEPTH 16

struct jw {
    char  *buf;
    size_t cap;
    size_t len;
    bool   overflow;
    int    depth;
    bool   first[JW_MAX_DEPTH + 1];   /* first element at this depth not yet written */
    bool   in_key[JW_MAX_DEPTH + 1];  /* a key was written, value expected */
};

void jw_init(struct jw *w, char *buf, size_t cap);
void jw_obj_open(struct jw *w);
void jw_obj_close(struct jw *w);
void jw_arr_open(struct jw *w);
void jw_arr_close(struct jw *w);
void jw_key(struct jw *w, const char *key);
void jw_str(struct jw *w, const char *s);           /* NUL-terminated */
void jw_strn(struct jw *w, const char *s, size_t n);
void jw_int(struct jw *w, long long v);
void jw_uint(struct jw *w, unsigned long long v);
void jw_double(struct jw *w, double v, int decimals); /* fixed decimals; NaN/Inf -> null */
void jw_bool(struct jw *w, bool v);
void jw_null(struct jw *w);
void jw_raw(struct jw *w, const char *json);        /* pre-formed JSON value */
bool jw_ok(const struct jw *w);                     /* complete and not overflowed */

/* Convenience: key + value in one call. */
void jw_kv_str(struct jw *w, const char *k, const char *v);
void jw_kv_int(struct jw *w, const char *k, long long v);
void jw_kv_uint(struct jw *w, const char *k, unsigned long long v);
void jw_kv_double(struct jw *w, const char *k, double v, int decimals);
void jw_kv_bool(struct jw *w, const char *k, bool v);
void jw_kv_null(struct jw *w, const char *k);

#endif
