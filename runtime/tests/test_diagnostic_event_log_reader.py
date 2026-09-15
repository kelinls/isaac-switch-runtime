"""统一诊断事件流的解析测试（含与 C++ 编码器的逐字节一致性）。

解析器与 Runtime 是两份独立实现，只有把它们对同一批字节的解读钉死，才能保证
"真机上写出的事件在本地读得出来"。这里既有纯 Python 的合成记录测试，也有一次
真实交叉验证：用 C++ 端 `BinaryEventCodec::Encode` 产生字节，再由 Python 解析。
"""

import hashlib
import importlib.util
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "runtime" / "src"
TOOLS = ROOT / "tools"
DIAGNOSTIC_MAGIC = b"ISAACDV1"
RECORD_SIZE = 60


def _load_reader():
    spec = importlib.util.spec_from_file_location(
        "read_persistence_event_log", TOOLS / "read_persistence_event_log.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reader = _load_reader()


def encode(sequence, *, build_id=0x20260910530000, origin=1, subsystem=7, event=9,
           severity=1, phase=1, flags=0, thread=1, result_domain=0, result_code=0,
           detail=0):
    """按 C++ 端二进制布局编码一条记录（小端、逐字段）。"""
    record = bytearray(RECORD_SIZE)
    struct.pack_into("<8sHH", record, 0, DIAGNOSTIC_MAGIC, 1, RECORD_SIZE)
    struct.pack_into("<I", record, 12, origin)
    struct.pack_into("<Q", record, 16, build_id)
    struct.pack_into("<I", record, 24, sequence)
    struct.pack_into("<HH", record, 28, subsystem, event)
    record[32] = severity
    record[33] = phase
    struct.pack_into("<H", record, 34, flags)
    struct.pack_into("<I", record, 36, thread)
    struct.pack_into("<II", record, 40, result_domain, result_code)
    struct.pack_into("<Q", record, 48, detail)
    checksum = reader._checksum(bytes(record[:RECORD_SIZE - 4]) + b"\x00" * 4)
    struct.pack_into("<I", record, RECORD_SIZE - 4, checksum)
    return bytes(record)


class DiagnosticEventLogReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="diagnostic-reader-")
        self.path = Path(self.temporary.name) / "isaac-runtime-events.bin"

    def tearDown(self):
        self.temporary.cleanup()

    def test_reads_and_names_events(self):
        self.path.write_bytes(
            encode(1, subsystem=4, event=4, phase=1) +
            encode(2, subsystem=4, event=4, phase=2, result_code=0) +
            encode(3, subsystem=8, event=2, phase=0, result_code=3)  # HealthChanged/ShortWrite
        )
        result = reader.read_diagnostic_event_log(self.path, 0x20260910530000)
        self.assertEqual(result["protocol"], "diagnostic-v1")
        self.assertEqual(result["records"], 3)
        self.assertEqual(result["events"][0]["subsystem"], "Manifest")
        self.assertEqual(result["events"][2]["event"], "HealthChanged")
        self.assertEqual(result["health"], [
            {"reason": "ShortWrite", "reason_value": 3, "sequence": 3}])
        self.assertEqual(result["attach_failures"], [])
        self.assertTrue(result["degraded"])
        # entered/returned 必须配对成功，且不产生"未匹配"告警。
        self.assertEqual(len(result["pairs"]), 1)
        self.assertEqual(result["pairs"][0]["outcome"], "Returned")
        self.assertEqual(result["missing_returns"], [])
        self.assertEqual(result["warnings"], [])

    def test_reports_unmatched_entered(self):
        self.path.write_bytes(encode(1, subsystem=7, event=9, phase=1))
        result = reader.read_diagnostic_event_log(self.path, 0x20260910530000)
        self.assertEqual(len(result["missing_returns"]), 1)
        self.assertEqual(result["missing_returns"][0]["event_value"], 9)
        self.assertFalse(result["degraded"])

    def test_reports_unmatched_returned_as_warning(self):
        self.path.write_bytes(encode(1, subsystem=7, event=9, phase=2))
        result = reader.read_diagnostic_event_log(self.path, 0x20260910530000)
        self.assertTrue(any("未匹配" in warning for warning in result["warnings"]))
        self.assertEqual(result["pairs"], [])

    def test_thread_is_part_of_pairing_identity(self):
        self.path.write_bytes(
            encode(1, subsystem=7, event=9, phase=1, thread=1) +
            encode(2, subsystem=7, event=9, phase=2, thread=2)
        )
        result = reader.read_diagnostic_event_log(self.path, 0x20260910530000)
        self.assertEqual(result["pairs"], [])
        self.assertEqual(len(result["missing_returns"]), 1)

    def test_filters_other_build_ids_and_warns_about_trailing_bytes(self):
        self.path.write_bytes(
            encode(1, build_id=0x1111111111111111) +
            encode(2) +
            b"\x00\x01\x02"
        )
        result = reader.read_diagnostic_event_log(self.path, 0x20260910530000)
        self.assertEqual(result["records"], 1)
        self.assertTrue(any("不足一个完整诊断记录" in warning for warning in result["warnings"]))

    def test_rejects_corrupted_record(self):
        corrupted = bytearray(encode(1))
        corrupted[24] ^= 0xFF  # 篡改 sequence 使校验和失配
        self.path.write_bytes(bytes(corrupted))
        with self.assertRaises(ValueError):
            reader.read_diagnostic_event_log(self.path, 0x20260910530000)

    def test_rejects_unknown_schema(self):
        wrong = bytearray(encode(1))
        struct.pack_into("<H", wrong, 8, 99)
        self.path.write_bytes(bytes(wrong))
        with self.assertRaises(ValueError):
            reader.read_diagnostic_event_log(self.path, 0x20260910530000)

    def test_missing_file_reports_no_events(self):
        result = reader.read_diagnostic_event_log(self.path, 0x20260910530000)
        self.assertEqual(result["records"], 0)
        self.assertEqual(result["interpretation"], "未发现指定 Build ID 的诊断事件")

    def test_cpp_encoder_and_python_parser_agree_byte_for_byte(self):
        """C++ 编码 → Python 解析的交叉验证：两份实现不得各自漂移。"""
        compiler = None
        for candidate in ("c++", "clang++", "g++"):
            import shutil
            found = shutil.which(candidate)
            if found:
                compiler = found
                break
        if compiler is None:
            self.skipTest("需要宿主 C++ 编译器")

        program = r"""
        #include "diagnostics/binary_event_codec.hpp"
        #include <cstdio>
        using namespace isaac::runtime;
        int main() {
            DiagnosticEvent entered{};
            entered.origin = DiagnosticOrigin::RuntimeModule;
            entered.buildId = 0x20260910530000ULL;
            entered.sequence = 1;
            entered.subsystem = DiagnosticSubsystem::Manifest;
            entered.event = 4;
            entered.severity = DiagnosticSeverity::Warning;
            entered.phase = DiagnosticPhase::Entered;
            entered.flags = 0x2A;
            entered.threadTag = 7;
            entered.result.domain = 3;
            entered.result.code = 9;
            entered.detail = 0x1122334455667788ULL;
            std::uint8_t record[BinaryEventCodec::kRecordSize]{};
            if (!BinaryEventCodec::Encode(entered, record, sizeof(record)).ok()) { return 2; }
            DiagnosticEvent returned = entered;
            returned.sequence = 2;
            returned.phase = DiagnosticPhase::Returned;
            std::uint8_t second[BinaryEventCodec::kRecordSize]{};
            if (!BinaryEventCodec::Encode(returned, second, sizeof(second)).ok()) { return 2; }
            // 以十六进制输出：原始字节经 stdout 管道会受文本模式与换行转换影响，
            // 而这份测试要断言的正是"逐字节一致"，所以输出必须是可精确复原的文本。
            for (std::size_t i = 0; i < sizeof(record); ++i) { std::printf("%02x", record[i]); }
            for (std::size_t i = 0; i < sizeof(second); ++i) { std::printf("%02x", second[i]); }
            std::printf("\n");
            return 0;
        }
        """
        with tempfile.TemporaryDirectory(prefix="diagnostic-crosscheck-") as temporary:
            directory = Path(temporary)
            source = directory / "encode.cpp"
            binary = directory / "encode"
            source.write_text(program, encoding="utf-8")
            compile_result = subprocess.run(
                [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(SRC),
                 str(source), str(SRC / "diagnostics" / "binary_event_codec.cpp"),
                 "-o", str(binary)],
                capture_output=True, text=True)
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            produced = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(produced.returncode, 0, produced.stderr)

        hexline = produced.stdout.strip()
        self.assertEqual(len(hexline), 2 * RECORD_SIZE * 2, "编码器应输出两条记录的十六进制")
        self.path.write_bytes(bytes.fromhex(hexline))
        result = reader.read_diagnostic_event_log(self.path, 0x20260910530000)
        self.assertEqual(result["records"], 2)
        self.assertEqual(len(result["pairs"]), 1)
        first = result["events"][0]
        self.assertEqual(first["origin"], "RuntimeModule")
        self.assertEqual(first["subsystem"], "Manifest")
        self.assertEqual(first["severity"], "Warning")
        self.assertEqual(first["phase"], "Entered")
        self.assertEqual(first["flags"], 0x2A)
        self.assertEqual(first["thread"], 7)
        self.assertEqual(first["result_domain"], "Module")
        self.assertEqual(first["result_code"], 9)
        self.assertEqual(first["detail"], 0x1122334455667788)
        self.assertEqual(first["build_id"], 0x20260910530000)
        self.assertEqual(result["events"][1]["phase"], "Returned")
        self.assertEqual(result["warnings"], [])


if __name__ == "__main__":
    unittest.main()
