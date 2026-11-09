/* ThreadSanitizer harness for classifier.c (independent review 2026-11-08,
 * finding R6: observation metadata written by the libmosquitto network
 * thread and read by the frame thread).
 *
 * The daemon source is compiled natively against the stub mosquitto/lws
 * headers in stubs/ (nothing on the network is ever touched: replay mode
 * with -B none) and run on the main thread, while a second thread plays
 * the libmosquitto network thread by calling the daemon's real
 * mqtt_on_message() with changing retained-topic values for the whole
 * replay. Any unsynchronized read of those fields by the frame thread is
 * a TSan "data race" report.
 *
 *   make -C app/classifier tsan           # builds build/tsan/harness
 *   make -C app/classifier tsan-run       # runs it on a 300-frame replay
 *
 * CLASSIFIER_SRC selects the daemon source file (the Makefile points it
 * at ../classifier.c; point it at an older revision to see the races it
 * had). On kernels with high-entropy ASLR TSan needs `setarch -R`. */
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>

/* The writer thread must not start before the daemon's main() has
 * initialised A.lock, or TSan reports the init itself as a race with the
 * first lock (a harness artefact, not a daemon bug). The harness owns the
 * mutex init and turns the daemon's own call into a no-op. */
static pthread_mutex_t *harness_lock;
static int harness_mutex_init(pthread_mutex_t *m, const pthread_mutexattr_t *a)
{
    if (harness_lock == m) return 0;
    harness_lock = m;
    return pthread_mutex_init(m, a);
}
#define pthread_mutex_init(m, a) harness_mutex_init((m), (a))
#define main classifier_main
#include CLASSIFIER_SRC
#undef main
#undef pthread_mutex_init

static atomic_int done;

static void feed(const char *topic, const char *val)
{
    struct mosquitto_message m;
    memset(&m, 0, sizeof(m));
    m.topic = (char *)topic;
    m.payload = (void *)val;
    m.payloadlen = (int)strlen(val);
    m.retain = true;
    mqtt_on_message(NULL, NULL, &m);
}

static void *writer(void *arg)
{
    (void)arg;
    unsigned long i = 0;
    char buf[64];
    while (!atomic_load(&done)) {
        snprintf(buf, sizeof(buf), "SN-%lu", i);
        feed("state/main/serial", buf);
        feed("state/rx/frequency", (i & 1) ? "433000000" : "1090000000");
        feed("state/rx/sampling", (i & 2) ? "2500000" : "2400000");
        feed("state/rx/rfinput", (i & 4) ? "1" : "0");
        feed("state/rx/sweep/activate", (i & 8) ? "1" : "0");
        feed("state/rx/sweep/engaged", (i & 8) ? "1" : "0");
        i++;
    }
    fprintf(stderr, "harness: writer made %lu update rounds\n", i);
    return NULL;
}

int main(int argc, char **argv)
{
    harness_mutex_init(&A.lock, NULL);
    pthread_t th;
    pthread_create(&th, NULL, writer, NULL);
    int rc = classifier_main(argc, argv);
    atomic_store(&done, 1);
    pthread_join(th, NULL);
    return rc;
}
