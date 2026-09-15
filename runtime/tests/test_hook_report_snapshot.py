"""挂点安装报告经既有快照 `reserved` 的高位回读，不改变 48 字节布局、不动启动状态机。

Ruling 26：原计划的 state/detail/sequence 是**在用的启动状态机**，不能复用。
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"
OBSERVER = SOURCE / "test_run_observer.cpp"
PROTOCOL = SOURCE / "test_run_observer_protocol.hpp"
REPORT = SOURCE / "hook_install_report_state.hpp"


class HookReportSnapshotTests(unittest.TestCase):
    def test_snapshot_layout_is_unchanged(self):
        text = PROTOCOL.read_text(encoding="utf-8")
        self.assertIn("static_assert(sizeof(TestRunSnapshot) == 48);", text,
                      "回读必须复用既有 48 字节布局，不得改协议")

    def test_observer_keeps_the_state_machine_fields_untouched(self):
        """state/detail/sequence 是启动状态机，报告不得占用它们。"""
        text = OBSERVER.read_text(encoding="utf-8")
        self.assertIn("snapshot.state = static_cast<std::uint32_t>(stateDetail);", text)
        self.assertIn("snapshot.detail = static_cast<std::uint32_t>(stateDetail >> 32);", text)
        self.assertIn("snapshot.sequence = g_sequence.load", text)

    def test_observer_shifts_the_report_into_the_reserved_high_bits(self):
        """低 8 位留给诊断 attach，报告整体经 PackHookInstallReport 左移 8 位写入高位。

        位移口径固化在 `hook_install_report_state.hpp`（见 `test_packing_layout_is_pinned`）；
        本测试只确认观察者把打包后的报告 OR 进 `reserved` 高位。
        """
        text = OBSERVER.read_text(encoding="utf-8")
        self.assertIn("HookInstallReportSnapshot()", text)
        self.assertRegex(text, r"snapshot\.reserved = [^;]*PackHookInstallReport\(",
                         "报告必须经 PackHookInstallReport 打包后 OR 进 reserved 高位")

    def test_observer_keeps_sequential_consistency(self):
        """既有门禁（test_test_run_observer.py）要求本文件不得出现 relaxed 序。"""
        self.assertNotIn("std::memory_order_relaxed", OBSERVER.read_text(encoding="utf-8"))

    def test_report_state_uses_32bit_atomics_only(self):
        text = REPORT.read_text(encoding="utf-8")
        self.assertIn("std::atomic<std::uint32_t>", text)
        self.assertNotIn("std::atomic<bool>", text)

    def test_packing_layout_is_pinned(self):
        """位域口径固化：位移一旦漂移，设备读数就会串位。"""
        text = REPORT.read_text(encoding="utf-8")
        for shift in ("<< 8", "<< 16", "<< 24", ">> 8", ">> 16", ">> 24"):
            self.assertIn(shift, text, f"打包位移 {shift} 缺失")


if __name__ == "__main__":
    unittest.main()
