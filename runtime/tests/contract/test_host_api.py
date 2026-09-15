"""SaltyNX Host API 契约测试（宿主编译 + 行为断言 + 源码契约）。

Task 6 把插件与 Runtime 之间的多个字符串符号收敛为 `RuntimeHostApiV1`：
插件只注册平台能力，Runtime 在发布前校验版本、结构体大小、Build ID 与四个
文件函数，认证通过后才一次性发布完整文件表。
"""

import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "runtime" / "src"
SOURCE = ROOT / "runtime" / "source"
BUILD_ID = "0x20260910250000"

DRIVER = textwrap.dedent(
    r"""
    #include "infrastructure/saltynx/runtime_host_api_service.hpp"

    #include <cstdio>

    using namespace isaac::runtime;

    extern "C" const RuntimeHostApiV1* IsaacModRuntime_GetHostApi(std::uint32_t requestedVersion);

    namespace {

    int failures = 0;
    void Check(bool condition, const char* what) {
        if (!condition) { std::printf("FAILED_CHECK %s\n", what); ++failures; }
    }

    void* FakeOpen(const char*, const char*) { return reinterpret_cast<void*>(0x1234); }
    std::size_t FakeRead(void*, std::size_t, std::size_t, void*) { return 0; }
    std::size_t FakeWrite(const void*, std::size_t, std::size_t, void*) { return 0; }
    int FakeClose(void*) { return 0; }

    HostFileApiV1 CompleteTable() {
        HostFileApiV1 table{};
        table.open = &FakeOpen;
        table.read = &FakeRead;
        table.write = &FakeWrite;
        table.close = &FakeClose;
        return table;
    }

    } // namespace

    int main() {
        RuntimeHostApiService service{0xABCDEF1234ULL};

        const RuntimeHostApiV1& abi = service.Abi();
        Check(abi.abiVersion == kHostApiVersion1, "abi_version_published");
        Check(abi.structSize == kRuntimeHostApiSizeV1, "struct_size_published");
        Check(abi.buildId == 0xABCDEF1234ULL, "build_id_published");
        Check((abi.capabilities & kHostCapabilityFilePort) != 0, "file_port_capability");
        Check((abi.capabilities & kHostCapabilityHostEvent) != 0, "host_event_capability");
        Check(abi.RegisterFilePort != nullptr && abi.PublishHostEvent != nullptr,
              "abi_entry_points_present");

        HostFileApiV1 published{};
        Check(!service.HasFileApi(), "no_file_api_before_registration");
        Check(!service.PublishedFileApi(&published), "no_snapshot_before_registration");
        Check(!service.PublishedFileApi(nullptr), "null_snapshot_rejected");

        HostFileApiV1 wrongVersion = CompleteTable();
        wrongVersion.abiVersion = kHostApiVersion1 + 1;
        Check(service.RegisterFilePort(wrongVersion) == HostRegisterResult::RejectedVersion,
              "version_mismatch_rejected");
        HostFileApiV1 wrongSize = CompleteTable();
        wrongSize.structSize = kHostFileApiSizeV1 + 8;
        Check(service.RegisterFilePort(wrongSize) == HostRegisterResult::RejectedVersion,
              "struct_size_mismatch_rejected");
        HostFileApiV1 incomplete = CompleteTable();
        incomplete.close = nullptr;
        Check(service.RegisterFilePort(incomplete) == HostRegisterResult::RejectedIncomplete,
              "partial_registration_rejected");
        Check(!service.HasFileApi(), "rejected_registration_stays_invisible");

        const HostFileApiV1 complete = CompleteTable();
        Check(service.RegisterFilePort(complete) == HostRegisterResult::Accepted,
              "complete_registration_accepted");
        Check(service.HasFileApi(), "file_api_visible_after_registration");
        Check(service.PublishedFileApi(&published), "snapshot_available");
        Check(published.open == &FakeOpen && published.close == &FakeClose,
              "published_table_matches_registration");

        // Re-registration must replace the table atomically, not merge into it.
        const HostFileApiV1 replacement = CompleteTable();
        Check(service.RegisterFilePort(replacement) == HostRegisterResult::Accepted,
              "second_registration_accepted");
        Check(service.PublishedFileApi(&published) && published.open == &FakeOpen,
              "second_registration_replaces_table");

        // The ABI thunks must route into the process-wide instance the exported
        // entry point publishes, not into the local test instance.
        Check(!HostApiService().HasFileApi(), "global_instance_starts_empty");
        Check(service.Abi().RegisterFilePort(&complete) == HostRegisterResult::Accepted,
              "abi_register_entry_accepted");
        HostFileApiV1 throughThunk{};
        Check(HostApiService().PublishedFileApi(&throughThunk) && throughThunk.open == &FakeOpen,
              "abi_register_entry_reaches_global_instance");

        Check(service.PublishHostEvent(nullptr, 4) == HostRegisterResult::RejectedIncomplete,
              "null_event_rejected");
        const char payload[4] = {0, 0, 0, 0};
        Check(service.PublishHostEvent(payload, 0) == HostRegisterResult::RejectedIncomplete,
              "empty_event_rejected");
        Check(service.HostEventCount() == 0, "rejected_event_not_counted");
        Check(service.PublishHostEvent(payload, 4) == HostRegisterResult::Accepted,
              "event_accepted");
        Check(service.PublishHostEvent(payload, 4) == HostRegisterResult::Accepted,
              "second_event_accepted");
        Check(service.HostEventCount() == 2, "event_count_tracks_publications");
        Check(service.LastHostEventSize() == 4, "event_size_recorded");

        const RuntimeHostApiV1* exported = IsaacModRuntime_GetHostApi(kHostApiVersion1);
        Check(exported != nullptr, "exported_entry_available");
        Check(exported == nullptr || exported->abiVersion == kHostApiVersion1,
              "exported_entry_reports_v1");
        Check(IsaacModRuntime_GetHostApi(kHostApiVersion1 + 1) == nullptr,
              "unsupported_version_returns_null");

        if (failures != 0) { std::printf("HOST_API_CHECKS_FAILED %d\n", failures); return 1; }
        std::printf("HOST_API_CHECKS_PASSED\n");
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


class HostApiContractTests(unittest.TestCase):
    def test_bridge_routes_the_legacy_symbol_through_the_host_api(self):
        bridge = (SOURCE / "saltynx_runtime_bridge.cpp").read_text(encoding="utf-8")
        self.assertIn("IsaacModRuntime_RegisterSaltyFileApi", bridge)
        layered = bridge[bridge.index("#if defined(EXL_LAYERED_RUNTIME)") :]
        self.assertIn("HostApiService().RegisterFilePort(table)", layered)
        self.assertIn("HostRegisterResult::Accepted", layered)
        # 非分层构建（探针/诊断）必须保持原路径，不引入新头文件依赖。
        self.assertIn("#if defined(EXL_LAYERED_RUNTIME)", bridge)

    def test_host_api_service_behaviour_on_host(self):
        compiler = host_compiler()
        if compiler is None:
            self.skipTest("需要宿主 C++ 编译器")
        with tempfile.TemporaryDirectory(prefix="runtime-host-api-") as temporary:
            directory = Path(temporary)
            source = directory / "host_api.cpp"
            binary = directory / "host_api"
            source.write_text(DRIVER, encoding="utf-8")
            compile_result = subprocess.run(
                [
                    compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    f"-DEXL_TEST_BUILD_ID={BUILD_ID}",
                    "-I", str(SRC),
                    str(source),
                    str(SRC / "infrastructure" / "saltynx" / "runtime_host_api_service.cpp"),
                    "-o", str(binary),
                ],
                capture_output=True, text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(run_result.returncode, 0, run_result.stdout + run_result.stderr)
            self.assertIn("HOST_API_CHECKS_PASSED", run_result.stdout)

    def test_host_api_rejects_a_zero_build_id_at_compile_time(self):
        service = (SRC / "infrastructure" / "saltynx" / "runtime_host_api_service.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn("static_assert", service)
        self.assertIn("EXL_TEST_BUILD_ID", service)
        self.assertNotIn("__cxa_guard", service)


if __name__ == "__main__":
    unittest.main()
