/* Minimal stand-in for <mosquitto.h>: just enough for classifier.c to
 * compile natively for a ThreadSanitizer run in replay mode (-B none),
 * where none of these are ever called. Every function fails safely. */
#ifndef STUB_MOSQUITTO_H
#define STUB_MOSQUITTO_H
#include <stdbool.h>
#include <stddef.h>
struct mosquitto;
struct mosquitto_message { int mid; char *topic; void *payload; int payloadlen; int qos; bool retain; };
enum { MOSQ_ERR_SUCCESS = 0, MOSQ_ERR_UNKNOWN = 1 };
static inline int mosquitto_lib_init(void) { return 0; }
static inline int mosquitto_lib_cleanup(void) { return 0; }
static inline struct mosquitto *mosquitto_new(const char *id, bool cs, void *ud) { (void)id; (void)cs; (void)ud; return NULL; }
static inline void mosquitto_destroy(struct mosquitto *m) { (void)m; }
static inline void mosquitto_connect_callback_set(struct mosquitto *m, void (*cb)(struct mosquitto *, void *, int)) { (void)m; (void)cb; }
static inline void mosquitto_message_callback_set(struct mosquitto *m, void (*cb)(struct mosquitto *, void *, const struct mosquitto_message *)) { (void)m; (void)cb; }
static inline int mosquitto_reconnect_delay_set(struct mosquitto *m, unsigned a, unsigned b, bool c) { (void)m; (void)a; (void)b; (void)c; return 0; }
static inline int mosquitto_will_set(struct mosquitto *m, const char *t, int l, const void *p, int q, bool r) { (void)m; (void)t; (void)l; (void)p; (void)q; (void)r; return 0; }
static inline int mosquitto_connect_async(struct mosquitto *m, const char *h, int p, int k) { (void)m; (void)h; (void)p; (void)k; return MOSQ_ERR_UNKNOWN; }
static inline int mosquitto_loop_start(struct mosquitto *m) { (void)m; return MOSQ_ERR_UNKNOWN; }
static inline int mosquitto_loop_stop(struct mosquitto *m, bool f) { (void)m; (void)f; return 0; }
static inline int mosquitto_disconnect(struct mosquitto *m) { (void)m; return 0; }
static inline int mosquitto_subscribe(struct mosquitto *m, int *mid, const char *t, int q) { (void)m; (void)mid; (void)t; (void)q; return 0; }
static inline int mosquitto_publish(struct mosquitto *m, int *mid, const char *t, int l, const void *p, int q, bool r) { (void)m; (void)mid; (void)t; (void)l; (void)p; (void)q; (void)r; return MOSQ_ERR_UNKNOWN; }
static inline const char *mosquitto_strerror(int e) { (void)e; return "stub"; }
static inline const char *mosquitto_connack_string(int e) { (void)e; return "stub"; }
#endif
