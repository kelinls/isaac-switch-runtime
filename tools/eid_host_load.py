"""宿主机验证通道：用**仓库里真实的 Lua Runtime 源码**在本机跑真实 PC Mod EID 的 `main.lua`。

## 为什么需要它

真机一轮只能问一个问题，而 EID 现在卡在"加载期 Lua 抛错"上：最新报告 `01789201566` 只说明了

* 诊断字 `[11] = 0x515` → `LuaInit` + detail 5 = `ScriptRunFailed`（编译成功、执行抛错）；
* 错误文本长度 102 字节、前 8 字节是 `..._mods`。

"到底哪一行、什么消息"在后 41 个字节里，而这 41 个字节必须有第二次真机才能拿到。本工具把同一条
路径搬到宿主机：**用同一份 `runtime/source` + `runtime/src` 源码**（`lua_runtime.cpp` 与
`interfaces/lua/*`、`application/mod/*`、`infrastructure/*`，加上 vendored Lua 5.3.3），
按真机的顺序走完

    读 `rom:/isaac_mods/manifest.json`
      → `ManifestService::Resolve`（拼 `entryPath` / `modRoot` / `chunkName`）
      → `ModLoadService::Load` 按 `entryPath` 读入口脚本
      → `EmbeddedLuaAdapter::LoadManifestMod` → `LuaRuntime::InitializeManifestMod`
      → `luaL_loadbufferx` + `lua_pcallk`

并把执行期的 Lua 错误文本**整段**取出来（`LuaRuntime::CopyLastLuaErrorText`，探针用的同一条通道），
于是"行号 + 消息"在宿主机上就能看到，不用再花一轮真机。

## chunk name 与错误文本的形状（与真机对齐的判据）

真机传给 `luaL_loadbufferx` 的 chunk name 是 `"@" + entryPath`，即
`@rom:/isaac_mods/mods/external item descriptions_836319872/main.lua`（`@` 后 66 字符）。
Lua 5.3.3 的 `luaO_chunkid`（`third_party/lua-5.3.3/src/lobject.c`）对超长文件名输出
`"..."` + 名字末 57 字节，因此错误信息以 `..._mods/mods/external item descriptions_836319872/main.lua:`
开头（61 字节），后面才是"行号 + 消息"。本工具把这条形状作为**一致性检查**打印出来：

* `textLength` 与真机的 102 字节比对；
* 前 8 字节与真机的 `..._mods` 比对。

对不上时会明确报出差异，而不是假装一致 —— 宿主机没有引擎（`Isaac.GetItemConfig()` 之类的调用
返回的是我们自己的桩值），所以"宿主机与真机在**同一个** API 上失败"才算真正复现。

## 用法

    python3 tools/eid_host_load.py                     # 跑一次，报出结果/错误原文/模块:行号/触发器
    python3 tools/eid_host_load.py --max-bytes 16384    # 复现 16 KiB 缓冲区时代的 EntryRead 失败
    python3 tools/eid_host_load.py --sweep              # 逐次补偿，列出加载期**所有**阻塞点
    python3 tools/eid_host_load.py --trace-addcallback  # 插桩打印每一次 Mod:AddCallback（有序）
    python3 tools/eid_host_load.py --callback-probe     # 同一 Mod 同一 id 多次登记是否并存（PC 契约）
    python3 tools/eid_host_load.py --engine-shims both --font-load fail --keep --json

## 宿主机与真机的已知差异（必须显式说明，不许假装一致）

* **没有引擎**：真机上 `Sprite`/`Font` 家族的入口由 Hook 安装阶段校验后经
  `SetSpriteBindings`/`SetFontBindings` 发布。宿主 harness 默认（`--engine-shims inert`）发布一组
  **惰性**宿主实现，让调用成功返回但不做引擎工作；`--engine-shims none` 则完全不发布，脚本会停在
  第一处引擎调用（`main.lua:61` 的 `Sprite()`）并报 "Sprite native binding is unavailable"。
* **`Font:Load` 的返回值**：`--font-load fail` 让 `Load`/`IsLoaded` 都答假，复现真机"引擎读不到
  `.fnt`"的分叉 —— EID 会在字体块里**顶层 `return`**：脚本"成功结束"，但之后所有
  `Mod:AddCallback`（含 `MC_POST_UPDATE`/`MC_POST_RENDER`）都不执行（报告 `01789203482`/`01789203805`
  的普查就是这样）。这是本工具最有用的一个开关。
* **`ItemConfig` / `os` 两个全局**：`--sweep` 会补成宽容桩以便继续往下走；桩的数值是假的，
  只用于"找出阻塞点"，不用于判断语义是否等价。
* 宿主峰值内存用 `getrusage(RUSAGE_SELF)`；真机的模块假堆用量是另一套读数（`HeapUsedBytes()`，
  宿主上不可用）。

夹具不存在时**不是**失败：EID 目录在 `.gitignore` 覆盖的 `analysis/` 下，工具会打印可读的说明并
以退出码 77（`unittest` 的 skip 约定）退出；测试侧据此 `skipTest`，不让它成为硬依赖。
"""

from __future__ import annotations

import argparse
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "runtime" / "source"
SRC = ROOT / "runtime" / "src"

#: 真机上的 romfs 内容目录（Program ID 与 `dist/` 部署树一致）。
CONTENT_ID = "010021C000B6A000"
EID_DIRECTORY = "external item descriptions_836319872"
EID_ENTRY_RELATIVE = f"mods/{EID_DIRECTORY}/main.lua"

#: 真机报告 `01789201566` 里被截断的错误文本前缀（`luaO_chunkid` 的 `"..."` + 末 57 字节）。
DEVICE_ERROR_PREFIX = b"..._mods"
#: 真机报告的 `X[04] = 0x66` = 102。
DEVICE_ERROR_LENGTH = 102
#: `@rom:/isaac_mods/mods/external item descriptions_836319872/main.lua` 的长度（`@` 后 66 字符）。
DEVICE_CHUNK_NAME = f"@rom:/isaac_mods/{EID_ENTRY_RELATIVE}"
#: 错误文本里 chunk 位置前缀的长度：`luaO_chunkid` 输出 `"..."` + 路径末 56 字节（共 59），
#: 再加 `luaG_addinfo` 的 `:` —— 60。真机 102 字节减去它，剩下的就是"行号位数 + 2 + 消息长度"。
LUA_CHUNKID_PREFIX_LENGTH = 60

#: 三种可能的 romfs 根，按"离真机最近"排序：
#:   1. 设备状态树（`runtime/.eid-artifacts`，清单里 EID 已启用、`disable.it` 已摘掉）；
#:   2. `analysis/` 下的未跟踪夹具（EID 被 `disable.it` 禁用，需要就地启用后跑）。
_ROMFS_CANDIDATES = (
    ROOT / "runtime/.eid-artifacts/deploy/atmosphere/contents" / CONTENT_ID / "romfs/isaac_mods",
    ROOT / "analysis/pc-mod-contract/romfs-preview/atmosphere/contents" / CONTENT_ID
    / "romfs/isaac_mods",
)


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------
#
# 与 `runtime/tests/test_lua_entity_query.py` 同形：真实运行时源码 + vendored Lua 5.3.3，
# 宿主侧只补"引擎观测缝"（`game_observer.hpp` 的那几个入口）和**文件系统**——
# `GameFileReader::ReadTextFile` 在真机上由 SaltyNX/引擎实现，宿主机上改成按同一套语义读真实文件：
#   * 路径必须是 `rom:/isaac_mods/...`，其余一律 `InvalidArgument`；
#   * 文件不存在 → `OpenFailed`；
#   * `length <= 0 || length > capacity` → `LengthOutOfRange`（**并把观测到的长度交出去**，
#     与真机 `game_file_reader.cpp` 的行为一致，这正是 16 KiB 那次能定位到根因的通道）；
#   * 读到的字节数不符 → `ReadMismatch`。
HARNESS = r'''
#include "application/mod/manifest_service.hpp"
#include "application/mod/mod_load_service.hpp"
#include "infrastructure/content/game_file_reader_adapter.hpp"
#include "infrastructure/lua/embedded_lua_adapter.hpp"
#include "infrastructure/mod/manifest_selector_adapter.hpp"

#include "game_file_reader.hpp"
#include "game_observer.hpp"
#include "application/callback/callback_registry.hpp"
#include "interfaces/lua/font_api.hpp"
#include "lua_runtime.hpp"
#include "lua_runtime_state.hpp"
#include "runtime_constants.hpp"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <sys/resource.h>
#include <vector>

// --- 引擎观测缝：真机上由各自 family 的实现提供，宿主机一律答"不可用" ------------------
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

namespace {

// 宿主 romfs 根（`.../romfs/isaac_mods` 的宿主路径）。
std::string g_RomfsRoot;

// 读请求轨迹：出错时用来说明"这条路径到底有没有被读过、读成没读成"（诊断用，条数有上限）。
// 每条形如 `<相对路径>|<ok|missing|toobig|mismatch>` —— `require` 的成败因此可以直接看出来。
std::vector<std::string> g_Reads;
constexpr std::size_t kMaximumRecordedReads = 512;

std::string ReadTrace() {
    std::string text;
    for (std::size_t index = 0; index < g_Reads.size(); ++index) {
        if (index != 0) text += ",";
        text += g_Reads[index];
    }
    return text;
}

std::string JsonEscape(const std::string& text) {
    std::string out;
    out.reserve(text.size() + 8);
    for (const char raw : text) {
        const unsigned char value = static_cast<unsigned char>(raw);
        switch (raw) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (value < 0x20) {
                    char buffer[8]{};
                    std::snprintf(buffer, sizeof(buffer), "\\u%04x", value);
                    out += buffer;
                } else {
                    out += raw;
                }
        }
    }
    return out;
}

long PeakRss() {
    struct rusage usage{};
    if (getrusage(RUSAGE_SELF, &usage) != 0) {
        return -1;
    }
    return usage.ru_maxrss;  // macOS: 字节；Linux: KiB（报告侧按平台换算）
}

std::string ToHex(const char* data, std::size_t length) {
    static constexpr char kDigits[] = "0123456789abcdef";
    std::string out;
    out.reserve(length * 2);
    for (std::size_t index = 0; index < length; ++index) {
        const unsigned char value = static_cast<unsigned char>(data[index]);
        out += kDigits[value >> 4];
        out += kDigits[value & 0x0F];
    }
    return out;
}

const char* LuaInitResultName(LuaRuntime::LuaInitResult result) {
    switch (result) {
        case LuaRuntime::LuaInitResult::Success: return "Success";
        case LuaRuntime::LuaInitResult::StateCreateFailed: return "StateCreateFailed";
        case LuaRuntime::LuaInitResult::RuntimePreparationMemoryFailed:
            return "RuntimePreparationMemoryFailed";
        case LuaRuntime::LuaInitResult::RuntimePreparationFailed: return "RuntimePreparationFailed";
        case LuaRuntime::LuaInitResult::ScriptLoadFailed: return "ScriptLoadFailed";
        case LuaRuntime::LuaInitResult::ScriptRunFailed: return "ScriptRunFailed";
        case LuaRuntime::LuaInitResult::MissingPostUpdateCallback:
            return "MissingPostUpdateCallback";
    }
    return "?";
}

const char* StepName(isaac::runtime::ModLoadStep step) {
    switch (step) {
        case isaac::runtime::ModLoadStep::None: return "None";
        case isaac::runtime::ModLoadStep::ManifestRead: return "ManifestRead";
        case isaac::runtime::ModLoadStep::ManifestParse: return "ManifestParse";
        case isaac::runtime::ModLoadStep::PathBuild: return "PathBuild";
        case isaac::runtime::ModLoadStep::EntryRead: return "EntryRead";
        case isaac::runtime::ModLoadStep::LuaInit: return "LuaInit";
        case isaac::runtime::ModLoadStep::Request: return "Request";
    }
    return "?";
}

const char* StateName(isaac::runtime::ModScriptState state) {
    switch (state) {
        case isaac::runtime::ModScriptState::Executed: return "Executed";
        case isaac::runtime::ModScriptState::DeclaredScriptless: return "DeclaredScriptless";
        case isaac::runtime::ModScriptState::EntryAbsent: return "EntryAbsent";
    }
    return "?";
}

} // namespace

namespace GameFileReader {
TextReadResult ReadTextFile(const Bindings&, const char* path, u8* buffer,
                            std::size_t capacity, std::size_t* outputLength) {
    constexpr const char* kPrefix = "rom:/isaac_mods/";
    if (path == nullptr || path[0] == '\0' || buffer == nullptr || capacity == 0 ||
        outputLength == nullptr) {
        return TextReadResult::InvalidArgument;
    }
    if (std::strncmp(path, kPrefix, std::strlen(kPrefix)) != 0) {
        return TextReadResult::InvalidArgument;
    }
    const std::string relative = path + std::strlen(kPrefix);
    // 轨迹里带上**观测到的字节数**：`require` 读进来的 Lua 源码总量因此可以统计（量级问题）。
    const auto record = [&relative](const char* status, long bytes) {
        if (g_Reads.size() < kMaximumRecordedReads) {
            g_Reads.push_back(relative + "|" + status + "|" + std::to_string(bytes));
        }
    };
    const std::string hostPath = g_RomfsRoot + "/" + relative;
    std::FILE* file = std::fopen(hostPath.c_str(), "rb");
    if (file == nullptr) {
        record("missing", 0);
        return TextReadResult::OpenFailed;
    }
    std::fseek(file, 0, SEEK_END);
    const long fileLength = std::ftell(file);
    std::fseek(file, 0, SEEK_SET);
    if (fileLength <= 0 || static_cast<std::size_t>(fileLength) > capacity) {
        // 与真机 `game_file_reader.cpp` 一致：超长时把**观测到的长度**交出去。
        if (fileLength > 0) {
            *outputLength = static_cast<std::size_t>(fileLength);
        }
        record("toobig", fileLength > 0 ? fileLength : 0);
        std::fclose(file);
        return TextReadResult::LengthOutOfRange;
    }
    const std::size_t wanted = static_cast<std::size_t>(fileLength);
    const std::size_t bytesRead = std::fread(buffer, 1, wanted, file);
    std::fclose(file);
    if (bytesRead != wanted) {
        record("mismatch", static_cast<long>(bytesRead));
        return TextReadResult::ReadMismatch;
    }
    *outputLength = wanted;
    record("ok", static_cast<long>(wanted));
    return TextReadResult::Success;
}
} // namespace GameFileReader

// --- 宿主惰性引擎绑定（`Sprite` 家族）----------------------------------------------
//
// 真机上 `Sprite` 家族的入口地址由 Hook 安装阶段校验后经 `SetSpriteBindings` 发布，所以
// `Sprite()` / `Sprite:Load(...)` 在真机上**正常工作**；宿主机没有引擎，若不发布绑定，
// 加载脚本会在第一处引擎调用（`main.lua:61` 的 `Sprite()`）就抛
// "Sprite native binding is unavailable" —— 这是**宿主机环境差异**，不是真机的问题。
//
// 所以宿主 harness 发布一组**惰性**宿主实现：接口形状（参数、返回值）与引擎一致，调用**成功
// 返回**，但不做任何引擎工作。于是"真机有引擎、宿主没有"这条差异被压到最小，脚本可以继续
// 往下跑到真正与引擎无关的失败点。惰性带来的偏差也必须写明（报告里的 `engineShims` 字段）：
//   * `Load`/`Play` 等都是空实现，`Load` 之后**没有真的图形数据**；
//   * ctor 把 `kSpriteLoadedFlagOffset` 置 1，与引擎 `ANM2::Load` 成功后的读数一致
//     （`Sprite:IsLoaded()` 读的就是这一字节）；
//   * `libcxxStringAssign` 只把对象保持为全零 —— 那在 libc++ 里是**合法的空短串**，
//     而惰性 `Load` 不会去读它。
void HostSpriteCtor(void* sprite) {
    if (sprite == nullptr) {
        return;
    }
    std::memset(sprite, 0, kSpriteObjectSize);
    reinterpret_cast<unsigned char*>(sprite)[kSpriteLoadedFlagOffset] = 1;
}
void* HostLibcxxStringAssign(void* object, const char*) {
    return object;
}
void HostSpriteDestructor(void*) {}
void HostSpriteLoad(void*, const void*, bool) {}
void HostSpriteLoadGraphics(void*) {}
void HostSpriteReplaceSpritesheet(void*, int, const void*) {}
void HostSpritePlay(void*, const char*, bool) {}

// `Font` 家族同理：真机上由 `SetFontBindings` 发布，宿主机上没有就报 "Font native binding is
// unavailable"，而 EID 在 `main.lua:165` 就 `Font()`。惰性实现让这一族"能用但不干活"：
// `Load` 返回真（真机加载成功时的返回值）、`IsLoaded` 返回真（EID 用它判成败）。
void HostFontCtor(void* font) {
    if (font != nullptr) {
        std::memset(font, 0, kFontObjectSize);
    }
}
void HostFontDestructor(void*) {}
// `Font:Load` 的返回值可切换：真机上是**引擎真的去读 .fnt 文件**，读不到就返回 false，
// 而 EID 在 `main.lua:187` 拿到 false 会**顶层 `return`** —— 脚本"成功结束"，
// 但后面所有 `Mod:AddCallback`（含 `MC_POST_UPDATE`/`MC_POST_RENDER`）都不会执行。
// 这就是"加载成功却什么都不显示"的关键分叉，所以宿主 harness 必须能把两边都跑出来。
// ★ 判定点其实是 `IsLoaded()` 而不是 `Load()` 的返回值：EID 的 `EID:loadFont`
// （`features/eid_api.lua:577`）做完 `Load` 后看的是 `EID.font:IsLoaded()`，false 才算失败。
// 所以这一个开关同时驱动 `Load` 与 `IsLoaded` —— 真机上"文件读不到"就是两者都 false。
bool g_FontLoadResult = true;
// `Font:Load` 的**入参口径**（第七个参数 `font-arg`）。
//
// 真机上 `Font:Load` 最终落到引擎的资源解析：只有**内容挂载点相对名**（`font/eid_default.fnt`）
// 才解析得到。宿主 harness 惰性 shim 原本对任何字符串都答"成功"，于是"EID 传进来的名字对不对"
// 这条信息被抹掉。这里加一档 `strict`：只有 `font/eid_default.fnt` 这种口径才答真，其它一律答假 ——
// 用来在不动真机的前提下验证"路径归一化确实把名字修对了"。
//   * `any`（默认）：任何名字都成功（旧行为，只关心脚本能否跑完）
//   * `strict`：只接受内容挂载点相对名（不含 `rom:/`、不含 `/../`、不以 `mods/` 开头）
const char* g_FontArgMode = "any";
// 归一化探针：把 argv[8] 喂给 Lua 绑定层用的**同一个**归一化实现（见下方 `RunNormalizeProbe`）。
std::string g_NormalizeProbeRaw;
std::string g_NormalizeProbeOut;
// `Load`/`IsLoaded` 的分段返回档（第八个参数 `font-phase`），用来镜像真机那两段行为：
//   * `all`（默认）：一次调用内 `Load` 与 `IsLoaded` 都按上面算出来的结果
//   * `first-fails`：**第 1 次** `Load` 答假且 `IsLoaded` 也答假（真机现在的形态：绝对路径那次没装上），
//     第 2 次起按"名字口径 + `g_FontLoadResult`"正常判定 —— 于是 EID 的兜底路径会被真正走一遍。
// 为什么需要它：真机上 EID 是"第一次失败 → 用 `../mods/...` 兜底再试一次"，两条路径都必须被验证到；
// 而惰性 shim 若一律答真，就永远走不到兜底那一条。
const char* g_FontPhaseMode = "all";
std::size_t g_FontLoadCount = 0;
std::vector<std::string> g_FontLoadCalls;
// 把探针字符串喂给 Lua 绑定层用的**同一个**归一化实现。
//
// 分层取证：`Font:Load` 的名字要经过两层才到引擎 —— ①Lua 拼出来的字符串交给
// `FontLoadThunk()`（= `HostFontLoad` 收到的那个）；②`font_api.cpp` 的
// `NormalizeModResourcePath()` 改写后才是引擎读的名字。只记第②层会把"是 EID 拼错了
// 还是我们的归一化改错了"混成一条信息，所以两层都要能看。
bool FontArgumentIsAcceptable(const char* path) {
    if (std::strcmp(g_FontArgMode, "strict") != 0) {
        return true;
    }
    if (path == nullptr || path[0] == '\0') {
        return false;
    }
    if (std::strncmp(path, "rom:/", 5) == 0) {
        return false;
    }
    if (std::strstr(path, "/../") != nullptr) {
        return false;
    }
    if (std::strncmp(path, "mods/", 5) == 0 || std::strncmp(path, "../", 3) == 0) {
        return false;
    }
    return true;
}
bool HostFontLoad(void*, const char* path, const char* extension) {
    ++g_FontLoadCount;
    const bool acceptable = FontArgumentIsAcceptable(path);
    const bool firstFails = std::strcmp(g_FontPhaseMode, "first-fails") == 0;
    const bool phasedFailure = firstFails && g_FontLoadCount == 1;
    const bool result = g_FontLoadResult && acceptable && !phasedFailure;
    std::string record = path == nullptr ? std::string("<null>") : std::string(path);
    record += " | ext=";
    record += extension == nullptr ? std::string("<null>") : std::string("\"") + extension + "\"";
    record += " | accepts=";
    record += acceptable ? "true" : "false";
    record += " | call=" + std::to_string(g_FontLoadCount);
    record += " | result=";
    record += result ? "true" : "false";
    g_FontLoadCalls.push_back(record);
    return result;
}
void HostFontUnload(void*) {}
bool HostFontIsLoaded(const void*) {
    if (std::strcmp(g_FontPhaseMode, "first-fails") == 0) {
        // 真机形态：`Load` 失败后 `IsLoaded()` 为假；兜底那次之后为真。
        return g_FontLoadResult && g_FontLoadCount > 1;
    }
    return g_FontLoadResult;
}
int HostFontGetStringWidth(const void*, const char*) { return 0; }
std::uint16_t HostFontGetLineHeight(const void*) { return 0; }
std::uint16_t HostFontGetBaselineHeight(const void*) { return 0; }
int HostFontGetCharacterWidth(const void*, char) { return 0; }
void HostFontSetMissingCharacter(void*, std::uint16_t) {}

void PublishInertEngineBindings() {
    LuaRuntime::LuaSpriteBindings bindings{};
    bindings.ctor = reinterpret_cast<uintptr_t>(&HostSpriteCtor);
    // `CreateSpriteHandle` 要求 ctor 与析构**都**在位，缺一个就报 "Sprite native binding is
    // unavailable" —— 只发布 ctor 会让人误以为惰性绑定没生效。
    bindings.destructor_ = reinterpret_cast<uintptr_t>(&HostSpriteDestructor);
    bindings.load = reinterpret_cast<uintptr_t>(&HostSpriteLoad);
    bindings.loadGraphics = reinterpret_cast<uintptr_t>(&HostSpriteLoadGraphics);
    bindings.replaceSpritesheet = reinterpret_cast<uintptr_t>(&HostSpriteReplaceSpritesheet);
    bindings.play = reinterpret_cast<uintptr_t>(&HostSpritePlay);
    bindings.libcxxStringAssign = reinterpret_cast<uintptr_t>(&HostLibcxxStringAssign);
    LuaRuntime::SetSpriteBindings(bindings);

    LuaRuntime::LuaFontBindings font{};
    font.ctor = reinterpret_cast<uintptr_t>(&HostFontCtor);
    font.destructor_ = reinterpret_cast<uintptr_t>(&HostFontDestructor);
    font.load = reinterpret_cast<uintptr_t>(&HostFontLoad);
    font.unload = reinterpret_cast<uintptr_t>(&HostFontUnload);
    font.isLoaded = reinterpret_cast<uintptr_t>(&HostFontIsLoaded);
    font.getStringWidth = reinterpret_cast<uintptr_t>(&HostFontGetStringWidth);
    font.getStringWidthUTF8 = reinterpret_cast<uintptr_t>(&HostFontGetStringWidth);
    font.getLineHeight = reinterpret_cast<uintptr_t>(&HostFontGetLineHeight);
    font.getBaselineHeight = reinterpret_cast<uintptr_t>(&HostFontGetBaselineHeight);
    font.getCharacterWidth = reinterpret_cast<uintptr_t>(&HostFontGetCharacterWidth);
    font.setMissingCharacter = reinterpret_cast<uintptr_t>(&HostFontSetMissingCharacter);
    LuaRuntime::SetFontBindings(font);
}

// --- 回调探针（`--callback-probe`）---------------------------------------------
//
// 验证一条 PC 契约：**同一 Mod 对同一 id 登记多个回调，派发时全部都要被调用**。
// EID 重度依赖它（`MC_PRE_USE_ITEM` 9 次、`MC_POST_NEW_ROOM` 4 次登记）。
// 用合成脚本驱动，不碰夹具：两个回调登记在同一个 Mod 上、第三个登记在另一个 Mod 上，
// 派发两帧后读三个计数全局 —— 同一个 Mod 的两个都必须各自涨到 2。
int RunCallbackProbe() {
    // 只登记一个 Mod：本 Runtime 每个 Lua state 只支持一个 Mod（第二次 `RegisterMod`
    // 会报 "RegisterMod accepts exactly one Mod"），所以"不同 Mod 各注册一条"在宿主机上
    // 无法表达；同一 Mod 的两条同 id 登记才是要验证的那条契约。
    static constexpr char kProbeScript[] = R"lua(
local probe = RegisterMod('Probe', 1)
probe:AddCallback(ModCallbacks.MC_POST_UPDATE, function() PROBE_A = (PROBE_A or 0) + 1 end)
probe:AddCallback(ModCallbacks.MC_POST_UPDATE, function() PROBE_B = (PROBE_B or 0) + 1 end)
probe:AddCallback(ModCallbacks.MC_POST_RENDER, function() PROBE_R = (PROBE_R or 0) + 1 end)
PROBE_REGISTER_DONE = 1
)lua";
    const LuaRuntime::LuaInitResult init = LuaRuntime::InitializeFromBuffer(
        kProbeScript, sizeof(kProbeScript) - 1, "@callback_probe.lua");
    if (init != LuaRuntime::LuaInitResult::Success) {
        std::uint32_t initErrorLength = 0;
        static_cast<void>(LuaRuntime::LastLuaErrorHead(&initErrorLength));
        char initError[300]{};
        const std::size_t initErrorCopied =
            LuaRuntime::CopyLastLuaErrorText(initError, sizeof(initError));
        std::printf("EIDHOST {\"probeOk\":false,\"probeInit\":\"%s\",\"errorText\":\"%s\"}\n",
                    LuaInitResultName(init), JsonEscape(std::string(initError, initErrorCopied)).c_str());
        return 1;
    }
    const std::uint32_t live = LuaRuntime::RegisteredCallbackCount(1);
    LuaRuntime::DispatchPostUpdate();
    LuaRuntime::DispatchPostUpdate();
    double a = 0.0, b = 0.0;
    const bool hasA = LuaRuntime::ReadLuaGlobalNumber("PROBE_A", &a);
    const bool hasB = LuaRuntime::ReadLuaGlobalNumber("PROBE_B", &b);
    // 同一个 Mod 的两条同 id 登记都必须被派发（各 2 次 = 两帧）。
    const bool sameOwnerCoexists = hasA && hasB && a == 2.0 && b == 2.0;
    std::printf(
        "EIDHOST {\"probeOk\":true,\"livePostUpdate\":%u,\"probeA\":%.0f,\"probeB\":%.0f,"
        "\"sameOwnerCoexists\":%s,\"postUpdateCount\":%u,"
        "\"callbackError\":%s}\n",
        live, hasA ? a : -1.0, hasB ? b : -1.0,
        sameOwnerCoexists ? "true" : "false", LuaRuntime::PostUpdateCount(),
        LuaRuntime::TakeCallbackError() ? "true" : "false");
    return sameOwnerCoexists ? 0 : 2;
}

int RunNormalizeProbe(const char* raw) {
    std::string storage;
    const std::string_view normalized =
        isaac::runtime::NormalizeModResourcePathForProbe(raw, storage);
    std::printf("NORMALIZE {\"raw\":\"%s\",\"out\":\"%s\",\"rawLen\":%zu,\"outLen\":%zu}\n",
                JsonEscape(raw == nullptr ? "" : raw).c_str(),
                JsonEscape(std::string(normalized)).c_str(),
                raw == nullptr ? 0 : std::strlen(raw), normalized.size());
    return 0;
}

int main(int argc, char** argv) {
    if (argc == 2 && std::strcmp(argv[1], "--callback-probe") == 0) {
        return RunCallbackProbe();
    }
    if (argc == 3 && std::strcmp(argv[1], "--normalize") == 0) {
        return RunNormalizeProbe(argv[2]);
    }
    if (argc != 8) {
        return 90;
    }
    g_RomfsRoot = argv[1];
    const std::size_t entryCapacity = static_cast<std::size_t>(std::strtoull(argv[2], nullptr, 10));
    const unsigned frames = static_cast<unsigned>(std::strtoul(argv[3], nullptr, 10));
    const bool engineShims = std::strcmp(argv[4], "shims") == 0;
    g_FontLoadResult = std::strcmp(argv[5], "font-fail") != 0;
    g_FontArgMode = argv[6];
    g_FontPhaseMode = argv[7];
    if (entryCapacity == 0) {
        return 91;
    }
    if (engineShims) {
        PublishInertEngineBindings();
    }

    // 全部显式限定：`runtime/source/lib/nx/types.h` 在全局命名空间里也有一个 `Result`，
    // 常量名在 `runtime_constants.hpp` 与 `manifest_service.hpp` 里各有一份（真机同理），
    // 所以这里不用 `using namespace`，否则名字查找会二义。
    const GameFileReader::Bindings bindings{};
    isaac::runtime::GameFileReaderAdapter content{bindings};
    isaac::runtime::EmbeddedLuaAdapter lua{bindings};
    isaac::runtime::ManifestService manifestService{
        content, isaac::runtime::ManifestParser{
                     isaac::runtime::ManifestSelectorAdapter::SelectFunction()}};
    isaac::runtime::ModLoadService service{content, lua};

    std::vector<std::uint8_t> manifestBuffer(
        isaac::runtime::kRomfsModManifestMaximumLength);
    std::vector<char> entryPath(isaac::runtime::kModEntryPathCapacity);
    std::vector<char> modRoot(isaac::runtime::kModRootPathCapacity);
    std::vector<char> chunkName(isaac::runtime::kModChunkNameCapacity);
    std::vector<std::uint8_t> entryBuffer(entryCapacity);

    isaac::runtime::ModLoadFailure failure{};
    const isaac::runtime::Result<isaac::runtime::ResolvedManifestMod> resolved =
        manifestService.Resolve(
        manifestBuffer.data(), manifestBuffer.size(), entryPath.data(), entryPath.size(),
        modRoot.data(), modRoot.size(), chunkName.data(), chunkName.size(), &failure);

    const long peakRssBefore = PeakRss();
    std::string report = "{";
    report += "\"peakRssBefore\":" + std::to_string(peakRssBefore);
    report += ",\"engineShims\":" + std::string(engineShims ? "true" : "false");
    report += ",\"resolveOk\":" + std::string(resolved.ok() ? "true" : "false");
    report += ",\"entryCapacity\":" + std::to_string(entryCapacity);
    report += ",\"resolveFailureStep\":\"" + std::string(StepName(failure.step)) + "\"";
    report += ",\"resolveFailureDetail\":" + std::to_string(failure.detail);
    report += ",\"resolveObservedBytes\":" + std::to_string(failure.observedBytes);
    if (!resolved.ok()) {
        report += ",\"loadOk\":false,\"loadSkipped\":true";
        report += ",\"readTrace\":\"" + JsonEscape(ReadTrace()) + "\"}";
        std::printf("EIDHOST %s\n", report.c_str());
        return 3;
    }

    const isaac::runtime::ResolvedManifestMod& manifestMod = resolved.value();
    report += ",\"entryPath\":\"" + JsonEscape(manifestMod.entryPath) + "\"";
    report += ",\"modRoot\":\"" + JsonEscape(manifestMod.modRoot) + "\"";
    report += ",\"chunkName\":\"" + JsonEscape(manifestMod.chunkName) + "\"";
    report += ",\"chunkNameLength\":" + std::to_string(std::strlen(manifestMod.chunkName));
    report += ",\"manifestBytes\":" + std::to_string(manifestMod.manifestBytes);
    report += ",\"hasEntry\":" + std::string(manifestMod.hasEntry ? "true" : "false");

    isaac::runtime::ModLoadRequest request{};
    request.resolved = &manifestMod;
    request.entryBuffer = entryBuffer.data();
    request.entryCapacity = entryBuffer.size();
    const isaac::runtime::Result<isaac::runtime::ModLoadOutcome> loaded =
        service.Load(request, &failure);

    report += ",\"loadOk\":" + std::string(loaded.ok() ? "true" : "false");
    report += ",\"loadSkipped\":false";
    report += ",\"failureStep\":\"" + std::string(StepName(failure.step)) + "\"";
    report += ",\"failureDetail\":" + std::to_string(failure.detail);
    report += ",\"observedBytes\":" + std::to_string(failure.observedBytes);
    report += ",\"luaInitDetail\":" + std::to_string(failure.detail);
    report += ",\"luaInitResult\":\"" +
              std::string(failure.step == isaac::runtime::ModLoadStep::LuaInit
                              ? LuaInitResultName(static_cast<LuaRuntime::LuaInitResult>(
                                    failure.detail))
                              : "n/a") +
              "\"";
    report += ",\"preparationFailureDetail\":" +
              std::to_string(LuaRuntime::PreparationFailureDetail());
    report += ",\"spriteCtorThunk\":" + std::to_string(LuaRuntime::SpriteCtorThunk());
    report += ",\"spriteLoadThunk\":" + std::to_string(LuaRuntime::SpriteLoadThunk());
    report += ",\"fontCtorThunk\":" + std::to_string(LuaRuntime::FontCtorThunk());
    report += ",\"fontLoadThunk\":" + std::to_string(LuaRuntime::FontLoadThunk());
    if (loaded.ok()) {
        report += ",\"scriptState\":\"" + std::string(StateName(loaded.value().scriptState)) + "\"";
        report += ",\"entryBytes\":" + std::to_string(loaded.value().entryBytes);
    } else {
        report += ",\"scriptState\":\"n/a\",\"entryBytes\":0";
    }

    // 错误文本：真机探针看的同一条通道（`g_LastLuaErrorText`，最多 256 字节）。
    std::uint32_t errorLength = 0;
    const std::uint64_t errorHead = LuaRuntime::LastLuaErrorHead(&errorLength);
    std::vector<char> errorText(300, '\0');
    const std::size_t copied = LuaRuntime::CopyLastLuaErrorText(errorText.data(), errorText.size());
    report += ",\"errorLength\":" + std::to_string(errorLength);
    report += ",\"errorCopied\":" + std::to_string(copied);
    report += ",\"errorHead64\":" + std::to_string(errorHead);
    report += ",\"errorTextHex\":\"" + ToHex(errorText.data(), copied) + "\"";

    // 加载成功后就派发几帧：让"跑通了"这件事也带上"回调真的在跑"的证据。
    if (loaded.ok() && loaded.value().ScriptExecuted() && frames != 0) {
        for (unsigned frame = 0; frame < frames; ++frame) {
            LuaRuntime::DispatchPostUpdate();
        }
        const bool error = LuaRuntime::TakeCallbackError();
        report += ",\"frames\":" + std::to_string(frames);
        report += ",\"postUpdateCount\":" + std::to_string(LuaRuntime::PostUpdateCount());
        report += ",\"callbackError\":" + std::string(error ? "true" : "false");
        report += ",\"registeredPostUpdate\":" +
                  std::to_string(LuaRuntime::RegisteredCallbackCount(1));
        if (error) {
            std::uint32_t tailLength = 0;
            static_cast<void>(LuaRuntime::LastLuaErrorHead(&tailLength));
            std::vector<char> tail(300, '\0');
            const std::size_t tailCopied =
                LuaRuntime::CopyLastLuaErrorText(tail.data(), tail.size());
            report += ",\"callbackErrorLength\":" + std::to_string(tailLength);
            report += ",\"callbackErrorTextHex\":\"" + ToHex(tail.data(), tailCopied) + "\"";
        }
    }

    // --- 回调普查（与真机诊断字同一套读数）-------------------------------------
    // 真机上"加载成功但没有派发点登记"只能靠这套掩码分辨；宿主机上同样报出来，才能逐项对照。
    const std::uint64_t kindsLow = LuaRuntime::RegisteredCallbackKindMaskLow();
    const std::uint64_t kindsHigh = LuaRuntime::RegisteredCallbackKindMaskHigh();
    std::string kindList;
    for (int id = 0; id < 128; ++id) {
        const std::uint64_t bit = id < 64 ? (kindsLow >> id) & 1ULL : (kindsHigh >> (id - 64)) & 1ULL;
        if (bit == 0) continue;
        if (!kindList.empty()) kindList += ",";
        kindList += std::to_string(id);
    }
    report += ",\"callbackKindMask\":\"" + kindList + "\"";
    report += ",\"callbackKindMaskLow\":" + std::to_string(kindsLow);
    report += ",\"callbackKindMaskHigh\":" + std::to_string(kindsHigh);
    report += ",\"dispatchableRegistrations\":" +
              std::to_string(LuaRuntime::DispatchableCallbackRegistrationCount());
    report += ",\"unhookedRegistrations\":" +
              std::to_string(LuaRuntime::UnhookedCallbackRegistrationCount());
    report += ",\"registryCount\":" +
              std::to_string(LuaRuntime::ManagedCallbackRegistry().Count());

    // 每个已登记种类**还活着**几条（派发期出错的回调会被摘掉，所以"注册过"与"还在"要分开看）。
    std::string liveCounts;
    for (int id = 0; id < 128; ++id) {
        const std::uint64_t bit = id < 64 ? (kindsLow >> id) & 1ULL : (kindsHigh >> (id - 64)) & 1ULL;
        if (bit == 0) continue;
        if (!liveCounts.empty()) liveCounts += ",";
        liveCounts += std::to_string(id) + ":" +
                      std::to_string(LuaRuntime::RegisteredCallbackCount(
                          static_cast<std::uint32_t>(id)));
    }
    report += ",\"liveCallbackCounts\":\"" + liveCounts + "\"";
    report += ",\"heapUsedBytes\":" + std::to_string(LuaRuntime::HeapUsedBytes());

    // `require` 失败快照：Mod 用 `pcall(require, ...)` 静默吞掉的那些失败在这里可见
    // （代码 19/20/21/22/23/26/27，见 `Require` 的 `RaiseRequireFailure` 调用点）。
    LuaRuntime::RequireFailureView requireView{};
    LuaRuntime::RequireFailureSnapshot(&requireView);
    report += ",\"requireFailures\":" + std::to_string(requireView.total);
    report += ",\"requireFirstCode\":" + std::to_string(requireView.firstCode);
    report += ",\"requireLastCode\":" + std::to_string(requireView.lastCode);
    {
        std::string heads;
        char buffer[9]{};
        auto appendHead = [&heads, &buffer](std::uint64_t head) {
            std::memcpy(buffer, &head, 8);
            if (!heads.empty()) heads += " / ";
            heads += buffer;
        };
        appendHead(requireView.firstNameHead);
        appendHead(requireView.lastNameHead);
        report += ",\"requireNameHeads\":\"" + JsonEscape(heads) + "\"";
    }

    report += ",\"readTrace\":\"" + JsonEscape(ReadTrace()) + "\"";
    // `Font:Load` 的实参逐字记录（见 `HostFontLoad` 的长注释）。
    {
        std::string calls;
        for (std::size_t index = 0; index < g_FontLoadCalls.size(); ++index) {
            if (index != 0) calls += " ;; ";
            calls += g_FontLoadCalls[index];
        }
        report += ",\"fontLoadCalls\":\"" + JsonEscape(calls) + "\"";
        report += ",\"fontLoadCallCount\":" + std::to_string(g_FontLoadCalls.size());
        report += ",\"normalizeProbeRaw\":\"" + JsonEscape(g_NormalizeProbeRaw) + "\"";
        report += ",\"normalizeProbeOut\":\"" + JsonEscape(g_NormalizeProbeOut) + "\"";
    }
    report += ",\"peakRssAfter\":" + std::to_string(PeakRss());
    report += "}";
    std::printf("EIDHOST %s\n", report.c_str());
    return loaded.ok() ? 0 : 1;
}
'''


# ---------------------------------------------------------------------------
# 夹具定位与预处理
# ---------------------------------------------------------------------------


def locate_eid_romfs() -> Path | None:
    """返回含 `mods/<EID>/main.lua` 的宿主 `isaac_mods` 目录；找不到返回 None。"""
    override = os.environ.get("EID_HOST_LOAD_ROMFS")
    candidates = ([Path(override)] if override else []) + list(_ROMFS_CANDIDATES)
    for candidate in candidates:
        if (candidate / EID_ENTRY_RELATIVE).is_file():
            return candidate
    return None


def prepare_romfs(source_romfs: Path, workdir: Path) -> Path:
    """把 `isaac_mods` 铺成一份"EID 是唯一启用 Mod"的等价树，返回新根。

    为什么要这一步：`analysis/` 夹具里的清单把 EID 标成 `enabled: false`（它带着 `disable.it`），
    而 `ManifestSelectorAdapter` 走的是 PC 的"取第一个启用的 Mod"语义 —— 直接用那份清单跑，
    选中会是 `MuteOnPause`，EID 一行都不会执行。真机上这一轮用的是"只放 EID、无 `disable.it`"的
    清单（`runtime/.eid-artifacts/deploy/.../manifest.json` 就是它），所以这里把源清单**过滤**
    成同一形状：只保留 EID 那条并把 `enabled` 置真，`entry` 字符串原样保留（它决定 chunk name）。

    Mod 目录**逐文件**建符号链接（不是整目录一个链接）：这样 8 MB 的夹具不用复制，而单个文件
    （`eid_config.lua`、失败的那一行所在的模块）可以随时换成真实副本做补偿。
    """
    manifest_path = source_romfs / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    selected = None
    for entry in payload.get("mods", []):
        if entry.get("directory") == EID_DIRECTORY:
            selected = entry
            break
    if selected is None:
        raise SystemExit(f"清单里没有 EID：{manifest_path}")
    selected = dict(selected)
    selected["enabled"] = True
    payload["mods"] = [selected]

    prepared = workdir / "romfs" / "isaac_mods"
    (prepared / "mods").mkdir(parents=True, exist_ok=True)
    (prepared / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    _link_tree(source_romfs / "mods" / EID_DIRECTORY, prepared / "mods" / EID_DIRECTORY)
    return prepared


def _link_tree(source: Path, target: Path) -> None:
    """把 `source` 目录镜像成 `target`（目录真实、文件是符号链接）。"""
    target.mkdir(parents=True, exist_ok=True)
    for child in sorted(source.iterdir()):
        destination = target / child.name
        if child.is_dir():
            _link_tree(child, destination)
        elif not destination.exists():
            destination.symlink_to(child.resolve())


# ---------------------------------------------------------------------------
# 失败定位（chunk 名被 `luaO_chunkid` 截断，必须按后缀反查真实文件）
# ---------------------------------------------------------------------------


def mod_file_index(romfs: Path) -> dict[str, Path]:
    """`<Mod 内相对路径>` → 宿主文件；键同时包含设备路径形式（`mods/<EID>/<rel>`）。"""
    mod_root = romfs / "mods" / EID_DIRECTORY
    index: dict[str, Path] = {}
    for path in sorted(mod_root.rglob("*")):
        if path.is_file():
            index[path.relative_to(mod_root).as_posix()] = path
    return index


def _chunk_tail_to_relative(tail: str, index: dict[str, Path]) -> str | None:
    """把 `...` 后面的尾巴（**完整设备路径**的末 56 字节）还原成 Mod 内相对路径。

    截断发生在 `@rom:/isaac_mods/mods/<EID>/<rel>` 这个完整 chunk 名上，所以尾巴里带着
    `_mods/...`（`isaac_mods` 的尾）—— 按 `<rel>` 自己匹配是错的（第一版就错在这里，
    连 `main.lua` 都反查不到）。
    """
    for relative in index:
        device_path = f"rom:/isaac_mods/mods/{EID_DIRECTORY}/{relative}"
        if device_path.endswith(tail):
            return relative
    return None


def resolve_failure_position(text: str, index: dict[str, Path]) -> dict | None:
    """从错误文本里取 (模块, 行号, 消息, 触发语句)。

    Lua 5.3.3 的 `luaO_chunkid` 会把超过 60 字节的 chunk 名截成 `"..."` + 末 56 字节，
    所以这里先取出尾巴，再在夹具里按后缀反查真实文件 —— 行号对的是**那个文件**的行号，
    不是 `main.lua` 的（第一版工具在这里错过一次：把 `eid_data.lua:1025` 报成了
    `main.lua:1025`，贴出来的上下文完全不相干）。
    """
    match = re.search(r"^\.\.\.([^\n]*?\.lua):(\d+): (.*)$", text, re.S)
    if match is None:
        return None
    tail, line, message = match.group(1), int(match.group(2)), match.group(3)
    relative = _chunk_tail_to_relative(tail, index)
    statement = None
    if relative is not None:
        source = index[relative].read_text(encoding="utf-8", errors="replace").splitlines()
        if 1 <= line <= len(source):
            statement = source[line - 1].strip()
    return {
        "chunkTail": tail,
        "module": relative if relative is not None else f"<未知模块 …{tail}>",
        "line": line,
        "message": message.strip(),
        "statement": statement,
    }


def annotate_failure(report: dict, romfs: Path) -> dict:
    """把失败位置（模块 / 行号 / 语句）与真机长度算式补进报告。"""
    index = mod_file_index(romfs)
    report["failurePosition"] = resolve_failure_position(report.get("errorText", ""), index)
    report["deviceLengthBudget"] = DEVICE_ERROR_LENGTH - LUA_CHUNKID_PREFIX_LENGTH
    return report


def failure_context(romfs: Path, position: dict | None, context: int = 4) -> list[str]:
    """贴出失败行附近的源码（从**失败模块**里读）。"""
    if not position or "line" not in position:
        return []
    mod_root = romfs / "mods" / EID_DIRECTORY
    path = mod_root / position["module"]
    if not path.is_file():
        return []
    source = path.read_text(encoding="utf-8", errors="replace").splitlines()
    line = position["line"]
    start = max(1, line - context)
    end = min(len(source), line + context)
    lines = [f"触发语句附近（{position['module']} {start}–{end}，★ = 失败行）："]
    for number in range(start, end + 1):
        marker = "★" if number == line else " "
        lines.append(f"  {marker} {number:5d}| {source[number - 1]}")
    return lines


# ---------------------------------------------------------------------------
# 构建与运行
# ---------------------------------------------------------------------------


def host_compilers() -> tuple[str, str] | None:
    """(C 编译器, C++ 编译器)；缺任何一个返回 None（调用方据此 skip，不伪装成通过）。"""
    cc = shutil.which("cc")
    if cc is None:
        return None
    for candidate in ("c++", "clang++", "g++"):
        found = shutil.which(candidate)
        if found is not None:
            return cc, found
    return None


def _layered_sources() -> list[Path]:
    """复用 `runtime/tests/test_support.py` 的真实运行时源码清单（不另写一套）。"""
    try:
        from runtime.tests.test_support import layered_lua_runtime_sources
    except ImportError:  # 直接以 `python3 tools/eid_host_load.py` 运行时的兜底
        sys.path.insert(0, str(ROOT / "runtime" / "tests"))
        from test_support import layered_lua_runtime_sources
    return layered_lua_runtime_sources(SOURCE)


def _service_sources() -> list[Path]:
    """加载路径上的应用/基础设施源码（清单解析、选择、入口读取、Lua 驱动）。"""
    return [
        SRC / "application/mod/manifest_parser.cpp",
        SRC / "application/mod/manifest_service.cpp",
        SRC / "application/mod/mod_load_service.cpp",
        SRC / "infrastructure/mod/manifest_selector_adapter.cpp",
        SRC / "infrastructure/content/game_file_reader_adapter.cpp",
        SRC / "infrastructure/lua/embedded_lua_adapter.cpp",
        SOURCE / "mod_manifest.cpp",
    ]


def build_harness(workdir: Path) -> Path:
    """编译 harness（真实运行时源码 + vendored Lua 5.3.3），返回可执行文件路径。"""
    compilers = host_compilers()
    if compilers is None:
        raise RuntimeError("需要宿主 C/C++ 编译器（cc + c++/clang++/g++）")
    cc, cxx = compilers
    compatibility = workdir / "compatibility"
    compatibility.mkdir(parents=True, exist_ok=True)
    (compatibility / "stdfloat").write_text(
        "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n",
        encoding="utf-8",
    )
    harness = workdir / "eid_host_harness.cpp"
    harness.write_text(HARNESS.lstrip(), encoding="utf-8")

    lua_root = SOURCE / "third_party/lua-5.3.3/src"
    excluded = {"lua.c", "luac.c", "liolib.c", "loslib.c", "loadlib.c", "ldblib.c", "linit.c"}
    lua_objects: list[Path] = []
    for lua_source in sorted(lua_root.glob("*.c")):
        if lua_source.name in excluded:
            continue
        output = workdir / f"{lua_source.stem}.o"
        build = subprocess.run(
            [cc, "-std=c99", "-w", "-DLUA_C89_NUMBERS", "-I", str(lua_root), "-c",
             str(lua_source), "-o", str(output)],
            text=True, capture_output=True,
        )
        if build.returncode != 0:
            raise RuntimeError(build.stdout + build.stderr)
        lua_objects.append(output)

    executable = workdir / "eid_host_harness"
    build = subprocess.run(
        # `-Wno-deprecated-declarations`：真机是 newlib（`sbrk` 合法），宿主 macOS SDK 把它标了
        # deprecated，而运行时那边确实在用它估算堆位置。这是**目标平台差异**，不该让宿主验证
        # 通道整体哑掉；除这一条外仍然 `-Werror`（harness 自己的代码不许有警告）。
        [cxx, "-std=c++23", "-Wall", "-Wextra", "-Werror", "-Wno-deprecated-declarations",
         "-DLUA_C89_NUMBERS",
         "-DEXL_LAYERED_RUNTIME=1", "-DEXL_LOAD_KIND=Module",
         "-DEXL_LOAD_KIND_ENUM=2", "-DEXL_PROGRAM_ID=0",
         "-I", str(compatibility), "-I", str(SOURCE), "-I", str(SRC), "-I", str(lua_root),
         str(harness),
         *(str(path) for path in _layered_sources()),
         *(str(path) for path in _service_sources()),
         *(str(path) for path in lua_objects), "-lm", "-o", str(executable)],
        text=True, capture_output=True,
    )
    if build.returncode != 0:
        raise RuntimeError(build.stdout + build.stderr)
    return executable


def run_harness(executable: Path, romfs: Path, capacity: int, frames: int = 3,
                engine_shims: bool = True, font_load: bool = True,
                font_arg: str = "any", font_phase: str = "all") -> dict:
    """跑一次 harness，返回结构化报告（含整段 Lua 错误文本与回调普查）。

    `font_load=False` 模拟真机上"引擎读不到 `.fnt` ⇒ `Font:Load` 返回 false"：
    EID 会在 `main.lua:187` 顶层 `return`，于是脚本"成功结束"却一个
    `MC_POST_UPDATE`/`MC_POST_RENDER` 都没登记 —— 这正是 2026-09-12 报告 `01789203482`
    的现象（诊断字 [11] = 0 + 有派发点的登记次数 0 + 屏幕空白）。

    `font_arg="strict"` 只接受内容挂载点相对名（见 harness 里 `g_FontArgMode` 的注释）：
    用来验证"路径归一化把名字修对了"，而不是靠惰性 shim 一律答真掩盖掉这条信息。
    """
    executed = subprocess.run(
        [str(executable), str(romfs), str(capacity), str(frames),
         "shims" if engine_shims else "none", "font-ok" if font_load else "font-fail",
         font_arg, font_phase],
        text=True, capture_output=True,
    )
    payload = None
    for line in executed.stdout.splitlines():
        if line.startswith("EIDHOST "):
            payload = json.loads(line[len("EIDHOST "):])
    if payload is None:
        raise RuntimeError(
            f"harness 没有输出报告（returncode={executed.returncode}）\n"
            f"{executed.stdout}\n{executed.stderr}"
        )
    payload["returncode"] = executed.returncode
    payload["stderr"] = executed.stderr
    payload["stdout"] = executed.stdout
    if "errorTextHex" in payload:
        payload["errorText"] = bytes.fromhex(payload["errorTextHex"]).decode("utf-8", "replace")
    if "callbackErrorTextHex" in payload:
        payload["callbackErrorText"] = bytes.fromhex(payload["callbackErrorTextHex"]).decode(
            "utf-8", "replace"
        )
    payload["readRequests"] = _parse_read_trace(payload.get("readTrace", ""))
    payload["chunkMatchesDevice"] = payload.get("chunkName") == DEVICE_CHUNK_NAME
    payload["errorPrefixMatchesDevice"] = payload.get("errorText", "").encode(
        "utf-8", "replace"
    ).startswith(DEVICE_ERROR_PREFIX)
    payload["errorLengthMatchesDevice"] = payload.get("errorLength") == DEVICE_ERROR_LENGTH
    # 失败位置（模块 / 行号 / 语句）就地解析：overlay 与夹具树同形，报出的就是**这次真正跑的**那份文件。
    annotate_failure(payload, romfs)
    return payload


def _parse_read_trace(trace: str) -> list[dict]:
    requests = []
    for item in trace.split(","):
        if not item:
            continue
        parts = item.rsplit("|", 2)
        if len(parts) != 3:
            continue
        path, status, size = parts
        requests.append({"path": path, "status": status, "bytes": int(size)})
    return requests


def run_normalize_probe(executable: Path, raw: str) -> dict:
    """把一条路径交给**真实现**的 `NormalizeModResourcePath`，返回原始与归一化结果。

    为什么要有它：`Font:Load` 的名字分两层（Lua 拼的 / 归一化改的），而真机一轮只能问一个问题。
    这个入口让"归一化"这一层可以在宿主上逐字验证，不必靠复刻实现去猜（2026-09-12 的
    off-by-one 就是靠它定案的）。
    """
    executed = subprocess.run([str(executable), "--normalize", raw], text=True,
                              capture_output=True)
    for line in executed.stdout.splitlines():
        if line.startswith("NORMALIZE "):
            payload = json.loads(line[len("NORMALIZE "):])
            payload["returncode"] = executed.returncode
            return payload
    raise RuntimeError(
        f"归一化探针没有输出报告（returncode={executed.returncode}）\n"
        f"{executed.stdout}\n{executed.stderr}"
    )


def run_callback_probe(executable: Path) -> dict:
    """跑一次回调探针：同一 Mod 对同一 id 的多次登记是否并存（PC 契约）。"""
    executed = subprocess.run([str(executable), "--callback-probe"], text=True,
                              capture_output=True)
    payload = None
    for line in executed.stdout.splitlines():
        if line.startswith("EIDHOST "):
            payload = json.loads(line[len("EIDHOST "):])
    if payload is None:
        raise RuntimeError(
            f"探针没有输出报告（returncode={executed.returncode}）\n"
            f"{executed.stdout}\n{executed.stderr}"
        )
    payload["returncode"] = executed.returncode
    return payload


_MOD_CALLBACK_NAMES: dict[int, str] | None = None


def mod_callback_names() -> dict[int, str]:
    """从生成的 PC 表里取 `ModCallbacks` 的 id → 名字（报告里把掩码翻译成人看得懂的名字）。"""
    global _MOD_CALLBACK_NAMES
    if _MOD_CALLBACK_NAMES is not None:
        return _MOD_CALLBACK_NAMES
    names: dict[int, str] = {}
    generated = SOURCE / "program" / "pc_lua_enum_data.cpp"
    if generated.is_file():
        text = generated.read_text(encoding="utf-8", errors="replace")
        block = re.search(r"kModCallbacksValues\[\] = \{(.*?)\n\};", text, re.S)
        if block:
            for key, value in re.findall(r'\{"([A-Z0-9_]+)", (-?\d+)\}', block.group(1)):
                names.setdefault(int(value), key)
    _MOD_CALLBACK_NAMES = names
    return names


def describe_callback_kinds(report: dict) -> list[str]:
    """把回调普查掩码逐项翻成 `id = MC_XXX`（含"还活着几条"）。"""
    names = mod_callback_names()
    live = {}
    for item in (report.get("liveCallbackCounts") or "").split(","):
        if ":" in item:
            key, value = item.split(":", 1)
            live[int(key)] = int(value)
    lines = []
    for item in (report.get("callbackKindMask") or "").split(","):
        if not item:
            continue
        identifier = int(item)
        lines.append(f"    {identifier:3d} = {names.get(identifier, '?'):32s} 存活 {live.get(identifier, 0)} 条")
    return lines


def prepare_workspace(romfs: Path, workdir: Path) -> tuple[Path, Path]:
    """预处理清单 + 编译 harness：返回 (可执行文件, 准备后的 romfs 根)。编译一次可多次运行。"""
    prepared = prepare_romfs(romfs, workdir)
    executable = build_harness(workdir)
    return executable, prepared


def _finish(report: dict, romfs: Path, workdir: Path) -> dict:
    entry_file = romfs / EID_ENTRY_RELATIVE
    report["workdir"] = str(workdir)
    report["entryFileBytes"] = entry_file.stat().st_size
    report["entryFile"] = str(entry_file)
    report["sourceRomfs"] = str(romfs)
    report["sourceRevision"] = source_revision()
    report["peakRssMiB"] = round(
        max(report.get("peakRssAfter", 0) - report.get("peakRssBefore", 0), 0)
        / (1024 * 1024), 1
    ) if sys.platform == "darwin" else round(
        report.get("peakRssAfter", 0) / 1024, 1
    )
    annotate_failure(report, romfs)
    return report


def run_eid_load(
    *, romfs: Path | None = None, capacity: int | None = None, frames: int = 3,
    workdir: Path | None = None, keep: bool = False, engine_shims: bool = True,
) -> dict:
    """一站式入口（CLI 用）：定位夹具 → 预处理清单 → 编译 → 运行 → 返回报告。"""
    if capacity is None:
        capacity = 1048576  # kRomfsModScriptMaximumLength
    if romfs is None:
        romfs = locate_eid_romfs()
        if romfs is None:
            raise FileNotFoundError("找不到 EID 夹具（analysis/ 或 runtime/.eid-artifacts）")

    owned = workdir is None
    temporary = None
    if owned:
        temporary = tempfile.TemporaryDirectory(prefix="eid-host-load-")
        workdir = Path(temporary.name)
    try:
        executable, prepared = prepare_workspace(romfs, workdir)
        report = run_harness(executable, prepared, capacity, frames, engine_shims)
        return _finish(report, romfs, workdir)
    finally:
        if owned and temporary is not None and not keep:
            temporary.cleanup()


def source_revision() -> dict:
    """本次结果对应的源码状态（仓库在被并行修改，结论必须可归因）。"""
    revision: dict[str, object] = {}
    try:
        revision["head"] = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True,
            capture_output=True, check=True,
        ).stdout.strip()
        revision["dirty"] = bool(subprocess.run(
            ["git", "status", "--porcelain", "--", "runtime/source", "runtime/src"],
            cwd=ROOT, text=True, capture_output=True, check=True,
        ).stdout.strip())
        revision["luaRuntimeSha256"] = hashlib.sha256(
            (SOURCE / "lua_runtime.cpp").read_bytes()
        ).hexdigest()[:16]
        revision["spriteApiSha256"] = hashlib.sha256(
            (SRC / "interfaces/lua/sprite_api.cpp").read_bytes()
        ).hexdigest()[:16]
    except Exception:  # 没有 git 也要能跑
        revision.setdefault("head", "unknown")
    return revision


# ---------------------------------------------------------------------------
# 补偿性扫描：一次性列出加载期**所有**失败点
# ---------------------------------------------------------------------------


def stub_prelude(names: list[str]) -> str:
    """生成"补上运行时缺失全局"的 Lua 前导块（只用于扫描，**不是**产品代码）。

    为什么需要它：一个缺失的全局（例如 PC 的 `ItemConfig` 枚举表）会让加载在第一处引用
    就停住，而"只报第一个失败点"对修复清单没用。前导块把缺失的全局补成一个**宽容桩**：
    任意字段取用都得到另一个桩、可以当函数调用、可以参与算术/拼接/比较，于是脚本能继续
    往下跑，下一个失败点随即暴露出来。每个被补的名字都记进报告 —— 它们就是"运行时缺什么"。
    """
    if not names:
        return ""
    listed = ", ".join(json.dumps(name) for name in names)
    return f"""

-- ==== 以下由 tools/eid_host_load.py 追加（宿主机扫描补偿，产品代码里没有这一段）====
-- 运行时缺这些全局：{listed}
-- 桩的语义是**故意宽容**的：任意字段返回新桩、可调用、可参与算术。它只保证"脚本能往下
-- 走"，不保证数值正确 —— 所以每个被补的名字在报告里都作为"运行时缺失"列出。
do
  local function makeStub(label)
    local cache = {{}}
    local stub = {{}}
    local meta = {{
      __index = function(_, key)
        local value = cache[key]
        if value == nil then
          value = makeStub(label .. "." .. tostring(key))
          cache[key] = value
        end
        return value
      end,
      __call = function(_, ...) return makeStub(label .. "()") end,
      __tostring = function() return "<host stub " .. label .. ">" end,
      __concat = function(a, b) return tostring(a) .. tostring(b) end,
      __len = function() return 0 end,
      __add = function() return 0 end,
      __sub = function() return 0 end,
      __mul = function() return 0 end,
      __div = function() return 0 end,
      __mod = function() return 0 end,
      __unm = function() return 0 end,
      __eq = function(a, b) return rawequal(a, b) end,
      __lt = function() return false end,
      __le = function() return false end,
    }}
    return setmetatable(stub, meta)
  end
  local missing = {{{listed}}}
  for _, name in ipairs(missing) do
    if _G[name] == nil then
      _G[name] = makeStub(name)
    end
  end
end
"""


#: 插桩前导块：`EID_HOST_TRACE_ADD` 只打印一行，Python 侧据此还原调用序列。
TRACE_PRELUDE = """

-- ==== 以下由 tools/eid_host_load.py 追加（宿主机调用序列追踪，产品代码里没有这一段）====
-- 每个 `Mod:AddCallback(...)` 调用点前面都插了一行 `EID_HOST_TRACE_ADD("<文件>:<行>", <id 表达式>)`，
-- 于是"到底登记了哪些 id、按什么顺序、有没有被拒"不再依赖运行时内部插桩。
EID_HOST_TRACE_ADD = function(where, identifier)
  print("EIDTRACE " .. tostring(where) .. " " .. tostring(identifier) .. " " .. type(identifier))
end
"""

_ADD_CALLBACK = re.compile(r"AddCallback\s*\(")


def _first_argument(text: str, open_paren: int) -> str:
    """取 `AddCallback(` 之后的第一段参数（到深度 0 的逗号或右括号为止）。

    `open_paren` 是左括号自身的下标，所以扫描从它后面一个字符开始、深度从 1 起 ——
    第一版从括号本身开始扫，深度立刻变成 2，于是整行剩下的参数都被当成"第一个参数"
    （插进去的 `return <整行>` 直接把文件写成了语法错误）。
    """
    depth = 1
    index = open_paren + 1
    quote = None
    while index < len(text):
        character = text[index]
        if quote is not None:
            if character == "\\":
                index += 2
                continue
            if character == quote:
                quote = None
        elif character in "\"'":
            quote = character
        elif character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1:index].strip()
        elif character == "," and depth == 1:
            return text[open_paren + 1:index].strip()
        index += 1
    return ""


def instrument_callback_calls(overlay: Path, source_romfs: Path) -> int:
    """在每个 `AddCallback(` 调用点所在的**行首**插入一行追踪调用（**不改变行号**）。

    为什么要插桩而不是改产品代码：`Mod:AddCallback` 是 catalog 里的 C 函数，
    在 Lua 侧拿不到可用的包装（`Mod` 的 `__index` 先解析内建方法），运行时的普查又只给
    累计掩码与计数。把追踪插进夹具副本的每一行之前，就能得到**有序**的
    "文件:行 + id"序列，而且行号与报错位置仍然一致。
    """
    mod_root = overlay / "mods" / EID_DIRECTORY
    source_mod = source_romfs / "mods" / EID_DIRECTORY
    instrumented = 0
    for path in sorted(source_mod.rglob("*.lua")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "AddCallback" not in text:
            continue
        lines = text.splitlines(keepends=True)
        offsets = []
        cursor = 0
        for line in lines:
            offsets.append(cursor)
            cursor += len(line)
        inserts: dict[int, list[str]] = {}
        for match in _ADD_CALLBACK.finditer(text):
            line_start = text.rfind("\n", 0, match.start()) + 1
            code = text[line_start:match.start()]
            if "--" in code:  # 注释里的 `AddCallback(` 不算
                continue
            identifier = _first_argument(text, match.end() - 1)
            line_number = text.count("\n", 0, match.start()) + 1
            inserts.setdefault(line_number - 1, []).append(identifier)
            instrumented += 1
        if not inserts:
            continue
        relative = path.relative_to(source_mod).as_posix()
        result = []
        for index, line in enumerate(lines):
            prefixes = inserts.get(index, ())
            if not prefixes:
                result.append(line)
                continue
            # **同一行前缀**：行数不变 ⇒ 报错行号仍然对得上夹具原文。
            rendered = "".join(
                f'EID_HOST_TRACE_ADD({json.dumps(f"{relative}:{index + 1}")}, '
                f"{identifier or 'nil'}); "
                for identifier in prefixes
            )
            result.append(rendered + line)
        target = mod_root / relative
        if target.is_symlink():
            target.unlink()
        target.write_text("".join(result), encoding="utf-8")
    return instrumented


def parse_callback_trace(stdout: str) -> list[dict]:
    """"从 harness 的 stdout 里取出追踪行。"""
    entries = []
    for line in stdout.splitlines():
        if not line.startswith("EIDTRACE "):
            continue
        _, where, identifier, kind = (line.split(" ", 3) + ["", "", "", ""])[:4]
        entries.append({"where": where, "identifier": identifier, "kind": kind})
    return entries


def apply_compensation(overlay: Path, source_romfs: Path, stubs: list[str],
                       masks: dict[str, set[int]], trace_callback: bool = False) -> None:
    """把补偿写进 overlay（符号链接换成真实副本；只有被补偿的文件才写）。"""
    mod_root = overlay / "mods" / EID_DIRECTORY
    source_mod = source_romfs / "mods" / EID_DIRECTORY

    config_link = mod_root / "eid_config.lua"
    if stubs or trace_callback:
        text = (source_mod / "eid_config.lua").read_text(encoding="utf-8", errors="replace")
        if config_link.is_symlink():
            config_link.unlink()
        text += stub_prelude(stubs) if stubs else ""
        text += TRACE_PRELUDE if trace_callback else ""
        config_link.write_text(text, encoding="utf-8")

    for relative, lines in masks.items():
        target = mod_root / relative
        if target.is_symlink():
            target.unlink()
        source_lines = (source_mod / relative).read_text(
            encoding="utf-8", errors="replace"
        ).splitlines(keepends=True)
        for line in lines:
            if 1 <= line <= len(source_lines):
                source_lines[line - 1] = "-- [eid_host_load sweep] 该行在宿主机上无法执行，已注释\n"
        target.write_text("".join(source_lines), encoding="utf-8")


def sweep_eid_load(
    *, romfs: Path | None = None, capacity: int | None = None, frames: int = 0,
    workdir: Path | None = None, keep: bool = False, engine_shims: bool = True,
    font_load: bool = True, max_iterations: int = 60, max_masks: int = 40,
) -> dict:
    """反复运行并逐次补偿，按出现顺序列出加载期的**每一个**失败点。

    补偿顺序（先温和后粗暴）：
      1. `attempt to index/call a nil value (global 'X')` → 把 `X` 记成"运行时缺的全局"并补桩；
      2. 其它（缺绑定、宿主桩返回 nil、语义不同）→ 注释掉那一行，记成"被掩盖的失败点"。

    这个扫描**不是**忠实运行：每次补偿都会改变脚本能走到的位置，所以报告里逐条标出
    "当时已经补了什么"，方便主线程区分"真阻塞"与"因为前面被补掉才出现的次生现象"。
    """
    if capacity is None:
        capacity = 1048576
    if romfs is None:
        romfs = locate_eid_romfs()
        if romfs is None:
            raise FileNotFoundError("找不到 EID 夹具（analysis/ 或 runtime/.eid-artifacts）")

    owned = workdir is None
    temporary = None
    if owned:
        temporary = tempfile.TemporaryDirectory(prefix="eid-host-sweep-")
        workdir = Path(temporary.name)
    try:
        executable, overlay = prepare_workspace(romfs, workdir)
        stubs: list[str] = []
        masks: dict[str, set[int]] = {}
        blockers: list[dict] = []
        seen: set[tuple[str, int]] = set()
        final: dict | None = None
        for iteration in range(1, max_iterations + 1):
            report = run_harness(executable, overlay, capacity, frames, engine_shims, font_load)
            report = _finish(report, romfs, workdir)
            report["iteration"] = iteration
            report["stubbedGlobals"] = list(stubs)
            report["maskedLines"] = {key: sorted(value) for key, value in masks.items()}
            if report.get("loadOk"):
                final = report
                break
            position = report.get("failurePosition")
            if position is None:
                blockers.append({
                    "iteration": iteration, "position": None,
                    "errorText": report.get("errorText", ""),
                    "errorLength": report.get("errorLength"),
                    "compensation": "无法定位（停在扫描）",
                    "stubbedGlobals": list(stubs),
                    "maskedLines": {key: sorted(value) for key, value in masks.items()},
                })
                final = report
                break
            if not (romfs / "mods" / EID_DIRECTORY / position["module"]).is_file():
                # 反查不到真实文件（截断太狠或来自未知模块）：记录后停止扫描，不猜。
                blockers.append({
                    "iteration": iteration, "position": position,
                    "errorText": report.get("errorText", ""),
                    "errorLength": report.get("errorLength"),
                    "compensation": "无法把被截断的 chunk 名还原成夹具里的文件（停止扫描）",
                    "stubbedGlobals": list(stubs),
                    "maskedLines": {key2: sorted(value) for key2, value in masks.items()},
                })
                final = report
                break
            key = (position["module"], position["line"])
            global_match = re.search(
                r"attempt to (?:index|call) a nil value \(global '([^']+)'\)",
                position["message"],
            )
            compensation = None
            if global_match is not None and global_match.group(1) not in stubs:
                stubs.append(global_match.group(1))
                compensation = f"补桩全局 {global_match.group(1)}（= 运行时缺这个全局）"
            elif key in seen or len(masks.get(position["module"], ())) >= max_masks:
                compensation = "同一位置重复失败（停止扫描）"
                blockers.append({
                    "iteration": iteration, "position": position,
                    "errorText": report.get("errorText", ""),
                    "errorLength": report.get("errorLength"),
                    "compensation": compensation,
                    "stubbedGlobals": list(stubs),
                    "maskedLines": {key2: sorted(value) for key2, value in masks.items()},
                })
                final = report
                break
            else:
                masks.setdefault(position["module"], set()).add(position["line"])
                compensation = f"注释掉 {position['module']}:{position['line']}"
            seen.add(key)
            blockers.append({
                "iteration": iteration, "position": position,
                "errorText": report.get("errorText", ""),
                "errorLength": report.get("errorLength"),
                "compensation": compensation,
                "stubbedGlobals": list(stubs),
                "maskedLines": {key2: sorted(value) for key2, value in masks.items()},
            })
            apply_compensation(overlay, romfs, stubs, masks)
        if final is None:
            final = report
        return {
            "blockers": blockers,
            "stubbedGlobals": stubs,
            "maskedLines": {key: sorted(value) for key, value in masks.items()},
            "finalReport": final,
            "iterations": len(blockers),
            "workdir": str(workdir),
            "sourceRomfs": str(romfs),
            "sourceRevision": source_revision(),
        }
    finally:
        if owned and temporary is not None and not keep:
            temporary.cleanup()


def trace_callback_registrations(
    *, romfs: Path | None = None, workdir: Path | None = None, keep: bool = False,
    capacity: int = 1048576,
) -> dict:
    """插桩跑一次加载，返回每个 `Mod:AddCallback` 调用点的**有序**序列（两种字体结果各一遍）。

    这一步回答的是"脚本到底调用过哪些登记、有没有被拒绝"：追踪打印在**调用点之前**，
    所以即使后面的调用被拒（`ResolveCallbackId` 报错），它也已经留下了记录。
    """
    if romfs is None:
        romfs = locate_eid_romfs()
        if romfs is None:
            raise FileNotFoundError("找不到 EID 夹具（analysis/ 或 runtime/.eid-artifacts）")
    owned = workdir is None
    temporary = None
    if owned:
        temporary = tempfile.TemporaryDirectory(prefix="eid-host-trace-")
        workdir = Path(temporary.name)
    try:
        executable, overlay = prepare_workspace(romfs, workdir)
        instrumented = instrument_callback_calls(overlay, romfs)
        apply_compensation(overlay, romfs, [], {}, trace_callback=True)
        outcomes = {}
        for font_ok in (True, False):
            report = run_harness(executable, overlay, capacity, 0, True, font_ok)
            report = _finish(report, romfs, workdir)
            outcomes["font-ok" if font_ok else "font-fail"] = {
                "calls": parse_callback_trace(report.get("stdout", "")),
                "report": report,
            }
        return {
            "instrumentedCallSites": instrumented,
            "outcomes": outcomes,
            "workdir": str(workdir),
            "sourceRomfs": str(romfs),
            "sourceRevision": source_revision(),
        }
    finally:
        if owned and temporary is not None and not keep:
            temporary.cleanup()


def describe_callback_trace(trace: dict) -> str:
    """把调用序列打成"按顺序的清单 + 每个 id 的计数"。"""
    names = mod_callback_names()
    lines = [f"=== Mod:AddCallback 调用序列（插桩点 {trace['instrumentedCallSites']} 处）==="]
    for label, outcome in trace["outcomes"].items():
        calls = outcome["calls"]
        report = outcome["report"]
        lines.append("")
        lines.append(f"--- 字体结果 = {label}：共 {len(calls)} 次调用 ---")
        counts: dict[str, int] = {}
        for index, call in enumerate(calls, start=1):
            identifier = call["identifier"]
            counts[identifier] = counts.get(identifier, 0) + 1
            lines.append(f"  {index:2d}. {call['where']:48s} {identifier}")
        lines.append("")
        lines.append("  按 id 计数（>1 说明同一 Mod 对同一 id 登记多次）：")
        for identifier in sorted(counts, key=lambda name: -counts[name]):
            value = counts[identifier]
            try:
                resolved = int(identifier)
            except ValueError:
                resolved = None
            suffix = f" = {names.get(resolved, '?')}" if resolved is not None else "（命名回调）"
            lines.append(f"    {identifier:34s}{suffix:34s} × {value}")
        lines.append("")
        lines.append(f"  本次普查：种类 = {{{report.get('callbackKindMask')}}}；"
                     f"有派发点登记 {report.get('dispatchableRegistrations')}、"
                     f"无派发点登记 {report.get('unhookedRegistrations')}、"
                     f"注册表 {report.get('registryCount')} 条")
        lines.append(f"  加载结果：loadOk={report.get('loadOk')} "
                     f"scriptState={report.get('scriptState')}")
    return "\n".join(lines)


def describe_sweep(sweep: dict, romfs: Path) -> str:
    """把扫描结果按出现顺序列出来（每个失败点：完整文本、模块:行号、触发语句、补偿方式）。"""
    lines: list[str] = []
    lines.append("=== EID 加载期失败点扫描（按出现顺序；每步都做了补偿，**不是**忠实运行）===")
    lines.append(f"夹具：{sweep['sourceRomfs']}")
    lines.append(f"源码状态：{sweep['sourceRevision']}")
    lines.append("")
    for index, blocker in enumerate(sweep["blockers"], start=1):
        position = blocker.get("position")
        lines.append(f"--- 第 {index} 个失败点（第 {blocker['iteration']} 次运行）---")
        if position is None:
            lines.append(f"  无法定位：{blocker['errorText']}")
        else:
            lines.append(f"  位置     : {position['module']}:{position['line']}")
            lines.append(f"  触发语句 : {position['statement']}")
            lines.append(f"  消息     : {position['message']}")
            lines.append(f"  完整文本 : {blocker['errorText']}（{blocker['errorLength']} 字节）")
            for context_line in failure_context(romfs, position, context=2):
                lines.append(f"  {context_line}")
        lines.append(f"  继续方式 : {blocker['compensation']}")
        lines.append(f"  当时已补全局: {', '.join(blocker['stubbedGlobals']) or '（无）'}")
        if blocker["maskedLines"]:
            rendered = "; ".join(
                f"{name}:{','.join(str(line) for line in lines_)}"
                for name, lines_ in blocker["maskedLines"].items()
            )
            lines.append(f"  当时已注释: {rendered}")
        lines.append("")
    final = sweep["finalReport"]
    lines.append(f"扫描结果：{sweep['iterations']} 个失败点之后，"
                 f"loadOk={final.get('loadOk')} scriptState={final.get('scriptState')}")
    stubs = sweep["stubbedGlobals"]
    lines.append(f"运行时缺失的全局（补桩才走过去）：{', '.join(stubs) if stubs else '（无）'}")
    if sweep["maskedLines"]:
        rendered = "; ".join(
            f"{name}:{','.join(str(line) for line in lines_)}"
            for name, lines_ in sweep["maskedLines"].items()
        )
        lines.append(f"被注释掉的语句（非全局缺失）：{rendered}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 结果解释
# ---------------------------------------------------------------------------


def describe_failure(report: dict, romfs: Path | None = None, context: int = 4) -> str:
    """把报告变成人读的说明：结果、完整错误消息、**失败模块**与行号、触发语句、一致性检查。"""
    lines: list[str] = []
    lines.append("=== EID 宿主机加载结果（真实 Runtime 源码 + vendored Lua 5.3.3） ===")
    if report.get("loadSkipped"):
        lines.append(f"清单解析阶段就失败了：step={report['resolveFailureStep']} "
                     f"detail={report['resolveFailureDetail']}")
        return "\n".join(lines)

    lines.append(f"entryPath  : {report['entryPath']}")
    lines.append(f"modRoot    : {report['modRoot']}")
    lines.append(f"chunkName  : {report['chunkName']}（{report['chunkNameLength']} 字符）")
    lines.append(f"入口文件   : {report.get('entryFileBytes')} 字节；缓冲区 {report['entryCapacity']} 字节")
    lines.append(f"清单       : {report['manifestBytes']} 字节")
    lines.append(f"惰性引擎绑定: {report.get('engineShims')}（spriteCtor=0x{report.get('spriteCtorThunk', 0):x}）")
    lines.append(f"宿主峰值内存: {report.get('peakRssMiB')} MiB（本次进程 maxrss）")
    lines.append("")
    lines.append(f"结果       : loadOk={report['loadOk']} scriptState={report['scriptState']}")
    lines.append(f"失败步骤   : {report['failureStep']}（detail={report['failureDetail']}"
                 f"，LuaInitResult={report['luaInitResult']}）")
    if report.get("observedBytes"):
        lines.append(f"观测长度   : {report['observedBytes']}（读取失败时才有意义）")

    text = report.get("errorText", "")
    if text:
        lines.append("")
        lines.append(f"完整错误消息（{report['errorLength']} 字节，原文）：")
        lines.append(f"  {text}")
        position = report.get("failurePosition")
        if position:
            lines.append("")
            lines.append(f"失败位置   : {position['module']}:{position['line']}")
            lines.append(f"消息正文   : {position['message']}")
            if position.get("statement"):
                lines.append(f"触发语句   : {position['statement'].strip()}")
            if romfs is not None:
                lines.extend(failure_context(romfs, position, context))

    if report.get("callbackError"):
        lines.append("")
        lines.append(f"回调错误   : {report.get('callbackErrorText', '')}")

    if report.get("callbackKindMask") is not None:
        lines.append("")
        lines.append(f"回调普查   : 已登记种类 = {{{report['callbackKindMask']}}}；"
                     f"有派发点登记 {report.get('dispatchableRegistrations')}、"
                     f"无派发点登记 {report.get('unhookedRegistrations')}、"
                     f"注册表 {report.get('registryCount')} 条")
        lines.extend(describe_callback_kinds(report))
        if report.get("requireFailures"):
            lines.append(f"require 失败: {report['requireFailures']} 次"
                         f"（首次 code={report.get('requireFirstCode')}、"
                         f"末次 code={report.get('requireLastCode')}；"
                         f"名字头 {report.get('requireNameHeads')!r}）—— Mod 用 `pcall` 吞掉的那些")

    requested = sum(1 for item in report.get("readRequests", []) if item["status"] == "ok")
    total = sum(item["bytes"] for item in report.get("readRequests", []))
    lines.append("")
    lines.append(f"读取统计   : {len(report.get('readRequests', []))} 次读取，"
                 f"{requested} 次成功，共 {total} 字节 Lua 源码")

    lines.append("")
    lines.append("=== 与真机证据的一致性检查 ===")
    lines.append(f"chunkName == 真机那条  : {report['chunkMatchesDevice']} "
                 f"({'一致' if report['chunkMatchesDevice'] else '不一致：' + report['chunkName']})")
    lines.append(f"错误长度 == 102        : {report['errorLengthMatchesDevice']} "
                 f"(宿主机 {report['errorLength']})")
    head = text.encode("utf-8", "replace")[:8]
    lines.append(f"前 8 字节 == '..._mods': {report['errorPrefixMatchesDevice']} "
                 f"(宿主机 {head!r})")
    if not report["errorLengthMatchesDevice"]:
        digits = len(str((report.get("failurePosition") or {}).get("line", 0)))
        lines.append(f"→ 长度差 {report['errorLength'] - DEVICE_ERROR_LENGTH} 字节；"
                     f"真机 102 = 前缀 {LUA_CHUNKID_PREFIX_LENGTH} + 行号位数 + 2 + 消息长度，"
                     f"即'行号位数 + 消息长度 = "
                     f"{DEVICE_ERROR_LENGTH - LUA_CHUNKID_PREFIX_LENGTH - 2}'。"
                     f"宿主机这次是 {digits} 位行号 + {len((report.get('failurePosition') or {}).get('message', ''))} 字消息。")
    if not report["errorLengthMatchesDevice"] or not report["errorPrefixMatchesDevice"]:
        lines.append("→ 与真机不一致时**不要**把它当成同一条错误：差异要么来自宿主机缺引擎/缺 API，"
                     "要么来自真机走到的那一行不同；下面逐条说明。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="在宿主机上用本仓库的 Lua Runtime 跑真实 PC Mod EID 的 main.lua",
    )
    parser.add_argument("--max-bytes", type=int, default=None,
                        help="入口脚本缓冲区上限（默认 1 MiB = kRomfsModScriptMaximumLength）；"
                             "给 16384 可复现 16 KiB 缓冲区时代的 EntryRead 失败")
    parser.add_argument("--frames", type=int, default=3,
                        help="加载成功后派发多少帧 POST_UPDATE（默认 3）")
    parser.add_argument("--romfs", type=Path, default=None,
                        help="宿主 isaac_mods 目录（默认自动定位 analysis/ 或 runtime/.eid-artifacts）")
    parser.add_argument("--workdir", type=Path, default=None, help="构建目录（默认临时目录）")
    parser.add_argument("--keep", action="store_true", help="保留构建目录")
    parser.add_argument("--engine-shims", choices=("inert", "none", "both"), default="inert",
                        help="inert（默认）= 发布惰性宿主引擎绑定，让脚本越过第一处引擎调用；"
                             "none = 不发布（宿主机没有引擎的真实形态）；both = 两种都跑并对比")
    parser.add_argument("--font-load", choices=("ok", "fail"), default="ok",
                        help="惰性 Font 绑定里 `Font:Load` 的返回值（默认 ok）。给 fail 可复现"
                             "真机'字体加载失败 ⇒ main.lua 顶层 return ⇒ 什么都不显示'那条分叉")
    parser.add_argument("--font-arg", choices=("any", "strict"), default="any",
                        help="惰性 `Font:Load` 的入参口径。`strict` 只接受内容挂载点相对名"
                             "（`font/eid_default.fnt`），用来验证路径归一化是否真的把名字修对了；"
                             "`any`（默认）对任何字符串都答成功")
    parser.add_argument("--font-phase", choices=("all", "first-fails"), default="all",
                        help="`Font:Load`/`IsLoaded` 的分段返回。`first-fails` 镜像真机形态："
                             "第 1 次 `Load` 失败、`IsLoaded` 为假，于是 EID 的 `../mods/...` 兜底"
                             "分支会被真正走一遍；`all`（默认）两次都按名字口径判定")
    parser.add_argument("--sweep", action="store_true",
                        help="反复补偿并列出加载期**所有**失败点（不是忠实运行，用于列修复清单）")
    parser.add_argument("--callback-probe", action="store_true",
                        help="只跑回调契约探针：同一 Mod 同一 id 的多次登记是否并存（PC 语义）")
    parser.add_argument("--trace-addcallback", action="store_true",
                        help="插桩打印每一次 `Mod:AddCallback`（文件:行 + id），字体成功/失败各跑一遍")
    parser.add_argument("--json", action="store_true", help="只输出机器可读 JSON（测试用）")
    arguments = parser.parse_args(argv)

    romfs = arguments.romfs or locate_eid_romfs()
    if romfs is None:
        print("找不到 EID 夹具：", file=sys.stderr)
        for candidate in _ROMFS_CANDIDATES:
            print(f"  - {candidate}", file=sys.stderr)
        print("（EID 目录在 .gitignore 覆盖的 analysis/ 下，缺失时本工具按 skip 处理）",
              file=sys.stderr)
        return 77
    if host_compilers() is None:
        print("缺少宿主 C/C++ 编译器（cc + c++/clang++/g++），无法构建 harness", file=sys.stderr)
        return 77

    def build_or_report(workdir: Path):
        """编译失败要打印编译器原文，而不是丢一个 traceback。

        工作树正在被并行修改（本项目常态），所以这里必须让失败**可读**：是 `runtime/source`
        当前编不过，还是工具本身的问题，一眼可分。
        """
        try:
            return prepare_workspace(romfs, workdir)
        except RuntimeError as error:
            print(f"harness 编译失败（先确认 runtime/source、runtime/src 当前能否编译）：\n{error}",
                  file=sys.stderr)
            raise SystemExit(70) from error

    if arguments.trace_addcallback:
        try:
            trace = trace_callback_registrations(
                romfs=romfs, workdir=arguments.workdir, keep=arguments.keep,
                capacity=arguments.max_bytes or 1048576,
            )
        except RuntimeError as error:
            print(f"harness 编译失败：\n{error}", file=sys.stderr)
            return 70
        if arguments.json:
            print(json.dumps(trace, ensure_ascii=False, indent=2, sort_keys=True, default=str))
        else:
            print(describe_callback_trace(trace))
        return 0

    if arguments.callback_probe:
        workdir = arguments.workdir or Path(tempfile.mkdtemp(prefix="eid-host-probe-"))
        try:
            executable = build_harness(Path(workdir))
        except RuntimeError as error:
            print(f"harness 编译失败：\n{error}", file=sys.stderr)
            return 70
        probe = run_callback_probe(executable)
        if arguments.json:
            print(json.dumps(probe, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print("=== 回调契约探针（同一 Mod 同一 id 多次登记）===")
            print("脚本：同一个 Mod 登记 2 条 MC_POST_UPDATE + 1 条 MC_POST_RENDER；派发 2 帧")
            print(f"派发前 id=1 的存活条数 : {probe.get('livePostUpdate')}（PC 语义应为 2）")
            print(f"第 1 条回调被调用次数   : {probe.get('probeA')}（应为 2）")
            print(f"第 2 条回调被调用次数   : {probe.get('probeB')}（应为 2）")
            print(f"同一 Mod 的重复登记并存: {probe.get('sameOwnerCoexists')}")
            print(f"派发计数 {probe.get('postUpdateCount')}，回调错误 {probe.get('callbackError')}")
            if not probe.get("sameOwnerCoexists"):
                print("→ 不符合 PC 契约：同一 Mod 对同一 id 的第二次登记把第一次**替换**掉了"
                      "（PC 上是并存、按登记顺序全部调用）。")
        return 0 if probe.get("sameOwnerCoexists") else 1

    if arguments.sweep:
        try:
            sweep = sweep_eid_load(
                romfs=romfs, capacity=arguments.max_bytes, frames=arguments.frames,
                workdir=arguments.workdir, keep=arguments.keep,
                engine_shims=arguments.engine_shims != "none",
                font_load=arguments.font_load == "ok",
            )
        except RuntimeError as error:
            print(f"harness 编译失败（先确认 runtime/source、runtime/src 当前能否编译）：\n{error}",
                  file=sys.stderr)
            return 70
        if arguments.json:
            print(json.dumps(sweep, ensure_ascii=False, indent=2, sort_keys=True, default=str))
        else:
            print(describe_sweep(sweep, romfs))
            print()
            print(describe_failure(sweep["finalReport"], romfs))
        return 0 if sweep["finalReport"].get("loadOk") else 1

    modes = {"inert": (True,), "none": (False,), "both": (False, True)}[arguments.engine_shims]
    reports = []
    owned = arguments.workdir is None
    temporary = None
    workdir = arguments.workdir
    if owned:
        temporary = tempfile.TemporaryDirectory(prefix="eid-host-load-")
        workdir = Path(temporary.name)
    try:
        executable, prepared = build_or_report(workdir)
        for shims in modes:
            report = run_harness(executable, prepared, arguments.max_bytes or 1048576,
                                 arguments.frames, shims, arguments.font_load == "ok",
                                 arguments.font_arg, arguments.font_phase)
            reports.append(_finish(report, romfs, workdir))
    finally:
        if owned and temporary is not None and not arguments.keep:
            temporary.cleanup()

    if arguments.json:
        payload = reports[0] if len(reports) == 1 else reports
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))
        return 0 if all(item.get("loadOk") for item in reports) else 1
    for index, report in enumerate(reports):
        if index:
            print()
        print(describe_failure(report, romfs))
    return 0 if all(item.get("loadOk") for item in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
