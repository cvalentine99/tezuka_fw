/* sha256.h — minimal SHA-256 (FIPS 180-4) for hashing the template file. */
#ifndef SHA256_H
#define SHA256_H
#include <stddef.h>
#include <stdint.h>
void sha256(const uint8_t *data, size_t len, uint8_t out[32]);
#endif
