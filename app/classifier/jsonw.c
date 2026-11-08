/* jsonw.c — see jsonw.h. */
#include "jsonw.h"
#include "scft.h"   /* scft_utf8_valid */

#include <math.h>
#include <stdio.h>
#include <string.h>

static void put(struct jw *w, const char *s, size_t n)
{
    if (w->overflow) return;
    if (w->len + n >= w->cap) { w->overflow = true; return; }
    memcpy(w->buf + w->len, s, n);
    w->len += n;
    w->buf[w->len] = '\0';
}

static void putc_(struct jw *w, char c) { put(w, &c, 1); }

/* Before a value: emit the separator unless this is a key's value. */
static void before_value(struct jw *w)
{
    int d = w->depth;
    if (d > 0 && w->in_key[d]) { w->in_key[d] = false; return; }
    if (d > 0) {
        if (!w->first[d]) putc_(w, ',');
        w->first[d] = false;
    }
}

void jw_init(struct jw *w, char *buf, size_t cap)
{
    memset(w, 0, sizeof(*w));
    w->buf = buf; w->cap = cap;
    if (cap) buf[0] = '\0'; else w->overflow = true;
    w->first[0] = true;
}

static void open_(struct jw *w, char c)
{
    before_value(w);
    if (w->depth >= JW_MAX_DEPTH) { w->overflow = true; return; }
    putc_(w, c);
    w->depth++;
    w->first[w->depth] = true;
    w->in_key[w->depth] = false;
}

static void close_(struct jw *w, char c)
{
    if (w->depth <= 0) { w->overflow = true; return; }
    if (w->in_key[w->depth]) { w->overflow = true; return; } /* dangling key */
    w->depth--;
    putc_(w, c);
}

void jw_obj_open(struct jw *w)  { open_(w, '{'); }
void jw_obj_close(struct jw *w) { close_(w, '}'); }
void jw_arr_open(struct jw *w)  { open_(w, '['); }
void jw_arr_close(struct jw *w) { close_(w, ']'); }

static void write_escaped(struct jw *w, const unsigned char *s, size_t n)
{
    static const char hx[] = "0123456789abcdef";
    putc_(w, '"');
    size_t i = 0;
    while (i < n && !w->overflow) {
        unsigned char c = s[i];
        if (c == '"')       { put(w, "\\\"", 2); i++; }
        else if (c == '\\') { put(w, "\\\\", 2); i++; }
        else if (c == '\n') { put(w, "\\n", 2); i++; }
        else if (c == '\r') { put(w, "\\r", 2); i++; }
        else if (c == '\t') { put(w, "\\t", 2); i++; }
        else if (c < 0x20 || c == 0x7f) {
            char e[6] = { '\\', 'u', '0', '0', hx[c >> 4], hx[c & 0xf] };
            put(w, e, 6); i++;
        } else if (c < 0x80) { putc_(w, (char)c); i++; }
        else {
            /* multibyte: find the sequence length and validate just it */
            size_t need = (c & 0xe0) == 0xc0 ? 2 : (c & 0xf0) == 0xe0 ? 3 : (c & 0xf8) == 0xf0 ? 4 : 0;
            if (need && i + need <= n && scft_utf8_valid(s + i, need)) {
                put(w, (const char *)s + i, need); i += need;
            } else {
                put(w, "\xef\xbf\xbd", 3); i++;   /* U+FFFD */
            }
        }
    }
    putc_(w, '"');
}

void jw_key(struct jw *w, const char *key)
{
    if (w->depth <= 0 || w->in_key[w->depth]) { w->overflow = true; return; }
    if (!w->first[w->depth]) putc_(w, ',');
    w->first[w->depth] = false;
    write_escaped(w, (const unsigned char *)key, strlen(key));
    putc_(w, ':');
    w->in_key[w->depth] = true;
}

void jw_strn(struct jw *w, const char *s, size_t n) { before_value(w); write_escaped(w, (const unsigned char *)s, n); }
void jw_str(struct jw *w, const char *s) { jw_strn(w, s, s ? strlen(s) : 0); }

void jw_int(struct jw *w, long long v)
{
    char b[32]; int n = snprintf(b, sizeof(b), "%lld", v);
    before_value(w); put(w, b, (size_t)n);
}
void jw_uint(struct jw *w, unsigned long long v)
{
    char b[32]; int n = snprintf(b, sizeof(b), "%llu", v);
    before_value(w); put(w, b, (size_t)n);
}
void jw_double(struct jw *w, double v, int decimals)
{
    if (!isfinite(v)) { jw_null(w); return; }
    char b[64]; int n = snprintf(b, sizeof(b), "%.*f", decimals < 0 ? 0 : decimals > 17 ? 17 : decimals, v);
    before_value(w); put(w, b, (size_t)n);
}
void jw_bool(struct jw *w, bool v) { before_value(w); put(w, v ? "true" : "false", v ? 4 : 5); }
void jw_null(struct jw *w) { before_value(w); put(w, "null", 4); }
void jw_raw(struct jw *w, const char *json) { before_value(w); put(w, json, strlen(json)); }

bool jw_ok(const struct jw *w) { return !w->overflow && w->depth == 0 && w->len > 0; }

void jw_kv_str(struct jw *w, const char *k, const char *v) { jw_key(w, k); jw_str(w, v); }
void jw_kv_int(struct jw *w, const char *k, long long v) { jw_key(w, k); jw_int(w, v); }
void jw_kv_uint(struct jw *w, const char *k, unsigned long long v) { jw_key(w, k); jw_uint(w, v); }
void jw_kv_double(struct jw *w, const char *k, double v, int d) { jw_key(w, k); jw_double(w, v, d); }
void jw_kv_bool(struct jw *w, const char *k, bool v) { jw_key(w, k); jw_bool(w, v); }
void jw_kv_null(struct jw *w, const char *k) { jw_key(w, k); jw_null(w); }
