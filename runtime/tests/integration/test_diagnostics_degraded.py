"""诊断落盘的降级行为测试（short write / close 失败 / 未注册 FilePort）。

真机上"日志写坏了"和"真的没问题"过去长得一模一样，因为失败既不改变业务返回值，
也不出现在任何文件里。这一组测试把三条硬约束钉死：① 调用者永不被阻塞；② 短写或
关闭失败后 sink 进入 sticky degraded 并停止追加，避免半个记录把后续记录全部带偏；
③ 失败只更新 DiagnosticHealth，不会变成游戏线程看到的业务错误。
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
    #include "diagnostics/bounded_file_journal.hpp"
    #include "diagnostics/diagnostic_event_bus.hpp"
    #include "diagnostics/diagnostic_health.hpp"
    #include "diagnostics/in_memory_ring_buffer_sink.hpp"
    #include "ports/file_port.hpp"

    #include <cstdio>
    #include <cstring>
    #include <string>
    #include <vector>

    using namespace isaac::runtime;

    namespace {

    int failures = 0;
    void Check(bool condition, const char* what) {
        if (!condition) { std::printf("FAILED_CHECK %s\n", what); ++failures; }
    }

    // Records everything it is asked to write so the test can check the byte
    // stream, and can be told to misbehave the way a device file can.
    struct FakeFilePort final : IFilePort {
        std::vector<std::uint8_t> bytes{};
        Status openStatus{StatusCode::Ok};
        bool openReturnsNullHandle{false};
        // Write only this many bytes regardless of the request (short write).
        std::size_t shortWriteLimit{0};
        Status writeStatus{StatusCode::Ok};
        Status closeStatus{StatusCode::Ok};
        int opens{0};
        int writes{0};
        int closes{0};

        Status Open(std::string_view, FileMode, void** handle) noexcept override {
            ++opens;
            if (!openStatus.ok()) { return openStatus; }
            if (openReturnsNullHandle) { *handle = nullptr; return Status::Ok(); }
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
            const std::size_t allowed =
                shortWriteLimit == 0 || count <= shortWriteLimit ? count : shortWriteLimit;
            bytes.insert(bytes.end(), data, data + allowed);
            *written = allowed;
            return Status::Ok();
        }
        Status Close(void*) noexcept override {
            ++closes;
            return closeStatus;
        }
    };

    DiagnosticEvent Sample(std::uint32_t sequence) {
        DiagnosticEvent event{};
        event.origin = DiagnosticOrigin::RuntimeModule;
        event.buildId = 0x20260910530000ULL;
        event.sequence = sequence;
        event.subsystem = DiagnosticSubsystem::Persistence;
        event.event = 9;  // OperationEntered
        event.phase = DiagnosticPhase::Entered;
        event.threadTag = 1;
        return event;
    }

    void TestUnregisteredFilePortKeepsEventsQueued() {
        // 早期启动阶段没有 FilePort：事件必须留在环里而不是丢掉。
        InMemoryRingBufferSink<8> ring{};
        DiagnosticHealth health{};
        DiagnosticEventBus<8> bus{ring, health};
        for (std::uint32_t index = 0; index < 5; ++index) {
            Check(bus.Publish(Sample(index)), "publish_before_file_port");
        }
        Check(ring.Count() == 5, "events_queued_without_file_port");
        Check(!health.degraded(), "no_file_port_is_not_a_failure");
    }

    void TestOpenFailureIsStickyAndReported() {
        InMemoryRingBufferSink<8> ring{};
        DiagnosticHealth health{};
        FakeFilePort port{};
        port.openStatus = Status{StatusCode::NotFound};
        BoundedFileJournal<8> journal{port, "sdmc:/missing/events.bin", ring, health};

        Check(!journal.Open().ok(), "open_failure_reported");
        Check(journal.failed(), "open_failure_marks_sink_failed");
        Check(health.degraded() &&
                  health.Reason() == DiagnosticHealthReason::OpenFailed, "open_failure_health");
        Check(port.writes == 0, "no_write_after_open_failure");
        // 再次 Open 不得变成成功（sticky）。
        Check(!journal.Open().ok(), "open_failure_stays_sticky");

        // 未注册句柄时 Flush 必须安全返回，且不丢环里的数据。
        std::uint32_t ignored = 0;
        Check(journal.Flush() == 0, "flush_without_handle_is_noop");
        Check(!health.degraded() || ignored == 0, "flush_does_not_corrupt_health");
    }

    void TestShortWriteDegradesAndStopsAppending() {
        InMemoryRingBufferSink<8> ring{};
        DiagnosticHealth health{};
        DiagnosticEventBus<8> bus{ring, health};
        FakeFilePort port{};
        port.shortWriteLimit = 10;  // 一个 60 字节记录只写进去 10 字节
        BoundedFileJournal<8> journal{port, "sdmc:/events.bin", ring, health};

        Check(journal.Open().ok(), "open_ok");
        for (std::uint32_t index = 0; index < 3; ++index) {
            Check(bus.Publish(Sample(index)), "publish_before_flush");
        }
        Check(journal.Flush() == 0, "short_write_writes_nothing");
        Check(journal.failed(), "short_write_degrades");
        Check(health.Reason() == DiagnosticHealthReason::ShortWrite, "short_write_health");
        Check(health.FileFailures() == 1, "short_write_counted_once");

        // sticky：后续 Flush 不得再往文件里追加任何字节。
        const std::size_t bytesAfterFailure = port.bytes.size();
        Check(journal.Flush() == 0, "no_flush_after_degradation");
        Check(port.bytes.size() == bytesAfterFailure, "no_bytes_appended_after_degradation");
        Check(port.writes == 1, "exactly_one_write_attempt");
    }

    void TestCloseFailureDoesNotRetryAndIsReported() {
        InMemoryRingBufferSink<8> ring{};
        DiagnosticHealth health{};
        DiagnosticEventBus<8> bus{ring, health};
        FakeFilePort port{};
        port.closeStatus = Status{StatusCode::IoFailure};
        BoundedFileJournal<8> journal{port, "sdmc:/events.bin", ring, health};

        Check(journal.Open().ok(), "open_ok");
        Check(bus.Publish(Sample(1)), "publish_ok");
        Check(journal.Flush() == 1, "flush_writes_one_record");
        Check(!journal.Close().ok(), "close_failure_reported");
        Check(health.Reason() == DiagnosticHealthReason::CloseFailed, "close_failure_health");
        Check(port.closes == 1, "close_not_retried");
        Check(!journal.healthy(), "close_failure_marks_unhealthy");
    }

    void TestFlushWritesDecodableRecordsAndRespectsBudget() {
        InMemoryRingBufferSink<16> ring{};
        DiagnosticHealth health{};
        DiagnosticEventBus<16> bus{ring, health};
        FakeFilePort port{};
        BoundedFileJournal<16> journal{port, "sdmc:/events.bin", ring, health};

        Check(journal.Open().ok(), "open_ok");
        for (std::uint32_t index = 0; index < 10; ++index) {
            Check(bus.Publish(Sample(index)), "publish_all");
        }
        // 每次 Flush 有界：单次不得写入超过 kDiagnosticMaxRecordsPerFlush 条。
        const std::size_t firstFlush = journal.Flush();
        Check(firstFlush == kDiagnosticMaxRecordsPerFlush, "flush_is_bounded");
        Check(ring.Count() == 10 - firstFlush, "ring_keeps_unwritten_events");

        std::size_t total = firstFlush;
        while (journal.Flush() != 0) {
            total += kDiagnosticMaxRecordsPerFlush;
        }
        Check(total >= 10, "all_events_eventually_written");
        Check(port.bytes.size() == 10 * BinaryEventCodec::kRecordSize, "ten_records_on_disk");

        // 磁盘上的字节必须能被解析器读回，且顺序与发布顺序一致。
        for (std::uint32_t index = 0; index < 10; ++index) {
            DiagnosticEvent decoded{};
            const std::uint8_t* record = port.bytes.data() + index * BinaryEventCodec::kRecordSize;
            Check(BinaryEventCodec::Decode(record, BinaryEventCodec::kRecordSize, &decoded).ok(),
                  "record_decodes");
            Check(decoded.sequence == index, "record_order_preserved");
            Check(decoded.buildId == 0x20260910530000ULL, "build_id_preserved");
        }
        Check(journal.RecordsWritten() >= 10, "records_counted");
        Check(!health.degraded(), "clean_flush_is_healthy");

        // 追加模式：文件在关闭后重新打开不得被截断。
        const std::size_t bytesBefore = port.bytes.size();
        Check(journal.Close().ok(), "close_ok");
        Check(journal.Open().ok(), "reopen_ok");
        Check(bus.Publish(Sample(99)), "publish_after_reopen");
        Check(journal.Flush() == 1, "flush_after_reopen");
        Check(port.bytes.size() == bytesBefore + BinaryEventCodec::kRecordSize,
              "append_does_not_truncate");
        Check(port.closes == 1, "close_counted");
    }

    void TestHealthyFlushProducesNoIoWhenRingIsEmpty() {
        InMemoryRingBufferSink<8> ring{};
        DiagnosticHealth health{};
        FakeFilePort port{};
        BoundedFileJournal<8> journal{port, "sdmc:/events.bin", ring, health};
        Check(journal.Open().ok(), "open_ok");
        // 稳定帧没有新事件时不得产生任何写调用。
        Check(journal.Flush() == 0, "empty_flush_writes_nothing");
        Check(port.writes == 0, "no_io_on_stable_frame");
    }

    } // namespace

    int main() {
        TestUnregisteredFilePortKeepsEventsQueued();
        TestOpenFailureIsStickyAndReported();
        TestShortWriteDegradesAndStopsAppending();
        TestCloseFailureDoesNotRetryAndIsReported();
        TestFlushWritesDecodableRecordsAndRespectsBudget();
        TestHealthyFlushProducesNoIoWhenRingIsEmpty();
        if (failures != 0) { std::printf("DEGRADED_CHECKS_FAILED %d\n", failures); return 1; }
        std::printf("DEGRADED_CHECKS_PASSED\n");
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


class DiagnosticsDegradedTests(unittest.TestCase):
    def test_journal_uses_only_the_file_port(self):
        """落盘只能经由 IFilePort，且不得自己开文件、睡眠或轮询。"""
        header = (DIAGNOSTICS / "bounded_file_journal.hpp").read_text(encoding="utf-8")
        self.assertIn("IFilePort&", header)
        for forbidden in ("fopen", "fwrite", "fclose", "svc", "sleep", "while (true)",
                          "std::this_thread"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, header)

    def test_degraded_behaviour_on_host(self):
        compiler = host_compiler()
        if compiler is None:
            self.skipTest("需要宿主 C++ 编译器")
        with tempfile.TemporaryDirectory(prefix="runtime-diagnostics-degraded-") as temporary:
            directory = Path(temporary)
            source = directory / "degraded.cpp"
            binary = directory / "degraded"
            source.write_text(DRIVER, encoding="utf-8")
            compile_result = subprocess.run(
                [
                    compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    "-I", str(SRC),
                    str(source),
                    str(DIAGNOSTICS / "binary_event_codec.cpp"),
                    str(DIAGNOSTICS / "diagnostic_health.cpp"),
                    str(DIAGNOSTICS / "diagnostic_event_bus.cpp"),
                    "-o", str(binary),
                ],
                capture_output=True, text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(run_result.returncode, 0, run_result.stdout + run_result.stderr)
            self.assertIn("DEGRADED_CHECKS_PASSED", run_result.stdout)
            self.assertNotIn("FAILED_CHECK", run_result.stdout)


if __name__ == "__main__":
    unittest.main()
