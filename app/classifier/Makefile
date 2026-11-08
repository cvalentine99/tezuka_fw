# app/classifier — on-target daemon plus host-side test tools.
#
#   make                 cross-build `classifier` (CROSS_COMPILE from the SDK env)
#   make host-tools      native scft_check / features_check / jsonw_check in build/host/
#   make test            host-tools + the pytest suite in tests/ (needs python3 + pytest)

CC = $(CROSS_COMPILE)gcc
HOSTCC ?= gcc

CORE_SRCS = scft.c sha256.c jsonw.c features.c
CORE_HDRS = scft.h sha256.h jsonw.h features.h

all: classifier

classifier: classifier.c $(CORE_SRCS) $(CORE_HDRS)
	$(CC) -O2 -mfpu=neon -mfloat-abi=hard -Wall -Wextra classifier.c $(CORE_SRCS) -o classifier -lwebsockets -lmosquitto -lpthread -lm

HOSTDIR = build/host
HOST_CFLAGS = -O2 -Wall -Wextra -Wno-unused-parameter -fsanitize=address,undefined -fno-omit-frame-pointer

host-tools: $(HOSTDIR)/scft_check $(HOSTDIR)/features_check $(HOSTDIR)/jsonw_check

$(HOSTDIR):
	mkdir -p $(HOSTDIR)

$(HOSTDIR)/scft_check: scft_check.c $(CORE_SRCS) $(CORE_HDRS) | $(HOSTDIR)
	$(HOSTCC) $(HOST_CFLAGS) scft_check.c scft.c sha256.c jsonw.c -o $@ -lm

$(HOSTDIR)/features_check: features_check.c $(CORE_SRCS) $(CORE_HDRS) | $(HOSTDIR)
	$(HOSTCC) $(HOST_CFLAGS) features_check.c features.c jsonw.c scft.c sha256.c -o $@ -lm

$(HOSTDIR)/jsonw_check: jsonw_check.c $(CORE_SRCS) $(CORE_HDRS) | $(HOSTDIR)
	$(HOSTCC) $(HOST_CFLAGS) jsonw_check.c jsonw.c scft.c sha256.c -o $@ -lm

test: host-tools
	python3 -m pytest -q tests

# ThreadSanitizer harness (see tsan/harness.c): the daemon compiled natively
# against stub network headers, replaying frames while a second thread
# rewrites the MQTT-derived observation fields. CLASSIFIER_SRC may point at
# another revision's classifier.c. TSan needs ASLR off on recent kernels.
CLASSIFIER_SRC ?= $(abspath classifier.c)
TSAN_FLAGS = -O1 -g -fsanitize=thread -fno-omit-frame-pointer -Wall -Wno-unused-function -Wno-unused-parameter
tsan: build/tsan/harness
build/tsan/harness: tsan/harness.c tsan/stubs/mosquitto.h tsan/stubs/libwebsockets.h $(CLASSIFIER_SRC) $(CORE_SRCS) $(CORE_HDRS)
	mkdir -p build/tsan
	$(HOSTCC) $(TSAN_FLAGS) -I tsan/stubs -iquote . -DCLASSIFIER_SRC='"$(CLASSIFIER_SRC)"' tsan/harness.c $(CORE_SRCS) -o $@ -lm -lpthread
build/tsan/replay300.raw: tests/fixtures/wf_avg4096_frame0.bin
	mkdir -p build/tsan
	python3 -c 'import sys; d=open(sys.argv[1],"rb").read(); open(sys.argv[2],"wb").write(d*300)' $< $@
tsan-run: build/tsan/harness build/tsan/replay300.raw
	TSAN_OPTIONS="halt_on_error=0 $(TSAN_OPTIONS)" setarch $$(uname -m) -R build/tsan/harness -r build/tsan/replay300.raw -n 4096 -B none -S 2500000 -l 2 -F 7.5 -i 0.5 \
	    -f ../../board/tezuka/common/overlay_tezuka/etc/classifier-default-templates.bin > build/tsan/replay.out

clean:
	rm -rf classifier build

.PHONY: all host-tools test tsan tsan-run clean
