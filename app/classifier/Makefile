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

clean:
	rm -rf classifier build

.PHONY: all host-tools test clean
