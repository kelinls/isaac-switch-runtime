"""`Sprite`（引擎侧 `IsaacRepentance::ANM2`）第一步的行为测试（宿主可跑）。

覆盖：对象归属（我们分配/析构/释放）、`IsLoaded` 读引擎标志、`SetFrame` 两个重载的分派、
各方法参数与返回值原样透传、`Render`/`RenderLayer` 把 `Vector` 值按引用送到原生入口、
以及 `__gc` 恰好调用一次析构。

原生对象尺寸 `0x158` 的**出处**不在宿主侧（宿主不需要真实对象），而由
`tools/stage155_anm2_size_audit.py` 的分配步长分析给出，真机由探针校验。
"""

import re
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

// 假的原生 `ANM2`：对象尺寸与对齐按审计结论（0x158 / 16），ctor 在偏移 0 写入魔数，
// `+0x149` 是引擎自己的"已加载"标志（由脚本通过 `SPRITE_FLAG` 控制）。
constexpr std::uint64_t kSpriteMagic = 0x414E4D3253505231ULL;  // "ANM2SPR1"
// 对象大小按审计结论 `0x158`（`+0x149` 是引擎自己的"已加载"标志，由 `Sprite:IsLoaded` 直接读）。
constexpr std::size_t kSpriteSize = 0x158;

int g_ctorCalls = 0;
int g_dtorCalls = 0;
int g_updateCalls = 0;
int g_isPlayingAnyCalls = 0;
int g_isFinishedAnyCalls = 0;
struct Call {
    char name[64];
    int flag;
    int number;
    int calls;
};
Call g_play{};
Call g_setAnimation{};
Call g_setFrameNamed{};
Call g_setFrame{};
Call g_setLayerFrame{};
Call g_playRandom{};
struct RenderCall {
    int layer;
    float positionX;
    float positionY;
    float topLeftX;
    float topLeftY;
    float bottomRightX;
    float bottomRightY;
    int calls;
};
RenderCall g_render{};
RenderCall g_renderLayer{};

void FakeCtor(void* self) {
    std::memset(self, 0, kSpriteSize);
    *static_cast<std::uint64_t*>(self) = kSpriteMagic;
    ++g_ctorCalls;
}

void FakeDtor(void* self) {
    if (*static_cast<std::uint64_t*>(self) != kSpriteMagic) {
        std::exit(31);
    }
    ++g_dtorCalls;
}

void Record(Call* call, const char* text, int flag, int number) {
    std::snprintf(call->name, sizeof(call->name), "%s", text == nullptr ? "" : text);
    call->flag = flag;
    call->number = number;
    ++call->calls;
}

void FakePlay(void* self, const char* name, bool force) {
    if (*static_cast<std::uint64_t*>(self) != kSpriteMagic) std::exit(32);
    Record(&g_play, name, force ? 1 : 0, 0);
}

bool FakeSetAnimation(void* self, const char* name, bool reset) {
    if (*static_cast<std::uint64_t*>(self) != kSpriteMagic) std::exit(33);
    Record(&g_setAnimation, name, reset ? 1 : 0, 0);
    // 返回"是否成功"；`reset` 标志单独由宿主断言，避免两者混在一个返回值里。
    return true;
}

void FakeSetFrameNamed(void* self, const char* name, int frame) {
    if (*static_cast<std::uint64_t*>(self) != kSpriteMagic) std::exit(34);
    Record(&g_setFrameNamed, name, 0, frame);
}

void FakeSetFrame(void* self, int frame) {
    if (*static_cast<std::uint64_t*>(self) != kSpriteMagic) std::exit(35);
    Record(&g_setFrame, "", 0, frame);
}

int FakeGetFrame(const void* self) {
    if (*static_cast<const std::uint64_t*>(self) != kSpriteMagic) std::exit(36);
    return 7;
}

void FakeSetLayerFrame(void* self, int layer, int frame) {
    if (*static_cast<std::uint64_t*>(self) != kSpriteMagic) std::exit(37);
    Record(&g_setLayerFrame, "", layer, frame);
}

int FakeGetLayerFrame(void* self, int layer) {
    if (*static_cast<std::uint64_t*>(self) != kSpriteMagic) std::exit(38);
    return layer + 100;
}

void FakeUpdate(void* self) {
    if (*static_cast<std::uint64_t*>(self) != kSpriteMagic) std::exit(39);
    ++g_updateCalls;
}

// 引擎语义（`ANM2::IsPlaying(char const*)` / `IsFinished(char const*)`）：**空名字**表示
// "只看当前动画在不在播"，所以省参数的调用必须传空串而不是 nullptr。
bool FakeIsPlaying(const void* self, const char* name) {
    if (*static_cast<const std::uint64_t*>(self) != kSpriteMagic) std::exit(40);
    if (name == nullptr) std::exit(43);
    if (name[0] == '\0') { ++g_isPlayingAnyCalls; return true; }
    return std::strcmp(name, "Idle") == 0;
}

bool FakeIsFinished(const void* self, const char* name) {
    if (*static_cast<const std::uint64_t*>(self) != kSpriteMagic) std::exit(41);
    if (name == nullptr) std::exit(44);
    if (name[0] == '\0') { ++g_isFinishedAnyCalls; return false; }
    return std::strcmp(name, "Done") == 0;
}

void FakePlayRandom(void* self, unsigned int seed) {
    if (*static_cast<std::uint64_t*>(self) != kSpriteMagic) std::exit(42);
    Record(&g_playRandom, "", 0, static_cast<int>(seed));
}

void RecordRender(RenderCall* call, int layer, const void* position, const void* topLeft,
                  const void* bottomRight) {
    const auto* p = static_cast<const float*>(position);
    const auto* t = static_cast<const float*>(topLeft);
    const auto* b = static_cast<const float*>(bottomRight);
    call->layer = layer;
    call->positionX = p[0];
    call->positionY = p[1];
    call->topLeftX = t[0];
    call->topLeftY = t[1];
    call->bottomRightX = b[0];
    call->bottomRightY = b[1];
    ++call->calls;
}

void FakeRender(void* self, const void* position, const void* topLeft, const void* bottomRight) {
    if (*static_cast<std::uint64_t*>(self) != kSpriteMagic) std::exit(43);
    RecordRender(&g_render, -1, position, topLeft, bottomRight);
}

void FakeRenderLayer(void* self, int layer, const void* position, const void* topLeft,
                     const void* bottomRight) {
    if (*static_cast<std::uint64_t*>(self) != kSpriteMagic) std::exit(44);
    RecordRender(&g_renderLayer, layer, position, topLeft, bottomRight);
}

// 假的 libc++ `basic_string::assign(char const*)`：只验证**调用序列与内容**（零初始化 → assign(路径)
// → 把同一个对象交给 Load）。真实的 libc++ SSO 布局由真机探针校验，宿主侧不假装知道它。
char g_lastAssignedPath[256] = {};
int g_assignCalls = 0;
int g_loadCalls = 0;
int g_loadGraphicsCalls = 0;
int g_replaceCalls = 0;
int g_replaceLayer = -1;
const void* g_lastStringObject = nullptr;
void* g_stringObjects[4] = {};
int g_stringObjectCount = 0;

// 假 `basic_string::assign`：短路径写成 libc++ 的短串形式（首字节 = size<<1、内容内联），
// 长路径写成 `__long`（首字最低位 = 1、数据指针在偏移 16）并 malloc 一份 —— 这样可以验证
// "长路径必须用**游戏自己的** operator delete 释放，而不是本模块的 free"。
int g_releasedLongStrings = 0;
void* g_lastReleased = nullptr;

void FakeOperatorDelete(void* pointer) {
    ++g_releasedLongStrings;
    g_lastReleased = pointer;
    std::free(pointer);
}

void* FakeAssign(void* object, const char* text) {
    if (object == nullptr || text == nullptr) std::exit(51);
    std::snprintf(g_lastAssignedPath, sizeof(g_lastAssignedPath), "%s", text);
    ++g_assignCalls;
    auto* bytes = static_cast<unsigned char*>(object);
    const std::size_t length = std::strlen(text);
    std::memset(bytes, 0, 24);
    if (length <= 22) {
        bytes[0] = static_cast<unsigned char>(length << 1);
        std::memcpy(bytes + 1, text, length);
    } else {
        char* copy = static_cast<char*>(std::malloc(length + 1));
        std::memcpy(copy, text, length + 1);
        auto* words = reinterpret_cast<std::uint64_t*>(bytes);
        words[0] = (length + 1) | 1ULL;   // long 标志在最低位
        words[1] = length;
        words[2] = reinterpret_cast<std::uint64_t>(copy);
    }
    return object;
}

// 按 libc++ 的真实布局校验原生字符串对象装的是 `g_lastAssignedPath`：
// 短串首字节 = size<<1、内容内联在 +1；长串首字最低位 = 1、大小在 +8、数据指针在 +16。
void CheckNativeString(const void* nativeString, int code) {
    if (nativeString == nullptr) std::exit(code);
    const auto* bytes = static_cast<const unsigned char*>(nativeString);
    const auto* words = static_cast<const std::uint64_t*>(nativeString);
    const std::size_t length = std::strlen(g_lastAssignedPath);
    if ((words[0] & 1ULL) == 0) {
        if (bytes[0] != (length << 1)) std::exit(code);
        if (std::strncmp(reinterpret_cast<const char*>(bytes + 1), g_lastAssignedPath, length) != 0) {
            std::exit(code);
        }
        return;
    }
    if (words[1] != length) std::exit(code);
    const auto* data = reinterpret_cast<const char*>(words[2]);
    if (data == nullptr || std::strcmp(data, g_lastAssignedPath) != 0) std::exit(code);
}

void FakeLoad(void* self, const void* nativeString, bool loadGraphics) {
    if (*static_cast<const std::uint64_t*>(self) != kSpriteMagic) std::exit(52);
    CheckNativeString(nativeString, 53);
    g_lastStringObject = nativeString;
    if (g_stringObjectCount < 4) {
        g_stringObjects[g_stringObjectCount] = const_cast<void*>(nativeString);
    }
    ++g_stringObjectCount;
    g_play.flag = loadGraphics ? 1 : 0;  // 复用字段记录 bool 实参
    ++g_loadCalls;
}

void FakeLoadGraphics(void* self) {
    if (*static_cast<const std::uint64_t*>(self) != kSpriteMagic) std::exit(54);
    ++g_loadGraphicsCalls;
}

void FakeReplaceSpritesheet(void* self, int layer, const void* nativeString) {
    if (*static_cast<const std::uint64_t*>(self) != kSpriteMagic) std::exit(55);
    CheckNativeString(nativeString, 56);
    g_replaceLayer = layer;
    ++g_replaceCalls;
}

const char* kScript = R"lua(@SCRIPT@)lua";

int Report(int code) {
    std::printf("SPRITE_FAIL code=%d ctor=%d dtor=%d update=%d play=%s/%d setAnim=%s/%d "
                "setFrameNamed=%s/%d setFrame=%d layer=%d/%d random=%d render=%.1f,%.1f tl=%.1f,%.1f "
                "br=%.1f,%.1f renderLayer=%d %.1f,%.1f\n",
                code, g_ctorCalls, g_dtorCalls, g_updateCalls, g_play.name, g_play.flag,
                g_setAnimation.name, g_setAnimation.flag, g_setFrameNamed.name,
                g_setFrameNamed.number, g_setFrame.number, g_setLayerFrame.flag,
                g_setLayerFrame.number, g_playRandom.number, g_render.positionX, g_render.positionY,
                g_render.topLeftX, g_render.topLeftY, g_render.bottomRightX, g_render.bottomRightY,
                g_renderLayer.layer, g_renderLayer.positionX, g_renderLayer.positionY);
    return code;
}

} // namespace

int main() {
    LuaRuntime::LuaSpriteBindings bindings{};
    bindings.ctor = reinterpret_cast<uintptr_t>(&FakeCtor);
    bindings.destructor_ = reinterpret_cast<uintptr_t>(&FakeDtor);
    bindings.play = reinterpret_cast<uintptr_t>(&FakePlay);
    bindings.setAnimation = reinterpret_cast<uintptr_t>(&FakeSetAnimation);
    bindings.setFrameNamed = reinterpret_cast<uintptr_t>(&FakeSetFrameNamed);
    bindings.setFrame = reinterpret_cast<uintptr_t>(&FakeSetFrame);
    bindings.getFrame = reinterpret_cast<uintptr_t>(&FakeGetFrame);
    bindings.setLayerFrame = reinterpret_cast<uintptr_t>(&FakeSetLayerFrame);
    bindings.getLayerFrame = reinterpret_cast<uintptr_t>(&FakeGetLayerFrame);
    bindings.update = reinterpret_cast<uintptr_t>(&FakeUpdate);
    bindings.isPlaying = reinterpret_cast<uintptr_t>(&FakeIsPlaying);
    bindings.isFinished = reinterpret_cast<uintptr_t>(&FakeIsFinished);
    bindings.render = reinterpret_cast<uintptr_t>(&FakeRender);
    bindings.renderLayer = reinterpret_cast<uintptr_t>(&FakeRenderLayer);
    bindings.playRandom = reinterpret_cast<uintptr_t>(&FakePlayRandom);
    bindings.load = reinterpret_cast<uintptr_t>(&FakeLoad);
    bindings.loadGraphics = reinterpret_cast<uintptr_t>(&FakeLoadGraphics);
    bindings.replaceSpritesheet = reinterpret_cast<uintptr_t>(&FakeReplaceSpritesheet);
    bindings.libcxxStringAssign = reinterpret_cast<uintptr_t>(&FakeAssign);
    bindings.gameOperatorDelete = reinterpret_cast<uintptr_t>(&FakeOperatorDelete);
    LuaRuntime::SetSpriteBindings(bindings);

    const auto result = LuaRuntime::InitializeFromBuffer(kScript, std::strlen(kScript), "@sprite.lua");
    if (result != LuaRuntime::LuaInitResult::Success) return 2;
    LuaRuntime::DispatchPostUpdate();
    if (LuaRuntime::TakeCallbackError()) return 3;

    // 脚本里的断言（用退出码报告，方便定位）。
    if (g_ctorCalls != 1) return Report(11);
    if (std::strcmp(g_play.name, "Idle") != 0 || g_play.flag != 1 || g_play.calls != 1) return Report(12);
    if (std::strcmp(g_setAnimation.name, "Walk") != 0 || g_setAnimation.flag != 0) return Report(13);
    if (std::strcmp(g_setFrameNamed.name, "Walk") != 0 || g_setFrameNamed.number != 6) return Report(14);
    if (g_setFrame.number != 5 || g_setFrame.calls != 1) return Report(15);
    if (g_setLayerFrame.flag != 2 || g_setLayerFrame.number != 9) return Report(16);
    if (g_updateCalls != 2) return Report(17);
    // 省掉动画名的两次调用必须走"空串"路径（引擎语义：只看当前动画）
    if (g_isPlayingAnyCalls != 1) return Report(18);
    if (g_isFinishedAnyCalls != 1) return Report(19);
    if (g_playRandom.number != 1234) return Report(18);
    if (g_render.calls != 1 || g_render.positionX != 10.0f || g_render.positionY != 20.0f ||
        g_render.topLeftX != 0.0f || g_render.topLeftY != 0.0f || g_render.bottomRightX != 0.0f ||
        g_render.bottomRightY != 0.0f) {
        return Report(19);
    }
    if (g_renderLayer.calls != 1 || g_renderLayer.layer != 3 || g_renderLayer.positionX != 1.0f ||
        g_renderLayer.positionY != 2.0f || g_renderLayer.topLeftX != -1.0f ||
        g_renderLayer.bottomRightX != 5.0f) {
        return Report(20);
    }
    // 3 次常规 Load + 40 次"塞满缓存"的长路径 Load
    if (g_loadCalls != 3 + 40) return Report(22);
    if (g_loadGraphicsCalls != 1) return Report(23);
    // 2 次 `ReplaceSpritesheet`：一次真实路径（层 4）、一次**空串**（层 1，EID 的清层用法）。
    if (g_replaceCalls != 2 || g_replaceLayer != 1) return Report(24);
    // 同一路径的两次 Load 必须复用同一个原生字符串对象；不同路径必须换一个（缓存按路径键控）。
    // 前三次（三个不同路径）必须各自拿到一个对象；后面 40 次长路径会复用/轮转缓存，所以只看 >= 3。
    if (g_stringObjectCount < 3) return Report(25);
    if (g_stringObjects[0] != g_stringObjects[1]) return Report(26);
    if (g_stringObjects[2] == g_stringObjects[0]) return Report(27);
    // 三个**不同**路径 -> 恰好三次 assign（同路径的第二次 Load 命中缓存，不再 assign）
    // +1：`ReplaceSpritesheet(1, '')` 也要建一次原生字符串（空串是一条独立缓存条目）。
    if (g_assignCalls != 3 + 40 + 1) return Report(28);
    // 缓存塞满 40 条长路径后，必须有条目被释放，而且走的是游戏自己的 operator delete。
    if (g_releasedLongStrings < 8) return Report(29);
    if (g_lastReleased == nullptr) return Report(30);
    if (g_dtorCalls != 1) return Report(21);
    std::printf("SPRITE_OK ctor=%d dtor=%d update=%d any=%d/%d released=%d\n", g_ctorCalls,
                g_dtorCalls, g_updateCalls, g_isPlayingAnyCalls, g_isFinishedAnyCalls,
                g_releasedLongStrings);
    return 0;
}
'''


SCRIPT = r'''
local mod = RegisterMod('Sprite', 1)

local function near(actual, expected, label)
  if math.abs(actual - expected) > 0.0005 then
    error(label .. ': expected ' .. expected .. ', got ' .. actual)
  end
end

mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function()
  local ok, err = pcall(function()
  local sprite = Sprite()
  -- 刚构造的 Sprite 没有加载图形（引擎自己的标志 +0x149）
  if sprite:IsLoaded() then error('a fresh Sprite must not report IsLoaded') end

  -- 字符串桥：同一路径两次 Load 必须复用同一个原生字符串对象（缓存），另加一次不同路径。
  sprite:Load('gfx/probe147.anm2', true)
  sprite:Load('gfx/probe147.anm2', true)
  sprite:Load('gfx/other.anm2', false)
  sprite:LoadGraphics()
  sprite:ReplaceSpritesheet(4, 'gfx/replaced.png')
  -- ★ 空串是**合法**参数（2026-09-12 真机报告 `01789225234` 定案）：EID 的
  -- `loadCustomSprites` 先用空串清掉某一层的图集替换、再用真实路径替换。旧实现把空串
  -- 当成"建字符串失败"抛 Lua 错误 ⇒ 整条描述构建被打断（进到道具范围内什么都不显示）。
  -- 这条断言就是钉住"空串不再报错"。
  sprite:ReplaceSpritesheet(1, '')

  sprite:Play('Idle', true)
  if not sprite:SetAnimation('Walk', false) then error('SetAnimation must return the native bool') end
  sprite:SetFrame(5)
  sprite:SetFrame('Walk', 6)
  if sprite:GetFrame() ~= 7 then error('GetFrame must return the native value') end
  sprite:SetLayerFrame(2, 9)
  if sprite:GetLayerFrame(4) ~= 104 then error('GetLayerFrame must return the native value') end
  sprite:Update()
  sprite:Update()
  if not sprite:IsPlaying('Idle') then error('IsPlaying(Idle)') end
  if sprite:IsPlaying('Walk') then error('IsPlaying(Walk) must be false') end
  if not sprite:IsFinished('Done') then error('IsFinished(Done)') end
  -- PC 文档形参是 [0, 1]：省掉动画名也必须可用，且传下去的是**空串**（引擎的"只看当前动画"语义）
  if not sprite:IsPlaying() then error('IsPlaying() must ask for the current animation') end
  if sprite:IsFinished() then error('IsFinished() must be false in the fake') end
  sprite:PlayRandom(1234)

  -- `Sprite:GetTexel`（2026-09-12）：EID 的 `IsAltChoice`（`main.lua:230`）用它逐像素比对
  -- 底座贴图，PC 签名是 `GetTexel(Vector SamplePos, Vector RenderPos = Vector.Zero,
  -- float AlphaThreshold = 0, int LayerID = 0)`，返回 KColor。
  -- 缺这个方法就是"调 nil"、整条渲染链被派发器摘除（真机报告 `01789211565`）。
  --
  -- ★ 本轮（动作一）把它从"恒返回不透明白色"的占位换成**调用引擎自己的
  -- `ANM2::GetTexel`**（偏移 `0xC074`，HFA 按值传 s0..s3、返回值走 x8）。
  -- 宿主侧没有引擎模块，所以这里只能钉"名字在、返回类型对、有默认值"——
  -- 真实像素读数由真机探针 `SpriteGetTexelProbeSnapshot` 负责（引擎调用次数 / 落值次数 /
  -- 原因码 / 最近一次颜色）。占位时代"两个 sprite 处处相等 → 每个底座都被判成赎罪线
  -- 问号底座"的偏差，就是靠这条真读数在真机上判定的。
  local texel = sprite:GetTexel(Vector(1, 2), Vector(0, 0), 1, 1)
  if type(texel) ~= 'userdata' then error('GetTexel must return a KColor userdata') end
  if type(texel.Red) ~= 'number' or type(texel.Green) ~= 'number' or type(texel.Blue) ~= 'number' then
    error('GetTexel must return a KColor with numeric channels')
  end
  -- PC 的后三个形参都有默认值：只给采样点也必须可用（不能报错、仍返回 KColor）。
  local shortTexel = sprite:GetTexel(Vector(1, 2))
  if type(shortTexel) ~= 'userdata' then
    error('GetTexel must accept the sample position alone (the other three have defaults)')
  end
  -- 参数**类型**不对仍必须报错（与其它成员同一口径）：阈值必须是数字、层号必须是整数。
  if pcall(function() return sprite:GetTexel(Vector(1, 2), Vector(0, 0), 'nope', 1) end) then
    error('GetTexel must reject a non-number alpha threshold')
  end
  if pcall(function() return sprite:GetTexel(Vector(1, 2), Vector(0, 0), 1, 1.5) end) then
    error('GetTexel must reject a non-integer layer id')
  end

  -- Render 的 clamp 参数有默认值（Vector.Zero），位置是 Vector 值类型
  sprite:Render(Vector(10, 20))
  sprite:RenderLayer(3, Vector(1, 2), Vector(-1, 0), Vector(5, 0))

  -- 参数类型不对必须报错，而不是把垃圾送进原生入口
  local bad = {
    function() return sprite:Play() end,
    function() return sprite:Play(1, true) end,
    function() return sprite:SetFrame() end,
    function() return sprite:SetFrame('Walk') end,
    function() return sprite:SetFrame(1, 2, 3) end,
    function() return sprite:Render() end,
    function() return sprite:Render(1, 2) end,
    function() return sprite:RenderLayer(1, Vector(0, 0), Vector(0, 0), Vector(0, 0), 5) end,
    function() return sprite:GetLayerFrame('x') end,
    function() return sprite:Update(1) end,
    function() return sprite:IsPlaying(1) end,
    function() return sprite:IsPlaying('Idle', 1) end,
    function() return sprite:IsFinished(1) end,
    function() return sprite:Load() end,
    function() return sprite:Load(1, true) end,
    function() return sprite:ReplaceSpritesheet(1) end,
    function() return sprite:LoadGraphics(1) end,
  }
  for index, call in ipairs(bad) do
    if pcall(call) then error('expected an error from bad call #' .. index) end
  end

  -- 长路径（> 22 字节）会走 libc++ 的 `__long`：缓存（32 条）塞满后，最旧一条必须用
  -- **游戏自己的** operator delete 释放（本模块的 free 会踩坏堆）。
  for index = 1, 40 do
    sprite:Load('gfx/items/collectibles/probe_long_path_padding_' .. index .. '.anm2', true)
  end

  -- 显式回收：析构必须恰好一次（handle 幂等）
  sprite = nil
  collectgarbage('collect')
  collectgarbage('collect')
  end)
  if not ok then print('SPRITE_LUA_ERROR: ' .. tostring(err)) end
end)
'''


class LuaSpriteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-lua-sprite-")
        temporary = Path(cls.temporary.name)
        compatibility = temporary / "compatibility"
        compatibility.mkdir()
        (compatibility / "stdfloat").write_text(
            "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n"
        )
        harness = temporary / "lua_sprite_harness.cpp"
        harness.write_text(HARNESS.replace("@SCRIPT@", SCRIPT).lstrip())
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
        cls.harness = temporary / "lua_sprite_harness"
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

    def test_sprite_step_one_marshals_arguments_and_owns_its_object(self):
        result = subprocess.run([str(self.harness)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("SPRITE_LUA_ERROR", result.stdout)
        self.assertIn("SPRITE_OK ctor=1 dtor=1 update=2", result.stdout)
        self.assertIn("released=", result.stdout)

    def test_sprite_object_size_and_alignment_come_from_the_stride_audit(self):
        constants = (SOURCE / "runtime_constants.hpp").read_text(encoding="utf-8")
        self.assertIn("inline constexpr std::size_t kSpriteObjectSize = 0x158;", constants)
        self.assertIn("inline constexpr std::size_t kSpriteObjectAlignment = 16;", constants)
        self.assertIn("inline constexpr std::size_t kSpriteLoadedFlagOffset = 0x149;", constants)
        # 每个入口都要有守卫，且守卫必须与镜像逐字节一致（由守卫核对工具覆盖）。
        for name in ("Ctor", "Destructor", "Play", "SetAnimation", "SetFrameNamed", "SetFrame",
                     "GetFrame", "SetLayerFrame", "GetLayerFrame", "Update", "IsPlaying",
                     "IsFinished", "Render", "RenderLayer", "PlayRandom"):
            with self.subTest(entry=name):
                self.assertIn(f"kSprite{name}Offset", constants)
                self.assertIn(f"kSprite{name}ExpectedBytes", constants)


if __name__ == "__main__":
    unittest.main()
