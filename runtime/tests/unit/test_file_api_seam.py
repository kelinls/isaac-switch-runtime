"""文件读写接口必须保持低耦合：**单一注入点 + 消费者不得绕过它 + 缺提供方是一等状态**。

为什么钉住这几条：文件能力是**注入**进来的（今天是宿主插件导出四个裸函数，明天可能是运行时自己
实现）。只要"注入点唯一、消费者只认接口、没有提供方时显式报 `Unavailable`"成立，换提供方就只需
换一个实现，不必翻遍运行时。

接口本体是 `src/ports/file_port.hpp` 的 `IFilePort`（Open/Read/Write/Close，句柄不透明）；
`mod_persistence.hpp` 是唯一的生产消费者（Lua 侧那 6 个模组数据入口都经它）。

背景是一条已实测的事实：**运行时自己直接写文件从来没成功过**（`isaac-runtime-events.bin`
从未出现）。所以消费方必须依赖注入，不能自己另找一条路。

同时钉住新记录"内容与落盘分离"的约定：换掉文件实现时，记录内容可以原样复用。
"""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "source"
SRC = ROOT / "src"
PERSISTENCE = SOURCE / "mod_persistence.hpp"
PORT = SRC / "ports" / "file_port.hpp"
PROVIDER = SOURCE / "saltynx_runtime_bridge.cpp"
PLUGIN = SRC / "host_plugin" / "saltynx_host_plugin.cpp"

CONSUMERS = (
    "mod_persistence.hpp",
    "persistence_trace.cpp",
    "persistence_event_journal.cpp",
    "persistence_event_journal.hpp",
    "manager_update_hook_audit.cpp",
    "manager_update_hook_audit.hpp",
)

FORBIDDEN_IO = ("fopen(", "fwrite(", "fread(", "fclose(", "fsdev", "nx_fs")

# 正式入口 `ConfigureFilePort` 与兼容入口 `ConfigureFileApi` 都只允许在这一处调用。
INJECTION_CALL = re.compile(
    r"\b(?:ModPersistence|PersistenceTrace|ManagerUpdateHookAudit|PersistenceEventJournal)"
    r"::ConfigureFile(?:Port|Api)\("
)


def production_sources():
    """运行时生产源文件；排除构建/部署产物目录。"""
    for base in (SOURCE, SRC):
        for path in base.rglob("*"):
            if path.suffix not in (".cpp", ".hpp"):
                continue
            if any(part.startswith(("build-", "deploy-")) for part in path.parts):
                continue
            yield path


class FileApiSeamTests(unittest.TestCase):
    def test_the_port_is_the_only_interface(self):
        """接口面本身就是替换契约：四个操作 + 句柄不透明。"""
        text = PORT.read_text(encoding="utf-8")
        self.assertIn("class IFilePort", text)
        for member in ("Open", "Read", "Write", "Close"):
            self.assertRegex(text, rf"\b{member}\(", member)
        self.assertIn("enum class FileMode", text)
        self.assertIn("void** handle", text, "句柄必须不透明，上层不得解引用原生 FILE*")

    def test_mod_persistence_consumes_only_the_port(self):
        """消费者只认端口：不得再自己持一份裸函数表直接调用。"""
        text = PERSISTENCE.read_text(encoding="utf-8")
        self.assertIn('#include "ports/file_port.hpp"', text)
        for token in ("ConfigureFilePort", "IsFileApiReady", "g_filePort"):
            self.assertIn(token, text, token)
        self.assertNotIn("g_fileApi.open(", text, "消费点必须走端口，不能直接调裸函数")

    def test_the_injection_point_is_single(self):
        """只允许运行时注册入口那一处注入；散成多处就没法一次换实现。"""
        callers = set()
        for path in production_sources():
            if INJECTION_CALL.search(path.read_text(encoding="utf-8")):
                callers.add(path.name)
        self.assertEqual(
            callers, {PROVIDER.name},
            "文件能力的注入点必须唯一（只允许 saltynx_runtime_bridge.cpp 里的注册入口）",
        )

    def test_consumers_do_not_open_files_behind_the_interface(self):
        """消费方不得自己调 libc/libnx 的文件函数 —— 那会让替换实现漏掉它们。"""
        for name in CONSUMERS:
            path = SOURCE / name
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            for token in FORBIDDEN_IO:
                self.assertNotIn(
                    token, text, f"{name} 直接使用了 {token}，绕过了注入的文件 API"
                )

    def test_new_records_keep_content_and_transport_apart(self):
        """新增记录必须内容/落盘分离：`Build...` 不落盘，`Append...` 只负责送出去。"""
        text = PLUGIN.read_text(encoding="utf-8")
        self.assertIn("void BuildHandoverStartRecord", text)
        builder = text.split("void BuildHandoverStartRecord", 1)[1].split("\n}", 1)[0]
        self.assertNotIn("AppendRecord(", builder, "内容构造函数不得自己落盘")
        writer = text.split("void AppendHandoverStartRecord", 1)[1].split("\n}", 1)[0]
        self.assertIn("BuildHandoverStartRecord(", writer)
        self.assertIn("AppendRecord(", writer)


DRIVER = r"""
#include "mod_persistence.hpp"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string_view>
#include <vector>

// 假的提供方：内存里的一个"文件"。它证明**任何 IFilePort 实现都能被接上**，
// 不需要宿主插件在场 —— 这正是"以后换提供方"要的性质。
class MemoryPort final : public isaac::runtime::IFilePort {
public:
    struct Handle {
        bool writable = false;
        std::vector<std::uint8_t> bytes;
    };

    int writes = 0;

    [[nodiscard]] isaac::runtime::Status Open(std::string_view, isaac::runtime::FileMode mode,
                                              void** handle) noexcept override {
        file_.writable = mode == isaac::runtime::FileMode::Write;
        if (file_.writable) {
            file_.bytes.clear();
        } else if (!exists_) {
            return isaac::runtime::Status{isaac::runtime::StatusCode::NotFound};
        }
        *handle = &file_;
        return isaac::runtime::Status::Ok();
    }

    [[nodiscard]] isaac::runtime::Status Read(void* handle, std::uint8_t* target,
                                              std::size_t capacity,
                                              std::size_t* readCount) noexcept override {
        auto* file = static_cast<Handle*>(handle);
        const std::size_t count = file->bytes.size() < capacity ? file->bytes.size() : capacity;
        std::memcpy(target, file->bytes.data(), count);
        *readCount = count;
        return isaac::runtime::Status::Ok();
    }

    [[nodiscard]] isaac::runtime::Status Write(void* handle, const std::uint8_t* bytes,
                                               std::size_t count,
                                               std::size_t* writtenCount) noexcept override {
        auto* file = static_cast<Handle*>(handle);
        file->bytes.assign(bytes, bytes + count);
        exists_ = true;
        ++writes;
        *writtenCount = count;
        return isaac::runtime::Status::Ok();
    }

    [[nodiscard]] isaac::runtime::Status Close(void*) noexcept override {
        return isaac::runtime::Status::Ok();
    }

private:
    Handle file_{};
    bool exists_ = false;
};

// 明确"没有提供方"：所有操作返回 Unsupported（能力缺失，不是操作失败）。
class AbsentPort final : public isaac::runtime::IFilePort {
public:
    [[nodiscard]] isaac::runtime::Status Open(std::string_view, isaac::runtime::FileMode,
                                              void**) noexcept override {
        return isaac::runtime::Status{isaac::runtime::StatusCode::Unsupported};
    }
    [[nodiscard]] isaac::runtime::Status Read(void*, std::uint8_t*, std::size_t,
                                              std::size_t*) noexcept override {
        return isaac::runtime::Status{isaac::runtime::StatusCode::Unsupported};
    }
    [[nodiscard]] isaac::runtime::Status Write(void*, const std::uint8_t*, std::size_t,
                                               std::size_t*) noexcept override {
        return isaac::runtime::Status{isaac::runtime::StatusCode::Unsupported};
    }
    [[nodiscard]] isaac::runtime::Status Close(void*) noexcept override {
        return isaac::runtime::Status{isaac::runtime::StatusCode::Unsupported};
    }
};

static int failures = 0;
static void Check(bool condition, const char* what) {
    if (!condition) {
        std::printf("FAILED_CHECK %s\n", what);
        ++failures;
    }
}

int main() {
    constexpr std::uint64_t kNamespace = 0x1122334455667788ULL;
    const char* kValue = "hello-mod-data";
    const std::size_t kValueLength = std::strlen(kValue);
    const char* loaded = nullptr;
    std::size_t length = 0;
    bool has = false;

    // ① 没有提供方：能力缺失必须是一等状态。
    ModPersistence::ResetForTesting();
    Check(!ModPersistence::IsFileApiReady(), "no_provider_reports_not_ready");
    Check(ModPersistence::SaveModData(kNamespace, kValue, kValueLength) ==
              ModPersistence::Result::Unavailable, "save_without_provider_is_unavailable");
    Check(ModPersistence::LoadModData(kNamespace, &loaded, &length) ==
              ModPersistence::Result::Unavailable, "load_without_provider_is_unavailable");
    Check(ModPersistence::HasModData(kNamespace, &has) == ModPersistence::Result::Unavailable,
          "has_without_provider_is_unavailable");

    // ② 显式接一个"没有提供方"的端口：同样报能力缺失。
    AbsentPort absent;
    ModPersistence::ConfigureFilePort(&absent);
    Check(ModPersistence::IsFileApiReady(), "explicit_absent_port_is_registered");
    Check(ModPersistence::SaveModData(kNamespace, kValue, kValueLength) ==
              ModPersistence::Result::Unavailable, "absent_port_save_is_unavailable");
    ModPersistence::ResetForTesting();

    // ③ 接上内存提供方：保存/读取/存在性/删除全程走端口。
    MemoryPort memory;
    ModPersistence::ConfigureFilePort(&memory);
    Check(ModPersistence::IsFileApiReady(), "provider_reports_ready");
    Check(ModPersistence::LoadModData(kNamespace, &loaded, &length) ==
              ModPersistence::Result::Missing, "empty_store_reports_missing");
    Check(ModPersistence::SaveModData(kNamespace, kValue, kValueLength) ==
              ModPersistence::Result::Success, "save_succeeds_through_port");
    Check(memory.writes == 1, "port_received_the_write");
    has = false;
    Check(ModPersistence::HasModData(kNamespace, &has) == ModPersistence::Result::Success && has,
          "has_after_save");
    loaded = nullptr;
    length = 0;
    Check(ModPersistence::LoadModData(kNamespace, &loaded, &length) ==
              ModPersistence::Result::Success, "load_after_save");
    Check(loaded != nullptr && length == kValueLength &&
              std::memcmp(loaded, kValue, length) == 0, "round_trip_payload");
    Check(ModPersistence::RemoveModData(kNamespace) == ModPersistence::Result::Success,
          "remove_succeeds_through_port");
    has = true;
    Check(ModPersistence::HasModData(kNamespace, &has) == ModPersistence::Result::Success && !has,
          "has_after_remove");

    // ④ 兼容入口（宿主四个裸函数）也走同一条缝：全空 ⇒ 能力缺失。
    ModPersistence::ResetForTesting();
    ModPersistence::ConfigureFileApi(nullptr, nullptr, nullptr, nullptr);
    Check(ModPersistence::SaveModData(kNamespace, kValue, kValueLength) ==
              ModPersistence::Result::Unavailable, "null_raw_functions_are_unavailable");

    if (failures == 0) {
        std::printf("SEAM_DRIVER_OK\n");
    }
    return failures == 0 ? 0 : 1;
}
"""


class FileApiSeamBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("c++") is None:
            raise unittest.SkipTest("宿主机没有 c++")
        cls._temporary = tempfile.TemporaryDirectory(prefix="isaac-file-seam-")
        directory = Path(cls._temporary.name)
        driver = directory / "file_seam_driver.cpp"
        driver.write_text(DRIVER.lstrip(), encoding="utf-8")
        binary = directory / "file_seam_driver"
        build = subprocess.run(
            ["c++", "-std=c++23", "-Wall", "-Wextra", "-Werror", "-I", str(SRC), "-I", str(SOURCE),
             str(driver), "-o", str(binary)],
            text=True, capture_output=True,
        )
        if build.returncode != 0:
            raise AssertionError(f"文件缝驱动在宿主机上编译失败：\n{build.stdout}{build.stderr}")
        cls._run = subprocess.run([str(binary)], text=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "_temporary", None) is not None:
            cls._temporary.cleanup()

    def test_seam_behavior_on_the_host(self):
        self.assertEqual(self._run.returncode, 0, self._run.stdout + self._run.stderr)
        self.assertEqual(self._run.stdout.strip(), "SEAM_DRIVER_OK")


if __name__ == "__main__":
    unittest.main()
