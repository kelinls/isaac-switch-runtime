import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PersistenceEventJournalTests(unittest.TestCase):
    def test_host_harness_covers_protocol_and_failure_paths(self):
        with tempfile.TemporaryDirectory(prefix="persistence-journal-") as temporary:
            root = Path(temporary)
            harness = root / "harness.cpp"
            harness.write_text(
                r'''
#include "persistence_event_journal.hpp"
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstring>

using namespace PersistenceEventJournal;
static int g_open_calls;
static int g_close_calls;
static std::size_t g_write_bytes;
static std::size_t g_write_limit = static_cast<std::size_t>(-1);
static bool g_open_fails;
static bool g_close_fails;
static const char* g_mode;

void* Open(const char*, const char* mode) {
    ++g_open_calls;
    g_mode = mode;
    return g_open_fails ? nullptr : reinterpret_cast<void*>(1);
}
std::size_t Write(const void*, std::size_t size, std::size_t count, void*) {
    const std::size_t bytes = size * count;
    const std::size_t written = bytes < g_write_limit ? bytes : g_write_limit;
    g_write_bytes += written;
    return written;
}
int Close(void*) { ++g_close_calls; return g_close_fails ? 1 : 0; }

int main() {
    static_assert(kRecordSize == 64);
    static_assert(kQueueCapacity == 32);
    static_assert(kMaxRecordsPerFlush == 8);
    Mark(Event::FileApiAccepted, Operation::None, 7, 9);
    assert(FlushBounded() == FlushResult::NoApi);
    ConfigureFileApi(Open, Write, Close);
    assert(FlushBounded() == FlushResult::Succeeded);
    assert(std::strcmp(g_mode, "ab") == 0);
    assert(g_write_bytes <= 8 * kRecordSize);
    assert(FlushBounded() == FlushResult::Succeeded);
    assert(FlushBounded() == FlushResult::NeverAttempted);

    g_open_fails = true;
    Mark(Event::ManifestEntered);
    assert(FlushBounded() == FlushResult::OpenFailed);
    g_open_fails = false;
    assert(FlushBounded() == FlushResult::Succeeded);
    assert(MarkOnce(Event::ManagerCallbackEntered));
    assert(!MarkOnce(Event::ManagerCallbackEntered));
    const int opens_before_once = g_open_calls;
    assert(FlushBounded() == FlushResult::Succeeded);
    assert(g_open_calls == opens_before_once + 1);
    assert(FlushBounded() == FlushResult::NeverAttempted);
    const int opens_after_once = g_open_calls;
    assert(!MarkOnce(Event::ManagerCallbackEntered));
    assert(FlushBounded() == FlushResult::NeverAttempted);
    assert(g_open_calls == opens_after_once);

    for (std::size_t i = 0; i < kQueueCapacity + 4; ++i) Mark(Event::GateEvaluated);
    assert(FlushBounded() == FlushResult::Succeeded);
    assert(g_open_calls > 0 && g_close_calls > 0);
    return 0;
}
'''.lstrip(),
                encoding="utf-8",
            )
            binary = root / "journal-harness"
            result = subprocess.run(
                [
                    "c++", "-std=c++20", "-pthread",
                    "-DEXL_PERSISTENCE_EVENT_DIAGNOSTIC",
                    "-DEXL_TEST_BUILD_ID=202609090001",
                    "-I", str(ROOT / "source"),
                    str(harness), str(ROOT / "source" / "persistence_event_journal.cpp"),
                    "-o", str(binary),
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            run = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_host_harness_covers_real_concurrency_delay_and_overflow(self):
        with tempfile.TemporaryDirectory(prefix="persistence-journal-concurrent-") as temporary:
            root = Path(temporary)
            harness = root / "concurrent-harness.cpp"
            harness.write_text(
                r'''
#include "persistence_event_journal.hpp"
#include <atomic>
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <thread>
#include <vector>

using namespace PersistenceEventJournal;
static std::mutex g_mutex;
static std::condition_variable g_cv;
static bool g_write_entered;
static bool g_release_write;
static bool g_block_first_write = true;
static int g_open_calls;
static int g_write_calls;
static int g_close_calls;
static std::vector<std::uint8_t> g_bytes;
static std::vector<std::size_t> g_write_sizes;
static std::atomic<int> g_flush_result{static_cast<int>(FlushResult::NeverAttempted)};
static std::atomic<bool> g_mark_done{false};

void* Open(const char*, const char*) {
    ++g_open_calls;
    return reinterpret_cast<void*>(1);
}

std::size_t Write(const void* data, std::size_t size, std::size_t count, void*) {
    const std::size_t bytes = size * count;
    {
        std::unique_lock<std::mutex> lock(g_mutex);
        ++g_write_calls;
        g_write_sizes.push_back(bytes);
        if (g_block_first_write) {
            g_block_first_write = false;
            g_write_entered = true;
            g_cv.notify_all();
            g_cv.wait(lock, [] { return g_release_write; });
        }
        const auto* begin = static_cast<const std::uint8_t*>(data);
        g_bytes.insert(g_bytes.end(), begin, begin + bytes);
    }
    return bytes;
}

int Close(void*) {
    ++g_close_calls;
    return 0;
}

std::uint32_t Checksum(const std::uint8_t* bytes, std::size_t length) {
    std::uint32_t value = 2166136261U;
    for (std::size_t index = 0; index < length; ++index) {
        value ^= bytes[index];
        value *= 16777619U;
    }
    return value;
}

int main() {
    ConfigureFileApi(Open, Write, Close);
    Mark(Event::FileApiAccepted);
    std::thread first_flush([] {
        g_flush_result.store(static_cast<int>(FlushBounded()), std::memory_order_release);
    });
    {
        std::unique_lock<std::mutex> lock(g_mutex);
        assert(g_cv.wait_for(lock, std::chrono::seconds(1), [] { return g_write_entered; }));
    }

    const auto concurrent_flush_started = std::chrono::steady_clock::now();
    std::atomic<int> second_flush_result{static_cast<int>(FlushResult::NeverAttempted)};
    std::thread second_flush([&] {
        second_flush_result.store(static_cast<int>(FlushBounded()), std::memory_order_release);
    });
    second_flush.join();
    const auto concurrent_flush_elapsed = std::chrono::steady_clock::now() - concurrent_flush_started;
    assert(second_flush_result.load(std::memory_order_acquire) == static_cast<int>(FlushResult::Busy));
    assert(concurrent_flush_elapsed < std::chrono::milliseconds(200));

    std::thread marker([] {
        for (std::size_t index = 0; index < kQueueCapacity + 16; ++index) {
            Mark(Event::GateEvaluated, Operation::None, static_cast<std::uint32_t>(index));
        }
        g_mark_done.store(true, std::memory_order_release);
    });
    for (int attempt = 0; attempt < 200 && !g_mark_done.load(std::memory_order_acquire); ++attempt) {
        std::this_thread::yield();
    }
    assert(g_mark_done.load(std::memory_order_acquire));
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        g_release_write = true;
    }
    g_cv.notify_all();
    marker.join();
    first_flush.join();
    assert(g_flush_result.load(std::memory_order_acquire) == static_cast<int>(FlushResult::Succeeded));

    assert(FlushBounded() == FlushResult::Succeeded);
    Mark(Event::ManifestEntered);
    assert(DrainPendingBounded(32) == FlushResult::Succeeded);
    assert(!HasPending());
    assert(g_open_calls == g_write_calls);
    assert(g_write_calls == g_close_calls);
    for (const std::size_t bytes : g_write_sizes) assert(bytes <= kMaxRecordsPerFlush * kRecordSize);
    assert(!g_bytes.empty() && g_bytes.size() % kRecordSize == 0);
    bool saw_overflow = false;
    for (std::size_t offset = 0; offset < g_bytes.size(); offset += kRecordSize) {
        assert(std::memcmp(g_bytes.data() + offset, "ISAACPE1", 8) == 0);
        std::uint32_t expected_checksum = 0;
        std::uint32_t event = 0;
        for (std::size_t byte = 0; byte < 4; ++byte) {
            expected_checksum |= static_cast<std::uint32_t>(g_bytes[offset + 60 + byte]) << (byte * 8);
            event |= static_cast<std::uint32_t>(g_bytes[offset + 28 + byte]) << (byte * 8);
        }
        assert(Checksum(g_bytes.data() + offset, kRecordSize - 4) == expected_checksum);
        saw_overflow |= event == static_cast<std::uint32_t>(Event::QueueOverflow);
    }
    assert(saw_overflow);
    const int opens_after_drain = g_open_calls;
    assert(DrainPendingBounded(32) == FlushResult::NeverAttempted);
    assert(g_open_calls == opens_after_drain);
    return 0;
}
'''.lstrip(),
                encoding="utf-8",
            )
            binary = root / "concurrent-harness"
            result = subprocess.run(
                [
                    "c++", "-std=c++20", "-pthread",
                    "-DEXL_PERSISTENCE_EVENT_DIAGNOSTIC",
                    "-DEXL_TEST_BUILD_ID=202609090002",
                    "-I", str(ROOT / "source"),
                    str(harness), str(ROOT / "source" / "persistence_event_journal.cpp"),
                    "-o", str(binary),
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            run = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_host_harness_stops_appending_after_short_write(self):
        with tempfile.TemporaryDirectory(prefix="persistence-journal-short-write-") as temporary:
            root = Path(temporary)
            harness = root / "short-write-harness.cpp"
            output = root / "events.bin"
            harness.write_text(
                r'''
#include "persistence_event_journal.hpp"
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <fstream>
#include <limits>
#include <vector>

using namespace PersistenceEventJournal;
static std::size_t g_limit = 1;
static std::vector<std::uint8_t> g_bytes;

void* Open(const char*, const char*) { return reinterpret_cast<void*>(1); }
std::size_t Write(const void* data, std::size_t size, std::size_t count, void*) {
    const std::size_t bytes = size * count;
    const std::size_t written = bytes < g_limit ? bytes : g_limit;
    const auto* begin = static_cast<const std::uint8_t*>(data);
    g_bytes.insert(g_bytes.end(), begin, begin + written);
    return written;
}
int Close(void*) { return 0; }

int main(int argc, char** argv) {
    assert(argc == 2);
    ConfigureFileApi(Open, Write, Close);
    Mark(Event::FileApiAccepted);
    assert(FlushBounded() == FlushResult::ShortWrite);
    g_limit = std::numeric_limits<std::size_t>::max();
    Mark(Event::GateEvaluated);
    assert(FlushBounded() == FlushResult::ShortWrite);
    std::ofstream file(argv[1], std::ios::binary);
    file.write(reinterpret_cast<const char*>(g_bytes.data()),
               static_cast<std::streamsize>(g_bytes.size()));
    assert(file.good());
    return 0;
}
'''.lstrip(),
                encoding="utf-8",
            )
            binary = root / "short-write-harness"
            result = subprocess.run(
                [
                    "c++", "-std=c++20", "-pthread",
                    "-DEXL_PERSISTENCE_EVENT_DIAGNOSTIC",
                    "-DEXL_TEST_BUILD_ID=202609090003",
                    "-I", str(ROOT / "source"),
                    str(harness), str(ROOT / "source" / "persistence_event_journal.cpp"),
                    "-o", str(binary),
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            run = subprocess.run([str(binary), str(output)], text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            data = output.read_bytes()
            self.assertEqual(len(data), 1)
            self.assertNotIn(b"ISAACPE1", data[1:])
            registration = root / "registration.bin"
            registration.write_bytes(b"")
            parsed = subprocess.run(
                ["python3", str(ROOT / "../tools/read_persistence_event_log.py"),
                 "--build-id", "202609090003", str(registration), str(output)],
                text=True, capture_output=True,
            )
            self.assertEqual(parsed.returncode, 0, parsed.stdout + parsed.stderr)
            self.assertNotIn('"event": "GateEvaluated"', parsed.stdout)
            self.assertTrue("不足" in parsed.stderr or "损坏" in parsed.stderr)

    def test_host_harness_does_not_restore_possibly_committed_close_failure(self):
        with tempfile.TemporaryDirectory(prefix="persistence-journal-close-failure-") as temporary:
            root = Path(temporary)
            harness = root / "close-failure-harness.cpp"
            harness.write_text(
                r'''
#include "persistence_event_journal.hpp"
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <vector>

using namespace PersistenceEventJournal;
static bool g_close_fails = true;
static std::vector<std::uint8_t> g_bytes;
void* Open(const char*, const char*) { return reinterpret_cast<void*>(1); }
std::size_t Write(const void* data, std::size_t size, std::size_t count, void*) {
    const auto* begin = static_cast<const std::uint8_t*>(data);
    g_bytes.insert(g_bytes.end(), begin, begin + size * count);
    return size * count;
}
int Close(void*) { return g_close_fails ? 1 : 0; }

int main() {
    ConfigureFileApi(Open, Write, Close);
    Mark(Event::DispatchReturned);
    assert(FlushBounded() == FlushResult::CloseFailed);
    g_close_fails = false;
    Mark(Event::GateEvaluated);
    assert(FlushBounded() == FlushResult::Succeeded);
    assert(g_bytes.size() == 2 * kRecordSize);
    assert(std::memcmp(g_bytes.data(), "ISAACPE1", 8) == 0);
    assert(std::memcmp(g_bytes.data() + kRecordSize, "ISAACPE1", 8) == 0);
    assert(g_bytes[kRecordSize + 56] == static_cast<std::uint8_t>(FlushResult::CloseFailed));
    return 0;
}
'''.lstrip(),
                encoding="utf-8",
            )
            binary = root / "close-failure-harness"
            result = subprocess.run(
                [
                    "c++", "-std=c++20", "-pthread",
                    "-DEXL_PERSISTENCE_EVENT_DIAGNOSTIC",
                    "-DEXL_TEST_BUILD_ID=202609090006",
                    "-I", str(ROOT / "source"),
                    str(harness), str(ROOT / "source" / "persistence_event_journal.cpp"),
                    "-o", str(binary),
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            run = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_host_harness_stops_retrying_after_drain_budget_is_exhausted(self):
        with tempfile.TemporaryDirectory(prefix="persistence-journal-budget-") as temporary:
            root = Path(temporary)
            harness = root / "budget-harness.cpp"
            harness.write_text(
                r'''
#include "persistence_event_journal.hpp"
#include <cassert>
#include <cstddef>

using namespace PersistenceEventJournal;
static int g_open_calls;
void* Open(const char*, const char*) { ++g_open_calls; return nullptr; }
std::size_t Write(const void*, std::size_t, std::size_t, void*) { return 0; }
int Close(void*) { return 0; }

int main() {
    ConfigureFileApi(Open, Write, Close);
    Mark(Event::DispatchReturned);
    for (std::size_t attempt = 0; attempt < kMaxPendingDrainAttempts + 4; ++attempt) {
        DrainPendingBounded(1);
    }
    assert(g_open_calls == static_cast<int>(kMaxPendingDrainAttempts));
    assert(!HasPending());
    DrainPendingBounded(1);
    assert(g_open_calls == static_cast<int>(kMaxPendingDrainAttempts));
    return 0;
}
'''.lstrip(),
                encoding="utf-8",
            )
            binary = root / "budget-harness"
            result = subprocess.run(
                [
                    "c++", "-std=c++20", "-pthread",
                    "-DEXL_PERSISTENCE_EVENT_DIAGNOSTIC",
                    "-DEXL_TEST_BUILD_ID=202609090007",
                    "-I", str(ROOT / "source"),
                    str(harness), str(ROOT / "source" / "persistence_event_journal.cpp"),
                    "-o", str(binary),
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            run = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_host_harness_flushes_install_failure_after_api_registration_in_both_orders(self):
        with tempfile.TemporaryDirectory(prefix="persistence-journal-api-order-") as temporary:
            root = Path(temporary)
            harness = root / "api-order-harness.cpp"
            output = root / "events.bin"
            harness.write_text(
                r'''
#include "persistence_event_journal.hpp"
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <vector>

using namespace PersistenceEventJournal;
static std::vector<std::uint8_t> g_bytes;
void* Open(const char*, const char*) { return reinterpret_cast<void*>(1); }
std::size_t Write(const void* data, std::size_t size, std::size_t count, void*) {
    const auto* begin = static_cast<const std::uint8_t*>(data);
    g_bytes.insert(g_bytes.end(), begin, begin + size * count);
    return size * count;
}
int Close(void*) { return 0; }

int main(int argc, char** argv) {
    assert(argc == 2);
    Mark(Event::CallbackFailed, Operation::None, 17);
    assert(FlushBounded() == FlushResult::NoApi);
    ConfigureFileApi(Open, Write, Close);
    Mark(Event::FileApiAccepted);
    assert(DrainPendingBounded() == FlushResult::Succeeded);
    assert(!HasPending());
    Mark(Event::ManifestEntered);
    assert(DrainPendingBounded() == FlushResult::Succeeded);
    assert(!HasPending());
    assert(g_bytes.size() == 5 * kRecordSize);
    assert(std::memcmp(g_bytes.data(), "ISAACPE1", 8) == 0);
    std::ofstream file(argv[1], std::ios::binary);
    file.write(reinterpret_cast<const char*>(g_bytes.data()),
               static_cast<std::streamsize>(g_bytes.size()));
    assert(file.good());
    return 0;
}
'''.lstrip(),
                encoding="utf-8",
            )
            binary = root / "api-order-harness"
            result = subprocess.run(
                [
                    "c++", "-std=c++20", "-pthread",
                    "-DEXL_PERSISTENCE_EVENT_DIAGNOSTIC",
                    "-DEXL_TEST_BUILD_ID=202609090004",
                    "-I", str(ROOT / "source"),
                    str(harness), str(ROOT / "source" / "persistence_event_journal.cpp"),
                    "-o", str(binary),
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            run = subprocess.run([str(binary), str(output)], text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_host_harness_flushes_install_failure_when_api_precedes_failure(self):
        with tempfile.TemporaryDirectory(prefix="persistence-journal-api-first-") as temporary:
            root = Path(temporary)
            harness = root / "api-first-harness.cpp"
            harness.write_text(
                r'''
#include "persistence_event_journal.hpp"
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <vector>

using namespace PersistenceEventJournal;
static std::vector<std::uint8_t> g_bytes;
void* Open(const char*, const char*) { return reinterpret_cast<void*>(1); }
std::size_t Write(const void* data, std::size_t size, std::size_t count, void*) {
    const auto* begin = static_cast<const std::uint8_t*>(data);
    g_bytes.insert(g_bytes.end(), begin, begin + size * count);
    return size * count;
}
int Close(void*) { return 0; }

int main() {
    ConfigureFileApi(Open, Write, Close);
    Mark(Event::FileApiAccepted);
    assert(DrainPendingBounded() == FlushResult::Succeeded);
    Mark(Event::CallbackFailed, Operation::None, 17);
    assert(DrainPendingBounded() == FlushResult::Succeeded);
    assert(!HasPending());
    assert(g_bytes.size() == 4 * kRecordSize);
    assert(std::memcmp(g_bytes.data() + 2 * kRecordSize, "ISAACPE1", 8) == 0);
    return 0;
}
'''.lstrip(),
                encoding="utf-8",
            )
            binary = root / "api-first-harness"
            result = subprocess.run(
                [
                    "c++", "-std=c++20", "-pthread",
                    "-DEXL_PERSISTENCE_EVENT_DIAGNOSTIC",
                    "-DEXL_TEST_BUILD_ID=202609090005",
                    "-I", str(ROOT / "source"),
                    str(harness), str(ROOT / "source" / "persistence_event_journal.cpp"),
                    "-o", str(binary),
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            run = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
