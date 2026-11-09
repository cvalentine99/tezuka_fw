/* Minimal stand-in for <libwebsockets.h>, see mosquitto.h in this dir. */
#ifndef STUB_LWS_H
#define STUB_LWS_H
#include <stddef.h>
struct lws;
struct lws_context;
enum lws_callback_reasons { LWS_CALLBACK_CLIENT_ESTABLISHED = 1, LWS_CALLBACK_CLIENT_RECEIVE, LWS_CALLBACK_CLIENT_CONNECTION_ERROR, LWS_CALLBACK_CLIENT_CLOSED, LWS_CALLBACK_OTHER };
typedef int (*lws_callback_function)(struct lws *, enum lws_callback_reasons, void *, void *, size_t);
struct lws_protocols { const char *name; lws_callback_function callback; size_t per_session_data_size; size_t rx_buffer_size; unsigned int id; void *user; size_t tx_packet_size; };
struct lws_context_creation_info { int port; const struct lws_protocols *protocols; int gid; int uid; };
struct lws_client_connect_info { struct lws_context *context; const char *address; int port; const char *path; const char *host; const char *origin; const char *protocol; struct lws **pwsi; };
#define CONTEXT_PORT_NO_LISTEN (-1)
static inline struct lws_context *lws_create_context(const struct lws_context_creation_info *i) { (void)i; return NULL; }
static inline void lws_context_destroy(struct lws_context *c) { (void)c; }
static inline struct lws *lws_client_connect_via_info(const struct lws_client_connect_info *i) { (void)i; return NULL; }
static inline int lws_service(struct lws_context *c, int ms) { (void)c; (void)ms; return -1; }
static inline int lws_is_final_fragment(struct lws *w) { (void)w; return 1; }
#endif
