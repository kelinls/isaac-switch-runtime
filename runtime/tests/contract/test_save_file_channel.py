"""存档文件通道（模组开关状态的存储）的契约测试。

## 为什么需要它

开关状态是运行时**唯一**会往玩家存档里写东西的地方。这一层出错的后果不是崩溃，而是
"某个模组这次没加载""状态丢了""存档里多出一个看不懂的文件"。所以把三条纪律钉成门禁：

1. **只走具名常量**：适配器里的每个引擎入口都必须引用 `runtime_constants.hpp` 里的常量，
   不许出现裸偏移；常量注释里要写出**证据函数**（`OpenFile`/`CreateFile`/…），
   这样"偏移从哪来"永远可查；
2. **每个入口先守卫**：所有调用都必须先过 16 字节守卫解析（`Guarded<…>`），
   任何"直接用基址加偏移去调"的写法都是把玩家存档交给一个可能错的地址；
3. **顺序**：`hook_manager` 必须在**注册资源挂载点与加载脚本之前**读取开关状态并按它过滤 ——
   顺序反了的表现是"关掉的模组照样加载"，而且完全看不出来。
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "runtime" / "src"
CONSTANTS = ROOT / "runtime" / "source" / "runtime_constants.hpp"
ADAPTER = SRC / "infrastructure" / "persistence" / "engine_save_file_adapter.cpp"
ADAPTER_HPP = SRC / "infrastructure" / "persistence" / "engine_save_file_adapter.hpp"
STATE = SRC / "domain" / "mod" / "mod_toggle_state.hpp"
SERVICE = SRC / "application" / "mod" / "mod_toggle_service.cpp"
HOOK_MANAGER = ROOT / "runtime" / "source" / "hook_manager.cpp"

#: 适配器必须用到的具名常量（缺一个就说明有一处入口是"手写地址"调起来的）。
REQUIRED_CONSTANTS = (
    "kFilesysOpenFileThunkOffset",
    "kFilesysCreateFileThunkOffset",
    "kFilesysWriteFileThunkOffset",
    "kFilesysReadFileThunkOffset",
    "kFilesysSetFileSizeThunkOffset",
    "kFilesysFlushFileThunkOffset",
    "kFilesysGetFileSizeThunkOffset",
    "kFilesysCloseFileThunkOffset",
    "kFilesysDeleteFileThunkOffset",
    "kFilesysCommitSaveDataThunkOffset",
    "kSaveDataManagerGetMountPointOffset",
)

#: 常量注释里必须出现的**证据函数名**（`nn::fs` 的函数名），说明偏移出自引擎自己的调用点。
EVIDENCE_NAMES = (
    "OpenFile", "CreateFile", "WriteFile", "SetFileSize", "ReadFile", "GetFileSize",
    "FlushFile", "CloseFile", "CommitSaveData",
)


def code_lines(text: str):
    """只要代码行（丢掉 `//` 开头的注释行）—— 注释里举例说明与断言无关。"""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
            continue
        yield line


class SaveFileChannelContractTests(unittest.TestCase):
    def setUp(self):
        self.adapter = ADAPTER.read_text(encoding="utf-8")
        self.adapter_hpp = ADAPTER_HPP.read_text(encoding="utf-8")
        self.constants = CONSTANTS.read_text(encoding="utf-8")
        self.state = STATE.read_text(encoding="utf-8")
        self.service = SERVICE.read_text(encoding="utf-8")
        self.hook_manager = HOOK_MANAGER.read_text(encoding="utf-8")

    def test_every_entry_uses_a_named_constant_with_evidence(self):
        for name in REQUIRED_CONSTANTS:
            with self.subTest(constant=name):
                self.assertIn(f"inline constexpr uintptr_t {name} =", self.constants,
                              f"{name} 没在 `runtime_constants.hpp` 里声明")
                self.assertIn(name, self.adapter, f"{name} 没有被适配器引用（手写偏移？）")
        # 守卫字节数组也要存在，否则守卫无从谈起。
        for name in REQUIRED_CONSTANTS:
            array = name.replace("Offset", "ExpectedBytes")
            with self.subTest(array=array):
                self.assertIn(f"inline constexpr std::array<u8, 16> {array} =", self.constants,
                              f"{array} 缺失：这个入口没有守卫字节")
        for evidence in EVIDENCE_NAMES:
            with self.subTest(evidence=evidence):
                self.assertIn(evidence, self.constants,
                              f"常量注释里没有 {evidence}：偏移缺少「出自哪个调用点」的证据")

    def test_adapter_never_calls_an_unguarded_address(self):
        """所有调用点都必须来自 `Guarded<…>` 解析出来的函数指针。"""
        # 裸的"基址 + 偏移"调用形态（`module->base +` 直接当函数用）一律不许出现。
        offenders = [line.strip() for line in code_lines(self.adapter)
                     if re.search(r"reinterpret_cast<[^>]*\(\*\)", line)
                     and "Guarded" not in line]
        self.assertEqual(offenders, [], f"出现未经守卫的函数指针转换：{offenders}")
        # 每个常量都要在 `Guarded<...>` 的实参里出现一次。
        for name in REQUIRED_CONSTANTS:
            with self.subTest(constant=name):
                self.assertRegex(
                    self.adapter,
                    rf"Guarded<[^>]*>\([^;]*{name}",
                    f"{name} 没有经 `Guarded<…>` 解析（守卫会被绕过）")

    def test_all_or_nothing_when_a_guard_fails(self):
        """任何一个守卫不过 ⇒ 整条通道不可用（返回 `Unsupported`），不许"能用的先用"。"""
        self.assertIn("calls.valid =", self.adapter)
        self.assertIn("StatusCode::Unsupported", self.adapter)

    def test_state_file_name_is_a_named_constant(self):
        self.assertIn("inline constexpr char kModToggleStateFileName[]", self.state)
        self.assertIn("kModToggleStateFileName", self.service)

    def test_filter_runs_before_mounts_and_scripts(self):
        """顺序纪律：先读状态并过滤，再注册挂载点、再加载脚本。"""
        load_index = self.hook_manager.index("g_ModToggleService.Load()")
        mount_index = self.hook_manager.index("g_ContentMountService.RegisterMod(")
        self.assertLess(load_index, mount_index,
                        "读开关状态必须在注册模组挂载点之前")
        self.assertIn("ApplyToBatch(&batch)", self.hook_manager)
        self.assertLess(self.hook_manager.index("ApplyToBatch(&batch)"), mount_index,
                        "过滤必须在注册挂载点之前")


if __name__ == "__main__":
    unittest.main()
