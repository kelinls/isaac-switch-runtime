"""`Font:Load` 路径归一化的回归测试（真机报告 `01789205321` 的根因）。

## 为什么单独一个文件

2026-09-12 的真机普查字给出的现象是"脚本成功结束、注册表却有 10 条、没有
`MC_POST_UPDATE`/`MC_POST_RENDER`"。宿主 harness 的 `--font-load fail` 分支逐项复现了同一组数字，
于是根因收敛到**一处**：EID 交给引擎的字体路径被改坏，字体装不上 ⇒ `main.lua:187` 顶层 `return`。

那一处就是 `font_api.cpp` 的 `NormalizeModResourcePath`。它的输入是 EID 用
`debug.getinfo().source` 拼出来的**绝对路径**（中间带 `main.lua/../`），输出必须是引擎认的
**内容挂载点相对名**（`font/eid_default.fnt`）。这条链路此前没有任何宿主测试盯着 —— 于是同一个
函数里的下标/锚点问题接连躲过了三轮真机。

测试用的是 `NormalizeModResourcePathForProbe`（`font_api.hpp` 暴露的**真实现**出口），
不是复刻一份实现：复刻的实现永远不能证明真实现的行为。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(ROOT / "tools"))
import eid_host_load as host  # noqa: E402  (工具就是被测通道，路径先补齐)


#: 真机报告 `01789205321` 里 EID 实际交给 `Font:Load` 的字符串（宿主 harness 逐字复现）。
DEVICE_FONT_ARGUMENT = (
    "rom:/isaac_mods/mods/external item descriptions_836319872"
    "/main.lua/../resources/font/eid_default.fnt"
)
#: EID 的兜底路径（`main.lua:169` 的 `"../mods/"..modfolder.."/resources/..."`）。
EID_FALLBACK_ARGUMENT = (
    "../mods/external item descriptions_836319872/resources/font/eid_default.fnt"
)
#: 引擎认的形状：内容挂载点 `resources` 的相对名。
EXPECTED_RELATIVE = "font/eid_default.fnt"


def _build() -> Path:
    """编译一次 harness（真实现 + vendored Lua），供本文件所有用例共用。"""
    if host.host_compilers() is None:
        raise unittest.SkipTest("缺少宿主 C/C++ 编译器")
    workdir = Path(tempfile.mkdtemp(prefix="eid-font-normalize-"))
    try:
        return host.build_harness(workdir)
    except RuntimeError as error:  # 工作树正被并行修改时给出可读原因，而不是 traceback
        raise unittest.SkipTest(f"harness 编译失败：{error}") from error


class FontPathNormalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.executable = _build()

    def normalize(self, raw: str) -> str:
        return host.run_normalize_probe(self.executable, raw)["out"]

    def test_device_argument_becomes_the_mount_point_relative_name(self):
        """真机那一条：绝对路径 + `main.lua/../` 必须收敛到 `font/eid_default.fnt`。

        这条断言失败就意味着字体装不上、EID 顶层 `return`、屏幕全空 —— 也就是
        `01789205321` 的现象。它是本文件存在的理由。
        """
        self.assertEqual(self.normalize(DEVICE_FONT_ARGUMENT), EXPECTED_RELATIVE)

    def test_eid_fallback_argument_lands_on_the_same_name(self):
        """EID 的兜底路径必须落到**同一个**挂载点根。

        否则第一次失败之后兜底必然再失败一次：`../mods/...` 是相对于 `rom:/` 的，
        而 Mod 树在 `rom:/isaac_mods/mods/` 下，引擎永远解析不到。
        """
        self.assertEqual(self.normalize(EID_FALLBACK_ARGUMENT), EXPECTED_RELATIVE)

    def test_both_eid_attempts_hand_the_engine_one_name(self):
        """两次尝试给引擎的名字必须完全相同（真机上就是这两条）。"""
        self.assertEqual(self.normalize(DEVICE_FONT_ARGUMENT),
                         self.normalize(EID_FALLBACK_ARGUMENT))

    def test_already_relative_names_pass_through(self):
        """已经是挂载点相对名时不许改写（Stage148 真机验证过的形态）。"""
        self.assertEqual(self.normalize(EXPECTED_RELATIVE), EXPECTED_RELATIVE)
        self.assertEqual(self.normalize("gfx/items/collectibles/c001.png"),
                         "gfx/items/collectibles/c001.png")

    def test_unrelated_paths_are_never_rewritten(self):
        """不属于本 Mod 内容树的路径原样返回 —— 归一化不许替引擎猜。"""
        for raw in (
            "rom:/resources/font/terminus.fnt",
            "font/terminus.fnt",
            "../not-a-mod/whatever.fnt",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(self.normalize(raw), raw)

    def test_content_leaf_is_normalized_too(self):
        """`content/` 是同级的另一条挂载点叶子，规则必须一致。"""
        self.assertEqual(
            self.normalize("rom:/isaac_mods/mods/mymod/content/gfx/x.anm2"), "gfx/x.anm2")

    def test_base_content_prefix_is_untouched(self):
        """本体自己的 `rom:/...` 路径（不含 mods 前缀）不得被当成 Mod 内容树。"""
        raw = "rom:/gfx/items/collectibles/collectibles_001_thesadonion.png"
        self.assertEqual(self.normalize(raw), raw)

    def test_inner_navigation_inside_the_mount_point_is_collapsed(self):
        """锚之后的 `./`、`../` 要按段折叠（`resources/../gfx/y.png` → `gfx/y.png`）。"""
        self.assertEqual(
            self.normalize("rom:/isaac_mods/mods/mymod/resources/a/./b.fnt"), "a/b.fnt")
        self.assertEqual(
            self.normalize("../mods/mymod/resources/gfx/../gfx/y.png"), "gfx/y.png")

    def test_escaping_the_mount_point_is_not_guessed(self):
        """`..` 想跳出挂载点根时原样返回，不编造一个自己都不确定的名字。"""
        raw = "rom:/isaac_mods/mods/mymod/resources/../../escape.fnt"
        self.assertEqual(self.normalize(raw), raw)


class DeviceMirrorLoadTests(unittest.TestCase):
    """把真机的两段行为镜像到宿主：名字口径严格 + 第一次 `Load` 失败后走兜底。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.executable = _build()
        romfs = host.locate_eid_romfs()
        if romfs is None:
            raise unittest.SkipTest("找不到 EID 夹具")
        cls.workdir = Path(tempfile.mkdtemp(prefix="eid-font-mirror-"))
        try:
            _, cls.prepared = host.prepare_workspace(romfs, cls.workdir)
        except RuntimeError as error:
            raise unittest.SkipTest(f"harness 工作区准备失败：{error}") from error

    def mirror_run(self) -> dict:
        return host.run_harness(self.executable, self.prepared, 1048576, 3, True, True,
                                "strict", "first-fails")

    def test_both_attempts_use_the_mount_point_relative_name(self):
        """真机两次尝试里，引擎收到的名字都必须是 `font/eid_default.fnt`。"""
        report = self.mirror_run()
        calls = report["fontLoadCalls"]
        self.assertEqual(calls.count("accepts=true"), 2, calls)
        self.assertIn(f"{EXPECTED_RELATIVE} | ext=\"\" | accepts=true | call=1", calls)
        self.assertIn(f"{EXPECTED_RELATIVE} | ext=\"\" | accepts=true | call=2", calls)

    def test_font_success_registers_update_and_render(self):
        """字体装上之后，两个已挂派发点的种类必须登记（真机上正是这两条缺失）。"""
        report = self.mirror_run()
        kinds = {int(item) for item in report["callbackKindMask"].split(",") if item}
        self.assertIn(1, kinds, "MC_POST_UPDATE 未登记：main.lua 又提前 return 了")
        self.assertIn(2, kinds, "MC_POST_RENDER 未登记：main.lua 又提前 return 了")
        self.assertGreaterEqual(report["registryCount"], 30, report["registryCount"])


if __name__ == "__main__":
    unittest.main()
