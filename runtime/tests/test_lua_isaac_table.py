"""`Isaac` 门面与 `Options` 表的行为测试（纯宿主可跑，不需要 docker，也不需要任何原生入口）。

这一族的定位是"加载地基"：PC Mod（EID 的第一句就是 `Isaac.GetItemConfig()`）在加载阶段
会先摸这些 API，本轮必须保证它们**存在、成员齐全、被调用时不产生 Lua 错误**。

因此这个用例断言三件事：

1. 表面齐全：`Isaac` 19 个成员逐个点名、`Options` 两个字段的类型与默认值；
2. 有真实行为的成员真的在做事：帧计数随 `MC_POST_UPDATE` 派发单调递增、每帧正好加一、
   `GetTime() == GetFrameCount() // 30`（近似换算，逐帧校验）、
   `DebugString` 走 Lua `print` 的同一个通道、`IsInGame` 在托管回调里为真、
   `RunCallback` 参数原样透传且递归越界报错后深度能还回去；
3. 安全 stub 真的安全：全部用 `pcall` 包裹并断言成功、返回值是安全默认值，
   而且每个成员每会话只告警一次（第一帧每个 stub 恰好 1 行告警，第二帧 0 行）。

`GetTime()` 是**近似**：帧计数 / 30。PC 版返回引擎自己的游戏内计时，我们的引擎时钟
偏移尚未定位，所以这里只断言 Runtime 自己的换算关系，不声称与 PC 逐值一致。

数值口径：脚本把结论写进全局，C++ harness 用 `ReadLuaGlobalNumber` 逐条核对（并额外核对
`ManagedFrameClock::Count()` 确实每次派发加一），Python 侧再对 harness 打印出来的观测值
断言一次，避免"脚本什么都没做也算通过"。
"""

import shutil
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

# 宿主场景派发的帧数：够让 `GetTime()` 走完一秒（30 帧），近似换算就不只是在 0 上成立。
FRAMES = 31


HARNESS = r'''
#include "lua_runtime.hpp"
#include "game_file_reader.hpp"
#include "game_observer.hpp"
#include "interfaces/lua/isaac_api.hpp"

#include "application/callback/callback_dispatcher.hpp"

#include <cstdint>
#include <cstdio>
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

const char* kSurfaceScript = R"lua(@SURFACE_SCRIPT@)lua";
const char* kEidScript = R"lua(@EID_SCRIPT@)lua";
const char* g_HostLanguage = "zh";

// 读脚本写下的全局并核对；脚本侧的自检结果只有真的写下来才算数。
bool Expect(const char* name, double expected, const char* stage) {
    double value = 0.0;
    if (!LuaRuntime::ReadLuaGlobalNumber(name, &value)) {
        std::printf("ISAAC_FAIL %s: global %s was never written\n", stage, name);
        return false;
    }
    if (value != expected) {
        std::printf("ISAAC_FAIL %s: %s expected %.0f, got %.0f\n", stage, name, expected, value);
        return false;
    }
    return true;
}

int ReportScriptFailure(const char* stage) {
    // 回调里的 Lua 错误由派发器接住并保留回调，但"出过错"仍然应当让测试失败。
    std::printf("ISAAC_FAIL %s: the Lua callback raised an error\n", stage);
    return 1;
}

} // namespace

int main(int argc, char** argv) {
    if (argc != 2) return 90;
    const bool surface = std::strcmp(argv[1], "surface") == 0;
    const bool eid = std::strcmp(argv[1], "eid") == 0;
    if (!surface && !eid) return 91;
    const char* stage = surface ? "surface" : "eid";

    const char* script = surface ? kSurfaceScript : kEidScript;
    const char* chunk = surface ? "@isaac_surface.lua" : "@isaac_eid.lua";
    isaac::runtime::SetCurrentLanguageCodeHostImplementation([]() { return g_HostLanguage; });
    const auto result = LuaRuntime::InitializeFromBuffer(script, std::strlen(script), chunk);
    if (result != LuaRuntime::LuaInitResult::Success) return 2;

    // `Options.Language` 必须动态反映引擎语言，而不是在 Lua 初始化时缓存一次。
    if (eid) {
        g_HostLanguage = "en";
    }

    // Mod 用 Lua 表的 `MC_POST_UPDATE` 登记，Runtime 按自己的 id 派发：两者不一致时
    // 回调永远不会触发，而且现象是"脚本什么都没做"，所以这里先把这个前提单独核对一次。
    if (LuaRuntime::RegisteredCallbackCount(isaac::runtime::kCallbackPostUpdate) == 0) {
        std::printf("ISAAC_FAIL %s: the Mod's MC_POST_UPDATE registration did not land on the "
                    "Runtime's dispatch id\n", stage);
        return 1;
    }

    // 每次 `DispatchPostUpdate()` 就是一次 MC_POST_UPDATE 派发，也就是一帧。
    const int frames = surface ? @FRAMES@ : 1;
    for (int frame = 0; frame < frames; ++frame) {
        LuaRuntime::DispatchPostUpdate();
    }
    if (LuaRuntime::TakeCallbackError()) return ReportScriptFailure(stage);

    // 帧计数必须**恰好**每次派发加一：这是"帧计数真的挂在派发路径上"的宿主侧证据
    // （脚本侧的单调性/换算断言只有在计数确实推进时才有意义）。
    const auto clock = isaac::runtime::ManagedFrameClock::Count();
    if (clock != static_cast<std::uint64_t>(frames)) {
        std::printf("ISAAC_FAIL %s: frame clock expected %d, got %llu\n", stage, frames,
                    static_cast<unsigned long long>(clock));
        return 1;
    }

    if (eid) {
        if (!Expect("ISAAC_EID_OK", 1.0, stage)) return 1;
        std::printf("ISAAC_EID_OK\n");
        return 0;
    }

    if (!Expect("ISAAC_SURFACE_OK", 1.0, stage)) return 1;
    if (!Expect("ISAAC_FRAMES", static_cast<double>(frames), stage)) return 1;
    if (!Expect("ISAAC_MONOTONIC", 1.0, stage)) return 1;
    if (!Expect("ISAAC_FRAME_STEP_OK", 1.0, stage)) return 1;
    if (!Expect("ISAAC_TIME_OK", 1.0, stage)) return 1;
    if (!Expect("ISAAC_TIME_REACHED_ONE", 1.0, stage)) return 1;
    if (!Expect("ISAAC_FIRST_COUNT", 1.0, stage)) return 1;
    if (!Expect("ISAAC_LAST_COUNT", static_cast<double>(frames), stage)) return 1;
    if (!Expect("ISAAC_RUN_CALLBACK_OK", 1.0, stage)) return 1;
    if (!Expect("ISAAC_FIRST_FRAME_WARNINGS", 1.0, stage)) return 1;
    // 第二帧调用同样的 stub：同一会话里的告警不许重复。
    if (!Expect("ISAAC_SECOND_FRAME_WARNINGS", 0.0, stage)) return 1;
    std::printf("ISAAC_SURFACE_OK frames=%d first=1 last=%d clock=%llu run_callback=1 "
                "first_warnings=1 second_warnings=0\n",
                frames, frames, static_cast<unsigned long long>(clock));
    return 0;
}
'''


# 场景一：表面齐全 + 有真实行为的成员 + 安全 stub。断言全在 Lua 里做，宿主只核对全局。
SURFACE_SCRIPT = r'''
local mod = RegisterMod('Isaac surface', 1)

-- `DebugString` 与 stub 告警都走 Lua `print`，所以临时把 `print` 换成收集器：既验证
-- "同一个输出通道"，也验证"每个 stub 每会话只告警一次"。
local realPrint = print
local collected = {}
local function installCollector()
  collected = {}
  print = function(...)
    local parts = {}
    for index = 1, select('#', ...) do
      parts[#parts + 1] = tostring(select(index, ...))
    end
    collected[#collected + 1] = table.concat(parts, '\t')
  end
end
local function restorePrint()
  print = realPrint
end
local function countLines(needle)
  local total = 0
  for _, line in ipairs(collected) do
    if line:find(needle, 1, true) then total = total + 1 end
  end
  return total
end

local frames = 0
local firstCount = nil
local lastCount = nil
local lastSampled = nil
local monotonic = true
local frameStep = true
local timeMatchesFrames = true
local timeReachedOne = false

-- RunCallback 的目标：21 号自递归（验递归上限），22 号记录参数（验原样透传）。
local forwarded, forwardedNumber
mod:AddCallback(21, function()
  Isaac.RunCallback(21)
end)
mod:AddCallback(22, function(first, second)
  forwarded = first
  forwardedNumber = second
end)

-- 19 个成员逐个点名：表在但少一个成员必须失败，不能靠"能调用几个常用的"蒙混过关。
local ISAAC_MEMBERS = {
  'GetFrameCount', 'GetTime', 'DebugString', 'IsInGame', 'RunCallback',
  'GetPlayer', 'GetItemConfig', 'FindByType', 'FindInRadius',
  'CountEnemies', 'CountBosses', 'WorldToScreen', 'WorldToRenderPosition',
  'GetPersistentGameData', 'GetTrinketIdByName', 'GetCallbacks',
  'LoadModData', 'SaveModData', 'RenderScaledText',
}

-- 每个成员都必须"被调用时不报错"，返回值必须是这个 API 的**安全值**。
--
-- `Isaac.FindByType`/`FindInRadius`/`CountEnemies` 批次 4 起是**真实现**（房间实体容器遍历），
-- 不再属于 stub 告警名单：宿主 harness 没有发布引擎模块基址，所以它们这里返回的是"读不到容器"
-- 的空表/0 —— 与 stub 的返回值恰好同类，但语义完全不同（见下面的告警计数断言）。
local STUB_PROBES = {
  {name = 'Isaac.FindByType', call = function() return Isaac.FindByType(6, 16, -1, true, false) end,
   check = function(value) return type(value) == 'table' and next(value) == nil end,
   expected = 'an empty table'},
  {name = 'Isaac.FindInRadius',
   call = function() return Isaac.FindInRadius(Vector(0, 0), 100, 1) end,
   check = function(value) return type(value) == 'table' and next(value) == nil end,
   expected = 'an empty table'},
  {name = 'Isaac.CountEnemies', call = function() return Isaac.CountEnemies() end,
   check = function(value) return value == 0 end, expected = '0'},
  {name = 'Isaac.CountBosses', call = function() return Isaac.CountBosses() end,
   check = function(value) return value == 0 end, expected = '0'},
  {name = 'Isaac.GetPersistentGameData',
   call = function() return Isaac.GetPersistentGameData() end,
   check = function(value) return value == 0 end, expected = '0'},
  {name = 'Isaac.GetTrinketIdByName',
   call = function() return Isaac.GetTrinketIdByName('Swallowed Penny') end,
   check = function(value) return value == 0 end, expected = '0'},
  {name = 'Isaac.GetCallbacks',
   -- 批次 3 起这是真实现（字符串名 → 命名回调表；数字 id → 我们自己的回调注册表），
   -- 所以"空表"这条要用一个**没有登记过**的 id 来验：本脚本登记了 21/22 与
   -- MC_POST_UPDATE，MC_POST_RENDER(2) 没有登记者 → 空表。
   -- "登记过的 id 返回非空表"由 `test_lua_api_gap_batch3.py` 覆盖。
   call = function() return Isaac.GetCallbacks(ModCallbacks.MC_POST_RENDER) end,
   check = function(value) return type(value) == 'table' and next(value) == nil end,
   expected = 'an empty table'},
  {name = 'Isaac.LoadModData', call = function() return Isaac.LoadModData(mod) end,
   check = function(value) return value == nil end, expected = 'nil'},
  {name = 'Isaac.SaveModData', call = function() return Isaac.SaveModData(mod, 'x') end,
   check = function(value) return value == nil end, expected = 'nil'},
  {name = 'Isaac.RenderScaledText',
   call = function() return Isaac.RenderScaledText('x', 1, 2, 1, 1, 0, 0, 0, 1) end,
   check = function(value) return value == nil end, expected = 'nil'},
  {name = 'Isaac.WorldToScreen',
   call = function() return Isaac.WorldToScreen(Vector(3, 4)) end,
   check = function(value) return type(value) == 'userdata' end, expected = 'a Vector'},
  {name = 'Isaac.WorldToRenderPosition',
   call = function() return Isaac.WorldToRenderPosition(Vector(3, 4)) end,
   check = function(value) return type(value) == 'userdata' end, expected = 'a Vector'},
}

-- 采样函数：帧计数与 `GetTime()` 的逐帧断言都在这里做。
local function sample()
  local count = Isaac.GetFrameCount()
  -- 同一帧只采样一次：这个用例每帧只派发一次，闩锁是为了让"重复派发"这类改动不会
  -- 悄悄把帧断言变成"两帧一次"。
  if lastSampled == count then return end
  lastSampled = count
  frames = frames + 1
  local time = Isaac.GetTime()
  if firstCount == nil then firstCount = count end
  if lastCount ~= nil then
    if count < lastCount then monotonic = false end
    -- 每一次 MC_POST_UPDATE 派发正好加一帧：不是"大致增长"，是逐帧 +1。
    if count ~= lastCount + 1 then frameStep = false end
  end
  lastCount = count
  -- 近似换算逐帧成立：GetTime() 就是 GetFrameCount() // 30。
  if time ~= math.floor(count / 30) then timeMatchesFrames = false end
  if time >= 1 then timeReachedOne = true end
  ISAAC_FRAMES = frames
  ISAAC_FIRST_COUNT = firstCount
  ISAAC_LAST_COUNT = lastCount
  ISAAC_MONOTONIC = monotonic and 1 or 0
  ISAAC_FRAME_STEP_OK = frameStep and 1 or 0
  ISAAC_TIME_OK = timeMatchesFrames and 1 or 0
  ISAAC_TIME_REACHED_ONE = timeReachedOne and 1 or 0

  if frames == 2 then
    -- 第二帧再调同样的 stub：每会话只告警一次，所以这里必须一行告警都没有。
    installCollector()
    Isaac.CountBosses()
    Isaac.WorldToScreen(Vector(1, 1))
    ISAAC_SECOND_FRAME_WARNINGS = countLines('未实现：')
    restorePrint()
  end

  if frames == 1 then
    -- ===== 表存在且成员齐全 =====
    if type(Isaac) ~= 'table' then error('Isaac must be a table, got ' .. type(Isaac)) end
    if type(Options) ~= 'table' then error('Options must be a table, got ' .. type(Options)) end
    for _, name in ipairs(ISAAC_MEMBERS) do
      if type(Isaac[name]) ~= 'function' then error('missing Isaac.' .. name) end
    end
    -- `Options` 只有这两个字段（EID 用的就是它们）。
    if type(Options.HUDOffset) ~= 'number' then error('Options.HUDOffset must be a number') end
    if Options.HUDOffset ~= 1.0 then error('Options.HUDOffset must be the normalized 0..1 default') end
    if type(Options.Language) ~= 'string' then error('Options.Language must be a string') end
    if Options.Language ~= 'zh' then error('Options.Language must default to zh') end

    -- ===== 有真实行为的成员 =====
    if Isaac.IsInGame() ~= true then
      error('Isaac.IsInGame must be true inside a managed callback')
    end
    installCollector()
    Isaac.DebugString('isaac-debug-line')
    Isaac.DebugString('isaac-debug-line')
    if countLines('isaac-debug-line') ~= 2 then
      error('Isaac.DebugString must write every line to the same channel as print')
    end

    -- ===== 安全 stub：不报错 + 安全默认值 + 每会话只告警一次 =====
    for _, probe in ipairs(STUB_PROBES) do
      local callOk, value = pcall(probe.call)
      if not callOk then
        error(probe.name .. ' must not raise a Lua error: ' .. tostring(value))
      end
      if not probe.check(value) then
        error(probe.name .. ' must return ' .. probe.expected)
      end
    end
    for _, probe in ipairs(STUB_PROBES) do
      local callOk = pcall(probe.call)
      if not callOk then error(probe.name .. ' must not raise on the second call') end
    end
    -- 真实现（不再有 "未实现" 告警）：批次 3 的 `Isaac.GetCallbacks`，批次 4 的房间实体查询
    -- 三件套。"不报错 + 安全值"由上面的 `check` 覆盖；这里钉的是"它们不再自报未实现"
    -- （否则真机的"容器读不到"会被误导成"这个 API 还是 stub"）。
    local IMPLEMENTED = {
      ['Isaac.GetCallbacks'] = true,
      ['Isaac.FindByType'] = true,
      ['Isaac.FindInRadius'] = true,
      ['Isaac.CountEnemies'] = true,
    }
    for _, probe in ipairs(STUB_PROBES) do
      local warnings = countLines('未实现：' .. probe.name .. '（')
      local expected = IMPLEMENTED[probe.name] and 0 or 1
      if warnings ~= expected then
        error(probe.name .. ' must warn exactly ' .. expected .. ' time(s), warned '
              .. warnings .. ' time(s)')
      end
    end
    ISAAC_FIRST_FRAME_WARNINGS = 1

    -- 传进去的不是 Vector 时必须返回 nil，而不是假装算出一个坐标。
    if Isaac.WorldToScreen(42) ~= nil then
      error('Isaac.WorldToScreen must return nil for a non-Vector argument')
    end
    if Isaac.WorldToRenderPosition('nope') ~= nil then
      error('Isaac.WorldToRenderPosition must return nil for a non-Vector argument')
    end

    -- `Isaac.GetPlayer` 批次 2 起是真实现（读引擎内存），所以它不在 stub 名单里：宿主 harness
    -- 没有发布引擎模块基址（`SetEngineModuleBase` 由真机 Hook 安装阶段调用），此时它必须
    -- 安静地返回 nil —— 既不报 Lua 错误，也不留 "未实现" 告警（那会把"引擎基址没发布"
    -- 误导成"这个 API 还是 stub"）。
    local playerOk, playerValue = pcall(function() return Isaac.GetPlayer(0) end)
    if not playerOk then
      error('Isaac.GetPlayer must not raise a Lua error: ' .. tostring(playerValue))
    end
    if playerValue ~= nil then
      error('Isaac.GetPlayer must return nil while the engine module base is unpublished')
    end
    -- 越界/负数/非整数同样只返回 nil。
    if Isaac.GetPlayer(-1) ~= nil then error('Isaac.GetPlayer(-1) must be nil') end
    if Isaac.GetPlayer(99) ~= nil then error('Isaac.GetPlayer(99) must be nil') end
    if Isaac.GetPlayer('nope') ~= nil then error('Isaac.GetPlayer must be nil for a non-integer index') end
    if countLines('未实现：Isaac.GetPlayer（') ~= 0 then
      error('Isaac.GetPlayer must not warn as an unimplemented stub any more')
    end

    -- `Isaac.GetItemConfig` 批次 2b 起也是真实现（`IC = Manager + 0x36538` 的内嵌对象），
    -- 同样不在 stub 名单里：宿主 harness 没有发布引擎模块基址，所以它必须安静地返回 nil ——
    -- 既不报 Lua 错误、也不留 "未实现" 告警（那会把"基址拿不到"误导成"这个 API 还是 stub"）。
    local configOk, configValue = pcall(function() return Isaac.GetItemConfig() end)
    if not configOk then
      error('Isaac.GetItemConfig must not raise a Lua error: ' .. tostring(configValue))
    end
    if configValue ~= nil then
      error('Isaac.GetItemConfig must return nil while the engine module base is unpublished')
    end
    if countLines('未实现：Isaac.GetItemConfig（') ~= 0 then
      error('Isaac.GetItemConfig must not warn as an unimplemented stub any more')
    end
    restorePrint()

    -- ===== RunCallback =====
    ISAAC_RUN_CALLBACK_OK = 0
    -- 参数原样透传（22 号 id 没有派发点，只会被 RunCallback 调到）。
    Isaac.RunCallback(22, 'forwarded', 42)
    if forwarded ~= 'forwarded' or forwardedNumber ~= 42 then
      error('Isaac.RunCallback must forward the extra arguments as-is')
    end
    -- 没有注册者：安静返回。
    local silent = pcall(function() Isaac.RunCallback(23) end)
    if not silent then error('Isaac.RunCallback without registrations must be silent') end
    -- 递归上限：越界报错……
    local recursed, recursionError = pcall(function() Isaac.RunCallback(21) end)
    if recursed then error('Isaac.RunCallback must stop recursion at its nesting limit') end
    if not tostring(recursionError):find('nesting') then
      error('unexpected recursion error: ' .. tostring(recursionError))
    end
    -- ……而且出错之后深度必须已经还回去：泄漏的话下面这一次会被上限挡住。
    forwarded = nil
    Isaac.RunCallback(22, 'again', 7)
    if forwarded ~= 'again' then
      error('Isaac.RunCallback must restore its nesting depth after an error')
    end
    ISAAC_RUN_CALLBACK_OK = 1
    ISAAC_SURFACE_OK = 1
  end
end

-- 采样函数登记在 Lua 表的 `MC_POST_UPDATE` 上（Mod 的唯一可见写法）。帧计数只认 Runtime
-- 内部那一个 id，所以"Lua 表的值"与"内部 id"必须一致——harness 会先核对这一点再派发。
mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function()
  local ok, err = pcall(sample)
  if not ok then
    ISAAC_SURFACE_OK = 0
    realPrint('ISAAC_ERROR: ' .. tostring(err))
  end
end)
'''


# 场景二：EID 的加载序列。EID 的 `main.lua` 第 37 行就是 `Isaac.GetItemConfig()`，
# 第 1248 行用 `Options.HUDOffset` 做算术，之后用 `Isaac.FindByType` 遍历实体集合。
EID_SCRIPT = r'''
local mod = RegisterMod('EID load order', 1)

-- EID main.lua 的加载阶段（在注册回调之前，也就是"脚本顶层的调用"）。
local languageAtLoad = Options.Language
local itemConfig = Isaac.GetItemConfig()
local hudOffsetPosition = Vector(20, 12) * (Options.HUDOffset - 1) + Vector(0, 3)

local done = false
local function verify()
  if done then return end
  done = true
  local ok, err = pcall(function()
    -- 宿主 harness 没有发布引擎模块基址，所以 `GetItemConfig` 在这里是 nil —— 不是断言
    -- "本轮还没实现"，而是断言 "基址拿不到时不报错、返回 nil"；真机路径（内嵌 ItemConfig、
    -- 733 项收藏品向量、libc++ SSO 名字）由 `test_lua_item_config.py` 的伪造引擎内存覆盖。
    if itemConfig ~= nil then error('Isaac.GetItemConfig must be nil without an engine base') end
    -- EID main.lua 第 1248 行的表达式：Vector(20, 12) * (Options.HUDOffset - 1) + Vector(0, 3)
    -- HUDOffset = 1.0 时结果是 (0, 3)；这里同时锁定类型与 EID 的算术输入。
    if hudOffsetPosition.X ~= 0 or hudOffsetPosition.Y ~= 3 then
      error('Options.HUDOffset must be usable in arithmetic, got ' ..
            tostring(hudOffsetPosition.X) .. ',' .. tostring(hudOffsetPosition.Y))
    end
    if languageAtLoad ~= 'zh' then error('Options.Language must be zh during script load') end
    -- Harness 在初始化完成后把宿主语言切到 en；动态属性必须在回调里读到新值。
    if Options.Language ~= 'en' then error('Options.Language must follow a language change to en') end
    for _, entity in ipairs(Isaac.FindByType(6, 16, -1, true, false)) do
      error('Isaac.FindByType must return an empty collection, saw ' .. tostring(entity))
    end
    -- 宿主 harness 没有发布引擎模块基址，所以 `GetPlayer` 在这里同样是 nil（不是断言
    -- "本轮还没实现"，而是断言"基址拿不到时不报错、返回 nil"；真机路径由
    -- `test_lua_entity_player.py` 的伪造引擎内存覆盖）。
    if Isaac.GetPlayer(0) ~= nil then error('Isaac.GetPlayer must be nil without an engine base') end
    if Isaac.GetTime() < 0 then error('Isaac.GetTime must not be negative') end
    if Isaac.IsInGame() ~= true then error('Isaac.IsInGame must be true in a callback') end
    ISAAC_EID_OK = 1
  end)
  if not ok then
    ISAAC_EID_OK = 0
    print('ISAAC_ERROR: ' .. tostring(err))
  end
end

-- 与场景一同一个理由：登记在 Lua 表的 `MC_POST_UPDATE` 上，`done` 保证只跑一次。
mod:AddCallback(ModCallbacks.MC_POST_UPDATE, verify)
'''


def host_compilers() -> tuple[str, str] | None:
    """(C 编译器, C++ 编译器)；缺任何一个就整体 skip，不伪装成通过。"""
    cc = shutil.which("cc")
    if cc is None:
        return None
    for candidate in ("c++", "clang++", "g++"):
        found = shutil.which(candidate)
        if found is not None:
            return cc, found
    return None


class LuaIsaacTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compilers = host_compilers()
        if compilers is None:
            raise unittest.SkipTest("需要宿主 C/C++ 编译器（本用例不需要 docker）")
        cc, cxx = compilers
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-lua-isaac-")
        temporary = Path(cls.temporary.name)
        compatibility = temporary / "compatibility"
        compatibility.mkdir()
        (compatibility / "stdfloat").write_text(
            "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n"
        )
        harness = temporary / "lua_isaac_harness.cpp"
        harness.write_text(
            HARNESS.replace("@SURFACE_SCRIPT@", SURFACE_SCRIPT)
            .replace("@EID_SCRIPT@", EID_SCRIPT)
            .replace("@FRAMES@", str(FRAMES))
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
                [cc, "-std=c99", "-w", "-DLUA_C89_NUMBERS", "-I", str(lua_root), "-c",
                 str(lua_source), "-o", str(output)], text=True, capture_output=True,
            )
            if build.returncode != 0:
                raise AssertionError(build.stdout + build.stderr)
            lua_objects.append(output)
        cls.harness = temporary / "lua_isaac_harness"
        build = subprocess.run(
            [cxx, "-std=c++23", "-Wall", "-Wextra", "-Werror", "-DLUA_C89_NUMBERS",
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
        if hasattr(cls, "temporary"):
            cls.temporary.cleanup()

    def run_scenario(self, scenario: str) -> subprocess.CompletedProcess:
        result = subprocess.run([str(self.harness), scenario], text=True, capture_output=True)
        self.assertEqual(
            result.returncode, 0,
            f"scenario {scenario} exited with {result.returncode}\n{result.stdout}{result.stderr}",
        )
        self.assertNotIn("ISAAC_ERROR", result.stdout + result.stderr)
        self.assertNotIn("ISAAC_FAIL", result.stdout + result.stderr)
        return result

    def test_isaac_and_options_surface_has_every_member_and_no_lua_errors(self):
        """19 个成员逐个点名 + `Options` 两个字段 + 全部 stub 都不报错。

        断言体在 Lua 脚本里（表、成员类型、`pcall` 包裹的 stub 调用、安全默认值、
        每会话一次告警），harness 用 `ReadLuaGlobalNumber` 核对结论。
        """
        result = self.run_scenario("surface")
        self.assertIn("ISAAC_SURFACE_OK", result.stdout)
        self.assertIn("run_callback=1", result.stdout)
        self.assertIn("first_warnings=1 second_warnings=0", result.stdout)

    def test_frame_count_and_time_follow_post_update_dispatches(self):
        """帧计数与 `GetTime()` 的近似换算。

        宿主派发 31 帧：帧计数从 1 单调走到 31（逐帧正好 +1），
        `GetTime()` 每一帧都等于 `GetFrameCount() // 30`，并在第 30 帧到达 1 秒。
        """
        result = self.run_scenario("surface")
        self.assertIn(f"ISAAC_SURFACE_OK frames={FRAMES} first=1 last={FRAMES} clock={FRAMES}",
                      result.stdout)

    def test_eid_load_order_calls_do_not_raise(self):
        """EID 的加载序列（`Isaac.GetItemConfig()` 在脚本顶层先跑一次）不报错。"""
        result = self.run_scenario("eid")
        self.assertIn("ISAAC_EID_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
