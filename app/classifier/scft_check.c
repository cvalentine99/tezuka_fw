/*
 * scft_check.c — host-side test tool: validate an SCFT file with the SAME
 * parser the daemon uses (scft.c) and dump it as JSON for the Python
 * tests to compare against the writer's intent.
 *
 *   scft_check FILE            -> JSON report on stdout, exit 0 if valid, 2 if not
 *   scft_check --utf8 HEXBYTES -> exit 0 if the bytes are valid UTF-8, 1 if not
 */
#include "scft.h"
#include "jsonw.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int hexval(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

int main(int argc, char **argv)
{
    if (argc == 3 && strcmp(argv[1], "--utf8") == 0) {
        size_t n = strlen(argv[2]) / 2;
        unsigned char *b = malloc(n + 1);
        for (size_t i = 0; i < n; i++) b[i] = (unsigned char)(hexval(argv[2][2*i]) * 16 + hexval(argv[2][2*i+1]));
        int ok = scft_utf8_valid(b, n);
        free(b);
        return ok ? 0 : 1;
    }
    if (argc != 2) { fprintf(stderr, "usage: %s FILE | --utf8 HEX\n", argv[0]); return 64; }

    struct scft_file f;
    char err[256];
    enum scft_status st = scft_load(argv[1], &f, err, sizeof(err));
    static char buf[1 << 20];
    struct jw w;
    jw_init(&w, buf, sizeof(buf));
    jw_obj_open(&w);
    jw_kv_bool(&w, "valid", st == SCFT_OK);
    jw_kv_str(&w, "status", scft_status_name(st));
    if (st != SCFT_OK) {
        jw_kv_str(&w, "error", err);
        jw_obj_close(&w);
        puts(buf);
        return 2;
    }
    char hex[65];
    scft_sha256_hex(&f, hex);
    jw_kv_uint(&w, "version", f.version);
    jw_kv_uint(&w, "count", f.count);
    jw_kv_uint(&w, "canonical_n", f.canonical_n);
    jw_kv_uint(&w, "size_bytes", f.size_bytes);
    jw_kv_str(&w, "sha256", hex);
    jw_key(&w, "templates");
    jw_arr_open(&w);
    for (uint32_t i = 0; i < f.count; i++) {
        const struct scft_template *t = &f.items[i];
        jw_obj_open(&w);
        jw_kv_str(&w, "label", t->label);
        jw_kv_uint(&w, "id", t->id);
        jw_kv_bool(&w, "has_gate", t->has_gate);
        jw_kv_double(&w, "hz_per_bin", t->hz_per_bin, 6);
        if (t->has_gate) {
            jw_key(&w, "ranges");
            jw_obj_open(&w);
            const struct scft_range *rs[4] = { &t->bandwidth_frac, &t->papr_db, &t->flatness, &t->peak_count };
            const char *names[4] = { "bandwidth_frac", "papr_db", "flatness", "peak_count" };
            for (int k = 0; k < 4; k++) {
                jw_key(&w, names[k]);
                if (rs[k]->hi < rs[k]->lo) { jw_null(&w); continue; }
                jw_arr_open(&w); jw_double(&w, rs[k]->lo, 6); jw_double(&w, rs[k]->hi, 6); jw_arr_close(&w);
            }
            jw_obj_close(&w);
        }
        jw_key(&w, "data_head");
        jw_arr_open(&w);
        for (uint32_t k = 0; k < f.canonical_n && k < 4; k++) jw_double(&w, t->data[k], 7);
        jw_arr_close(&w);
        double s = 0; for (uint32_t k = 0; k < f.canonical_n; k++) s += (double)t->data[k] * t->data[k];
        jw_kv_double(&w, "l2", s, 9);
        jw_obj_close(&w);
    }
    jw_arr_close(&w);
    jw_obj_close(&w);
    if (!jw_ok(&w)) { fprintf(stderr, "json buffer overflow\n"); scft_free(&f); return 70; }
    puts(buf);
    scft_free(&f);
    return 0;
}
