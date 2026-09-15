"""`HasDispatchSite` 的名单必须与代码里真实的派发调用逐项一致。

## 为什么要有这条门禁

设备侧那份"常驻状态面"会报两个数：**有派发点的登记数**、**无派发点的登记数**
（`IsaacModRuntime_GetHookDiagnostics` 的输出槽，宿主通道同源）。这两个数是判断
"某个回调为什么没反应"的第一手依据 —— 但它的可信度完全取决于
`HasDispatchSite()` 那张名单是否与代码一致。

2026-09-15 实测发现它**不一致**：名单里写了 `kCallbackInputAction`（13，
`MC_INPUT_ACTION`），而全仓没有任何地方派发它（`CallbackDispatcher` 的三处调用是
PostGameStarted / PostUpdate / PostRender，加上手工那条 PreGetCollectible）。
后果不是崩溃，而是**读数骗人**：操作者会以为"这种回调有派发点"，于是去别处找原因，
而它一次都不会跑。EID 恰好就注册了 `MC_INPUT_ACTION`。

## 这条门禁怎么判

从**源码文本**里两侧各提取一份集合再比相等：

* 名单侧：`HasDispatchSite()` 函数体里的 `id == kCallback<名字>`；
* 真实派发侧：`lua_runtime.cpp` 里
  * `Dispatch(isaac::runtime::kCallback<名字>,` —— 交给派发器的那几条；
  * `g_CallbackRegistry.At(isaac::runtime::kCallback<名字>, 0)` —— 手工取登记那条。

两侧集合相等才通过。这样"名单多写一项"和"代码新增派发却忘了记名单"都会立刻红，
而不是等下一次真机读数把人带到错误方向。

（与项目其它源码门禁同一形态：不做 C++ 解析，只做受控的文本提取。代价是写法被限制
在有限几种，好处是宿主上零依赖、跑得极快，且不义不容辞地维护一个 C++ 前端。）
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"
MOD_API = ROOT / "runtime" / "src" / "interfaces" / "lua" / "mod_api.cpp"
LUA_RUNTIME = SOURCE / "lua_runtime.cpp"

#: `kCallback` 后面的标识符（`kCallbackPostUpdate` → `PostUpdate`）。
_CALLBACK_NAME = r"kCallback([A-Za-z0-9_]+)"


def declared_dispatch_sites(text: str) -> set[str]:
    """从 `HasDispatchSite()` 函数体里取出名单。"""
    start = text.index("bool HasDispatchSite(")
    body = text[start:text.index("}", start)]
    return set(re.findall(r"id\s*==\s*" + _CALLBACK_NAME, body))


def actual_dispatch_sites(text: str) -> set[str]:
    """从运行时源码里取出**真的**会被派发的回调种类。

    两种形态都要认：交给 `CallbackDispatcher` 的，以及手工从注册表取登记的那条。
    只认这两种写法是刻意的 —— 引入第三种派发方式时这条门禁会红，提醒把它补进来
    （宁可红一次，也不让名单悄悄失真）。
    """
    dispatched = set(re.findall(r"Dispatch\(isaac::runtime::" + _CALLBACK_NAME, text))
    dispatched |= set(re.findall(r"g_CallbackRegistry\.At\(isaac::runtime::" + _CALLBACK_NAME, text))
    return dispatched


class CallbackDispatchSiteAgreementTests(unittest.TestCase):
    def setUp(self):
        self.mod_api = MOD_API.read_text(encoding="utf-8")
        self.runtime = LUA_RUNTIME.read_text(encoding="utf-8")

    def test_the_ledger_matches_the_real_dispatch_calls(self):
        declared = declared_dispatch_sites(self.mod_api)
        actual = actual_dispatch_sites(self.runtime)
        self.assertTrue(declared, "`HasDispatchSite` 里一个回调种类都没解析到 —— 门禁失去意义")
        self.assertTrue(actual, "运行时源码里一个派发调用都没解析到 —— 门禁失去意义")
        self.assertEqual(
            declared,
            actual,
            "`HasDispatchSite` 的名单与真实派发调用不一致：\n"
            f"  名单多出（报了但不会触发）：{sorted(declared - actual)}\n"
            f"  代码多出（会触发但没报）：{sorted(actual - declared)}",
        )

    def test_the_known_never_dispatched_kind_is_not_claimed(self):
        """回归：`MC_INPUT_ACTION` 曾经被误报成"有派发点"，它必须留在名单之外。

        留一条针对性断言的原因：这条误报是通过**真机读数对不上**才发现的，
        而上面那条集合相等断言在"名单和代码一起写错"时不会红。这一条把当时的
        具体结论钉住，避免有人为了让某个统计好看而把它加回来。
        """
        declared = declared_dispatch_sites(self.mod_api)
        self.assertNotIn(
            "InputAction",
            declared,
            "`MC_INPUT_ACTION` 没有任何派发调用（见 HasDispatchSite 上方的注释）——"
            "把它写进名单会让设备侧读数骗人。要真的支持它，先实现派发点再登记。",
        )
