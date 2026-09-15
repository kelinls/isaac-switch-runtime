"""诊断会话的端到端行为（早期入队 → 附件端口 → 有界落盘 → 可判读）。

真机验收要回答两个问题：事件写出来了吗？读得回来吗？这一组测试用假 FilePort
把整条链路跑通，并断言"未注册端口时的早期事件不会丢"以及"降级会被写成事件"。
"""

import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "runtime" / "src"
DIAGNOSTICS = SRC / "diagnostics"

DRIVER = textwrap.dedent(
    r"""
    #include "diagnostics/diagnostic_session.hpp"
    #include "diagnostics/diagnostic_event_ids.hpp"
    #include "ports/file_port.hpp"

    #include <cstdio>
    #include <string>
    #include <vector>

    using namespace isaac::runtime;

    namespace {

    int failures = 0;
    void Check(bool condition, const char* what) {
        if (!condition) { std::printf("FAILED_CHECK %s\n", what); ++failures; }
    }

    struct MemoryFilePort final : IFilePort {
        std::vector<std::uint8_t> bytes{};
        Status openStatus{StatusCode::Ok};
        Status writeStatus{StatusCode::Ok};
        std::size_t shortWriteLimit{0};
        int writes{0};

        Status Open(std::string_view, FileMode, void** handle) noexcept override {
            if (!openStatus.ok()) { return openStatus; }
            *handle = this;
            return Status::Ok();
        }
        Status Read(void*, std::uint8_t*, std::size_t, std::size_t*) noexcept override {
            return Status{StatusCode::Unsupported};
        }
        Status Write(void*, const std::uint8_t* data, std::size_t count,
                     std::size_t* written) noexcept override {
            ++writes;
            if (!writeStatus.ok()) { *written = 0; return writeStatus; }
            const std::size_t allowed = (shortWriteLimit == 0 || count <= shortWriteLimit)
                                            ? count : shortWriteLimit;
            bytes.insert(bytes.end(), data, data + allowed);
            *written = allowed;
            return Status::Ok();
        }
        Status Close(void*) noexcept override { return Status::Ok(); }
    };

    void TestEarlyEventsSurviveUntilThePortArrives() {
        DiagnosticSession session{0x20260910530000ULL, DiagnosticOrigin::RuntimeModule};
        // 早期启动：没有文件端口，事件只能进内存环。
        static_cast<void>(session.Publish(session.MakeEvent(DiagnosticSubsystem::Bootstrap,
                                          kBootstrapEventModuleEntered, DiagnosticPhase::None)));
        static_cast<void>(session.Publish(session.MakeEvent(DiagnosticSubsystem::Bootstrap,
                                          kBootstrapEventPlatformInitialized, DiagnosticPhase::None)));
        static_cast<void>(session.Publish(session.MakeEvent(DiagnosticSubsystem::Bootstrap,
                                          kBootstrapEventWorkerStarted, DiagnosticPhase::None)));
        Check(session.Ring().Count() == 3, "early_events_queued");
        Check(session.Flush() == 0, "flush_without_port_is_noop");
        Check(session.Ring().Count() == 3, "early_events_not_lost");

        MemoryFilePort port{};
        Check(session.AttachFilePort(port, "sdmc:/events.bin").ok(), "attach_ok");
        Check(session.filePortAttached(), "port_attached");

        std::size_t total = 0;
        for (int guard = 0; guard < 64; ++guard) {
            const std::size_t written = session.Flush();
            if (written == 0) { break; }
            total += written;
        }
        // 早期三条 + FilePortRegistered 一条。
        Check(total >= 4, "queued_events_written");
        Check(session.Ring().Count() == 0, "queue_drained");
        // Flush 本身不得再产生待写事件（否则调用方的 drain 循环永不终止）。
        Check(session.Flush() == 0, "stable_frame_writes_nothing");
        Check(port.bytes.size() % BinaryEventCodec::kRecordSize == 0,
              "file_holds_whole_records");

        // 顺序：早期事件在前，端口注册在后。
        DiagnosticEvent first{};
        Check(BinaryEventCodec::Decode(port.bytes.data(), BinaryEventCodec::kRecordSize, &first).ok(),
              "first_record_decodes");
        Check(first.subsystem == DiagnosticSubsystem::Bootstrap, "bootstrap_first");
        Check(first.event == kBootstrapEventModuleEntered, "module_entered_first");
        Check(first.buildId == 0x20260910530000ULL, "build_id_preserved");
        Check(first.phase == DiagnosticPhase::None, "one_shot_phase_none");
        Check(!session.Health().degraded(), "healthy_session");
    }

    void TestAttachFailureIsRecordedAsAnEventNotAnException() {
        DiagnosticSession session{0x20260910530000ULL, DiagnosticOrigin::RuntimeModule};
        MemoryFilePort port{};
        port.openStatus = Status{StatusCode::NotFound};
        const Status attached = session.AttachFilePort(port, "sdmc:/missing/events.bin");
        Check(!attached.ok(), "attach_reports_failure");
        Check(session.Health().Reason() == DiagnosticHealthReason::OpenFailed,
              "attach_failure_health");
        // 失败本身必须以事件形式留下痕迹，且业务侧只是拿到一个失败状态。
        Check(session.Ring().Count() >= 1, "failure_queued_as_event");
        Check(session.Flush() == 0, "no_write_after_open_failure");
        Check(port.writes == 0, "no_write_calls");
    }

    void TestHealthEventIsWrittenAndDegradedIsVisible() {
        DiagnosticSession session{0x20260910530000ULL, DiagnosticOrigin::RuntimeModule};
        MemoryFilePort port{};
        Check(session.AttachFilePort(port, "sdmc:/events.bin").ok(), "attach_ok");
        static_cast<void>(session.PublishHealth());
        std::size_t total = 0;
        for (;;) {
            const std::size_t written = session.Flush();
            if (written == 0) { break; }
            total += written;
        }
        Check(total >= 2, "health_event_written");

        bool foundHealth = false;
        for (std::size_t offset = 0; offset + BinaryEventCodec::kRecordSize <= port.bytes.size();
             offset += BinaryEventCodec::kRecordSize) {
            DiagnosticEvent event{};
            if (!BinaryEventCodec::Decode(port.bytes.data() + offset,
                                          BinaryEventCodec::kRecordSize, &event).ok()) {
                continue;
            }
            if (event.subsystem == DiagnosticSubsystem::Diagnostics &&
                event.event == kDiagnosticsEventHealthChanged) {
                foundHealth = true;
                Check(event.result.code == 0, "clean_health_reason_none");
            }
        }
        Check(foundHealth, "health_changed_event_present");
    }

    void TestShortWriteDegradesTheSessionButKeepsTheRuntimeUsable() {
        DiagnosticSession session{0x20260910530000ULL, DiagnosticOrigin::RuntimeModule};
        MemoryFilePort port{};
        port.shortWriteLimit = 7;
        Check(session.AttachFilePort(port, "sdmc:/events.bin").ok(), "attach_ok");
        static_cast<void>(session.Publish(session.MakeEvent(DiagnosticSubsystem::Bootstrap,
                                                           kBootstrapEventRuntimeEntered,
                                                           DiagnosticPhase::None)));
        Check(session.Flush() == 0, "short_write_writes_nothing");
        Check(session.Health().Reason() == DiagnosticHealthReason::ShortWrite, "degraded_reason");
        // 降级之后仍然允许继续发布与尝试写入：不得抛异常、不得阻塞。
        Check(session.Publish(session.MakeEvent(DiagnosticSubsystem::Lua,
                                                kLuaEventStateCreated, DiagnosticPhase::None)),
              "publish_after_degradation_still_returns");
        Check(session.Flush() == 0, "no_write_after_degradation");
    }

    } // namespace

    int main() {
        TestEarlyEventsSurviveUntilThePortArrives();
        TestAttachFailureIsRecordedAsAnEventNotAnException();
        TestHealthEventIsWrittenAndDegradedIsVisible();
        TestShortWriteDegradesTheSessionButKeepsTheRuntimeUsable();
        if (failures != 0) { std::printf("SESSION_CHECKS_FAILED %d\n", failures); return 1; }
        std::printf("SESSION_CHECKS_PASSED\n");
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


class DiagnosticSessionTests(unittest.TestCase):
    def test_session_has_no_platform_dependency(self):
        """会话层同样不得直接碰平台：落盘只能通过 IFilePort。"""
        header = (DIAGNOSTICS / "diagnostic_session.hpp").read_text(encoding="utf-8")
        for forbidden in ("fopen", "fwrite", "svc", "nn::", "exlaunch", "lua_runtime"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, header)
        # 事件号是线格式：必须与解析器里的映射保持同一份编号。
        ids = (DIAGNOSTICS / "diagnostic_event_ids.hpp").read_text(encoding="utf-8")
        self.assertIn("kDiagnosticsEventHealthChanged = 2", ids)

    def test_session_end_to_end_on_host(self):
        compiler = host_compiler()
        if compiler is None:
            self.skipTest("需要宿主 C++ 编译器")
        with tempfile.TemporaryDirectory(prefix="runtime-diagnostics-session-") as temporary:
            directory = Path(temporary)
            source = directory / "session.cpp"
            binary = directory / "session"
            source.write_text(DRIVER, encoding="utf-8")
            compile_result = subprocess.run(
                [
                    compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    "-I", str(SRC), str(source),
                    str(DIAGNOSTICS / "binary_event_codec.cpp"),
                    str(DIAGNOSTICS / "diagnostic_event_bus.cpp"),
                    str(DIAGNOSTICS / "diagnostic_health.cpp"),
                    "-o", str(binary),
                ],
                capture_output=True, text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(run_result.returncode, 0, run_result.stdout + run_result.stderr)
            self.assertIn("SESSION_CHECKS_PASSED", run_result.stdout)
            self.assertNotIn("FAILED_CHECK", run_result.stdout)


if __name__ == "__main__":
    unittest.main()
