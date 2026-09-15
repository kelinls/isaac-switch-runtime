"""Domain 与 Ports 层的边界和行为测试。

Domain 层必须是可宿主编译、可宿主运行的纯 C++：它决定 Runtime 的状态迁移、
能力集合和持久化编码，但不接触 Switch、SaltyNX、exlaunch、Lua 或任何文件系统。
因此这里同时检查“依赖方向”和“行为”，避免 Domain 悄悄长回平台细节。
"""

import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "runtime" / "src"
LEGACY_SOURCE = ROOT / "runtime" / "source"

FORBIDDEN_INCLUDES = (
    "lua",
    "exlaunch",
    "saltynx",
    "SaltySD",
    "nn/",
    "switch.h",
    "diagnostics/",
    "infrastructure/",
    "application/",
    "interfaces/",
)

HOST_DRIVER = textwrap.dedent(
    r"""
    #include "domain/runtime/runtime_state.hpp"
    #include "domain/runtime/runtime_state_machine.hpp"
    #include "domain/runtime/runtime_context.hpp"
    #include "domain/runtime/capability_set.hpp"
    #include "domain/runtime/thread_affinity.hpp"
    #include "domain/mod/mod_manifest.hpp"
    #include "domain/mod/mod_handle.hpp"
    #include "domain/callback/callback_descriptor.hpp"
    #include "domain/persistence/persistence_record.hpp"
    #include "domain/persistence/persistence_codec.hpp"
    #include "ports/content_port.hpp"
    #include "ports/event_sink.hpp"
    #include "ports/file_port.hpp"
    #include "ports/game_memory_port.hpp"
    #include "ports/hook_port.hpp"
    #include "ports/lua_engine_port.hpp"
    #include "ports/module_scanner_port.hpp"
    #include "ports/runtime_host_api.hpp"
    #include "ports/thread_port.hpp"

    #include <cstddef>
    #include <cstdio>
    #include <cstring>

    using namespace isaac::runtime;

    namespace {

    int failures = 0;

    void Check(bool condition, const char* what) {
        if (!condition) {
            std::printf("FAILED_CHECK %s\n", what);
            ++failures;
        }
    }

    void TestStateChain() {
        RuntimeContext context{};
        const RuntimeEvent chain[] = {
            RuntimeEvent::ModuleEntered,     RuntimeEvent::HeapReady,
            RuntimeEvent::PlatformReady,     RuntimeEvent::ConstructorsReady,
            RuntimeEvent::RuntimeEntered,    RuntimeEvent::WorkerStarting,
            RuntimeEvent::Scanning,          RuntimeEvent::HooksInstalling,
            RuntimeEvent::RuntimeReady,      RuntimeEvent::ModLoadStarted,
            RuntimeEvent::ModLoaded,         RuntimeEvent::ModRunStarted,
        };
        for (RuntimeEvent event : chain) {
            Check(RuntimeStateMachine::Apply(context, event).ok(), ToString(event));
        }
        Check(context.state == RuntimeState::Running, "chain_ends_in_running");
        Check(context.enteredStateCount == 12, "twelve_transitions");
        Check(context.lastFailure.ok(), "successful_chain_has_no_failure");
        Check(context.running() && context.usable(), "running_context_is_usable");
    }

    void TestRejectedTransitions() {
        RuntimeContext cold{};
        Check(RuntimeStateMachine::Apply(cold, RuntimeEvent::ModRunStarted).code() ==
                  StatusCode::InvalidState,
              "cold_cannot_jump_to_running");
        Check(cold.state == RuntimeState::Cold, "failed_transition_keeps_state");
        Check(!cold.lastFailure.ok(), "failed_transition_is_recorded");

        RuntimeContext running{};
        running.state = RuntimeState::Running;
        Check(RuntimeStateMachine::Apply(running, RuntimeEvent::HeapReady).code() ==
                  StatusCode::InvalidState,
              "running_cannot_go_backwards");

        RuntimeContext disabled{};
        disabled.state = RuntimeState::Disabled;
        Check(!RuntimeStateMachine::Apply(disabled, RuntimeEvent::ModuleEntered).ok(),
              "terminal_state_rejects_every_event");
        Check(IsTerminal(RuntimeState::ModDisabled), "mod_disabled_is_terminal");
        Check(AllowsGameToContinue(RuntimeState::ModDisabled), "mod_failure_keeps_game_running");
        Check(!AllowsGameToContinue(RuntimeState::Disabled), "runtime_failure_stops_runtime");
    }

    void TestFailureEdges() {
        RuntimeContext scanning{};
        scanning.state = RuntimeState::Scanning;
        Check(RuntimeStateMachine::Apply(scanning, RuntimeEvent::ModuleMismatch).ok() &&
                  scanning.state == RuntimeState::Disabled,
              "module_mismatch_disables_runtime");

        RuntimeContext modLoading{};
        modLoading.state = RuntimeState::ModLoading;
        Check(RuntimeStateMachine::Apply(modLoading, RuntimeEvent::ModLoadFailed).ok() &&
                  modLoading.state == RuntimeState::ModDisabled,
              "mod_load_failure_disables_mod_only");
    }

    void TestCapabilities() {
        CapabilitySet set{};
        Check(!set.Has(Capability::FileApi), "capability_starts_empty");
        Check(set.Add(Capability::FileApi), "first_add_reports_change");
        Check(!set.Add(Capability::FileApi), "duplicate_add_reports_no_change");
        Check(set.Has(Capability::FileApi) && !set.Has(Capability::LuaEngine), "only_added_bit_set");
        set.Remove(Capability::FileApi);
        Check(!set.Has(Capability::FileApi), "removed_capability_cleared");
    }

    void TestPersistenceFormat() {
        Check(PersistenceCodec::kVersion == 2, "version_is_two");
        Check(PersistenceCodec::kHeaderSize == 32, "header_is_32_bytes");
        Check(PersistenceCodec::kRecordHeaderSize == 24, "record_header_is_24_bytes");
        Check(PersistenceCodec::kMaximumFileSize == 16 * 1024, "maximum_file_is_16k");
        Check(PersistenceCodec::kMaximumRecordCount == 256, "maximum_records_is_256");
        Check(kModDataKey == 0x6D6F642D64617461ULL, "mod_data_key_matches_on_device_format");
        Check(kStringRecordType == 1, "string_type_matches_on_device_format");
        Check(std::memcmp(PersistenceCodec::kMagic, "ISMODST2", 8) == 0, "magic_matches");
        // Values computed with the same FNV-1a 64 algorithm the on-device
        // implementation uses; a change here would silently re-key every
        // existing Mod's saved data.
        Check(HashModNamespace("MuteOnPause", 11) == 0x43655449B4EACD35ULL,
              "namespace_hash_matches_on_device_value");
        Check(HashModNamespace("mod-data", 8) == 0x87A22078BBAE20BAULL,
              "namespace_hash_of_known_name");
    }

    void TestCodecRoundTrip() {
        std::uint8_t payload[64]{};
        std::uint8_t file[PersistenceCodec::kMaximumFileSize]{};
        const char* value = "12345678";

        PersistenceRecordView record{};
        record.namespaceId = HashModNamespace("MuteOnPause", 11);
        record.key = kModDataKey;
        record.type = kStringRecordType;
        record.valueLength = 8;
        record.value = reinterpret_cast<const std::uint8_t*>(value);

        std::size_t next = 0;
        Check(PersistenceCodec::EncodeRecord(payload, sizeof(payload), 0, record, &next).ok(),
              "encode_record");
        Check(next == PersistenceCodec::kRecordHeaderSize + 8, "encoded_record_length");
        Check(PersistenceCodec::EncodeRecord(payload, 8, 0, record, &next).code() ==
                  StatusCode::CapacityExceeded,
              "encode_record_rejects_small_capacity");
        next = 0;
        Check(PersistenceCodec::EncodeRecord(payload, sizeof(payload), 0, record, &next).ok(),
              "encode_record_again");

        PersistenceFileHeader header{};
        header.generation = 1;
        header.recordCount = 1;
        header.payloadLength = static_cast<std::uint32_t>(next);
        header.payloadChecksum = PersistenceCodec::Checksum(payload, next);
        Check(PersistenceCodec::EncodeHeader(file, sizeof(file), header).ok(), "encode_header");
        std::memcpy(file + PersistenceCodec::kHeaderSize, payload, next);
        const std::size_t total = PersistenceCodec::kHeaderSize + next;
        Check(PersistenceCodec::ValidateFile(file, total).ok(), "validate_round_trip");

        header.recordCount = PersistenceCodec::kMaximumRecordCount + 1;
        Check(PersistenceCodec::EncodeHeader(file, sizeof(file), header).code() ==
                  StatusCode::CapacityExceeded,
              "encode_header_rejects_too_many_records");
        header.recordCount = 1;
        Check(PersistenceCodec::EncodeHeader(file, sizeof(file), header).ok(), "encode_header_again");
        std::memcpy(file + PersistenceCodec::kHeaderSize, payload, next);

        std::uint8_t broken[PersistenceCodec::kMaximumFileSize];
        std::memcpy(broken, file, total);
        broken[total - 1] = static_cast<std::uint8_t>(broken[total - 1] ^ 0xFF);
        Check(PersistenceCodec::ValidateFile(broken, total).code() == StatusCode::Corrupted,
              "checksum_mismatch_rejected");
        Check(PersistenceCodec::ValidateFile(file, total - 1).code() == StatusCode::Corrupted,
              "truncated_file_rejected");

        std::uint8_t badMagic[PersistenceCodec::kHeaderSize]{};
        PersistenceFileHeader decodedHeader{};
        Check(PersistenceCodec::DecodeHeader(badMagic, sizeof(badMagic), &decodedHeader).code() ==
                  StatusCode::Corrupted,
              "bad_magic_rejected");

        PersistenceRecordView decoded{};
        std::size_t offset = 0;
        Check(PersistenceCodec::DecodeRecord(file + PersistenceCodec::kHeaderSize,
                                             header.payloadLength, offset, &decoded, &offset).ok(),
              "decode_record");
        Check(decoded.key == kModDataKey && decoded.type == kStringRecordType,
              "decoded_record_identity");
        Check(decoded.valueLength == 8 && std::memcmp(decoded.value, value, 8) == 0,
              "decoded_record_value");
    }

    void TestHostApiAbi() {
        Check(offsetof(HostFileApiV1, open) == 8, "host_file_api_open_offset");
        Check(offsetof(HostFileApiV1, close) == 32, "host_file_api_close_offset");
        Check(offsetof(RuntimeHostApiV1, RegisterFilePort) == 24,
              "runtime_host_api_register_offset");
        Check(offsetof(RuntimeHostApiV1, PublishHostEvent) == 32,
              "runtime_host_api_publish_offset");
        Check(kHostApiVersion1 == 1, "host_api_version_is_one");

        HostFileApiV1 incomplete{};
        Check(!incomplete.complete(), "incomplete_file_api_rejected");
        incomplete.open = [](const char*, const char*) -> void* { return nullptr; };
        incomplete.read = [](void*, std::size_t, std::size_t, void*) -> std::size_t { return 0; };
        incomplete.write = [](const void*, std::size_t, std::size_t, void*) -> std::size_t { return 0; };
        incomplete.close = [](void*) -> int { return 0; };
        Check(incomplete.complete(), "complete_file_api_accepted");
    }

    void TestValueObjects() {
        ModHandle handle{};
        Check(!handle.valid(), "default_handle_invalid");
        handle.index = 1;
        handle.generation = 1;
        Check(handle.valid(), "assigned_handle_valid");

        CallbackDescriptor descriptor{};
        Check(!descriptor.valid(), "default_callback_invalid");
        descriptor.id = 7;
        descriptor.owner = handle;
        descriptor.luaReference = 3;
        Check(descriptor.valid(), "complete_callback_valid");

        ModManifest manifest{};
        Check(manifest.Directory()[0] == '\0' && manifest.Entry()[0] == '\0',
              "manifest_starts_empty");
    }

    } // namespace

    int main() {
        TestStateChain();
        TestRejectedTransitions();
        TestFailureEdges();
        TestCapabilities();
        TestPersistenceFormat();
        TestCodecRoundTrip();
        TestHostApiAbi();
        TestValueObjects();
        if (failures != 0) {
            std::printf("HOST_CHECKS_FAILED %d\n", failures);
            return 1;
        }
        std::printf("HOST_CHECKS_PASSED\n");
        return 0;
    }
    """
)

# Byte-for-byte comparison against the encoder that produced the files already
# on the device. If the new codec ever diverges, every existing Mod's saved data
# becomes unreadable, so this is checked by running both implementations.
COMPATIBILITY_DRIVER = textwrap.dedent(
    r"""
    #include "domain/persistence/persistence_codec.hpp"
    #include "mod_persistence.hpp"

    #include <cstdio>
    #include <cstring>
    #include <vector>

    namespace {

    std::vector<unsigned char> g_written;

    void* FakeOpen(const char* path, const char* mode) {
        (void)path;
        // "rb" reports a missing file so no read path runs; "wb" yields a handle.
        return (mode != nullptr && mode[0] == 'w') ? reinterpret_cast<void*>(0x1) : nullptr;
    }

    std::size_t FakeRead(void*, std::size_t, std::size_t, void*) { return 0; }

    std::size_t FakeWrite(const void* source, std::size_t size, std::size_t count, void*) {
        const unsigned char* bytes = static_cast<const unsigned char*>(source);
        g_written.insert(g_written.end(), bytes, bytes + size * count);
        return count;
    }

    int FakeClose(void*) { return 0; }

    } // namespace

    int main() {
        using isaac::runtime::PersistenceCodec;
        using isaac::runtime::PersistenceFileHeader;
        using isaac::runtime::PersistenceRecordView;

        const char* value = "12345678";
        ModPersistence::ConfigureFileApi(&FakeOpen, &FakeRead, &FakeWrite, &FakeClose);
        const std::uint64_t namespaceId = ModPersistence::HashModNamespace("MuteOnPause", 11);
        if (ModPersistence::SaveModData(namespaceId, value, 8) != ModPersistence::Result::Success) {
            std::printf("LEGACY_ENCODER_FAILED\n");
            return 1;
        }

        std::uint8_t file[PersistenceCodec::kMaximumFileSize]{};
        std::uint8_t payload[64]{};
        PersistenceRecordView record{};
        record.namespaceId = namespaceId;
        record.key = isaac::runtime::kModDataKey;
        record.type = isaac::runtime::kStringRecordType;
        record.valueLength = 8;
        record.value = reinterpret_cast<const std::uint8_t*>(value);

        std::size_t payloadLength = 0;
        if (!PersistenceCodec::EncodeRecord(payload, sizeof(payload), 0, record, &payloadLength).ok()) {
            std::printf("NEW_CODEC_ENCODE_RECORD_FAILED\n");
            return 1;
        }

        PersistenceFileHeader header{};
        header.generation = 1;
        header.recordCount = 1;
        header.payloadLength = static_cast<std::uint32_t>(payloadLength);
        header.payloadChecksum = PersistenceCodec::Checksum(payload, payloadLength);
        if (!PersistenceCodec::EncodeHeader(file, sizeof(file), header).ok()) {
            std::printf("NEW_CODEC_ENCODE_HEADER_FAILED\n");
            return 1;
        }
        std::memcpy(file + PersistenceCodec::kHeaderSize, payload, payloadLength);
        const std::size_t total = PersistenceCodec::kHeaderSize + payloadLength;

        if (g_written.size() != total) {
            std::printf("SIZE_MISMATCH legacy=%zu new=%zu\n", g_written.size(), total);
            return 1;
        }
        if (std::memcmp(g_written.data(), file, total) != 0) {
            std::printf("BYTE_MISMATCH\n");
            return 1;
        }
        if (!PersistenceCodec::ValidateFile(g_written.data(), g_written.size()).ok()) {
            std::printf("VALIDATE_LEGACY_BYTES_FAILED\n");
            return 1;
        }
        std::printf("CODEC_MATCHES_LEGACY_ENCODER\n");
        return 0;
    }
    """
)


def host_compiler() -> str | None:
    for candidate in ("c++", "clang++", "g++"):
        found = shutil.which(candidate)
        if found is not None:
            return found
    return None


class DomainAndPortBoundaryTests(unittest.TestCase):
    def test_domain_and_ports_have_no_platform_dependencies(self):
        offenders = []
        for directory in (SRC / "domain", SRC / "ports"):
            for path in sorted(directory.rglob("*")):
                if path.suffix not in (".hpp", ".cpp"):
                    continue
                for line in path.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if not stripped.startswith("#include"):
                        continue
                    for token in FORBIDDEN_INCLUDES:
                        if token in stripped:
                            offenders.append(f"{path.relative_to(ROOT)}: {stripped}")
        self.assertEqual(offenders, [], "Domain/Ports 不得依赖平台或上层模块")

    def test_domain_and_ports_are_host_runnable(self):
        compiler = host_compiler()
        if compiler is None:
            self.skipTest("需要宿主 C++ 编译器")
        with tempfile.TemporaryDirectory(prefix="runtime-domain-host-") as temporary:
            directory = Path(temporary)
            source = directory / "driver.cpp"
            binary = directory / "driver"
            source.write_text(HOST_DRIVER, encoding="utf-8")
            compile_result = subprocess.run(
                [
                    compiler,
                    "-std=c++17",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(SRC),
                    str(source),
                    str(SRC / "domain" / "runtime" / "runtime_state_machine.cpp"),
                    str(SRC / "domain" / "persistence" / "persistence_codec.cpp"),
                    "-o",
                    str(binary),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(run_result.returncode, 0, run_result.stdout + run_result.stderr)
            self.assertIn("HOST_CHECKS_PASSED", run_result.stdout)

    def test_new_codec_matches_the_on_device_encoder(self):
        compiler = host_compiler()
        if compiler is None:
            self.skipTest("需要宿主 C++ 编译器")
        with tempfile.TemporaryDirectory(prefix="runtime-codec-compat-") as temporary:
            directory = Path(temporary)
            source = directory / "compat.cpp"
            binary = directory / "compat"
            source.write_text(COMPATIBILITY_DRIVER, encoding="utf-8")
            compile_result = subprocess.run(
                [
                    compiler,
                    "-std=c++17",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(SRC),
                    "-I",
                    str(LEGACY_SOURCE),
                    str(source),
                    str(SRC / "domain" / "persistence" / "persistence_codec.cpp"),
                    "-o",
                    str(binary),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(run_result.returncode, 0, run_result.stdout + run_result.stderr)
            self.assertIn("CODEC_MATCHES_LEGACY_ENCODER", run_result.stdout)


if __name__ == "__main__":
    unittest.main()
