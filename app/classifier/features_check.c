/*
 * features_check.c — host-side test tool: read little-endian float32
 * power bins from FILE (the whole file is one frame) and print the
 * features extract_frame_features() computes, as JSON. The Python tests
 * run features.py on the same bins and compare.
 *
 *   features_check FILE [--skip-first]   (--skip-first drops f[0], the
 *                                         /waterfall step word)
 */
#include "features.h"
#include "jsonw.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv)
{
    if (argc < 2) { fprintf(stderr, "usage: %s FILE [--skip-first]\n", argv[0]); return 64; }
    bool skip = argc > 2 && strcmp(argv[2], "--skip-first") == 0;
    FILE *f = fopen(argv[1], "rb");
    if (!f) { perror(argv[1]); return 1; }
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    if (sz < 0 || sz % 4) { fprintf(stderr, "not a float32 file\n"); fclose(f); return 1; }
    uint32_t n = (uint32_t)(sz / 4);
    float *v = malloc(n * sizeof(float));
    if (fread(v, sizeof(float), n, f) != n) { fprintf(stderr, "short read\n"); fclose(f); return 1; }
    fclose(f);
    const float *p = v; uint32_t m = n;
    if (skip && n > 0) { p++; m--; }
    struct frame_features ff = extract_frame_features(p, m);
    char buf[512]; struct jw w; jw_init(&w, buf, sizeof(buf));
    jw_obj_open(&w);
    jw_kv_bool(&w, "valid", ff.valid);
    jw_kv_uint(&w, "n", m);
    jw_kv_double(&w, "bandwidth_frac", ff.bandwidth_frac, 9);
    jw_kv_double(&w, "papr_db", ff.papr_db, 6);
    jw_kv_double(&w, "flatness", ff.flatness, 9);
    jw_kv_uint(&w, "peak_count", (unsigned long long)ff.peak_count);
    jw_kv_uint(&w, "peak_bin", ff.peak_bin);
    jw_kv_str(&w, "semantics", FEATURE_SEMANTICS);
    jw_obj_close(&w);
    puts(buf);
    free(v);
    return 0;
}
