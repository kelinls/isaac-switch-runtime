"""`IFilePort` 适配器测试（宿主行为驱动）。

`SaltyNxFileAdapter` 把 SaltyNX 宿主插件注册进来的四个 C 函数包装成
`IFilePort`：应用层只拿到不透明句柄，永远看不到原生 FILE* 或函数指针。
"""

import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "runtime" / "src"

DRIVER = textwrap.dedent(
    r"""
    #include "infrastructure/saltynx/saltynx_file_adapter.hpp"

    #include <cstdio>
    #include <cstring>
    #include <string>

    using namespace isaac::runtime;

    namespace {

    int failures = 0;
    void Check(bool condition, const char* what) {
        if (!condition) { std::printf("FAILED_CHECK %s\n", what); ++failures; }
    }

    struct Calls {
        int opens{0};
        int reads{0};
        int writes{0};
        int closes{0};
        char path[64]{};
        char mode[8]{};
        std::size_t readResult{0};
        std::size_t writeResult{0};
        int closeResult{0};
        bool failOpen{false};
    };
    Calls g_calls{};

    void* FakeOpen(const char* path, const char* mode) {
        ++g_calls.opens;
        std::strncpy(g_calls.path, path, sizeof(g_calls.path) - 1);
        std::strncpy(g_calls.mode, mode, sizeof(g_calls.mode) - 1);
        return g_calls.failOpen ? nullptr : reinterpret_cast<void*>(0x55);
    }
    std::size_t FakeRead(void* target, std::size_t size, std::size_t count, void*) {
        ++g_calls.reads;
        if (target == nullptr || size == 0 || count == 0) { return 0; }
        return g_calls.readResult;
    }
    std::size_t FakeWrite(const void*, std::size_t, std::size_t count, void*) {
        ++g_calls.writes;
        return g_calls.writeResult != 0 ? g_calls.writeResult : count;
    }
    int FakeClose(void*) {
        ++g_calls.closes;
        return g_calls.closeResult;
    }

    struct FakeHost final : IRuntimeHostApi {
        HostFileApiV1 table{};
        bool published{false};
        HostRegisterResult RegisterFilePort(const HostFileApiV1& api) noexcept override {
            table = api;
            published = true;
            return HostRegisterResult::Accepted;
        }
        HostRegisterResult PublishHostEvent(const void*, std::uint32_t) noexcept override {
            return HostRegisterResult::Accepted;
        }
        bool PublishedFileApi(HostFileApiV1* out) const noexcept override {
            if (!published || out == nullptr) { return false; }
            *out = table;
            return true;
        }
        const RuntimeHostApiV1& Abi() const noexcept override {
            static RuntimeHostApiV1 abi{};
            return abi;
        }
    };

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
        FakeHost host{};
        SaltyNxFileAdapter adapter{host};
        void* handle = nullptr;

        Check(!adapter.Available(), "unavailable_before_registration");
        Check(adapter.Open("rom:/isaac_mods/x.bin", FileMode::Read, &handle).code() ==
                  StatusCode::InvalidState,
              "open_without_host_table_rejected");

        Check(host.RegisterFilePort(CompleteTable()) == HostRegisterResult::Accepted,
              "host_registration_accepted");
        Check(adapter.Available(), "available_after_registration");

        Status openStatus = adapter.Open("rom:/isaac_mods/x.bin", FileMode::Read, &handle);
        Check(openStatus.ok() && handle == reinterpret_cast<void*>(0x55), "read_open_ok");
        Check(std::strcmp(g_calls.mode, "rb") == 0, "read_mode_mapped");
        Check(std::strcmp(g_calls.path, "rom:/isaac_mods/x.bin") == 0, "path_forwarded");
        Check(adapter.Open("rom:/isaac_mods/x.bin", FileMode::Write, &handle).ok() &&
                  std::strcmp(g_calls.mode, "wb") == 0,
              "write_mode_mapped");

        g_calls.failOpen = true;
        Check(adapter.Open("rom:/isaac_mods/x.bin", FileMode::Read, &handle).code() ==
                  StatusCode::NotFound,
              "missing_file_maps_to_not_found");
        g_calls.failOpen = false;

        Check(adapter.Open("", FileMode::Read, &handle).code() == StatusCode::InvalidArgument,
              "empty_path_rejected");
        Check(adapter.Open("rom:/x.bin", FileMode::Read, nullptr).code() ==
                  StatusCode::InvalidArgument,
              "null_handle_out_rejected");
        const std::string longPath(200, 'a');
        Check(adapter.Open(longPath, FileMode::Read, &handle).code() == StatusCode::CapacityExceeded,
              "overlong_path_rejected");

        std::uint8_t buffer[16]{};
        std::size_t count = 0;
        g_calls.readResult = 7;
        Check(adapter.Read(handle, buffer, sizeof(buffer), &count).ok() && count == 7,
              "read_count_forwarded");
        Check(adapter.Read(nullptr, buffer, sizeof(buffer), &count).code() ==
                  StatusCode::InvalidArgument,
              "null_handle_read_rejected");
        Check(adapter.Read(handle, nullptr, sizeof(buffer), &count).code() ==
                  StatusCode::InvalidArgument,
              "null_target_read_rejected");
        Check(adapter.Read(handle, buffer, 0, &count).code() == StatusCode::InvalidArgument,
              "zero_capacity_read_rejected");

        g_calls.writeResult = 0;
        Check(adapter.Write(handle, buffer, sizeof(buffer), &count).ok() && count == sizeof(buffer),
              "full_write_ok");
        g_calls.writeResult = 3;
        Check(adapter.Write(handle, buffer, sizeof(buffer), &count).code() ==
                  StatusCode::IoFailure && count == 3,
              "short_write_is_a_failure");
        Check(adapter.Write(nullptr, buffer, sizeof(buffer), &count).code() ==
                  StatusCode::InvalidArgument,
              "null_handle_write_rejected");

        g_calls.closeResult = 0;
        Check(adapter.Close(handle).ok(), "close_ok");
        g_calls.closeResult = 1;
        Check(adapter.Close(handle).code() == StatusCode::IoFailure, "close_failure_reported");
        Check(adapter.Close(nullptr).code() == StatusCode::InvalidArgument, "null_handle_close_rejected");

        if (failures != 0) { std::printf("FILE_PORT_CHECKS_FAILED %d\n", failures); return 1; }
        std::printf("FILE_PORT_CHECKS_PASSED\n");
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


class SaltyNxFileAdapterTests(unittest.TestCase):
    def test_file_port_behaviour_on_host(self):
        compiler = host_compiler()
        if compiler is None:
            self.skipTest("需要宿主 C++ 编译器")
        with tempfile.TemporaryDirectory(prefix="runtime-file-port-") as temporary:
            directory = Path(temporary)
            source = directory / "file_port.cpp"
            binary = directory / "file_port"
            source.write_text(DRIVER, encoding="utf-8")
            compile_result = subprocess.run(
                [
                    compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    "-I", str(SRC),
                    str(source),
                    str(SRC / "infrastructure" / "saltynx" / "saltynx_file_adapter.cpp"),
                    "-o", str(binary),
                ],
                capture_output=True, text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(run_result.returncode, 0, run_result.stdout + run_result.stderr)
            self.assertIn("FILE_PORT_CHECKS_PASSED", run_result.stdout)


if __name__ == "__main__":
    unittest.main()
