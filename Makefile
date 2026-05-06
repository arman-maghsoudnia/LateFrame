CC = gcc
CFLAGS = -Wall -Wextra -g
PREFIX ?= /usr/local
SRC = src/lateframe.c
BIN = package/usr/bin/lateframe

all: directories
	$(CC) $(CFLAGS) -o $(BIN) $(SRC) -lm -lrt -lpcap

directories:
	mkdir -p package/usr/bin

clean:
	rm -f $(BIN)

install: all
	install -d $(PREFIX)/bin
	install -m 0755 $(BIN) $(PREFIX)/bin/lateframe
