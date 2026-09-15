"""诊断事件核心的宿主测试（编解码 + 环形缓冲 + 事件配对）。

诊断管线的硬约束来自设计 §11/§14：日志是旁路观察者，**不得阻塞调用者、不得改变
业务返回值**；早期启动阶段没有可靠文件 API，因此事件先在固定容量内存里排队。这里
只验证与平台无关的那一半——字节格式、容量与并发、配对语义——因为这几条一旦写错，
真机上只会表现为"文件读不出来"或"少了事件"，无法离线定位。
"""

import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "runtime" / "src"
DIAGNOSTICS = SRC / "diagnostics"

_LINE_COMMENT = re.compile(r"//[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def without_comments(text: str) -> str:
    """去掉 `//` 与 `/* */` 注释后的源码。

    依赖类判据只能看代码：注释里说明"本文件经谁进设备构建"是正常写法，
    不该被当成依赖（`in_memory_ring_buffer_sink.hpp` 就因此在旧判据下误报过）。
    """
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", text))

DRIVER = textwrap.dedent(
    r"""
    #include "diagnostics/binary_event_codec.hpp"
    #include "diagnostics/diagnostic_event.hpp"
    #include "diagnostics/diagnostic_event_bus.hpp"
    #include "diagnostics/diagnostic_health.hpp"
    #include "diagnostics/in_memory_ring_buffer_sink.hpp"

    #include <cstdio>
    #include <cstring>
    #include <thread>
    #include <vector>

    using namespace isaac::runtime;

    namespace {

    int failures = 0;
    void Check(bool condition, const char* what) {
        if (!condition) { std::printf("FAILED_CHECK %s\n", what); ++failures; }
    }

    DiagnosticEvent Sample(std::uint32_t sequence = 7) {
        DiagnosticEvent event{};
        event.origin = DiagnosticOrigin::RuntimeModule;
        event.buildId = 0x20260910530000ULL;
        event.sequence = sequence;
        event.subsystem = DiagnosticSubsystem::Manifest;
        event.event = 4;
        event.severity = DiagnosticSeverity::Warning;
        event.phase = DiagnosticPhase::Returned;
        event.flags = 0x2A;
        event.threadTag = 7;
        event.result.domain = 3;
        event.result.code = 9;
        event.detail = 0x1122334455667788ULL;
        return event;
    }

    void TestCodecRoundTrip() {
        const DiagnosticEvent original = Sample();
        std::uint8_t record[BinaryEventCodec::kRecordSize]{};
        Check(BinaryEventCodec::Encode(original, record, sizeof(record)).ok(), "encode_ok");
        Check(std::memcmp(record, "ISAACDV1", 8) == 0, "magic_written");
        Check(BinaryEventCodec::Checksum(record, sizeof(record)) != 0, "checksum_nonzero");

        DiagnosticEvent decoded{};
        Check(BinaryEventCodec::Decode(record, sizeof(record), &decoded).ok(), "decode_ok");
        Check(decoded.origin == original.origin, "origin_roundtrip");
        Check(decoded.buildId == original.buildId, "build_id_roundtrip");
        Check(decoded.sequence == original.sequence, "sequence_roundtrip");
        Check(decoded.subsystem == original.subsystem, "subsystem_roundtrip");
        Check(decoded.event == original.event, "event_roundtrip");
        Check(decoded.severity == original.severity, "severity_roundtrip");
        Check(decoded.phase == original.phase, "phase_roundtrip");
        Check(decoded.flags == original.flags, "flags_roundtrip");
        Check(decoded.threadTag == original.threadTag, "thread_tag_roundtrip");
        Check(decoded.result.domain == original.result.domain, "result_domain_roundtrip");
        Check(decoded.result.code == original.result.code, "result_code_roundtrip");
        Check(decoded.detail == original.detail, "detail_roundtrip");
    }

    void TestCodecRejections() {
        const DiagnosticEvent original = Sample();
        std::uint8_t record[BinaryEventCodec::kRecordSize]{};
        Check(BinaryEventCodec::Encode(original, record, sizeof(record)).ok(), "encode_for_rejects");

        // 缓冲区太小必须报错，不能写半个记录。
        std::uint8_t small[BinaryEventCodec::kRecordSize - 1]{};
        Check(BinaryEventCodec::Encode(original, small, sizeof(small)).code() ==
                  StatusCode::CapacityExceeded, "short_buffer_rejected");

        DiagnosticEvent decoded{};
        Check(BinaryEventCodec::Decode(record, sizeof(record) - 1, &decoded).code() ==
                  StatusCode::InvalidArgument, "short_record_rejected");

        std::uint8_t wrongMagic[BinaryEventCodec::kRecordSize]{};
        std::memcpy(wrongMagic, record, sizeof(record));
        wrongMagic[0] = 'X';
        Check(BinaryEventCodec::Decode(wrongMagic, sizeof(wrongMagic), &decoded).code() ==
                  StatusCode::Corrupted, "wrong_magic_rejected");

        std::uint8_t wrongSchema[BinaryEventCodec::kRecordSize]{};
        std::memcpy(wrongSchema, record, sizeof(record));
        wrongSchema[8] = 99;
        Check(BinaryEventCodec::Decode(wrongSchema, sizeof(wrongSchema), &decoded).code() ==
                  StatusCode::Unsupported, "wrong_schema_rejected");

        // 记录内容被改动（校验和不匹配）必须被拒，不能静默读出坏数据。
        std::uint8_t corrupted[BinaryEventCodec::kRecordSize]{};
        std::memcpy(corrupted, record, sizeof(record));
        corrupted[24] ^= 0xFF;
        Check(BinaryEventCodec::Decode(corrupted, sizeof(corrupted), &decoded).code() ==
                  StatusCode::Corrupted, "checksum_mismatch_rejected");

        // 半个记录（例如断电）也必须被拒。
        std::uint8_t truncated[BinaryEventCodec::kRecordSize]{};
        std::memcpy(truncated, record, sizeof(record) / 2);
        Check(BinaryEventCodec::Decode(truncated, sizeof(truncated), &decoded).code() ==
                  StatusCode::Corrupted, "truncated_record_rejected");
    }

    void TestPairing() {
        DiagnosticEvent entered = Sample();
        entered.phase = DiagnosticPhase::Entered;
        DiagnosticEvent returned = Sample();
        returned.phase = DiagnosticPhase::Returned;
        Check(IsPairedWith(entered, returned), "entered_returned_pair");

        DiagnosticEvent failed = Sample();
        failed.phase = DiagnosticPhase::Failed;
        Check(IsPairedWith(entered, failed), "entered_failed_pair");

        DiagnosticEvent otherThread = returned;
        otherThread.threadTag = 8;
        Check(!IsPairedWith(entered, otherThread), "different_thread_does_not_pair");

        DiagnosticEvent otherEvent = returned;
        otherEvent.event = 5;
        Check(!IsPairedWith(entered, otherEvent), "different_event_does_not_pair");

        DiagnosticEvent otherBuild = returned;
        otherBuild.buildId = 1;
        Check(!IsPairedWith(entered, otherBuild), "different_build_does_not_pair");

        DiagnosticEvent otherOrigin = returned;
        otherOrigin.origin = DiagnosticOrigin::HostPlugin;
        Check(!IsPairedWith(entered, otherOrigin), "different_origin_does_not_pair");

        // 两个 returned 不能配对，否则解析器会把两条独立事件当成一对。
        Check(!IsPairedWith(returned, returned), "returned_does_not_pair_with_returned");
    }

    void TestRingBufferCapacityAndOverflow() {
        InMemoryRingBufferSink<4> ring{};
        Check(ring.Capacity == 4, "capacity_reported");
        for (std::uint32_t index = 0; index < 4; ++index) {
            DiagnosticEvent event = Sample(index);
            Check(ring.TryPublish(event), "publish_within_capacity");
        }
        Check(ring.Count() == 4, "count_at_capacity");
        Check(ring.DroppedCount() == 0, "no_drop_at_capacity");

        DiagnosticEvent overflow = Sample(4);
        Check(!ring.TryPublish(overflow), "publish_beyond_capacity_fails");
        Check(ring.DroppedCount() == 1, "drop_counted");

        // 溢出只影响诊断：业务返回值不受影响（这里以 TryPublish 的布尔结果表达）。
        DiagnosticEvent first{};
        Check(ring.At(0, &first), "oldest_entry_readable");
        Check(first.sequence == 0, "oldest_entry_is_first_published");

        DiagnosticEvent last{};
        Check(ring.At(3, &last) && last.sequence == 3, "newest_entry_readable");
        Check(!ring.At(4, &last), "out_of_range_read_fails");

        ring.Reset();
        Check(ring.Count() == 0, "reset_clears_count");
        Check(!ring.At(0, &last), "reset_clears_entries");
    }

    void TestRingBufferSingleProducerOrdering() {
        InMemoryRingBufferSink<8> ring{};
        std::vector<std::uint32_t> accepted{};
        for (std::uint32_t index = 0; index < 8; ++index) {
            DiagnosticEvent event = Sample(index);
            if (ring.TryPublish(event)) { accepted.push_back(index); }
        }
        Check(accepted.size() == 8, "all_accepted_in_order");
        bool ordered = true;
        for (std::size_t index = 0; index < accepted.size(); ++index) {
            DiagnosticEvent event{};
            static_cast<void>(ring.At(index, &event));
            if (event.sequence != accepted[index]) { ordered = false; }
        }
        Check(ordered, "ring_preserves_publish_order");
    }

    void TestRingBufferConcurrentProducers() {
        // 多生产者是有界 try-lock 协议：任一生产者都不得在满时自旋或丢失计数。
        InMemoryRingBufferSink<8> ring{};
        constexpr int kThreadCount = 4;
        constexpr int kPerThread = 50;
        std::vector<std::thread> producers{};
        std::atomic<int> accepted{0};
        for (int threadIndex = 0; threadIndex < kThreadCount; ++threadIndex) {
            producers.emplace_back([&ring, &accepted, threadIndex] {
                for (int index = 0; index < kPerThread; ++index) {
                    DiagnosticEvent event = Sample(0);
                    event.threadTag = static_cast<std::uint32_t>(threadIndex);
                    event.sequence = static_cast<std::uint32_t>(index);
                    if (ring.TryPublish(event)) { accepted.fetch_add(1); }
                }
            });
        }
        for (std::thread& producer : producers) { producer.join(); }
        Check(accepted.load() == static_cast<int>(ring.Count()), "accepted_matches_count");
        Check(accepted.load() + static_cast<int>(ring.DroppedCount()) == kThreadCount * kPerThread,
              "every_publish_accounted_for");
        Check(accepted.load() <= static_cast<int>(ring.Capacity), "never_exceeds_capacity");
    }

    void TestHealthTracksDegradationWithoutChangingBusinessResult() {
        DiagnosticHealth health{};
        Check(!health.degraded(), "fresh_health_not_degraded");
        Check(health.Reason() == DiagnosticHealthReason::None, "fresh_reason_none");

        health.RecordDrop();
        Check(health.degraded(), "drop_degrades");
        Check(health.Reason() == DiagnosticHealthReason::RingOverflow, "drop_reason");
        Check(health.DroppedEvents() == 1, "drop_counted");

        // 健康状态是粘性的：一次写失败之后不允许再被"看起来正常"的事件清掉。
        health.RecordFileFailure(DiagnosticHealthReason::ShortWrite);
        Check(health.Reason() == DiagnosticHealthReason::ShortWrite, "first_failure_kept");
        Check(health.FileFailures() == 1, "file_failure_counted");
        health.RecordFileFailure(DiagnosticHealthReason::CloseFailed);
        Check(health.FileFailures() == 2, "second_failure_counted");
        Check(health.Reason() == DiagnosticHealthReason::ShortWrite, "reason_stays_sticky");

        health.Reset();
        Check(!health.degraded() && health.FileFailures() == 0, "reset_clears_health");
    }

    void TestBusReportsHealthAndKeepsPublishing() {
        InMemoryRingBufferSink<2> ring{};
        DiagnosticHealth health{};
        DiagnosticEventBus bus{ring, health};

        Check(bus.Publish(Sample(1)), "bus_publish_ok");
        Check(bus.Publish(Sample(2)), "bus_publish_second_ok");
        Check(!bus.Publish(Sample(3)), "bus_publish_overflow_reported");
        Check(health.degraded(), "bus_overflow_degrades_health");
        Check(ring.Count() == 2, "bus_keeps_ring_bounded");
        Check(bus.PublishedCount() == 3, "bus_counts_attempts");
    }

    } // namespace

    int main() {
        TestCodecRoundTrip();
        TestCodecRejections();
        TestPairing();
        TestRingBufferCapacityAndOverflow();
        TestRingBufferSingleProducerOrdering();
        TestRingBufferConcurrentProducers();
        TestHealthTracksDegradationWithoutChangingBusinessResult();
        TestBusReportsHealthAndKeepsPublishing();
        if (failures != 0) { std::printf("DIAGNOSTICS_CHECKS_FAILED %d\n", failures); return 1; }
        std::printf("DIAGNOSTICS_CHECKS_PASSED\n");
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


class DiagnosticCoreTests(unittest.TestCase):
    def test_diagnostics_core_has_no_platform_or_legacy_dependency(self):
        """诊断核心必须可宿主编译：不碰平台、文件、Lua 或旧 Runtime 源。

        判据只看**代码**，不看注释：注释里提到某个文件"怎么进设备构建"（例如
        `in_memory_ring_buffer_sink.hpp` 说明自己经 `diagnostic_session.hpp` →
        `saltynx_runtime_bridge.cpp` 进设备）是正常且有用的，构不成依赖。
        """
        sources = sorted(DIAGNOSTICS.glob("*.hpp")) + sorted(DIAGNOSTICS.glob("*.cpp"))
        self.assertTrue(sources, "diagnostics 目录为空")
        forbidden = ("exlaunch", "saltynx", "SaltySD", "switch.h", "lib/nx",
                     "lua.h", "lua_runtime", "hook_manager", "game_file_reader")
        for path in sources:
            body = without_comments(path.read_text(encoding="utf-8"))
            for token in forbidden:
                with self.subTest(path=path.name, token=token):
                    self.assertNotIn(token, body, f"{path.name} 不得依赖 {token}")
        # 平台相关的落盘只能经由 IFilePort 适配器，diagnostics 目录本身不得直接调文件 API。
        for path in sources:
            body = without_comments(path.read_text(encoding="utf-8"))
            for token in ("fopen", "fwrite", "svc", "nn::"):
                with self.subTest(path=path.name, token=token):
                    self.assertNotIn(token, body)

    def test_diagnostics_core_behaviour_on_host(self):
        compiler = host_compiler()
        if compiler is None:
            self.skipTest("需要宿主 C++ 编译器")
        with tempfile.TemporaryDirectory(prefix="runtime-diagnostics-") as temporary:
            directory = Path(temporary)
            source = directory / "diagnostics.cpp"
            binary = directory / "diagnostics"
            source.write_text(DRIVER, encoding="utf-8")
            compile_result = subprocess.run(
                [
                    compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    "-I", str(SRC),
                    str(source),
                    str(DIAGNOSTICS / "binary_event_codec.cpp"),
                    str(DIAGNOSTICS / "diagnostic_event_bus.cpp"),
                    str(DIAGNOSTICS / "diagnostic_health.cpp"),
                    "-pthread",
                    "-o", str(binary),
                ],
                capture_output=True, text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(run_result.returncode, 0, run_result.stdout + run_result.stderr)
            self.assertIn("DIAGNOSTICS_CHECKS_PASSED", run_result.stdout)
            self.assertNotIn("FAILED_CHECK", run_result.stdout)


if __name__ == "__main__":
    unittest.main()
