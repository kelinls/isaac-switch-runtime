"""Composition Root 与 Bootstrap 的集成测试。

Task 3 的目标是让默认入口在分层构建下先经过 `RuntimeBootstrap`，同时不改动
既有的模块扫描、Hook、Lua 和持久化控制流。这里验证三件事：启动顺序只记录
入口边界、重复启动被拒绝、以及分层构建的接线不会污染旧构建。
"""

import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"
SRC = RUNTIME / "src"
MAKEFILE = RUNTIME / "Makefile"
COMMON_MK = RUNTIME / "misc" / "mk" / "common.mk"

KERNEL_DRIVER = textwrap.dedent(
    r"""
    #include "bootstrap/runtime_bootstrap.hpp"
    #include "composition/runtime_kernel.hpp"
    #include "ports/event_sink.hpp"
    #include "ports/module_scanner_port.hpp"
    #include "ports/thread_port.hpp"

    #include <cstdio>
    #include <cstring>
    #include <vector>

    using namespace isaac::runtime;

    namespace {

    int failures = 0;

    void Check(bool condition, const char* what) {
        if (!condition) {
            std::printf("FAILED_CHECK %s\n", what);
            ++failures;
        }
    }

    struct CountingSink final : IEventSink {
        std::vector<std::uint32_t> ids;
        void Publish(const EventHeader& header, const std::uint8_t*, std::size_t) noexcept override {
            ids.push_back(header.id);
        }
    };

    struct FakeThreads final : IThreadPort {
        Status StartWorker(ThreadEntry, void*) noexcept override { return Status::Ok(); }
        std::uint64_t CurrentThreadId() const noexcept override { return 1; }
        void SleepMilliseconds(std::uint32_t) noexcept override {}
    };

    struct FakeScanner final : IModuleScannerPort {
        Status WaitForTarget(const TargetModuleSpec&, ModuleInfo*) noexcept override {
            return Status{StatusCode::NotFound};
        }
    };

    } // namespace

    int main(int argc, char** argv) {
        const bool withSink = argc > 1 && std::strcmp(argv[1], "with-sink") == 0;

        CountingSink sink;
        FakeThreads threads;
        FakeScanner scanner;
        RuntimeDependencies dependencies{};
        dependencies.threads = &threads;
        dependencies.scanner = &scanner;
        dependencies.events = withSink ? &sink : nullptr;
        dependencies.buildId = 0x1122334455667788ULL;

        RuntimeKernel& kernel = RuntimeKernel::Instance();
        Check(!kernel.initialized(), "kernel_starts_uninitialised");

        const Status started = RuntimeBootstrap::Start(dependencies);
        Check(started.ok(), "bootstrap_start_ok");
        Check(kernel.initialized(), "kernel_initialised");
        Check(kernel.context().state == RuntimeState::ModuleEntered, "state_is_module_entered");
        Check(kernel.context().buildId == dependencies.buildId, "build_id_propagated");
        Check(kernel.publishedEventCount() == 1, "exactly_one_event_published");
        Check(kernel.dependencies().threads == &threads, "dependency_pointers_kept");
        if (withSink) {
            Check(sink.ids.size() == 1 && sink.ids[0] == kEventModuleEntered,
                  "sink_received_entry_event");
        } else {
            Check(sink.ids.empty(), "sink_unused_when_not_injected");
        }

        const Status second = RuntimeBootstrap::Start(dependencies);
        Check(second.code() == StatusCode::InvalidState, "second_start_rejected");
        Check(kernel.context().state == RuntimeState::ModuleEntered,
              "state_unchanged_after_rejection");
        Check(kernel.publishedEventCount() == 1, "no_extra_event_after_rejection");
        Check(kernel.context().usable(), "state_is_still_usable");

        if (failures != 0) {
            std::printf("KERNEL_CHECKS_FAILED %d\n", failures);
            return 1;
        }
        std::printf("KERNEL_CHECKS_PASSED\n");
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


class BootstrapKernelTests(unittest.TestCase):
    def test_layered_build_wiring_is_the_default_runtime_path(self):
        makefile = MAKEFILE.read_text(encoding="utf-8")
        self.assertIn("LAYERED_RUNTIME ?= 1", makefile)
        self.assertIn("RUNTIME_EXTRA_SOURCE_ROOTS ?= src", makefile)
        self.assertRegex(
            makefile,
            r"(?s)runtime_module:.*?LAYERED_RUNTIME=1 RUNTIME_EXTRA_SOURCE_ROOTS=src",
        )
        self.assertIn("EXL_LAYERED_RUNTIME_CFLAGS := -DEXL_LAYERED_RUNTIME=1", makefile)

        # 旧入口只在分层构建下引用 Bootstrap；诊断与探针构建保持原样。
        entry = (RUNTIME / "source" / "runtime_entry.cpp").read_text(encoding="utf-8")
        self.assertIn("#if defined(EXL_LAYERED_RUNTIME)", entry)
        self.assertIn("isaac::runtime::RuntimeBootstrap::Start()", entry)
        call_index = entry.index("isaac::runtime::RuntimeBootstrap::Start()")
        prefix = entry[:call_index]
        self.assertGreater(
            prefix.rindex("#if defined(EXL_LAYERED_RUNTIME)"),
            prefix.rindex("#endif"),
            "Bootstrap 调用必须位于分层守卫之内",
        )

        common = COMMON_MK.read_text(encoding="utf-8")
        self.assertIn("RUNTIME_EXTRA_SOURCE_ROOTS", common)
        self.assertIn("EXTRA_SOURCE_ROOTS", common)

    def test_source_roots_do_not_collide_on_object_names(self):
        # 对象文件名来自 basename，两个源码根出现同名文件会在链接前互相覆盖。
        # 旧源码根内部既有的同名文件不属于本次迁移范围，这里只检查跨源码根。
        per_root: dict[Path, dict[str, str]] = {}
        for root in (RUNTIME / "source", SRC):
            names: dict[str, str] = {}
            for path in sorted(root.rglob("*")):
                if path.suffix not in (".c", ".cpp", ".s"):
                    continue
                names.setdefault(path.name, str(path.relative_to(ROOT)))
            per_root[root] = names

        collisions = []
        legacy = per_root[RUNTIME / "source"]
        for name, path in sorted(per_root[SRC].items()):
            if name in legacy:
                collisions.append(f"{name}: {legacy[name]} vs {path}")
        self.assertEqual(collisions, [], "不同源码根之间不得出现同名源文件")

    def test_bootstrap_records_entry_and_rejects_second_start(self):
        compiler = host_compiler()
        if compiler is None:
            self.skipTest("需要宿主 C++ 编译器")
        with tempfile.TemporaryDirectory(prefix="runtime-kernel-host-") as temporary:
            directory = Path(temporary)
            source = directory / "kernel_driver.cpp"
            binary = directory / "kernel_driver"
            source.write_text(KERNEL_DRIVER, encoding="utf-8")
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
                    str(SRC / "composition" / "runtime_kernel.cpp"),
                    str(SRC / "bootstrap" / "runtime_bootstrap.cpp"),
                    str(SRC / "domain" / "runtime" / "runtime_state_machine.cpp"),
                    "-o",
                    str(binary),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            for mode in ("with-sink", "no-sink"):
                with self.subTest(mode=mode):
                    run_result = subprocess.run(
                        [str(binary), mode], capture_output=True, text=True
                    )
                    self.assertEqual(
                        run_result.returncode, 0, run_result.stdout + run_result.stderr
                    )
                    self.assertIn("KERNEL_CHECKS_PASSED", run_result.stdout)

    def test_reporting_event_ids_are_stable(self):
        header = (SRC / "bootstrap" / "runtime_bootstrap.hpp").read_text(encoding="utf-8")
        match = re.search(r"kEventModuleEntered = (0x[0-9A-Fa-f]+)", header)
        self.assertIsNotNone(match, "入口事件 ID 必须显式定义")
        self.assertEqual(int(match.group(1), 16), 0x01000001)


if __name__ == "__main__":
    unittest.main()
