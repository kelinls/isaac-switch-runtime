"""构建产物的未定义符号门禁。

真机事故（报告 `01789135138`）：`TryInstallRebuildMountPointsRelay` 的定义被写在
`#if EXL_DIAGNOSTIC_STAGE == 48` 块内，而调用点在所有构建里都编译，于是模块里
留下一个**未定义**符号：调用经 PLT 走 GOT，而该槽在加载时无人填充，保持 0，
`br x17` 直接跳到地址 0。崩溃报告表现为 `PC=0`、`LR` 指向那条 `bl ...@plt` 的
下一条指令、`RA` 在 `ModuleWorker` 里——从寄存器很难一眼看出是"少了一个定义"。

因此这里直接检查 ELF 的符号表：除了一份显式白名单（nnSdk 提供的 nn::ro 内部
符号）之外，**不允许出现任何未定义符号**。这条门禁不需要真机，也不需要反汇编。
"""

import unittest
from pathlib import Path

# ELF 解析与白名单在 `tools/check_runtime_elf_symbols.py` 里，构建流程用的也是同一份
# （2026-09-12 起：本测试读的是仓库里那份**陈旧**的 `runtime/runtime.elf`，所以它没能发现
# 新出现的未定义符号 —— 那一次的现场是真机报告 `01789202408` 的 PC=0 崩溃）。
from tools.check_runtime_elf_symbols import (
    ALLOWED_UNDEFINED,
    defined_symbol_names as _defined_symbol_names,
    elf_sections,
    undefined_symbols,
)


ROOT = Path(__file__).resolve().parents[1]
ELF = ROOT / "runtime.elf"


class RuntimeElfSymbolGateTests(unittest.TestCase):
    def setUp(self):
        if not ELF.is_file():
            self.skipTest("需要已构建的 runtime/runtime.elf（先跑一次容器构建）")
        self.data = ELF.read_bytes()

    def test_module_has_no_unexpected_undefined_symbols(self):
        undefined = sorted(set(undefined_symbols(self.data)))
        unexpected = [name for name in undefined if name not in ALLOWED_UNDEFINED]
        self.assertEqual(
            unexpected, [],
            "模块里出现了未定义的符号：调用会经 PLT 走 GOT，而槽为 0 时直接跳到地址 0"
            "（真机表现为 PC=0）。检查是否有定义被条件编译排除。",
        )

    def test_production_relay_install_is_defined(self):
        """所有构建都调用的中继安装函数必须在符号表里有定义。"""
        defined = _defined_symbol_names(self.data)
        self.assertIn("_Z33TryInstallRebuildMountPointsRelayRK12TargetModule", defined)
        self.assertIn("IsaacModRuntime_RebuildContentMountPointsRelay", defined)


if __name__ == "__main__":
    unittest.main()
