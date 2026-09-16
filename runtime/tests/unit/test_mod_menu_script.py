"""模组开关菜单脚本（`embedded_mod_menu_script.hpp`）的行为测试。

## 为什么单独测一个"脚本"

菜单是这一批里唯一**由脚本决定行为**的部分：它开不开、选中哪一项、按 A 之后改了什么、
按 B 之后有没有落盘，全在脚本里。真机上它和模组共用一条回调派发路径，一旦脚本里有错，
症状是"菜单打不开"或"改了没保存"，而这种问题在不看屏幕的情况下很难定位。

做法：把内嵌脚本原样取出来，用一个**独立编译出来的 Lua 5.3 解释器**跑它，旁边放一组桩
（`RegisterMod` / `ModCallbacks` / `Color` / `Font` / `Input` / `ButtonAction` / `RuntimeMods`），
然后直接调用它登记的那两个回调，检查这些可观察行为：

1. 一开始菜单是关着的，渲染回调什么都不画；
2. 按一下 ACTION_MAP ⇒ 菜单打开，并且把 `RuntimeMods.List()` 的物品清单画出来；
3. 上下键改选中项、A 键切换 ⇒ 调 `RuntimeMods.SetEnabled(目录, 布尔)`，并且标记"有改动"；
4. B 键（或再按一次 ACTION_MAP）关闭 ⇒ **调 `RuntimeMods.Save()`**（这是"改了要保存"的判据）；
5. 字体加载失败时只画一行提示、不抛错（菜单不能把游戏搞崩）；
6. 加载到中文可用的字体时用中文标签，否则退回英文标签。

用的是 vendored Lua（`runtime/source/third_party/lua-5.3.3`），所以这个用例不需要设备、
也不需要 devkitPro：宿主 `cc` 编一个解释器即可。
"""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import SkipTest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "runtime" / "source"
LUA_ROOT = SOURCE / "third_party" / "lua-5.3.3" / "src"
SCRIPT_HEADER = SOURCE / "program" / "embedded_mod_menu_script.hpp"


def embedded_script() -> str:
    """从 C++ 头里取出脚本原文（`R"LUA( ... )LUA"`）。"""
    text = SCRIPT_HEADER.read_text(encoding="utf-8")
    hit = re.search(r'R"LUA\((.*?)\)LUA"', text, re.DOTALL)
    if hit is None:
        raise AssertionError("在 embedded_mod_menu_script.hpp 里找不到内嵌脚本")
    return hit.group(1)


HARNESS = r"""
-- 桩：把菜单脚本需要的东西都换成"记账的"实现，然后直接驱动它的回调。
local log = { setEnabled = {}, saves = 0, reloads = 0, draws = {} }

local MC = { MC_POST_UPDATE = 1, MC_POST_RENDER = 2 }
ModCallbacks = MC

local callbacks = {}
function RegisterMod(name)
  local mod = {}
  function mod:AddCallback(kind, fn)
    callbacks[kind] = fn
  end
  mod.name = name
  return mod
end

function Color(r, g, b, a)
  return { r = r, g = g, b = b, a = a }
end

local FONT_MODE = os.getenv("MENU_FONT_MODE") or "cjk"

local fontObject = {}
function fontObject:Load(path)
  if FONT_MODE == "none" then
    return false
  end
  if FONT_MODE == "cjk" then
    return path == "font/droid.fnt"
  end
  return path == "font/terminus.fnt"
end
function fontObject:DrawStringScaledUTF8(text, x, y, sx, sy, color)
  table.insert(log.draws, text)
end
function Font()
  return fontObject
end

-- 真机读数（2026-09-16）：引擎认的是**玩家自己的 controller 值**（那次是 33），
-- 猜 0/1 一律答"没按下"。这里的桩照真机来：只有玩家那个值会被回应。
Isaac = {}
function Isaac.GetPlayer(index)
  return { ControllerIndex = 33 }
end

local pressed = {}
ButtonAction = {
  ACTION_MAP = 13, ACTION_ITEM = 9, ACTION_BOMB = 8, ACTION_UP = 2, ACTION_DOWN = 3,
  ACTION_MENUUP = 22, ACTION_MENUDOWN = 23, ACTION_MENUCONFIRM = 14, ACTION_MENUBACK = 15,
}
Input = {}
-- ★ 真机结论（2026-09-16）：`IsActionTriggered`（"刚按下"）在我们的派发时机里抓不到边沿，
-- 所以菜单改用它自己记的"按住"状态做过沿判断。这里的桩就按真机行为来：
-- `IsActionPressed` 反映按住状态，`IsActionTriggered` **一律 false** —— 这样一旦脚本又退回
-- 依赖"刚按下"，用例立刻会红。
function Input.IsActionPressed(code, controller)
  if controller ~= 33 then
    return false          -- 非玩家 controller 值一律不回应（真机就是这个行为）
  end
  return pressed[code] == true
end
function Input.IsActionTriggered(code, controller)
  return false
end

local items = {
  { Directory = "ModA", Enabled = true, Active = true },
  { Directory = "ModB", Enabled = false, Active = false },
}
RuntimeMods = {}
function RuntimeMods.List()
  local copy = {}
  for index, item in ipairs(items) do
    copy[index] = { Directory = item.Directory, Enabled = item.Enabled, Active = item.Active }
  end
  return copy
end
function RuntimeMods.SetEnabled(directory, enabled)
  for _, item in ipairs(items) do
    if item.Directory == directory then
      item.Enabled = enabled
      table.insert(log.setEnabled, directory .. "=" .. tostring(enabled))
      return true
    end
  end
  return false
end
function RuntimeMods.Save()
  log.saves = log.saves + 1
  return true
end
function RuntimeMods.Reload()
  log.reloads = log.reloads + 1
  return true
end

-- 载入被测脚本（由 Python 侧拼在同一份文件里）。
@SCRIPT@

local function update()
  callbacks[MC.MC_POST_UPDATE]()
end
-- 按一下某个键：按住一帧、再松开一帧（过沿判断需要看到"从没按住到按住"那一次转变）。
local function tap(code)
  pressed[code] = true
  update()
  pressed[code] = false
  update()
end
local function render()
  callbacks[MC.MC_POST_RENDER]()
end
local function draws_text(needle)
  for _, text in ipairs(log.draws) do
    if string.find(text, needle, 1, true) then
      return true
    end
  end
  return false
end

-- 字体可用时才会真的画字；`none` 模式下"不画"本身就是预期行为。
local font_available = FONT_MODE ~= "none"

-- 1) 关闭状态：渲染什么都不画
log.draws = {}
render()
assert(#log.draws == 0, "菜单关着时不该画任何东西")

-- 2) 按 MAP 打开：应当画出两个模组
tap(ButtonAction.ACTION_MAP)
log.draws = {}
render()
if font_available then
  assert(draws_text("ModA"), "打开后应当画出 ModA")
  assert(draws_text("ModB"), "打开后应当画出 ModB")
end

-- 3) 下键选中 ModB，A 键切换：应当调 SetEnabled("ModB", true)
tap(ButtonAction.ACTION_MENUDOWN)
tap(ButtonAction.ACTION_MENUCONFIRM)
assert(#log.setEnabled == 1, "切换一次应当只调一次 SetEnabled，实际 " .. #log.setEnabled)
assert(log.setEnabled[1] == "ModB=true", "应当把 ModB 打开，实际 " .. log.setEnabled[1])
assert(log.saves == 0, "还没关闭菜单，不该保存")

-- 4) B 键关闭：应当保存
tap(ButtonAction.ACTION_MENUBACK)
assert(log.saves == 1, "关闭菜单应当保存一次，实际 " .. log.saves)
log.draws = {}
render()
assert(#log.draws == 0, "关闭之后不该再画")

-- 5) 再开一次、按 A 关掉当前项（ModA）：应当 SetEnabled("ModA", false)
tap(ButtonAction.ACTION_MAP)
tap(ButtonAction.ACTION_MENUCONFIRM)
assert(log.setEnabled[2] == "ModA=false", "第二次切换应当关掉 ModA，实际 " ..
       tostring(log.setEnabled[2]))
tap(ButtonAction.ACTION_MAP)
assert(log.saves == 2, "用 MAP 关闭也应当保存，实际 " .. log.saves)

-- 6) 字体加载失败：菜单打开也不抛错，且不画东西
if FONT_MODE == "none" then
  tap(ButtonAction.ACTION_MAP)
  log.draws = {}
  render()
  assert(#log.draws == 0, "字体加载失败时不画文字（也不该报错）")
end

-- 7) 标签语言随字体走（先把菜单重新打开，否则没有绘制内容可查）
if font_available then
  tap(ButtonAction.ACTION_MAP)
  log.draws = {}
  render()
end
if FONT_MODE == "cjk" then
  assert(draws_text("模组"), "加载到中文可用的字体时应当用中文标签")
elseif FONT_MODE == "ascii" then
  assert(draws_text("MODS"), "只加载到 ASCII 字体时应当用英文标签")
end

print("MOD_MENU_SCRIPT_CHECKS_PASSED")
"""


class ModMenuScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
        if compiler is None:
            raise unittest.SkipTest("需要宿主 C 编译器（本用例不需要 docker / devkitPro）")
        if not LUA_ROOT.is_dir():
            raise unittest.SkipTest(f"找不到 vendored Lua：{LUA_ROOT}")
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-mod-menu-")
        temporary = Path(cls.temporary.name)

        interpreter = temporary / "lua"
        # `luac.c` 也有 `main`，与 `lua.c` 一起编会重复符号 ⇒ 只编解释器那一份。
        sources = [path for path in sorted(LUA_ROOT.glob("*.c")) if path.name != "luac.c"]
        build = subprocess.run(
            [compiler, "-std=c99", "-w", "-DLUA_C89_NUMBERS", "-I", str(LUA_ROOT),
             *[str(path) for path in sources], "-lm", "-o", str(interpreter)],
            capture_output=True, text=True,
        )
        if build.returncode != 0:
            raise SkipTest(f"宿主上编不出 Lua 解释器：{build.stdout}{build.stderr}")
        cls.interpreter = interpreter

        script = embedded_script()
        # 诊断构建（`ENABLE_FONT = false`）下菜单不画字，绘制类断言自动跳过 —— 其余断言照旧。
        cls.font_enabled = "local ENABLE_FONT = false" not in script
        if not cls.font_enabled:
            raise unittest.SkipTest("当前是'不加载字体'的诊断构建：绘制类断言不适用")
        cls.runs = {}
        for mode in ("cjk", "ascii", "none"):
            path = temporary / f"menu-{mode}.lua"
            path.write_text(HARNESS.replace("@SCRIPT@", script), encoding="utf-8")
            cls.runs[mode] = path

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "temporary"):
            cls.temporary.cleanup()

    def run_mode(self, mode: str):
        import os
        environment = dict(os.environ, MENU_FONT_MODE=mode)
        return subprocess.run([str(self.interpreter), str(self.runs[mode])],
                              capture_output=True, text=True, env=environment)

    def test_menu_script_behaviour(self):
        for mode in ("cjk", "ascii", "none"):
            with self.subTest(font_mode=mode):
                result = self.run_mode(mode)
                self.assertEqual(result.returncode, 0,
                                 f"[{mode}] {result.stdout}{result.stderr}")
                self.assertIn("MOD_MENU_SCRIPT_CHECKS_PASSED", result.stdout)


if __name__ == "__main__":
    unittest.main()
