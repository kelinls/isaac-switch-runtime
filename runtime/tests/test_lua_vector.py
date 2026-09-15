"""`Vector` 值类型的行为测试（宿主可跑，不需要任何原生入口）。

`Vector` 是 `KAGE::Math::Vector2` 的 Lua 投影：8 字节 `{float X, float Y}`、无虚表。整族都是纯算术，
所以这里用一个真实 Lua 状态跑脚本，把 PC 文档里的每条语义都断言一遍——构造、字段读写、
`Zero`/`One`/`FromAngle`、运算符、以及全部方法。原生侧没有可断言的入口，因此这个用例的全部证据
就是"脚本跑通且每一步的数值都对"。
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    from .test_support import layered_lua_runtime_sources
except ImportError:
    from test_support import layered_lua_runtime_sources


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"


HARNESS = r'''
#include "lua_runtime.hpp"
#include "game_file_reader.hpp"
#include "game_observer.hpp"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

// Observation seams owned by other families: the Lua runtime links them, so the harness answers
// "unavailable" exactly like the other Lua runtime tests do.
GameIsPausedObservation ObserveGameIsPaused(uintptr_t, uintptr_t) {
    return GameIsPausedObservation::ThunkUnavailable;
}
GameLevelStageObservation ReadCurrentGameLevelStage(uintptr_t, std::uint32_t*) {
    return GameLevelStageObservation::GameUnreadable;
}
GameIsGreedModeObservation ObserveGameIsGreedMode(uintptr_t, uintptr_t) {
    return GameIsGreedModeObservation::MethodUnavailable;
}
GameIsAscentObservation ObserveLevelIsAscent(uintptr_t, uintptr_t) {
    return GameIsAscentObservation::MethodUnavailable;
}
GameItemPoolObservation ReadCurrentGameItemPool(uintptr_t, void**) {
    return GameItemPoolObservation::GameUnreadable;
}
GameRoomObservation ReadCurrentGameRoom(uintptr_t, void**) {
    return GameRoomObservation::RoomUnreadable;
}
GameRoomObservation ReadCurrentGameRoomType(uintptr_t, std::uint32_t*) {
    return GameRoomObservation::RoomUnreadable;
}
namespace GameFileReader {
TextReadResult ReadTextFile(const Bindings&, const char*, u8*, std::size_t, std::size_t*) {
    return TextReadResult::OpenFailed;
}
}

namespace {
const char* kVectorScript = R"lua(@VECTOR_SCRIPT@)lua";
const char* kCatalogScript = R"lua(@CATALOG_SCRIPT@)lua";
} // namespace

int main(int argc, char** argv) {
    if (argc != 2) return 90;
    const char* script = nullptr;
    const char* chunk = "@vector.lua";
    if (std::strcmp(argv[1], "vector") == 0) {
        script = kVectorScript;
    } else if (std::strcmp(argv[1], "catalog") == 0) {
        script = kCatalogScript;
        chunk = "@catalog.lua";
    } else {
        return 91;
    }
    const auto result = LuaRuntime::InitializeFromBuffer(script, std::strlen(script), chunk);
    if (result != LuaRuntime::LuaInitResult::Success) return 2;
    LuaRuntime::DispatchPostUpdate();
    if (LuaRuntime::TakeCallbackError()) return 3;
    std::printf("VECTOR_OK\n");
    return 0;
}
'''


# 每条断言都在 Lua 里做：数值不符就 error()，宿主看到的就是非 0 退出码 + 具体哪个 error。
VECTOR_SCRIPT = r'''
local mod = RegisterMod('Vector', 1)

local function near(actual, expected, label)
  if math.abs(actual - expected) > 0.0005 then
    error(label .. ': expected ' .. expected .. ', got ' .. actual)
  end
end

mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function()
  local ok, err = pcall(function()
  -- 构造与字段
  local v = Vector(3, 4)
  near(v.X, 3, 'X')
  near(v.Y, 4, 'Y')
  if type(v:Length()) ~= 'number' then error('Length must be a number') end
  near(v:Length(), 5, 'Length')
  near(v:LengthSquared(), 25, 'LengthSquared')
  -- **运算符必须能被显式方法调用点到**（2026-09-12，真机报告 `01789211254`）。
  --
  -- Lua 的 `__sub` 等只在表达式里由 VM 查元表；`a:__sub(b)` 是一次普通的方法索引。PC 的
  -- `Vector` 两种写法都用，EID 就是：`main.lua:1475` 的 `entity.Position:__sub(sourcePos)`
  -- 与 `main.lua:1479` 的 `diff + entityDataRenderOffset` 同时出现。`__index` 里看不见
  -- 运算符时，前者是 "attempt to call a nil value"，整段描述渲染直接中断。
  do
    local a = Vector(5, 7)
    local b = Vector(2, 3)
    local diff = a:__sub(b)
    near(diff.X, 3, 'a:__sub(b).X')
    near(diff.Y, 4, 'a:__sub(b).Y')
    local sum = a:__add(b)
    near(sum.X, 7, 'a:__add(b).X')
    near(sum.Y, 10, 'a:__add(b).Y')
    -- 显式方法调用与表达式写法必须是同一个结果（本来就是同一份实现）。
    local expression = a - b
    if diff.X ~= expression.X or diff.Y ~= expression.Y then
      error('Vector:__sub must agree with the - operator')
    end
    -- `diff:Length()` 是 EID 紧跟着用的下一句（`main.lua:1478`）。
    near(diff:Length(), 5, 'diff:Length()')
  end


  -- 字段可写（PC 把 X/Y 作为可写变量）
  v.X = 6
  v.Y = 8
  near(v:Length(), 10, 'Length after assignment')

  -- 类表成员：Zero / One / FromAngle 每次读取都是新对象
  local zero = Vector.Zero
  local one = Vector.One
  near(zero.X, 0, 'Zero.X'); near(zero.Y, 0, 'Zero.Y')
  near(one.X, 1, 'One.X'); near(one.Y, 1, 'One.Y')
  zero.X = 99
  near(Vector.Zero.X, 0, 'Vector.Zero must not be mutated by a Mod')
  local fromAngle = Vector.FromAngle(90)
  near(fromAngle.X, 0, 'FromAngle(90).X'); near(fromAngle.Y, 1, 'FromAngle(90).Y')
  near(Vector.FromAngle(0).X, 1, 'FromAngle(0).X')

  -- 运算符
  local sum = Vector(1, 2) + Vector(3, 4)
  near(sum.X, 4, 'add.X'); near(sum.Y, 6, 'add.Y')
  local difference = Vector(5, 7) - Vector(1, 2)
  near(difference.X, 4, 'sub.X'); near(difference.Y, 5, 'sub.Y')
  local scaled = Vector(2, 3) * 5
  near(scaled.X, 10, 'mul.X'); near(scaled.Y, 15, 'mul.Y')
  local elementwise = Vector(2, 3) * Vector(5, 2)
  near(elementwise.X, 10, 'mulv.X'); near(elementwise.Y, 6, 'mulv.Y')
  local divided = Vector(6, 4) / 2
  near(divided.X, 3, 'div.X'); near(divided.Y, 2, 'div.Y')
  local negated = -Vector(1, -2)
  near(negated.X, -1, 'unm.X'); near(negated.Y, 2, 'unm.Y')
  if Vector(1, 2) ~= Vector(1, 2) then error('equal vectors compare unequal') end
  if Vector(1, 2) == Vector(1, 3) then error('different vectors compare equal') end
  if Vector(1, 2) == 5 then error('a Vector must not equal a number') end
  -- 形状按 PC 文档 `Vector(X,Y)`；精度用 %g（PC 未规定，见 vector_api.cpp 注释）。
  if tostring(Vector(1, 2)) ~= 'Vector(1,2)' then
    error('unexpected tostring: ' .. tostring(Vector(1, 2)))
  end

  -- 方法
  near(Vector(2, 0):Distance(Vector(4, 0)), 2, 'Distance')
  near(Vector(2, 0):DistanceSquared(Vector(4, 0)), 4, 'DistanceSquared')
  near(Vector(1, 2):Dot(Vector(3, 4)), 11, 'Dot')
  near(Vector(1, 2):Cross(Vector(3, 4)), 1 * 4 - 2 * 3, 'Cross')
  near(Vector(0, 1):GetAngleDegrees(), 90, 'GetAngleDegrees down')
  near(Vector(1, 0):GetAngleDegrees(), 0, 'GetAngleDegrees right')
  near(Vector(3, 4):Normalized():Length(), 1, 'Normalized length')
  near(Vector(3, 4):Normalized().X, 0.6, 'Normalized.X')
  near(Vector(3, 4):Resized(10):Length(), 10, 'Resized length')
  local inPlace = Vector(3, 4)
  inPlace:Normalize()
  near(inPlace.X, 0.6, 'Normalize.X')
  inPlace:Resize(10)
  near(inPlace:Length(), 10, 'Resize length')
  local rotated = Vector(1, 0):Rotated(90)
  near(rotated.X, 0, 'Rotated.X'); near(rotated.Y, 1, 'Rotated.Y')
  local clamped = Vector(10, -10):Clamped(0, 0, 5, 5)
  near(clamped.X, 5, 'Clamped.X'); near(clamped.Y, 0, 'Clamped.Y')
  local clampedInPlace = Vector(10, -10)
  clampedInPlace:Clamp(0, 0, 5, 5)
  near(clampedInPlace.X, 5, 'Clamp.X'); near(clampedInPlace.Y, 0, 'Clamp.Y')
  local lerped = Vector(0, 0)
  lerped:Lerp(Vector(1, 1), 0.25)
  near(lerped.X, 0.25, 'Lerp.X'); near(lerped.Y, 0.25, 'Lerp.Y')
  local zeroLength = Vector(0, 0)
  zeroLength:Normalize()
  near(zeroLength.X, 0, 'Normalize of a zero vector stays zero')

  -- 错误路径：参数类型不对、字段名不对都必须报错，而不是静默给出错误结果
  local bad = {
    function() return Vector('x', 1) end,
    function() return Vector(1) end,
    function() return Vector(1, 2, 3) end,
    function() return Vector(1, 2):Length(1) end,
    function() return Vector(1, 2):Dot(1) end,
    function() return Vector(1, 2):Resized('x') end,
    function() return Vector(1, 2):Clamp(1, 2, 3) end,
    function() return Vector(1, 2):Lerp(Vector(0, 0)) end,
    function() v = Vector(1, 2); v.Z = 3 end,
    function() v = Vector(1, 2); v.X = 'x' end,
    function() return Vector.FromAngle() end,
  }
  for index, call in ipairs(bad) do
    if pcall(call) then error('expected an error from bad call #' .. index) end
  end
  end)
  if not ok then print('VECTOR_ERROR: ' .. tostring(err)) end
end)
'''


CATALOG_SCRIPT = r'''
local mod = RegisterMod('Vector catalog', 1)
mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function()
  local ok, err = pcall(function()
  if type(Vector) ~= 'table' then error('Vector must be a callable table, got ' .. type(Vector)) end
  local v = Vector(1, 2)
  if type(v) ~= 'userdata' then error('Vector(...) must return userdata, got ' .. type(v)) end
  for _, name in ipairs({'Length', 'LengthSquared', 'Distance', 'DistanceSquared', 'Dot', 'Cross',
                         'GetAngleDegrees', 'Normalize', 'Normalized', 'Resize', 'Resized',
                         'Rotated', 'Clamp', 'Clamped', 'Lerp'}) do
    if type(v[name]) ~= 'function' then error('missing method: ' .. name) end
  end
  if type(Vector.FromAngle) ~= 'function' then error('missing Vector.FromAngle') end
  if type(Vector.Zero) ~= 'userdata' or type(Vector.One) ~= 'userdata' then
    error('Vector.Zero/One must be vectors')
  end
  end)
  if not ok then print('VECTOR_ERROR: ' .. tostring(err)) end
end)
'''


class LuaVectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-lua-vector-")
        temporary = Path(cls.temporary.name)
        compatibility = temporary / "compatibility"
        compatibility.mkdir()
        (compatibility / "stdfloat").write_text(
            "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n"
        )
        harness = temporary / "lua_vector_harness.cpp"
        harness.write_text(
            HARNESS.replace("@VECTOR_SCRIPT@", VECTOR_SCRIPT)
            .replace("@CATALOG_SCRIPT@", CATALOG_SCRIPT)
            .lstrip()
        )
        lua_root = SOURCE / "third_party/lua-5.3.3/src"
        excluded = {"lua.c", "luac.c", "liolib.c", "loslib.c", "loadlib.c", "ldblib.c", "linit.c"}
        lua_objects = []
        for lua_source in sorted(lua_root.glob("*.c")):
            if lua_source.name in excluded:
                continue
            output = temporary / f"{lua_source.stem}.o"
            build = subprocess.run(
                ["cc", "-std=c99", "-w", "-DLUA_C89_NUMBERS", "-I", str(lua_root), "-c",
                 str(lua_source), "-o", str(output)], text=True, capture_output=True,
            )
            if build.returncode != 0:
                raise AssertionError(build.stdout + build.stderr)
            lua_objects.append(output)
        cls.harness = temporary / "lua_vector_harness"
        build = subprocess.run(
            ["c++", "-std=c++23", "-Wall", "-Wextra", "-Werror", "-DLUA_C89_NUMBERS",
             "-DEXL_LAYERED_RUNTIME=1", "-DEXL_LOAD_KIND=Module",
             "-DEXL_LOAD_KIND_ENUM=2", "-DEXL_PROGRAM_ID=0",
             "-I", str(compatibility), "-I", str(SOURCE), "-I", str(SOURCE.parent / "src"),
             "-I", str(lua_root), str(harness),
             *(str(path) for path in layered_lua_runtime_sources(SOURCE)),
             *map(str, lua_objects), "-o", str(cls.harness)],
            text=True, capture_output=True,
        )
        if build.returncode != 0:
            raise AssertionError(build.stdout + build.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_scenario(self, scenario):
        result = subprocess.run([str(self.harness), scenario], text=True, capture_output=True)
        self.assertEqual(
            result.returncode, 0,
            f"scenario {scenario} exited with {result.returncode}\n{result.stdout}{result.stderr}",
        )
        self.assertNotIn("VECTOR_ERROR", result.stdout)
        self.assertIn("VECTOR_OK", result.stdout)

    def test_vector_semantics_match_the_pc_documentation(self):
        self.run_scenario("vector")

    def test_vector_family_is_registered_with_every_documented_member(self):
        self.run_scenario("catalog")


if __name__ == "__main__":
    unittest.main()
