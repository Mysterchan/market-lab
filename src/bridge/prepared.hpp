#pragma once

#include <cstddef>

extern "C" {
/** Create a single-owner exporter using the shared BinaryFILE reader and market core.
 * Input is immutable; output paths must be unpublished, exclusively owned staging files.
 * depth is 1..100. implicit permits EOF only after a complete S/C record.
 * profile enables intrusive per-frame stage timings. Returns null on creation failure.
 * Destroy the returned handle on every exit; cancellation occurs between next() calls.
 */
void* prepared_create(const char* source, const char* symbol, const char* events,
                      const char* states, size_t depth, int implicit, int profile) noexcept;

/** Process 1..8192 source frames and append relevant events/state versions.
 * Returns bounded JSON counters/timings/done, borrowed until the next call or destroy.
 * On null, inspect error() and discard the handle. Never call after done=true.
 * Successful completion flushes streams; Python validates/fsyncs and publishes the directory.
 */
const char* prepared_next(void* handle, size_t frames) noexcept;

/** Borrow diagnostics for a failed live handle; lifetime ends at destroy(). */
const char* prepared_error(void* handle) noexcept;

/** Close streams and free native book/output buffers; accepts null. */
void prepared_destroy(void* handle) noexcept;
}
