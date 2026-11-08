/*
 * jsonw_check.c — host-side test tool for the JSON writer: reads one
 * string from a file (raw bytes) and prints it as a JSON object
 * {"label": <escaped>, ...}; exit 70 if the writer reported overflow.
 * `--overflow N` builds a fixed document into an N-byte buffer and prints
 * "overflow" or "ok".
 */
#include "jsonw.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv)
{
    if (argc == 3 && strcmp(argv[1], "--overflow") == 0) {
        size_t cap = (size_t)atoi(argv[2]);
        char *buf = malloc(cap + 1);
        struct jw w; jw_init(&w, buf, cap);
        jw_obj_open(&w); jw_kv_str(&w, "label", "FM Broadcast 12/34+x"); jw_kv_double(&w, "v", 1.5, 3); jw_obj_close(&w);
        puts(jw_ok(&w) ? "ok" : "overflow");
        free(buf);
        return 0;
    }
    if (argc != 2) { fprintf(stderr, "usage: %s FILE | --overflow N\n", argv[0]); return 64; }
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
