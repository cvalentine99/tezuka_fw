/*
 * scft.h — loader/validator for the classifier's SCFT template file.
 *
 * Shared by the on-target daemon (classifier.c), its `classifier -V FILE`
 * validate mode (used by api_controller.sh before installing an upload)
 * and the host-side test tool (scft_check.c), so there is exactly one
 * parser for the format. No libwebsockets/mosquitto dependency here.
 *
 * File layout (little-endian), kept in lockstep with
 * tools/classifier_templates/templates_format.py:
 *
 *   char     magic[4]      = "SCFT"
 *   uint32_t version       = 1, 2 or 3
 *   uint32_t count         >= 1, <= SCFT_MAX_COUNT
 *   uint32_t canonical_n   >= SCFT_MIN_CANONICAL_N, <= SCFT_MAX_CANONICAL_N
 *   then `count` entries of:
 *     char     label[32]   UTF-8, NUL-terminated within the 32 bytes
 *                          (so at most 31 bytes of text), see LABEL CONTRACT
 *     uint32_t template_id unique within the file
 *     float32  data[canonical_n]   zero-mean, unit-L2-norm (SCFT_NORM_TOL)
 *     -- version >= 2 --
 *     float32  bandwidth_frac_lo, hi; papr_db_lo, hi; flatness_lo, hi;
 *              peak_count_lo, hi     (hi < lo: unbounded, no gate; all finite)
 *     -- version >= 3 --
 *     float32  hz_per_bin           (0 = no resolution; finite, >= 0, <= 1e9)
 *
 * The file must end exactly after the last entry (no trailing bytes), and
 * its total size is bounded by SCFT_MAX_FILE_BYTES before anything is
 * allocated.
 *
 * LABEL CONTRACT: the bytes before the first NUL must be valid UTF-8
 * (RFC 3629: no overlongs, no surrogates, max U+10FFFF), must not contain
 * C0 control bytes (0x00-0x1f) or DEL (0x7f), and must not be empty. Any
 * other Unicode text is allowed and is carried verbatim into the MQTT
 * payloads (JSON-escaped by jsonw.c). Spaces, '+', '/', quotes and
 * backslashes are all legal label characters.
 */
#ifndef SCFT_H
#define SCFT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define SCFT_MAGIC            "SCFT"
#define SCFT_LABEL_LEN        32
#define SCFT_MAX_COUNT        4096u
#define SCFT_MIN_CANONICAL_N  2u
#define SCFT_MAX_CANONICAL_N  (1u << 20)
#define SCFT_MAX_FILE_BYTES   (4u << 20)   /* 4 MiB; the board's JFFS2 is 896 KiB */
#define SCFT_NORM_TOL         1e-3         /* |mean| and |L2 - 1| tolerance */
#define SCFT_MAX_HZ_PER_BIN   1e9f

enum scft_status {
    SCFT_OK = 0,
    SCFT_E_IO,          /* cannot open/read/stat */
    SCFT_E_MAGIC,
    SCFT_E_VERSION,
    SCFT_E_HEADER,      /* count / canonical_n out of bounds */
    SCFT_E_SIZE,        /* file size != expected size, or over the cap */
    SCFT_E_LABEL,       /* label contract violated */
    SCFT_E_ID,          /* duplicate template_id */
    SCFT_E_DATA,        /* non-finite sample */
    SCFT_E_NORM,        /* not zero-mean / unit-norm within tolerance */
    SCFT_E_RANGE,       /* non-finite feature range */
    SCFT_E_RESOLUTION,  /* bad hz_per_bin */
    SCFT_E_NOMEM,
};

struct scft_range { float lo, hi; };

struct scft_template {
    char     label[SCFT_LABEL_LEN];
    uint32_t id;
    float   *data;          /* canonical_n floats, owned by the scft_file */
    float    hz_per_bin;    /* 0 = no resolution */
    bool     has_gate;      /* version >= 2 */
    struct scft_range bandwidth_frac, papr_db, flatness, peak_count;
};

struct scft_file {
    uint32_t version;
    uint32_t count;
    uint32_t canonical_n;
    struct scft_template *items;
    uint64_t size_bytes;
    uint8_t  sha256[32];    /* of the whole file as read */
};

/* Loads and fully validates `path`. On SCFT_OK `out` is populated and the
 * caller owns it (scft_free). On any error `out` is zeroed and `err`
 * (if non-NULL) holds a one-line human-readable reason. */
enum scft_status scft_load(const char *path, struct scft_file *out, char *err, size_t errlen);

/* Same, from an in-memory image (used by tests and by scft_load). */
enum scft_status scft_parse(const uint8_t *buf, size_t len, struct scft_file *out, char *err, size_t errlen);

void scft_free(struct scft_file *f);
const char *scft_status_name(enum scft_status s);

/* Hex string of f->sha256 into out[65]. */
void scft_sha256_hex(const struct scft_file *f, char out[65]);

/* Validates one label buffer against the LABEL CONTRACT. Returns the
 * text length in bytes on success, -1 on violation. */
int scft_label_check(const char label[SCFT_LABEL_LEN]);

/* Validates a UTF-8 byte string (RFC 3629). */
bool scft_utf8_valid(const unsigned char *s, size_t n);

#endif
