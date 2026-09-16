"""`Input.IsActionTriggered/IsActionPressed/GetActionValue` 的**调用约定**测试。

## 为什么需要它（真机 bug，2026-09-16）

模组开关菜单"按了没反应"。真机读数排除了脚本、字体、绑定之外的所有可能：0~27 号动作 ×
controller 0/1 全问一遍、玩家按遍所有键，**回答"是"的一个都没有**。

真因在这三个绑定**不是函数本体而是游戏自己的入口跳板**，逐字节读出来是：

    adrp x0, <g_Manager> ; ldr x0, [x0, #0x768]   → x0 = Manager 单例
    mov  x3, xzr                                  → x3 = 0（尾随实体参数清零）
    b    <真函数>

真函数签名是 `(Manager*, u32 action, u32 controller, void* entity)`，跳板只补 x0 与 x3，
**动作号在 x1、手柄序号在 x2**。照 PC 的形态 `query(action, controller)` 调用，会把动作号塞进
x0（被覆盖）、手柄序号塞进 x1（被当成动作号）⇒ 引擎永远答"没按下"。

这条测试用**假的跳板**（记录它收到了什么）驱动真实的 handler，钉住"动作号必须落到第 2 个参数、
手柄序号落到第 3 个参数"。它不依赖真机、不依赖游戏。
"""

import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

try:
    from .test_support import build_lua_harness
except ImportError:
    from test_support import build_lua_harness


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"
SRC = ROOT / "runtime" / "src"

DRIVER = textwrap.dedent(
    r"""
    #include "interfaces/lua/input_api.hpp"

    #include "lua_runtime.hpp"

    extern "C" {
    #include <lauxlib.h>
    #include <lualib.h>
    }
    
    #include <cstdio>
    #include <cstring>

    // 假跳板：记录它收到的 (x0 占位, x1, x2)，并按脚本给的答案回答。
    namespace {
    int failures = 0;
    void Check(bool condition, const char* what) {
        if (!condition) { std::printf("FAILED_CHECK %s\n", what); ++failures; }
    }

    struct Call { void* self; unsigned action; unsigned controller; };
    Call g_lastTriggered{nullptr, 0xFFFFFFFFu, 0xFFFFFFFFu};
    Call g_lastPressed{nullptr, 0xFFFFFFFFu, 0xFFFFFFFFu};
    Call g_lastValue{nullptr, 0xFFFFFFFFu, 0xFFFFFFFFu};
    bool g_triggeredAnswer = false;
    bool g_pressedAnswer = false;
    float g_valueAnswer = 0.0f;

    bool FakeTriggered(void* self, unsigned action, unsigned controller) {
        g_lastTriggered = {self, action, controller};
        return g_triggeredAnswer;
    }
    bool FakePressed(void* self, unsigned action, unsigned controller) {
        g_lastPressed = {self, action, controller};
        return g_pressedAnswer;
    }
    float FakeValue(void* self, unsigned action, unsigned controller) {
        g_lastValue = {self, action, controller};
        return g_valueAnswer;
    }

    const char* kScript = R"LUA(
        local results = {}
        results[1] = Input.IsActionTriggered(13, 1)          -- 动作 13、手柄 1
        results[2] = Input.IsActionTriggered(14, 0)          -- 动作 14、手柄 0
        results[3] = Input.IsActionPressed(15, 1)
        results[4] = Input.GetActionValue(16, 1)
        captured = results
    )LUA";
    } // namespace

    int main() {
        // 注意参数顺序：`SetInputBindings(isActionPressed, isActionTriggered, getActionValue)`
        // —— **第一个是"按住"、第二个才是"刚按下"**（第一次写这个用例时把两者传反了，
        // 于是记录出来的动作号正好互换；这类顺序错误只有"检查收到什么"才抓得住）。
        LuaRuntime::SetInputBindings(reinterpret_cast<uintptr_t>(&FakePressed),
                                     reinterpret_cast<uintptr_t>(&FakeTriggered),
                                     reinterpret_cast<uintptr_t>(&FakeValue));
        lua_State* state = luaL_newstate();
        if (state == nullptr) { std::printf("FAILED_CHECK luaL_newstate\n"); return 1; }
        // 不需要 `luaL_openlibs`：本用例只调我们自己的 handler（harness 也不编 `linit.c`）。
        // 与运行时准备阶段同路：建 `Input` 表并挂上 handler（`lua_runtime.cpp` 里
        // `RegisterNeutralInputApi` 做的就是这两句）。
        lua_newtable(state);
        static_cast<void>(isaac::runtime::AttachInputMethods(state));
        lua_setglobal(state, "Input");

        // 先让假跳板答"是"，脚本里的调用才会返回 true（同时记录它收到的参数）。
        g_triggeredAnswer = true;
        g_pressedAnswer = true;
        g_valueAnswer = 0.5f;
        if (luaL_dostring(state, kScript) != LUA_OK) {
            std::printf("FAILED_CHECK script error: %s\n", lua_tostring(state, -1));
            return 1;
        }

        std::printf("RECORDED triggered=(%u,%u) pressed=(%u,%u) value=(%u,%u)\n",
                    g_lastTriggered.action, g_lastTriggered.controller,
                    g_lastPressed.action, g_lastPressed.controller,
                    g_lastValue.action, g_lastValue.controller);
        // ★ 判据：动作号落在第 2 个参数、手柄序号落在第 3 个（第 1 个是被跳板覆盖的占位）。
        Check(g_lastTriggered.action == 14 && g_lastTriggered.controller == 0,
              "IsActionTriggered 第二次调用应当把 (动作=14, 手柄=0) 放进 x1/x2");
        Check(g_lastPressed.action == 15 && g_lastPressed.controller == 1,
              "IsActionPressed 应当把 (动作=15, 手柄=1) 放进 x1/x2");
        Check(g_lastValue.action == 16 && g_lastValue.controller == 1,
              "GetActionValue 应当把 (动作=16, 手柄=1) 放进 x1/x2");

        // 返回值也要透传（脚本把结果存在 `captured` 里）。
        lua_getglobal(state, "captured");
        Check(lua_istable(state, -1), "脚本应当把结果存进 captured");
        for (int index = 1; index <= 2; ++index) {
            lua_rawgeti(state, -1, index);
            Check(lua_toboolean(state, -1) == 1, "IsActionTriggered 的 true 必须透传");
            lua_pop(state, 1);
        }
        lua_rawgeti(state, -1, 3);
        Check(lua_toboolean(state, -1) == 1, "IsActionPressed 的 true 必须透传");
        lua_pop(state, 1);
        lua_rawgeti(state, -1, 4);
        Check(lua_tonumber(state, -1) == 0.5, "GetActionValue 的 float 必须透传");
        lua_pop(state, 2);

        lua_close(state);
        if (failures == 0) { std::printf("INPUT_ABI_CHECKS_PASSED\n"); }
        return failures == 0 ? 0 : 1;
    }
    """
)


class InputBindingAbiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-input-abi-")
        cls.binary = build_lua_harness(
            source_root=SOURCE,
            workdir=Path(cls.temporary.name),
            harness_source=DRIVER,
        )

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "temporary"):
            cls.temporary.cleanup()

    def test_input_binding_calling_convention(self):
        result = subprocess.run([str(self.binary)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("INPUT_ABI_CHECKS_PASSED", result.stdout)
        self.assertNotIn("FAILED_CHECK", result.stdout)


if __name__ == "__main__":
    unittest.main()
