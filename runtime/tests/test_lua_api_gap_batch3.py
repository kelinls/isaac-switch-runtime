"""批次 3 的 API 补齐：宿主行为测试。

这一批的每一项都是"EID 在真机上会在这一行报错/走错分支"的缺口，所以每一项都要有**宿主可跑的
行为证据**（偏移本身的出处是反汇编，记在 `runtime_constants.hpp`；这里证明我们这一侧读得对、
降级得对）：

1. `Mod:AddCallback("SOME_NAME", fn)` —— **字符串回调名**必须登记成功、不抛错
   （EID `features/eid_bagofcrafting_search.lua:58`，它失败会打断整个 `main.lua` 的加载）；
   配套的 `Isaac.GetCallbacks(name)` / `Isaac.RunCallback(name, ...)` 必须按名字派发
   （EID `main.lua:2030` 在**加载期最后一行**调用 `Isaac.RunCallback("EID_POST_LOAD")`）。
2. `Game:GetNumPlayers()` —— `players` 向量计数（EID `main.lua:1062`）；拿不到时降级为 1（单人）。
3. `Game:GetFrameCount()` —— 引擎自己的帧计数 `Game + 0x24F99C`（`Game::Update` 每帧 +1），
   本用例把伪造内存里的计数器推进一帧，Lua 侧必须跟着变；拿不到时退化到 `ManagedFrameClock`。
4. `EntityPlayer:GetOtherTwin()` 恒 nil（单人）、`GetEffectiveMaxHearts`/`GetSoulHearts`/
   `GetBrokenHearts` **恒返回数字**、`player.ControllerIndex` 可读（EID `main.lua:1064`-`1076`、
   `1288`）。
5. `sprite.Scale`/`sprite.Color`/`sprite.FlipX` 的赋值不报错、能读回、并且**绘制路径确实读到**
   （`Sprite:Render`/`RenderLayer` 会在调用引擎之前把缓存交给应用入口；设备侧入口目前是空操作，
   宿主用 `SetSpritePropertyApplyHostImplementation` 观察这一次读取）。
6. 全局 `Color(...)`（含七参形式）可用，且与 `KColor` 是同一张类表。

伪造的引擎内存与 `test_lua_entity_player.py` 同形（模块块 → `g_Game` 槽 → `Game` →
`players` 向量 → `Entity_Player`，首字是 vtable 指针），所以 `Isaac.GetPlayer(0)` 能拿到玩家。
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

# 伪造内存里的读数（Lua 侧逐值断言）。
PLAYER_TYPE_ISAAC = 0
CONTROLLER_INDEX = 3
RED_HEART_CONTAINERS = 6  # 半心单位
SOUL_HEARTS = 4           # 半心单位（含黑心）
BONE_HEARTS = 1
BROKEN_HEARTS = 2
# PlayerType 0 不在"骨心不计"掩码里，所以 `GetEffectiveMaxHearts == 6 + 2*1 == 8`。
EFFECTIVE_MAX_HEARTS = RED_HEART_CONTAINERS + 2 * BONE_HEARTS
FIRST_FRAME_COUNT = 1234
SECOND_FRAME_COUNT = 1235

HARNESS = r'''
#include "interfaces/lua/isaac_api.hpp"
#include "interfaces/lua/sprite_api.hpp"

#include "lua_runtime.hpp"
#include "game_file_reader.hpp"
#include "game_observer.hpp"
#include "lua_object_handles.hpp"
#include "lua_runtime_state.hpp"
#include "runtime_constants.hpp"

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

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

const char* kScriptTemplate = R"lua(@SCRIPT@)lua";

// 伪造内存里的读数（与 Python 侧的常量同源：下面的 `@NAME@` 会被替换成字面量，
// 脚本的 prelude 再由这些常量生成，所以两边不会写歪）。
constexpr std::uint32_t kPlayerTypeIsaac = @PLAYER_TYPE_ISAAC@;
constexpr std::uint32_t kControllerIndex = @CONTROLLER_INDEX@;
constexpr std::uint32_t kRedHeartContainers = @RED_HEART_CONTAINERS@;
constexpr std::uint32_t kSoulHearts = @SOUL_HEARTS@;
constexpr std::uint32_t kBoneHearts = @BONE_HEARTS@;
constexpr std::uint32_t kBrokenHearts = @BROKEN_HEARTS@;
constexpr std::uint32_t kEffectiveMaxHearts = kRedHeartContainers + 2 * kBoneHearts;
constexpr std::uint32_t kFirstFrameCount = @FIRST_FRAME_COUNT@;
constexpr std::uint32_t kSecondFrameCount = @SECOND_FRAME_COUNT@;
static_assert(kEffectiveMaxHearts == @EFFECTIVE_MAX_HEARTS@, "heart arithmetic must agree");

// --- 伪造的引擎内存 --------------------------------------------------------
//
//   模块块[base + 0xAAC698] = &g_Game 变量（槽里是**变量地址**，不是对象）
//   g_Game 变量             = Game*
//   Game + 0x25C50/+0x25C58 = std::vector<Entity_Player*>（步长 8）
//   Game + 0x24F99C         = 本局帧计数（u32）
//   Entity_Player 首字       = base + 0xA37110（vtable）
std::vector<unsigned char> g_ModuleBytes;
std::vector<unsigned char> g_GameBytes;
std::vector<unsigned char> g_PlayerArrayBytes;
std::vector<std::vector<unsigned char>> g_Players;
std::vector<unsigned char> g_OwnerSlotBytes;

std::uintptr_t g_EngineBase = 0;

template <typename T>
void WriteAt(std::vector<unsigned char>& block, std::size_t offset, T value) {
    std::memcpy(block.data() + offset, &value, sizeof(T));
}

void WriteWord(std::vector<unsigned char>& block, std::size_t offset, std::uint64_t value) {
    WriteAt(block, offset, value);
}

std::uintptr_t AddressOf(const std::vector<unsigned char>& block) {
    return reinterpret_cast<std::uintptr_t>(block.data());
}

void CreateBlocks() {
    g_ModuleBytes.assign(kGameOwnerGlobalSlotOffset + sizeof(std::uint64_t), 0);
    g_GameBytes.assign(kGameFrameCountOffset + sizeof(std::uint32_t), 0);
    g_OwnerSlotBytes.assign(sizeof(std::uint64_t), 0);
    g_EngineBase = AddressOf(g_ModuleBytes);
}

void PublishOwnerSlot(std::uint64_t variableAddress) {
    WriteWord(g_ModuleBytes, kGameOwnerGlobalSlotOffset, variableAddress);
}

void PublishPlayers(std::size_t count) {
    g_Players.assign(count, std::vector<unsigned char>(kEntityPlayerSize, 0));
    for (std::vector<unsigned char>& player : g_Players) {
        WriteWord(player, 0, g_EngineBase + kEntityPlayerVtableOffset);
        WriteAt<std::uint32_t>(player, kEntityTypeOffset, 1);
        WriteAt<std::uint32_t>(player, kEntityPlayerTypeOffset, kPlayerTypeIsaac);
        WriteAt<std::uint32_t>(player, kEntityPlayerIndexOffset, 0);
        WriteAt<std::uint32_t>(player, kEntityPlayerControllerIndexOffset, kControllerIndex);
        WriteAt<std::uint32_t>(player, kEntityPlayerRedHeartContainersOffset, kRedHeartContainers);
        WriteAt<std::uint32_t>(player, kEntityPlayerSoulHeartsOffset, kSoulHearts);
        WriteAt<std::uint32_t>(player, kEntityPlayerBoneHeartsOffset, kBoneHearts);
        WriteAt<std::uint32_t>(player, kEntityPlayerBrokenHeartsOffset, kBrokenHearts);
    }
    g_PlayerArrayBytes.assign(count * sizeof(std::uint64_t) + sizeof(std::uint64_t), 0);
    for (std::size_t index = 0; index < count; ++index) {
        WriteWord(g_PlayerArrayBytes, index * sizeof(std::uint64_t), AddressOf(g_Players[index]));
    }
    WriteWord(g_GameBytes, kGamePlayerArrayBeginOffset, AddressOf(g_PlayerArrayBytes));
    WriteWord(g_GameBytes, kGamePlayerArrayEndOffset,
              AddressOf(g_PlayerArrayBytes) + count * sizeof(std::uint64_t));
}

void PublishFrameCount(std::uint32_t frames) {
    WriteAt<std::uint32_t>(g_GameBytes, kGameFrameCountOffset, frames);
}

// --- 假的原生 Sprite（`ANM2`）---------------------------------------------
constexpr std::uint64_t kSpriteMagic = 0x414E4D3253505233ULL;  // "ANM2SPR3"
constexpr std::size_t kSpriteSize = kSpriteObjectSize;

int g_CtorCalls = 0;
int g_RenderCalls = 0;
int g_RenderLayerCalls = 0;

void FakeCtor(void* self) {
    std::memset(self, 0, kSpriteSize);
    *static_cast<std::uint64_t*>(self) = kSpriteMagic;
    ++g_CtorCalls;
}

void FakeDtor(void* self) {
    if (*static_cast<std::uint64_t*>(self) != kSpriteMagic) std::exit(31);
}

void FakeRender(void* self, const void*, const void*, const void*) {
    if (*static_cast<std::uint64_t*>(self) != kSpriteMagic) std::exit(32);
    ++g_RenderCalls;
}

void FakeRenderLayer(void* self, int, const void*, const void*, const void*) {
    if (*static_cast<std::uint64_t*>(self) != kSpriteMagic) std::exit(33);
    ++g_RenderLayerCalls;
}

// 应用入口的观察窗：宿主用注入实现证明"绘制路径确实读了句柄里的缓存"
// （2026-09-16 起设备侧也**真的写进** `ANM2` 了，写通道的字节级证据由下面
// `ExpectEngineBytes` 直接读那块伪造内存给出）。
struct AppliedProperties {
    int calls = 0;
    float scaleX = 0.0F;
    float scaleY = 0.0F;
    float colorRed = 0.0F;
    float colorAlpha = 0.0F;
    int flipX = -1;
    std::uintptr_t sprite = 0;
};
AppliedProperties g_Applied{};

void HarnessApplyProperties(void* sprite, const void* handle) {
    const auto* cached = static_cast<const LuaRuntime::SpriteHandle*>(handle);
    ++g_Applied.calls;
    g_Applied.sprite = reinterpret_cast<std::uintptr_t>(sprite);
    g_Applied.scaleX = cached->scaleX;
    g_Applied.scaleY = cached->scaleY;
    g_Applied.colorRed = cached->colorRed;
    g_Applied.colorAlpha = cached->colorAlpha;
    g_Applied.flipX = cached->flipX;
}

// 读伪造 `ANM2` 里某个偏移上的 float/字节，用来断言"Mod 写的值真的落进了原生对象"。
bool ReadEngineFloat(std::uintptr_t base, std::size_t offset, float* out) {
    if (base == 0 || out == nullptr) return false;
    std::memcpy(out, reinterpret_cast<const void*>(base + offset), sizeof(float));
    return true;
}

bool ReadEngineByte(std::uintptr_t base, std::size_t offset, unsigned* out) {
    if (base == 0 || out == nullptr) return false;
    *out = *reinterpret_cast<const unsigned char*>(base + offset);
    return true;
}

// `Sprite.Scale` / `Sprite.Color` / `Sprite.FlipX` 的落点（常量来自 `runtime_constants.hpp`，
// 也就是写通道真正引用的那几个名字 —— 这样"测试与实现同源"，不会各写一份偏移）。
bool ExpectSpriteBytes(std::uintptr_t base) {
    float scaleX = 0.0F;
    float scaleY = 0.0F;
    float tintRed = 0.0F;
    float tintGreen = 0.0F;
    float tintBlue = 0.0F;
    float tintAlpha = 0.0F;
    unsigned flipX = 0U;
    const bool read = ReadEngineFloat(base, kSpriteScaleOffset, &scaleX) &&
                      ReadEngineFloat(base, kSpriteScaleOffset + sizeof(float), &scaleY) &&
                      ReadEngineFloat(base, kSpriteColorOffset + kColorModTintOffset, &tintRed) &&
                      ReadEngineFloat(base, kSpriteColorOffset + kColorModTintOffset + sizeof(float),
                                      &tintGreen) &&
                      ReadEngineFloat(base,
                                      kSpriteColorOffset + kColorModTintOffset + 2 * sizeof(float),
                                      &tintBlue) &&
                      ReadEngineFloat(base,
                                      kSpriteColorOffset + kColorModTintOffset + 3 * sizeof(float),
                                      &tintAlpha) &&
                      ReadEngineByte(base, kSpriteFlipXOffset, &flipX);
    if (!read) {
        std::printf("GAP_FAIL: could not read the ANM2 bytes back\n");
        return false;
    }
    // 脚本写的是 `Scale = Vector(2.5, 0.5)`、`Color = Color(1, 0.5, 0.25, 0.25)`、`FlipX = true`。
    const bool ok = scaleX == 2.5F && scaleY == 0.5F && tintRed == 1.0F && tintGreen == 0.5F &&
                    tintBlue == 0.25F && tintAlpha == 0.25F && flipX == 1U;
    if (!ok) {
        std::printf("GAP_FAIL: ANM2 bytes scale=(%.3f,%.3f) tint=(%.3f,%.3f,%.3f,%.3f) flipX=%u\n",
                    static_cast<double>(scaleX), static_cast<double>(scaleY),
                    static_cast<double>(tintRed), static_cast<double>(tintGreen),
                    static_cast<double>(tintBlue), static_cast<double>(tintAlpha), flipX);
    }
    return ok;
}

bool ExpectNumber(const char* name, double expected) {
    double value = 0.0;
    if (!LuaRuntime::ReadLuaGlobalNumber(name, &value)) {
        std::printf("GAP_FAIL: global %s was never written\n", name);
        return false;
    }
    if (value != expected) {
        std::printf("GAP_FAIL: %s expected %.0f, got %.0f\n", name, expected, value);
        return false;
    }
    return true;
}

} // namespace

int main(int argc, char** argv) {
    if (argc != 2) return 90;
    const std::string scenario = argv[1];
    if (scenario != "valid" && scenario != "two_players" && scenario != "no_base") return 91;

    CreateBlocks();
    if (scenario != "no_base") {
        LuaRuntime::SetEngineModuleBase(g_EngineBase);
        PublishOwnerSlot(AddressOf(g_OwnerSlotBytes));
        WriteWord(g_OwnerSlotBytes, 0, AddressOf(g_GameBytes));
        PublishPlayers(scenario == "two_players" ? 2 : 1);
        PublishFrameCount(kFirstFrameCount);
    }

    LuaRuntime::LuaSpriteBindings bindings{};
    bindings.ctor = reinterpret_cast<uintptr_t>(&FakeCtor);
    bindings.destructor_ = reinterpret_cast<uintptr_t>(&FakeDtor);
    bindings.render = reinterpret_cast<uintptr_t>(&FakeRender);
    bindings.renderLayer = reinterpret_cast<uintptr_t>(&FakeRenderLayer);
    LuaRuntime::SetSpriteBindings(bindings);
    isaac::runtime::SetSpritePropertyApplyHostImplementation(&HarnessApplyProperties);

    // 脚本 prelude：伪造内存里的读数由 C++ 常量生成（单一真源在 Python 侧的字面量里）。
    const std::string prelude =
        "ENGINE_FRAME_COUNT = " + std::to_string(kFirstFrameCount) + "\n" +
        "SECOND_ENGINE_FRAME_COUNT = " + std::to_string(kSecondFrameCount) + "\n" +
        "PLAYER_TYPE_ISAAC = " + std::to_string(kPlayerTypeIsaac) + "\n" +
        "CONTROLLER_INDEX = " + std::to_string(kControllerIndex) + "\n" +
        "SOUL_HEARTS = " + std::to_string(kSoulHearts) + "\n" +
        "BROKEN_HEARTS = " + std::to_string(kBrokenHearts) + "\n" +
        "EFFECTIVE_MAX_HEARTS = " + std::to_string(kEffectiveMaxHearts) + "\n";
    const std::string script =
        "SCENARIO = '" + scenario + "'\n" + prelude + kScriptTemplate;
    const auto result =
        LuaRuntime::InitializeFromBuffer(script.c_str(), script.size(), "@api_gap_batch3.lua");
    if (result != LuaRuntime::LuaInitResult::Success) {
        std::printf("GAP_FAIL: the Lua state did not initialize\n");
        return 2;
    }

    // 第一帧：脚本在这里做全部断言。
    LuaRuntime::DispatchPostUpdate();
    if (LuaRuntime::TakeCallbackError()) {
        std::printf("GAP_FAIL: the Lua callback raised an error on the first frame\n");
        return 1;
    }

    // 第二帧：把伪造的引擎帧计数推进一格，`Game:GetFrameCount()` 必须跟着走。
    if (scenario != "no_base") {
        PublishFrameCount(kSecondFrameCount);
    }
    LuaRuntime::DispatchPostUpdate();
    if (LuaRuntime::TakeCallbackError()) {
        std::printf("GAP_FAIL: the Lua callback raised an error on the second frame\n");
        return 1;
    }

    if (!ExpectNumber("SCENARIO_OK", 1.0)) return 1;
    if (scenario != "no_base") {
        if (!ExpectNumber("GET_NUM_PLAYERS_OK", 1.0)) return 1;
        if (!ExpectNumber("PLAYER_FIELDS_OK", 1.0)) return 1;
        if (!ExpectNumber("ENGINE_FRAME_FOLLOWED", 1.0)) return 1;
    } else {
        // 没有引擎基址：`GetNumPlayers` 降级为 1（单人快速路径），`GetFrameCount` 退化为
        // 本 Runtime 的帧钟（单调递增）。
        if (!ExpectNumber("FALLBACK_OK", 1.0)) return 1;
    }
    if (!ExpectNumber("NAMED_CALLBACK_OK", 1.0)) return 1;
    if (!ExpectNumber("SPRITE_FIELDS_OK", 1.0)) return 1;
    if (!ExpectNumber("COLOR_OK", 1.0)) return 1;

    if (g_CtorCalls != 1 || g_RenderCalls != 1 || g_RenderLayerCalls != 1) {
        std::printf("GAP_FAIL: sprite calls ctor=%d render=%d renderLayer=%d\n", g_CtorCalls,
                    g_RenderCalls, g_RenderLayerCalls);
        return 1;
    }
    // 绘制路径必须读到 Lua 写进句柄的三个属性（应用入口的观察窗）。
    if (g_Applied.calls < 2) {
        std::printf("GAP_FAIL: the render path must apply the cached properties (calls=%d)\n",
                    g_Applied.calls);
        return 1;
    }
    if (g_Applied.scaleX != 2.5F || g_Applied.scaleY != 0.5F || g_Applied.colorRed != 1.0F ||
        g_Applied.colorAlpha != 0.25F || g_Applied.flipX != 1) {
        std::printf("GAP_FAIL: applied properties scale=(%.2f,%.2f) colorRed=%.2f alpha=%.2f "
                    "flipX=%d\n",
                    static_cast<double>(g_Applied.scaleX), static_cast<double>(g_Applied.scaleY),
                    static_cast<double>(g_Applied.colorRed),
                    static_cast<double>(g_Applied.colorAlpha), g_Applied.flipX);
        return 1;
    }

    // 2026-09-16 起：三个属性还要**真的写进原生对象**（写通道），不只是留在句柄里。
    // 判据是直接读那块伪造 `ANM2` 的字节 —— 与设备上 `GetDestQuad` 读的是同一批偏移。
    if (!ExpectSpriteBytes(g_Applied.sprite)) {
        return 1;
    }

    std::printf("API_GAP_BATCH3_OK scenario=%s apply_calls=%d sprite=%llx\n", scenario.c_str(),
                g_Applied.calls, static_cast<unsigned long long>(g_Applied.sprite));
    return 0;
}
'''

SCRIPT = r'''
local mod = RegisterMod('API gap batch 3', 1)

local function check(condition, message)
  if not condition then error(message, 2) end
end

local function describe(value)
  if value == nil then return 'nil' end
  return type(value) .. ':' .. tostring(value)
end

local frame = 0

-- ---------------------------------------------------------------------------
-- 命名回调（PC 的自定义回调名）
-- ---------------------------------------------------------------------------
local NAMED = 'EIDCallbacks.SEARCH_NAME_CONVERSION'

local function verifyNamedCallbacks()
  -- 登记：EID 在加载阶段就会执行这一句（`features/eid_bagofcrafting_search.lua:58`），
  -- 报错会打断整个 `main.lua`。
  local ok, err = pcall(function()
    mod:AddCallback(NAMED, function(self, text)
      check(self == mod, 'the named callback must receive its own Mod object')
      return 'converted:' .. tostring(text)
    end)
  end)
  check(ok, 'a string callback name must register without raising: ' .. tostring(err))

  -- 数字 id 的老语义不受影响（同一次调用里再登记一个数字回调）。
  mod:AddCallback(21, function() end)

  -- `Isaac.GetCallbacks(name)`：PC 的形状是 `{ { Function = fn, Mod = mod }, ... }`。
  local list = Isaac.GetCallbacks(NAMED)
  check(type(list) == 'table', 'GetCallbacks(name) must return a table, got ' .. describe(list))
  check(#list == 1, 'GetCallbacks(name) must list exactly one registration, got ' .. #list)
  check(type(list[1]) == 'table', 'each entry must be a table')
  check(type(list[1].Function) == 'function', 'the entry must carry Function')
  check(list[1].Mod == mod, 'the entry must carry its Mod object')

  -- 没有登记的名字：空表（不是 nil —— nil 会让 `pairs(...)` 直接报错）。
  local empty = Isaac.GetCallbacks('NOT_REGISTERED')
  check(type(empty) == 'table' and next(empty) == nil,
        'GetCallbacks for an unknown name must be an empty table')

  -- `Isaac.RunCallback(name, ...)`：按名字派发，并把**第一个非 nil 返回值**带回来
  -- （PC 文档 "breaking on the first return"）。
  local returned = Isaac.RunCallback(NAMED, 'korean')
  check(returned == 'converted:korean',
        'RunCallback(name) must return the first callback result, got ' .. describe(returned))
  -- 回调收到的第一个参数必须是 Mod 对象（上面那个函数体自己断言了）。

  -- EID 加载期最后一行就是这一句：没有登记者的名字必须安静返回。
  local silent, silentError = pcall(function() Isaac.RunCallback('EID_POST_LOAD') end)
  check(silent, 'RunCallback on an unknown name must be silent: ' .. tostring(silentError))

  -- 数字 id 的既有行为不变。
  local numericSilent, numericError = pcall(function() Isaac.RunCallback(21) end)
  check(numericSilent, 'RunCallback(id) must still work: ' .. tostring(numericError))
  -- 登记过的数字 id：`GetCallbacks(id)` 现在如实返回它（不再是 stub 空表）。
  local numericList = Isaac.GetCallbacks(21)
  check(type(numericList) == 'table' and #numericList == 1,
        'GetCallbacks(id) must list the numeric registration')
  check(type(numericList[1].Function) == 'function', 'the numeric entry must carry Function')

  NAMED_CALLBACK_OK = 1
end

-- ---------------------------------------------------------------------------
-- Sprite 的三个可写属性 + 全局 Color
-- ---------------------------------------------------------------------------
local function verifySpriteFields()
  local sprite = Sprite()
  check(sprite ~= nil, 'Sprite() must return an object')

  -- 赋值必须不报错（没有 `__newindex` 时这里是 "attempt to index a userdata value"）。
  sprite.Scale = Vector(2.5, 0.5)
  sprite.Color = Color(1, 0.5, 0.25, 0.25)
  sprite.FlipX = true

  -- 读回：缓存就是我们自己的真源。
  check(sprite.FlipX == true, 'FlipX must read back as true, got ' .. describe(sprite.FlipX))
  local scale = sprite.Scale
  check(type(scale) == 'userdata', 'Scale must read back as a Vector, got ' .. describe(scale))
  check(math.abs(scale.X - 2.5) < 0.001 and math.abs(scale.Y - 0.5) < 0.001,
        'Scale must read back (2.5, 0.5)')
  local color = sprite.Color
  check(type(color) == 'userdata', 'Color must read back as a KColor, got ' .. describe(color))
  check(math.abs(color.Red - 1.0) < 0.001 and math.abs(color.Alpha - 0.25) < 0.001,
        'Color must read back its RGBA components')
  -- 读出来的是**新对象**：改它不能影响缓存。
  color.Alpha = 0.0
  check(math.abs(sprite.Color.Alpha - 0.25) < 0.001,
        'reading Sprite.Color must return a fresh value')

  -- 绘制：harness 会在这一步检查缓存里的值确实到了应用入口。
  sprite:Render(Vector(0, 0))
  sprite:RenderLayer(1, Vector(0, 0))

  -- 类型错误必须报错（静默接受会让 Mod 的笔误变成"看起来生效了"）。
  local wrongScale = pcall(function() sprite.Scale = 5 end)
  check(not wrongScale, 'Sprite.Scale must reject a non-Vector')
  local wrongColor = pcall(function() sprite.Color = 5 end)
  check(not wrongColor, 'Sprite.Color must reject a non-KColor')
  local wrongFlip = pcall(function() sprite.FlipX = 1 end)
  check(not wrongFlip, 'Sprite.FlipX must reject a non-boolean')
  local unknown = pcall(function() sprite.NotAField = 1 end)
  check(not unknown, 'writing an unknown Sprite field must raise')

  SPRITE_FIELDS_OK = 1
end

local function verifyColorGlobal()
  check(Color ~= nil, 'the global Color must exist')
  check(KColor ~= nil, 'the global KColor must still exist')
  -- PC 的 `Color` 与 `KColor` 是同一个类（`KColor` 是别名之一）：本实现把**同一张类表**
  -- 挂了两个名字，所以两者恒等。
  check(Color == KColor, 'Color and KColor must be the same class table')

  local four = Color(0.1, 0.2, 0.3, 0.4)
  check(type(four) == 'userdata', 'Color(r,g,b,a) must build a value')
  check(math.abs(four.Red - 0.1) < 0.001 and math.abs(four.Green - 0.2) < 0.001 and
        math.abs(four.Blue - 0.3) < 0.001 and math.abs(four.Alpha - 0.4) < 0.001,
        'the four-argument form must set RGBA')
  -- 省略 alpha 时按 PC 的默认值 1。
  local three = Color(0.5, 0.5, 0.5)
  check(math.abs(three.Alpha - 1.0) < 0.001, 'alpha must default to 1')

  -- EID 的七参形式（`features/eid_api.lua:1369`、`main.lua:960`）。
  local seven = Color(1, 1, 1, 1, 0.25, 0.5, 0.75)
  check(type(seven) == 'userdata', 'the seven-argument form must build a value')
  check(math.abs(seven.Red - 1.0) < 0.001 and math.abs(seven.Alpha - 1.0) < 0.001,
        'the seven-argument form must set RGBA')
  check(math.abs(seven.RO - 0.25) < 0.001 and math.abs(seven.GO - 0.5) < 0.001 and
        math.abs(seven.BO - 0.75) < 0.001,
        'the seven-argument form must keep the three colour offsets')

  -- `KColor` 侧的老用法不受影响（常量与四参构造）。
  check(math.abs(KColor.White.Alpha - 1.0) < 0.001, 'KColor.White must still work')
  check(math.abs(KColor(0, 0.671875, 0.9296875, 1).Green - 0.671875) < 0.001,
        'KColor(r,g,b,a) must still work')
  COLOR_OK = 1
end

-- ---------------------------------------------------------------------------
-- Game / EntityPlayer 的引擎读数
-- ---------------------------------------------------------------------------
local function verifyEngineReads()
  local game = Game()
  check(game ~= nil, 'Game() must return an object')

  local players = game:GetNumPlayers()
  check(type(players) == 'number', 'GetNumPlayers must return a number')
  if SCENARIO == 'two_players' then
    check(players == 2, 'GetNumPlayers must count the vector, got ' .. tostring(players))
  else
    check(players == 1, 'GetNumPlayers must be 1 with one player, got ' .. tostring(players))
  end

  local frames = game:GetFrameCount()
  check(type(frames) == 'number', 'GetFrameCount must return a number')
  check(frames == ENGINE_FRAME_COUNT,
        'GetFrameCount must read the engine counter (' .. tostring(ENGINE_FRAME_COUNT) .. '), got '
        .. tostring(frames))
  GET_NUM_PLAYERS_OK = 1

  -- `Game:GetSeeds()` / `Seeds:IsCustomRun()` / `Game:GetVictoryLap()`（2026-09-12 第五轮）。
  --
  -- 真机报告 `01789210180`：EID 的 `features/eid_api.lua:2046` 写的是
  --   `if not game:GetSeeds():IsCustomRun() and not EID:PlayersHaveCollectible(...) then`
  -- —— `GetSeeds()` 以前返回 nil，于是"调 nil 的方法"直接抛错、整条 update 回调被派发器
  -- 静默摘除（屏幕全空、游戏不崩）。这三条断言盯的就是那条链：对象非 nil、方法可调、
  -- 返回值类型对。数值语义（挑战/种子局判定）需要引擎字段偏移，成熟度仍是 `Experimental`。
  local seeds = game:GetSeeds()
  check(seeds ~= nil, 'Game:GetSeeds must not return nil')
  check(type(seeds) == 'userdata', 'Game:GetSeeds must return a Seeds object, got ' .. describe(seeds))
  local customOk, custom = pcall(function() return seeds:IsCustomRun() end)
  check(customOk, 'Seeds:IsCustomRun must not raise')
  check(type(custom) == 'boolean', 'Seeds:IsCustomRun must return a boolean, got ' .. describe(custom))
  local seedOk, startSeed = pcall(function() return seeds:GetStartSeed() end)
  check(seedOk, 'Seeds:GetStartSeed must not raise')
  check(type(startSeed) == 'number', 'Seeds:GetStartSeed must return a number, got ' .. describe(startSeed))
  -- `GetVictoryLap` 在 EID 里都是 `> 0` 判断，非胜利圈时必须是数字 0。
  local lapOk, lap = pcall(function() return game:GetVictoryLap() end)
  check(lapOk, 'Game:GetVictoryLap must not raise')
  check(lap == 0, 'Game:GetVictoryLap must be 0 outside a victory lap, got ' .. describe(lap))
  SEEDS_OK = 1
end

local function verifyPlayerFields()
  local player = Isaac.GetPlayer(0)
  check(player ~= nil, 'Isaac.GetPlayer(0) must return the player')

  -- EID 用它当表键（`EID.controllerIndexes[p.ControllerIndex] = 1`）：nil 会直接报
  -- "table index is nil"。
  check(player.ControllerIndex == CONTROLLER_INDEX,
        'ControllerIndex must read the real field, got ' .. describe(player.ControllerIndex))
  check(player.PlayerType == PLAYER_TYPE_ISAAC, 'PlayerType must still read')

  -- 单人：分身必须是 nil（EID 写的是 `p:GetOtherTwin() or p`）。
  check(player:GetOtherTwin() == nil,
        'GetOtherTwin must be nil for a single player, got ' .. describe(player:GetOtherTwin()))

  -- 三个 hearts 方法必须返回**数字**：EID 对它们做算术（`main.lua:1288`）。
  local maxHearts = player:GetEffectiveMaxHearts()
  local soulHearts = player:GetSoulHearts()
  local brokenHearts = player:GetBrokenHearts()
  check(type(maxHearts) == 'number', 'GetEffectiveMaxHearts must return a number')
  check(type(soulHearts) == 'number', 'GetSoulHearts must return a number')
  check(type(brokenHearts) == 'number', 'GetBrokenHearts must return a number')
  check(maxHearts == EFFECTIVE_MAX_HEARTS,
        'GetEffectiveMaxHearts must be red containers + 2*bone hearts, got ' .. tostring(maxHearts))
  check(soulHearts == SOUL_HEARTS, 'GetSoulHearts must be the soul heart count, got '
        .. tostring(soulHearts))
  check(brokenHearts == BROKEN_HEARTS, 'GetBrokenHearts must be the broken heart count, got '
        .. tostring(brokenHearts))
  -- EID 的那一行算术必须能跑通（返回 nil 时这里会报 arithmetic on a nil value）。
  check(maxHearts + soulHearts + brokenHearts * 2 == EFFECTIVE_MAX_HEARTS + SOUL_HEARTS
        + BROKEN_HEARTS * 2, 'the EID heart arithmetic must work')
  PLAYER_FIELDS_OK = 1
end

local function verifyFallback()
  local game = Game()
  -- 没有引擎基址：**答 0**（2026-09-12 第五轮修正）。
  --
  -- 原口径是"降级答 1（单人）"，理由是"0 会让 EID 走多人分支"。真机把这条推翻了：玩家向量
  -- 在主界面是空向量、`Isaac.GetPlayer(0)` 因此是 nil，而"答 1"这一档让 EID 的
  -- `for i = 0, game:GetNumPlayers() - 1 do local player = Isaac.GetPlayer(i) ... player.QueuedItem`
  -- 真的进了循环体 → 索引 nil → 抛错 → 回调被派发器静默摘除（屏幕全空、游戏不崩）。
  -- 统一口径：答 0 表示"确实没有玩家"，调用方据此跳过；多人分支同样需要 `Isaac.GetPlayer`，
  -- 拿不到时它也只能给 nil，所以"答 1"并没有保护任何东西。
  check(game:GetNumPlayers() == 0, 'GetNumPlayers must answer 0 when the engine cannot be read')
  -- 退化到本 Runtime 的帧钟：第一帧至少是 1（每次 update 派发一次），且必须是数字。
  local frames = game:GetFrameCount()
  check(type(frames) == 'number' and frames >= 1,
        'GetFrameCount must fall back to the Runtime clock, got ' .. describe(frames))
  -- 退化时钟必须单调（EID 的 `frame - lastTouch > 45` 依赖它）。
  check(Isaac.GetFrameCount() >= frames, 'the Runtime clock must be monotonic')
  FALLBACK_OK = 1
end

local function run()
  frame = frame + 1
  if frame == 1 then
    verifyNamedCallbacks()
    verifySpriteFields()
    verifyColorGlobal()
    if SCENARIO == 'no_base' then
      verifyFallback()
    else
      verifyEngineReads()
      verifyPlayerFields()
    end
  elseif frame == 2 then
    if SCENARIO ~= 'no_base' then
      -- harness 已经把伪造的引擎帧计数推进了一格：读数必须跟着引擎走（而不是我们自己的时钟）。
      local frames = Game():GetFrameCount()
      check(frames == SECOND_ENGINE_FRAME_COUNT,
            'GetFrameCount must follow the engine counter on frame 2, got ' .. tostring(frames))
      ENGINE_FRAME_FOLLOWED = 1
    end
  end
  SCENARIO_OK = 1
end

mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function()
  local ok, err = pcall(run)
  if not ok then
    SCENARIO_OK = 0
    print('GAP_ERROR: ' .. tostring(err))
  end
end)
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


HARNESS_LITERALS = {
    "PLAYER_TYPE_ISAAC": PLAYER_TYPE_ISAAC,
    "CONTROLLER_INDEX": CONTROLLER_INDEX,
    "RED_HEART_CONTAINERS": RED_HEART_CONTAINERS,
    "SOUL_HEARTS": SOUL_HEARTS,
    "BONE_HEARTS": BONE_HEARTS,
    "BROKEN_HEARTS": BROKEN_HEARTS,
    "EFFECTIVE_MAX_HEARTS": EFFECTIVE_MAX_HEARTS,
    "FIRST_FRAME_COUNT": FIRST_FRAME_COUNT,
    "SECOND_FRAME_COUNT": SECOND_FRAME_COUNT,
}


def render_harness() -> str:
    """把 Python 侧的读数写进 harness（`@NAME@` 占位，避免误替换其它同名标识符）。"""
    text = HARNESS
    for name, value in HARNESS_LITERALS.items():
        text = text.replace(f"@{name}@", str(value))
    return text.replace("@SCRIPT@", SCRIPT).lstrip()


class LuaApiGapBatch3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compilers = host_compilers()
        if compilers is None:
            raise unittest.SkipTest("需要宿主 C/C++ 编译器（本用例不需要 docker）")
        cc, cxx = compilers
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-lua-api-gap-batch3-")
        temporary = Path(cls.temporary.name)
        compatibility = temporary / "compatibility"
        compatibility.mkdir()
        (compatibility / "stdfloat").write_text(
            "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n"
        )
        harness = temporary / "api_gap_batch3_harness.cpp"
        harness.write_text(render_harness())
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
        cls.harness = temporary / "api_gap_batch3_harness"
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
        self.assertNotIn("GAP_ERROR", result.stdout + result.stderr)
        self.assertNotIn("GAP_FAIL", result.stdout + result.stderr)
        return result

    def test_named_callbacks_registration_dispatch_and_sprite_color_surface(self):
        """字符串回调名、`GetCallbacks`/`RunCallback`、Sprite 三属性、全局 `Color` 全都要过。

        这些断言都在 Lua 里（脚本第一帧），宿主另外核对 Sprite 的绘制路径读到的缓存值：
        `apply_calls >= 2` 说明 `Render` 与 `RenderLayer` 都走了应用入口，且值就是 Lua 写的
        `Scale(2.5, 0.5)`、`Color.Red == 1`、`Alpha == 0.25`、`FlipX == true`。
        """
        result = self.run_scenario("valid")
        self.assertIn("API_GAP_BATCH3_OK scenario=valid", result.stdout)
        self.assertIn("apply_calls=2", result.stdout)

    def test_engine_reads_follow_the_fake_engine_memory(self):
        """`GetNumPlayers` 计数、`ControllerIndex`/hearts 读数、`GetFrameCount` 跟引擎走。

        `two_players` 把伪造的 `players` 向量摆成 2 个元素：计数必须跟着变（1 是"常量"就会
        被抓到）。`valid` 的第二帧把伪造的引擎帧计数从 1234 推到 1235，Lua 侧的
        `ENGINE_FRAME_FOLLOWED` 只有在**读到引擎那个字段**时才成立 —— 退化成我们自己的帧钟
        会读到 1/2，直接失败。
        """
        result = self.run_scenario("valid")
        self.assertIn("scenario=valid", result.stdout)
        two = self.run_scenario("two_players")
        self.assertIn("API_GAP_BATCH3_OK scenario=two_players", two.stdout)

    def test_without_the_engine_base_the_values_degrade_conservatively(self):
        """没有引擎模块基址时：玩家数答 0、单调帧钟、不报错。

        这是"宿主/真机早期"的形态：`Isaac.GetPlayer` 返回 nil（由批次 2 的测试覆盖），
        `Game:GetNumPlayers` 必须给 **0**（口径与 `Isaac.GetPlayer` 一致 —— 见 `verifyFallback`
        的注释与真机报告 `01789209547`），`Game:GetFrameCount` 退化为 Runtime 帧钟
        （单调，EID 的差值/取模用法仍然成立）。

        **2026-09-12 修正后的确切口径**（真机报告 `01789207110`）：给 1 只适用于
        "**读失败**"（本用例就是没有基址 —— 整体读不到）。读成功但玩家向量为空时必须
        **如实给 0**，因为那时 `Isaac.GetPlayer(0)` 也是 nil，报 1 会让 EID 的
        `for i = 0, game:GetNumPlayers() - 1 do local player = Isaac.GetPlayer(i)` 索引 nil、
        回调被静默摘除。两种口径的对照见 `runtime/tests/test_lua_entity_player.py` 的
        `verifyPlayerCountCoherence`。
        """
        result = self.run_scenario("no_base")
        self.assertIn("API_GAP_BATCH3_OK scenario=no_base", result.stdout)


if __name__ == "__main__":
    unittest.main()
