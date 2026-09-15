import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"


class PersistenceTraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-persistence-trace-")
        temporary = Path(cls.temporary.name)
        harness = temporary / "trace.cpp"
        cls.binary = temporary / "trace"
        harness.write_text(
            r'''
#include "persistence_trace.hpp"

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <vector>

namespace {
std::vector<std::uint8_t> g_file;
std::vector<std::size_t> g_write_sizes;

void* Open(const char* path, const char* mode) {
    if (std::strcmp(path, PersistenceTrace::kTracePath) != 0 || std::strcmp(mode, "wb") != 0) {
        return nullptr;
    }
    return reinterpret_cast<void*>(1);
}

std::size_t Write(const void* bytes, std::size_t size, std::size_t count, void*) {
    const std::size_t length = size * count;
    const auto* first = static_cast<const std::uint8_t*>(bytes);
    g_file.assign(first, first + length);
    g_write_sizes.push_back(length);
    return count;
}

int Close(void*) { return 0; }

std::uint32_t ReadU32(std::size_t offset) {
    std::uint32_t value = 0;
    for (std::size_t index = 0; index < 4; ++index) {
        value |= static_cast<std::uint32_t>(g_file[offset + index]) << (index * 8);
    }
    return value;
}

std::uint64_t ReadU64(std::size_t offset) {
    std::uint64_t value = 0;
    for (std::size_t index = 0; index < 8; ++index) {
        value |= static_cast<std::uint64_t>(g_file[offset + index]) << (index * 8);
    }
    return value;
}

std::uint32_t Checksum() {
    std::uint32_t value = 2166136261U;
    for (std::size_t index = 0; index < 36; ++index) {
        value ^= g_file[index];
        value *= 16777619U;
    }
    return value;
}
} // namespace

int main() {
    PersistenceTrace::RecordCallbackEntry();
    PersistenceTrace::Mark(PersistenceTrace::Phase::OriginalReturned);
    PersistenceTrace::ConfigureFileApi(Open, Write, Close);
    if (!g_write_sizes.empty()) return 1;
    PersistenceTrace::Flush();

    if (g_write_sizes.size() != 1 || g_write_sizes.back() != 43) return 2;
    if (g_file.size() != 43 || std::memcmp(g_file.data(), "ISAACPT2", 8) != 0) return 3;
    if (ReadU32(8) != 2 || ReadU32(12) != 3) return 4;
    if (ReadU64(16) != 0x7) return 5;
    if (ReadU64(24) != 0 || ReadU32(32) != 1) return 6;
    if (ReadU32(36) != Checksum()) return 7;

    PersistenceTrace::RecordCallbackEntry();
    PersistenceTrace::Mark(PersistenceTrace::Phase::OriginalReturned);
    if (g_write_sizes.size() != 1) return 8;

    PersistenceTrace::Mark(PersistenceTrace::Phase::ManifestStarted);
    PersistenceTrace::Flush();
    PersistenceTrace::Mark(PersistenceTrace::Phase::ManifestCompleted, 0x8000000000000014ULL);
    PersistenceTrace::Flush();
    PersistenceTrace::Mark(PersistenceTrace::Phase::PersistenceGateReady);
    PersistenceTrace::Flush();
    PersistenceTrace::Mark(PersistenceTrace::Phase::DispatchStarted);
    PersistenceTrace::Flush();
    PersistenceTrace::Mark(PersistenceTrace::Phase::DispatchReturned);
    PersistenceTrace::Flush();
    PersistenceTrace::Mark(PersistenceTrace::Phase::CallbackFailed, 0x123456789abcdef0ULL);
    PersistenceTrace::Flush();

    const std::size_t expected_sizes[] = {43, 44, 45, 46, 47, 48, 49};
    if (g_write_sizes.size() != sizeof(expected_sizes) / sizeof(expected_sizes[0])) return 9;
    for (std::size_t index = 0; index < g_write_sizes.size(); ++index) {
        if (g_write_sizes[index] != expected_sizes[index]) return 10;
    }
    if (ReadU32(12) != 9 || ReadU64(16) != 0x1ff) return 11;
    if (ReadU64(24) != 0x123456789abcdef0ULL || ReadU32(32) != 2) return 12;
    if (ReadU32(36) != Checksum()) return 13;
    return 0;
}
'''.lstrip(),
            encoding="utf-8",
        )
        build = subprocess.run(
            [
                "c++",
                "-std=c++23",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-pthread",
                "-DEXL_PERSISTENCE_TRACE",
                "-I",
                str(SOURCE),
                str(harness),
                str(SOURCE / "persistence_trace.cpp"),
                "-o",
                str(cls.binary),
            ],
            text=True,
            capture_output=True,
        )
        if build.returncode != 0:
            raise AssertionError(build.stdout + build.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_delayed_file_api_flushes_prior_milestones_and_encodes_snapshot(self):
        result = subprocess.run([str(self.binary)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_non_trace_build_exposes_noop_interface_without_trace_strings(self):
        header = (SOURCE / "persistence_trace.hpp").read_text(encoding="utf-8")
        source = (SOURCE / "persistence_trace.cpp").read_text(encoding="utf-8")
        self.assertIn("#if defined(EXL_PERSISTENCE_TRACE)", header)
        self.assertIn("#if defined(EXL_PERSISTENCE_TRACE)", source)
        self.assertIn("inline void Mark(Phase, std::uint64_t = 0) {}", header)

    def test_concurrent_phases_keep_details_in_phase_specific_slots(self):
        source = (SOURCE / "persistence_trace.cpp").read_text(encoding="utf-8")
        self.assertIn("std::array<std::atomic<std::uint64_t>, 9> g_details{};", source)
        self.assertIn("g_details[phase - 1].load", source)
        self.assertIn("g_details[value - 1].exchange", source)

    def test_flush_uses_a_dirty_bit_instead_of_revision_bookkeeping(self):
        source = (SOURCE / "persistence_trace.cpp").read_text(encoding="utf-8")
        self.assertIn("std::atomic<std::uint32_t> g_dirty{1};", source)
        self.assertIn("g_dirty.exchange(0", source)
        self.assertNotIn("g_flushedRevision", source)
        self.assertNotIn("g_revision", source)

    def test_trace_avoids_bool_atomics_in_runtime_callback_path(self):
        source = (SOURCE / "persistence_trace.cpp").read_text(encoding="utf-8")

        self.assertNotIn("std::atomic<bool>", source)
        self.assertIn("std::atomic<std::uint32_t> g_fileApiReady{0};", source)

    def test_callback_counter_uses_compact_atomic_increment(self):
        source = (SOURCE / "persistence_trace.cpp").read_text(encoding="utf-8")
        callback = source[source.index("void RecordCallbackEntry()") :]
        self.assertIn("g_callbackCount.fetch_add(1", callback)
        self.assertNotIn("numeric_limits", callback)


if __name__ == "__main__":
    unittest.main()
