CXX ?= clang++
CLANG_FORMAT ?= clang-format
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Wpedantic
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
QT_QPA_PLATFORM ?= offscreen
SOURCES := \
    src/app/main.cpp \
    src/replay/replay.cpp \
    src/feed/binaryfile/reader.cpp \
    src/feed/moldudp/live.cpp \
    src/common/binary.cpp \
    src/common/file_utils.cpp \
    src/feed/moldudp/moldudp.cpp \
    src/app/options.cpp \
    src/feed/itch/decoder.cpp \
    src/book/book.cpp \
    src/output/csv.cpp \
    src/output/staged_output.cpp \
    src/replay/checkpoint.cpp \
    src/common/digest.cpp
HEADERS := $(shell find include/itch -type f -name '*.hpp')

.PHONY: all test test-platform test-desktop clean format format-check
SHARED_SUFFIX := $(if $(filter Darwin,$(shell uname -s)),dylib,so)
MARKET_LIBRARY := build/libitch_market.$(SHARED_SUFFIX)
MARKET_SOURCES := src/bridge/market.cpp src/bridge/engine.cpp src/bridge/prepared.cpp \
    src/feed/itch/decoder.cpp src/book/book.cpp src/common/binary.cpp src/feed/binaryfile/reader.cpp
BRIDGE_HEADERS := src/bridge/engine.hpp src/bridge/prepared.hpp
all: build/itch_replay $(MARKET_LIBRARY)

$(MARKET_LIBRARY): $(MARKET_SOURCES) $(HEADERS) $(BRIDGE_HEADERS)
	mkdir -p build
	$(CXX) $(CXXFLAGS) -fPIC -shared -Iinclude $(MARKET_SOURCES) -o $@

build/itch_replay: $(SOURCES) $(HEADERS)
	mkdir -p build
	$(CXX) $(CXXFLAGS) -Iinclude $(SOURCES) -o $@

test: all
	"$(PYTHON)" tests/test_replay.py build/itch_replay
	"$(PYTHON)" tests/test_live.py build/itch_replay
	"$(PYTHON)" -m unittest discover -s tests/platform -p 'test_*.py'

test-platform: all
	"$(PYTHON)" -m unittest discover -s tests/platform -p 'test_*.py'

test-desktop: all
	QT_QPA_PLATFORM="$(QT_QPA_PLATFORM)" TRADING_DESKTOP_TESTS=1 "$(PYTHON)" -X faulthandler -m unittest discover -v -s tests/platform -p 'test_desktop*.py'

clean:
	rm -f build/itch_replay

format:
	$(CLANG_FORMAT) -i $(sort $(SOURCES) $(MARKET_SOURCES)) $(HEADERS) $(BRIDGE_HEADERS)

format-check:
	$(CLANG_FORMAT) --dry-run --Werror $(sort $(SOURCES) $(MARKET_SOURCES)) $(HEADERS) $(BRIDGE_HEADERS)

.PHONY: python-format python-quality
python-format:
	"$(PYTHON)" -m black sim_platform tests/platform tools

python-quality:
	"$(PYTHON)" -m black --check sim_platform tests/platform tools
	"$(PYTHON)" -m ruff check sim_platform tests/platform tools
	"$(PYTHON)" -m mypy
