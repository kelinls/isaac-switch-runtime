"""卡上开关（排障用）的一致性 + **一条血泪纪律**：开关只能在游戏主线程上读。

为什么值得单独盯：开关是"一份包测多种配置"的支点 —— 运行时按名字去卡上问"这个文件在吗"，
设备侧读数按**同一个顺序**把 `g_CardSwitchMask` 的位翻译回名字。两边一旦错位，
读数会把"没装菜单"显示成"没建 Lua 状态"，而这类错误**看起来完全正常**（掩码非 0、
名字像模像样），只有真机现象对不上时才会暴露 —— 那是最贵的一种错。

纪律那一条来自一次真实事故（2026-09-16，构建 442190 **每次开机必崩**）：
卡上开关最初是在"装挂点"那一步读的，而那一步跑在**我们自己的 worker 线程**上；
从那条路调游戏 nnSdk 的文件 API（`nn::fs::OpenFile`）会在 SDK 内部空指针崩溃
（两份崩溃报告 `01789571605` / `01789571620`）。同一个适配器的读写调用在游戏主线程上一直是好的
（模组开关状态的持久化就是它做的）。⇒ 所以：**探测前的"我在游戏线程上"闸门必须在，
且装挂点那条路径上不许出现任何读卡代码。**
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK_MANAGER = ROOT / "runtime" / "source" / "hook_manager.cpp"
CATALOG = ROOT / "runtime" / "src" / "domain" / "runtime" / "hook_catalog.hpp"
READER = ROOT / "tools" / "read_device_state_via_gdb.py"


def implementation_names() -> list:
    text = HOOK_MANAGER.read_text(encoding="utf-8")
    body = re.search(r"kCardSwitchNames\[\] = \{(.*?)\};", text, re.S).group(1)
    return re.findall(r'"([\w-]+)"', body)


def reader_names() -> list:
    text = READER.read_text(encoding="utf-8")
    body = re.search(r"CARD_SWITCH_NAMES = \((.*?)\)", text, re.S).group(1)
    return re.findall(r"'([\w-]+)'", body)


class CardSwitchTests(unittest.TestCase):
    def test_name_lists_match_between_runtime_and_reader(self):
        # 先证明解析器抓到了东西：表为空时下面那条"一致"会假绿。
        runtime_names = implementation_names()
        self.assertTrue(runtime_names, "没解析到 kCardSwitchNames")
        self.assertEqual(runtime_names, reader_names(),
                         "卡上开关的名字/顺序在实现与读数工具里必须逐字一致（掩码位序靠它）")

    def test_every_switch_has_a_reader(self):
        # 每个开关名字都必须真的被某处读过，否则它只是个摆设（掩码位亮着却没人看）。
        text = HOOK_MANAGER.read_text(encoding="utf-8")
        for name in implementation_names():
            self.assertIn(f'CardSwitchOn("{name}")', text, f"开关 {name} 没有任何读取点")

    def test_probe_is_gated_on_the_game_thread(self):
        # ★ 事故回归守卫：探测卡之前必须先确认"我们在游戏主线程上"。
        source = HOOK_MANAGER.read_text(encoding="utf-8")
        probe = re.search(r"void ProbeCardSwitches\(\) \{(.*?)\n\}", source, re.S)
        self.assertIsNotNone(probe, "找不到 ProbeCardSwitches")
        self.assertIn("g_CardSwitchProbeAllowed", probe.group(1),
                      "探测前必须先过“我在游戏线程上”这道闸 —— 少了它 442190 那次"
                      "每次开机必崩的事故会重演")
        self.assertIn("g_CardSwitchProbeAllowed.store(1,", source,
                      "没有任何地方把闸门打开 ⇒ 开关永远读不到"
                      "（闸门必须由游戏主线程上的代码打开）")

    def test_game_thread_gate_is_opened_only_on_the_mod_load_path(self):
        # 闸门只允许在"确定在游戏主线程上"的那一处打开：模组加载（它由
        # `ManagerUpdateHook::Callback` 调用，即游戏自己的帧里）。
        source = HOOK_MANAGER.read_text(encoding="utf-8")
        load = re.search(r"bool LoadDefaultManifestModThroughService\(u32\* failureDetail\) \{"
                         r"(.{0,600})", source, re.S)
        self.assertIsNotNone(load, "找不到 LoadDefaultManifestModThroughService")
        self.assertIn("g_CardSwitchProbeAllowed.store(1,", load.group(1))
        self.assertEqual(source.count("g_CardSwitchProbeAllowed.store(1,"), 1,
                         "闸门只能有一个开启点")

    def test_no_card_read_on_the_hook_install_path(self):
        # ★ 事故回归守卫：装挂点跑在 worker 线程上，那条路径上**不许**出现读卡代码。
        source = HOOK_MANAGER.read_text(encoding="utf-8")
        install = re.search(r"DefaultManifestInstallResult TryInstallDefaultManifestMod"
                            r"\(const TargetModule& module\) \{(.*?)\n\}", source, re.S)
        self.assertIsNotNone(install, "找不到 TryInstallDefaultManifestMod")
        body = install.group(1)
        # 只看**调用**（`CardSwitchOn(`），注释里提到名字不算 —— 注释正是解释"为什么不能在这儿读"的地方。
        self.assertNotIn("CardSwitchOn(", body, "装挂点路径上不许读卡（会在 nnSdk 里崩游戏）")
        self.assertNotIn("CardSkipHookPredicate", body, "装挂点路径上不许读卡（会在 nnSdk 里崩游戏）")
        # 挂点层面的二分判据只能是**编译期常量**。
        self.assertIn("SkipOptionalHooksPredicate", body)

    def test_switches_are_probed_once_and_self_reported(self):
        # 开关读不到时实现返回 false（"就当没这个开关"），所以必须有一个设备侧可读的
        # 自证字段，否则"开关没生效"与"开关生效了但没用"无法区分。
        source = HOOK_MANAGER.read_text(encoding="utf-8")
        self.assertIn("g_CardSwitchMask", source)
        self.assertIn("g_CardSwitchChannel", source)
        reader = READER.read_text(encoding="utf-8")
        self.assertIn("'g_CardSwitchMask'", reader)
        self.assertIn("'g_CardSwitchChannel'", reader)

    def test_switch_mask_fits_in_one_word(self):
        self.assertLessEqual(len(implementation_names()), 32,
                             "掩码只有 32 位：开关个数超过 32 就必须改成两字")


if __name__ == "__main__":
    unittest.main()
