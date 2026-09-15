import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"
# `LAYERED_RUNTIME` 的分层源码根，经 `RUNTIME_EXTRA_SOURCE_ROOTS=src` 参与设备编译。
# 布尔原子禁令必须同时覆盖这两个根：只扫 `source` 会漏掉整个 `runtime/src/`，
# 2026-09-13 就因此让一个布尔原子从盲区溜进了设备包（见 `docs/问题与解决记录.md`）。
LAYERED_SOURCE = ROOT / "runtime" / "src"


class ManagerUpdateHookAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-manager-hook-audit-")
        temporary = Path(cls.temporary.name)
        harness = temporary / "audit.cpp"
        cls.binary = temporary / "audit"
        harness.write_text(
            r'''
#include "manager_update_hook_audit.hpp"
#include "runtime_constants.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <vector>

namespace {
std::vector<std::uint8_t> g_file;
std::vector<std::vector<std::uint8_t>> g_writes;

void* Open(const char* path, const char* mode) {
    if (std::strcmp(path, ManagerUpdateHookAudit::kAuditPath) != 0 ||
        std::strcmp(mode, "wb") != 0) {
        return nullptr;
    }
    return reinterpret_cast<void*>(1);
}

std::size_t Write(const void* bytes, std::size_t size, std::size_t count, void*) {
    const std::size_t length = size * count;
    const auto* first = static_cast<const std::uint8_t*>(bytes);
    g_file.assign(first, first + length);
    g_writes.emplace_back(first, first + length);
    return count;
}

int Close(void*) { return 0; }

std::uint32_t ReadU32(const std::vector<std::uint8_t>& bytes, std::size_t offset) {
    std::uint32_t value = 0;
    for (std::size_t index = 0; index < 4; ++index) {
        value |= static_cast<std::uint32_t>(bytes[offset + index]) << (index * 8);
    }
    return value;
}

std::uint64_t ReadU64(const std::vector<std::uint8_t>& bytes, std::size_t offset) {
    std::uint64_t value = 0;
    for (std::size_t index = 0; index < 8; ++index) {
        value |= static_cast<std::uint64_t>(bytes[offset + index]) << (index * 8);
    }
    return value;
}

std::uint32_t Checksum(const std::vector<std::uint8_t>& bytes) {
    std::uint32_t value = 2166136261U;
    for (std::size_t index = 0; index < 124; ++index) {
        value ^= bytes[index];
        value *= 16777619U;
    }
    return value;
}
} // namespace

int main() {
    using namespace ManagerUpdateHookAudit;
    RecordWorkerState(WorkerState::Started, 0);
    RecordWorkerState(WorkerState::RuntimeEntered, 0);
    ConfigureFileApi(Open, Write, Close);
    if (!g_writes.empty()) return 1;
    Flush();
    if (g_writes.size() != 1 || g_file.size() != 128) return 2;
    if (std::memcmp(g_file.data(), "ISAACHK1", 8) != 0) return 3;
    if (ReadU32(g_file, 8) != 1 || ReadU32(g_file, 12) != 128) return 4;
    if (ReadU32(g_file, 16) != static_cast<std::uint32_t>(WorkerState::RuntimeEntered)) return 5;
    if (ReadU32(g_file, 20) != 0) return 17;

    const ObserverSnapshot entered = CaptureObserverSnapshot();
    if (entered.workerState != static_cast<std::uint32_t>(WorkerState::RuntimeEntered) ||
        entered.workerDetail != 0 || entered.scanAttempt != 0 || entered.sampleCount != 0 ||
        entered.callbackCount != 0 || entered.flags != 0) return 18;

    RecordScanAttempt(1);
    RecordScanAttempt(2);
    Flush();
    if (g_writes.size() != 2 || ReadU32(g_file, 24) != 2) return 5;
    RecordWorkerState(WorkerState::Found, 0);

    alignas(8) std::array<std::uint8_t, 4> target = kManagerRelayExpectedEntry;
    alignas(8) std::array<std::uint8_t, 32> relay = kManagerRelayExpectedBytes;
    constexpr std::uintptr_t callback = 0x123456789abcdef0ULL;
    std::memcpy(relay.data() + 24, &callback, sizeof(callback));
    const std::uintptr_t targetAddress = reinterpret_cast<std::uintptr_t>(target.data());
    const std::uintptr_t relayAddress = reinterpret_cast<std::uintptr_t>(relay.data());
    const std::uintptr_t slotAddress = reinterpret_cast<std::uintptr_t>(relay.data() + 24);

    RecordInstall(0x10000000, targetAddress, relayAddress, slotAddress, callback, true);
    Flush();
    const std::uint32_t allStable = kInstallRecorded | kTargetEntryMatches |
        kRelayMatches | kSlotMatches | kCallbackMappedRx;
    if ((ReadU32(g_file, 36) & allStable) != allStable) return 6;
    if (ReadU32(g_file, 28) != 1 || ReadU64(g_file, 88) != callback) return 7;
    if (ReadU64(g_file, 48) != 0x10000000 || ReadU64(g_file, 80) != callback) return 8;

    const std::size_t afterInstall = g_writes.size();
    for (int index = 0; index < 8; ++index) Sample(false);
    if (g_writes.size() != afterInstall) return 9;
    Sample(false);
    Flush();
    if (g_writes.size() != afterInstall + 1 || ReadU32(g_file, 28) != 10) return 10;

    target[0] ^= 0xff;
    Sample(false);
    Flush();
    if ((ReadU32(g_file, 36) & kTargetEntryMatches) == 0) return 11;
    target[0] ^= 0xff;
    Sample(false);
    Flush();
    if ((ReadU32(g_file, 36) & kTargetEntryMatches) == 0) return 12;

    const std::size_t beforeCallback = g_writes.size();
    RecordCallbackEntry();
    if (g_writes.size() != beforeCallback) return 13;
    Sample(false);
    Flush();
    if ((ReadU32(g_file, 36) & kCallbackEntered) == 0 || ReadU32(g_file, 40) != 1) return 14;

    Sample(true);
    Flush();
    if ((ReadU32(g_file, 36) & kAuditCompleted) == 0) return 15;
    const ObserverSnapshot completed = CaptureObserverSnapshot();
    if (completed.workerState != static_cast<std::uint32_t>(WorkerState::Found) ||
        completed.scanAttempt != 2 || completed.sampleCount != 14 ||
        completed.callbackCount != 1 ||
        (completed.flags & (kInstallRecorded | kCallbackEntered | kAuditCompleted)) !=
            (kInstallRecorded | kCallbackEntered | kAuditCompleted)) return 19;
    if (ReadU32(g_file, 124) != Checksum(g_file)) return 16;
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
                str(SOURCE / "manager_update_hook_audit.cpp"),
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

    def test_protocol_and_state_changes(self):
        result = subprocess.run([str(self.binary)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_non_trace_build_exposes_noop_interface(self):
        header = (SOURCE / "manager_update_hook_audit.hpp").read_text(encoding="utf-8")
        source = (SOURCE / "manager_update_hook_audit.cpp").read_text(encoding="utf-8")
        self.assertIn("#if defined(EXL_PERSISTENCE_TRACE)", header)
        self.assertIn("#if defined(EXL_PERSISTENCE_TRACE)", source)
        self.assertIn("inline void Sample(bool) {}", header)

    def test_flush_uses_a_dirty_bit_instead_of_revision_bookkeeping(self):
        source = (SOURCE / "manager_update_hook_audit.cpp").read_text(encoding="utf-8")
        self.assertIn("std::atomic<std::uint32_t> g_dirty{1};", source)
        self.assertIn("g_dirty.exchange(0", source)
        self.assertNotIn("g_flushedRevision", source)
        self.assertNotIn("g_revision", source)

    def test_audit_avoids_bool_atomics_in_runtime_snapshot_path(self):
        source = (SOURCE / "manager_update_hook_audit.cpp").read_text(encoding="utf-8")

        self.assertNotIn("std::atomic<bool>", source)
        self.assertIn("std::atomic<std::uint32_t> g_installRecorded{0};", source)
        self.assertIn("std::atomic<std::uint32_t> g_callbackMappedRx{0};", source)
        self.assertIn("std::atomic<std::uint32_t> g_finalSample{0};", source)

    def test_runtime_source_contains_no_bool_atomics(self):
        # 两个源码根都要扫：`source`（旧根）与 `src`（LAYERED_RUNTIME 的分层根）。
        for root in (SOURCE, LAYERED_SOURCE):
            for path in sorted(root.rglob("*")):
                if path.suffix not in {".cpp", ".hpp"}:
                    continue
                with self.subTest(path=path.relative_to(ROOT)):
                    self.assertNotIn("std::atomic<bool>", path.read_text(encoding="utf-8"))

    def test_observer_snapshot_is_atomic_and_has_no_file_io(self):
        header = (SOURCE / "manager_update_hook_audit.hpp").read_text(encoding="utf-8")
        source = (SOURCE / "manager_update_hook_audit.cpp").read_text(encoding="utf-8")

        self.assertIn("struct ObserverSnapshot", header)
        self.assertIn("ObserverSnapshot CaptureObserverSnapshot();", header)
        observer_body = source[source.index("ObserverSnapshot CaptureObserverSnapshot()") :]
        self.assertIn("g_workerState.load", observer_body)
        self.assertIn("g_workerDetail.load", observer_body)
        self.assertIn("g_callbackCount.load", observer_body)
        self.assertNotIn("TryFlush", observer_body)
        self.assertNotIn("open(", observer_body)

    def test_audit_snapshot_does_not_reread_target_module_memory(self):
        source = (SOURCE / "manager_update_hook_audit.cpp").read_text(encoding="utf-8")
        snapshot_body = source[source.index("std::array<std::uint8_t, kSnapshotSize> BuildSnapshot()") :
                               source.index("void TryFlush()")]

        self.assertNotIn("reinterpret_cast<const void*>(target)", snapshot_body)
        self.assertNotIn("reinterpret_cast<const void*>(relay)", snapshot_body)
        self.assertNotIn("reinterpret_cast<const void*>(slot)", snapshot_body)

    def test_trace_worker_flushes_audit_without_registration_thread_io(self):
        runtime_entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        bridge = (SOURCE / "saltynx_runtime_bridge.cpp").read_text(encoding="utf-8")

        self.assertIn(
            "ManagerUpdateHookAudit::RecordWorkerState(ManagerUpdateHookAudit::WorkerState::Started, 0);\n"
            "    ManagerUpdateHookAudit::Flush();",
            runtime_entry,
        )
        self.assertIn(
            "ManagerUpdateHookAudit::RecordScanAttempt(attempt + 1);\n"
            "        ManagerUpdateHookAudit::Flush();",
            runtime_entry,
        )
        self.assertIn(
            "ManagerUpdateHookAudit::Sample(finalSample);\n"
            "                    ManagerUpdateHookAudit::Flush();",
            runtime_entry,
        )
        register_body = bridge[bridge.index("IsaacModRuntime_RegisterSaltyFileApi") :
                               bridge.index("#if defined(EXL_PERSISTENCE_TRACE)")]
        self.assertNotIn("ManagerUpdateHookAudit::Flush", register_body)


if __name__ == "__main__":
    unittest.main()
