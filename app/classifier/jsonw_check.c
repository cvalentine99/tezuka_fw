/*
 * jsonw_check.c — host-side test tool for the JSON writer: reads one
 * string from a file (raw bytes) and prints it as a JSON object
 * {"label": <escaped>, ...}; exit 70 if the writer reported overflow.
 * `--overflow N` builds a fixed document into an N-byte buffer and prints
 * "overflow" or "ok".
 * `--double VALUE DECIMALS [CAP]` prints {"v": <VALUE formatted with
 * DECIMALS>} from a CAP-byte destination buffer (default 4096; VALUE as
 * strtod reads it: "1e308", "nan", "-inf", ...); exit 70 if the writer
 * reported overflow, which is the safe failure for a destination too small.
 */
#include "jsonw.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv)
{
    if ((argc == 4 || argc == 5) && strcmp(argv[1], "--double") == 0) {
        double v = strtod(argv[2], NULL);
        int decimals = atoi(argv[3]);
        size_t cap = argc == 5 ? (size_t)atoi(argv[4]) : 4096;
        char *out = malloc(cap + 1);
        if (!out) return 1;
        out[cap] = '\x7f';   /* canary just past the writer's buffer */
        struct jw w; jw_init(&w, out, cap);
        jw_obj_open(&w); jw_kv_double(&w, "v", v, decimals); jw_obj_close(&w);
        if (out[cap] != '\x7f') { fprintf(stderr, "canary overwritten\n"); return 71; }
        int rc = jw_ok(&w) ? 0 : 70;
        if (rc == 0) puts(out);
        free(out);
        return rc;
    }
    if (argc == 3 && strcmp(argv[1], "--overflow") == 0) {
        size_t cap = (size_t)atoi(argv[2]);
        char *buf = malloc(cap + 1);
        struct jw w; jw_init(&w, buf, cap);
        jw_obj_open(&w); jw_kv_str(&w, "label", "FM Broadcast 12/34+x"); jw_kv_double(&w, "v", 1.5, 3); jw_obj_close(&w);
        puts(jw_ok(&w) ? "ok" : "overflow");
        free(buf);
        return 0;
    }
    if (argc != 2) { fprintf(stderr, "usage: %s FILE | --overflow N | --double VALUE DECIMALS [CAP]\n", argv[0]); return 64; }
    FILE *f = fopen(argv[1], "rb");
    if (!f) { perror(argv[1]); return 1; }
    static char in[65536];
    size_t n = fread(in, 1, sizeof(in), f);
    fclose(f);
    static char out[262144];
    struct jw w; jw_init(&w, out, sizeof(out));
    jw_obj_open(&w);
    jw_key(&w, "label"); jw_strn(&w, in, n);
    jw_kv_double(&w, "nan", 0.0 / 0.0, 3);
    jw_kv_int(&w, "neg", -42);
    jw_kv_bool(&w, "flag", true);
    jw_kv_null(&w, "nothing");
    jw_obj_close(&w);
    if (!jw_ok(&w)) return 70;
    puts(out);
    return 0;
}
