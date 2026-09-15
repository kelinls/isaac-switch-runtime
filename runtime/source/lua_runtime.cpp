#include "lua_runtime.hpp"

#include "game_observer.hpp"
#include "lua_object_handles.hpp"
#include "lua_runtime_state.hpp"
#include "hook_manager.hpp"
#include "mod_persistence.hpp"
#include "persistence_event_journal.hpp"
#include "program/setting.hpp"

#include <cstdarg>
#include <ctime>

// 堆用量读数只在真机（devkitA64/newlib）上有意义：宿主 harness 用 clang 编译本文件，
// macOS 的 `sbrk` 被标成 deprecated 且 harness 带 `-Werror`（曾因此让 13 个宿主用例编不过）。
// `__SWITCH__` 是 devkitA64 预定义的。
#if defined(__SWITCH__) && defined(EXL_USE_FAKEHEAP)
#include <unistd.h>
extern "C" char __fake_heap[];
#endif
#include "program/pc_lua_enum_data.hpp"
#include "program/pc_lua_enum_data.cpp"
#include "program/embedded_lua_test_script.hpp"
#include "runtime_constants.hpp"

// Every build entry point (production module, diagnostics and the startup
// probes) defines `EXL_LAYERED_RUNTIME`, so the layered headers are required
// unconditionally: the non-layered duplicate implementations no longer exist.
#include "interfaces/lua/engine_frame_probe.hpp"
#include "interfaces/lua/engine_memory_guard.hpp"
#include "application/callback/callback_dispatcher.hpp"
#include "application/callback/callback_registry.hpp"
#include "domain/callback/callback_descriptor.hpp"
#include "interfaces/lua/api_catalog.hpp"
#include "interfaces/lua/json_api.hpp"
#include "interfaces/lua/color_api.hpp"
#include "interfaces/lua/font_api.hpp"
#include "interfaces/lua/game_api.hpp"
#include "interfaces/lua/input_api.hpp"
#include "interfaces/lua/isaac_api.hpp"
#include "interfaces/lua/mod_api.hpp"
#include "interfaces/lua/music_api.hpp"
#include "interfaces/lua/remaining_api.hpp"
#include "interfaces/lua/rng_api.hpp"
#include "interfaces/lua/vector_api.hpp"
#include "interfaces/lua/sprite_api.hpp"

#if defined(__SWITCH__)
#include "lib/nx/nx.h"
#endif

extern "C" {
#include <lauxlib.h>
#include <lualib.h>
}

#include <atomic>
#include <array>
#include <cstring>
#include <limits>

namespace LuaRuntime {

namespace {

constexpr char kModMethodsField[] = "__methods";
// PC 契约里 `MC_POST_UPDATE` 是 1（0 是 `MC_NPC_UPDATE`）。2026-09-12 起 Lua 侧的
// `ModCallbacks` 表由 `pc_lua_enum_data.cpp` 生成（非 stage13 构建直接用生成表），
// 所以这里的常量只在 stage13 诊断路径与派发查询里还有引用；`[[maybe_unused]]`
// 是为了不在 `-Werror` 下因某个构建配置未引用而失败。
[[maybe_unused]] constexpr int kPostUpdateCallback = 1;
// The Stage13 diagnostic build exposes only MC_POST_UPDATE, so these two are
// deliberately unused there; the attribute keeps the shared table complete
// without weakening the -Werror build.
[[maybe_unused]] constexpr int kPostRenderCallback = 2;
[[maybe_unused]] constexpr int kInputActionCallback = 13;
[[maybe_unused]] constexpr int kPreGetCollectibleCallback = 62;
#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 13
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 13
constexpr int kStage13PostUpdateCallback = kStage13CallbackValue;
#endif
constexpr std::size_t kStage13RequireMaximumDepth = 8;
constexpr std::size_t kStage13ModRootCapacity = 1024;
constexpr std::size_t kStage13RequirePathCapacity = 1536;
constexpr char kStage13ModRootPrefix[] = "rom:/isaac_mods/mods/";
#endif

struct LuaIntegerConstant {
    const char* name;
    lua_Integer value;
};

constexpr std::array<LuaIntegerConstant, 28> kButtonActionConstants{{
    {"ACTION_LEFT", 0}, {"ACTION_RIGHT", 1}, {"ACTION_UP", 2}, {"ACTION_DOWN", 3},
    {"ACTION_SHOOTLEFT", 4}, {"ACTION_SHOOTRIGHT", 5}, {"ACTION_SHOOTUP", 6},
    {"ACTION_SHOOTDOWN", 7}, {"ACTION_BOMB", 8}, {"ACTION_ITEM", 9},
    {"ACTION_PILLCARD", 10}, {"ACTION_DROP", 11}, {"ACTION_PAUSE", 12},
    {"ACTION_MAP", 13}, {"ACTION_MENUCONFIRM", 14}, {"ACTION_MENUBACK", 15},
    {"ACTION_RESTART", 16}, {"ACTION_FULLSCREEN", 17}, {"ACTION_MUTE", 18},
    {"ACTION_JOINMULTIPLAYER", 19}, {"ACTION_MENULEFT", 20}, {"ACTION_MENURIGHT", 21},
    {"ACTION_MENUUP", 22}, {"ACTION_MENUDOWN", 23}, {"ACTION_MENULT", 24},
    {"ACTION_MENURT", 25}, {"ACTION_MENUTAB", 26}, {"ACTION_CONSOLE", 28},
}};

constexpr std::array<LuaIntegerConstant, 120> kKeyboardConstants{{
    {"KEY_SPACE", 32}, {"KEY_APOSTROPHE", 39}, {"KEY_COMMA", 44}, {"KEY_MINUS", 45},
    {"KEY_PERIOD", 46}, {"KEY_SLASH", 47}, {"KEY_0", 48}, {"KEY_1", 49}, {"KEY_2", 50},
    {"KEY_3", 51}, {"KEY_4", 52}, {"KEY_5", 53}, {"KEY_6", 54}, {"KEY_7", 55},
    {"KEY_8", 56}, {"KEY_9", 57}, {"KEY_SEMICOLON", 59}, {"KEY_EQUAL", 61},
    {"KEY_A", 65}, {"KEY_B", 66}, {"KEY_C", 67}, {"KEY_D", 68}, {"KEY_E", 69},
    {"KEY_F", 70}, {"KEY_G", 71}, {"KEY_H", 72}, {"KEY_I", 73}, {"KEY_J", 74},
    {"KEY_K", 75}, {"KEY_L", 76}, {"KEY_M", 77}, {"KEY_N", 78}, {"KEY_O", 79},
    {"KEY_P", 80}, {"KEY_Q", 81}, {"KEY_R", 82}, {"KEY_S", 83}, {"KEY_T", 84},
    {"KEY_U", 85}, {"KEY_V", 86}, {"KEY_W", 87}, {"KEY_X", 88}, {"KEY_Y", 89},
    {"KEY_Z", 90}, {"KEY_LEFT_BRACKET", 91}, {"KEY_BACKSLASH", 92},
    {"KEY_RIGHT_BRACKET", 93}, {"KEY_GRAVE_ACCENT", 96}, {"KEY_WORLD_1", 161},
    {"KEY_WORLD_2", 162}, {"KEY_ESCAPE", 256}, {"KEY_ENTER", 257}, {"KEY_TAB", 258},
    {"KEY_BACKSPACE", 259}, {"KEY_INSERT", 260}, {"KEY_DELETE", 261}, {"KEY_RIGHT", 262},
    {"KEY_LEFT", 263}, {"KEY_DOWN", 264}, {"KEY_UP", 265}, {"KEY_PAGE_UP", 266},
    {"KEY_PAGE_DOWN", 267}, {"KEY_HOME", 268}, {"KEY_END", 269}, {"KEY_CAPS_LOCK", 280},
    {"KEY_SCROLL_LOCK", 281}, {"KEY_NUM_LOCK", 282}, {"KEY_PRINT_SCREEN", 283},
    {"KEY_PAUSE", 284}, {"KEY_F1", 290}, {"KEY_F2", 291}, {"KEY_F3", 292},
    {"KEY_F4", 293}, {"KEY_F5", 294}, {"KEY_F6", 295}, {"KEY_F7", 296},
    {"KEY_F8", 297}, {"KEY_F9", 298}, {"KEY_F10", 299}, {"KEY_F11", 300},
    {"KEY_F12", 301}, {"KEY_F13", 302}, {"KEY_F14", 303}, {"KEY_F15", 304},
    {"KEY_F16", 305}, {"KEY_F17", 306}, {"KEY_F18", 307}, {"KEY_F19", 308},
    {"KEY_F20", 309}, {"KEY_F21", 310}, {"KEY_F22", 311}, {"KEY_F23", 312},
    {"KEY_F24", 313}, {"KEY_F25", 314}, {"KEY_KP_0", 320}, {"KEY_KP_1", 321},
    {"KEY_KP_2", 322}, {"KEY_KP_3", 323}, {"KEY_KP_4", 324}, {"KEY_KP_5", 325},
    {"KEY_KP_6", 326}, {"KEY_KP_7", 327}, {"KEY_KP_8", 328}, {"KEY_KP_9", 329},
    {"KEY_KP_DECIMAL", 330}, {"KEY_KP_DIVIDE", 331}, {"KEY_KP_MULTIPLY", 332},
    {"KEY_KP_SUBTRACT", 333}, {"KEY_KP_ADD", 334}, {"KEY_KP_ENTER", 335},
    {"KEY_KP_EQUAL", 336}, {"KEY_LEFT_SHIFT", 340}, {"KEY_LEFT_CONTROL", 341},
    {"KEY_LEFT_ALT", 342}, {"KEY_LEFT_SUPER", 343}, {"KEY_RIGHT_SHIFT", 344},
    {"KEY_RIGHT_CONTROL", 345}, {"KEY_RIGHT_ALT", 346}, {"KEY_RIGHT_SUPER", 347},
    {"KEY_MENU", 348},
}};

lua_State* g_LuaState = nullptr;
// One registry owns every registered callback, so several Mods can register the
// same phase and dispatch order is deterministic. Probe and production builds
// share it; the legacy per-phase single slots were deleted with the migration.
isaac::runtime::CallbackRegistry g_CallbackRegistry;
constexpr isaac::runtime::ModHandle kRuntimeOwner{1, 1};  // RegisterMod allows one Mod
bool g_ModRegistered = false;
std::atomic<u32> g_Ready{false};
std::atomic<u32> g_CallbackError{false};
std::atomic<u32> g_InManagedCallbackDispatch{false};

// --- 最近一次 Lua 错误信息 ------------------------------------------------
//
// 真机上文件写入通道多轮无产物，错误信息只能走“装进诊断字、随探针报错进崩溃报告”
// 这条路。这里保留最近一次失败信息的前 256 字节，诊断出口只吐前 8 字节 + 长度。
std::array<char, 256> g_LastLuaErrorText{};
std::atomic<std::uint32_t> g_LastLuaErrorLength{0};

// 记录栈顶的错误值。**对非字符串错误值也能记录**（2026-09-12 第七轮）。
//
// 旧实现只认 `lua_tolstring` 成功的情形：Lua 允许 `error(任意值)`，一旦错误值不是字符串
// （表/函数/userdata/自定义对象——引擎自己的绑定层就这么抛），它**什么都不记**，
// 于是 `errorLength` 与内容长期不一致，报告里读到的是上一条消息的残渣。
// 真机连续性证据：`errorLength = 1`，而负载里读出 `"AKP2"`（上一次探针负载的残片）。
//
// 现在：非字符串时用 `luaL_tolstring`（接受任意值、保证产出字符串、自己压栈），
// 记完把栈复原 —— 调用方原本就打算 `lua_pop(state, 1)` 弹掉错误值，所以这里必须弹干净。
void CaptureLuaErrorTop(lua_State* state) {
    if (state == nullptr || lua_gettop(state) == 0) {
        return;
    }
    std::size_t length = 0;
    if (const char* message = lua_tolstring(state, -1, &length); message != nullptr) {
        if (length == 0) {
            return;
        }
        if (length > g_LastLuaErrorText.size()) {
            length = g_LastLuaErrorText.size();
        }
        std::memcpy(g_LastLuaErrorText.data(), message, length);
        g_LastLuaErrorLength.store(static_cast<std::uint32_t>(length),
                                  std::memory_order_release);
        return;
    }
    // 非字符串：转成可读文本（表会走 `__tostring` 或类型名）。
    const int topBefore = lua_gettop(state);
    const char* text = luaL_tolstring(state, -1, nullptr);
    if (text != nullptr) {
        const std::size_t textLength = std::strlen(text);
        if (textLength != 0) {
            const std::size_t copyLength = textLength < g_LastLuaErrorText.size()
                                               ? textLength
                                               : g_LastLuaErrorText.size();
            std::memcpy(g_LastLuaErrorText.data(), text, copyLength);
            g_LastLuaErrorLength.store(static_cast<std::uint32_t>(copyLength),
                                       std::memory_order_release);
        }
    }
    lua_settop(state, topBefore);  // 弹掉 `luaL_tolstring` 压入的字符串
}
#if defined(EXL_PERSISTENCE_TRACE) || defined(EXL_PERSISTENCE_EVENT_DIAGNOSTIC)
std::atomic<u8> g_CallbackOperation{static_cast<u8>(CallbackOperation::None)};
std::atomic<u64> g_CallbackFailureDetail{0};

u64 CallbackErrorTail(lua_State* state) {
    if (state == nullptr || lua_type(state, -1) != LUA_TSTRING) {
        return 0;
    }
    std::size_t length = 0;
    const char* error = lua_tolstring(state, -1, &length);
    constexpr std::size_t kMaximumTailLength = 6;
    const std::size_t tailLength = length > kMaximumTailLength ? kMaximumTailLength : length;
    u64 tail = 0;
    if (error != nullptr) {
        for (std::size_t index = length - tailLength; index < length; ++index) {
            tail = (tail << 8) | static_cast<unsigned char>(error[index]);
        }
    }
    return tail;
}
#endif
// Re-entrancy guard for the PreGetCollectible relay dispatch.
//
// NOT `thread_local`. This is an injected module, and a `thread_local` here cost a
// hardware crash: reading it on the game's MainThread calls `__aarch64_read_tp()`,
// which is `mrs x0, tpidrro_el0 ; ldr x0, [x0, #504]` -- 0 for this module -- and the
// very next instruction dereferences that null (`ldrb w0, [x0, x0]`). Crash report
// `01789099948`: Result 0x4A8 (Data Abort), fault address 0x0, PC = runtime+0x440c,
// reached from `Entity_Pickup::Init` through the PreGetCollectible relay, i.e. as soon
// as a room spawns a pickup. It was the only TLS access in the whole module.
//
// The dispatch only ever happens on the game thread, and its sibling
// `g_InManagedCallbackDispatch` below is an ordinary global, so a plain global is both
// equivalent here and consistent with the rest of the file.
bool g_InPreGetCollectibleDispatch = false;
std::atomic<uintptr_t> g_CurrentCallbackManager{0};
std::atomic<u32> g_PostUpdateCount{0};
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 15
std::atomic<u32> g_PostRenderCount{0};
std::atomic<u32> g_PostRenderPausedCount{0};
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && (EXL_DIAGNOSTIC_STAGE == 16 || EXL_DIAGNOSTIC_STAGE == 17)
std::atomic<u32> g_MusicDiagnosticCycleCompleted{false};
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 45
std::atomic<u32> g_MusicDiagnosticCurrentIdReady{false};
std::atomic<u32> g_MusicDiagnosticCurrentId{0};
#endif
std::atomic<u32> g_PreparationStep{0};
std::atomic<u32> g_PreparationStatus{0};
// 游戏本体 NRO 的加载基址（批次 2）：`Isaac.GetPlayer` 的指针链与 `Entity_Player` 的 vptr
// 判据都要用它，所以由 Hook 安装阶段在已拿到 `TargetModule::base` 的地方发布。
std::atomic<uintptr_t> g_EngineModuleBase{0};
std::atomic<uintptr_t> g_GameOwnerSlot{0};
std::atomic<uintptr_t> g_GameIsPausedThunk{0};
std::atomic<uintptr_t> g_GameIsGreedMode{0};
std::atomic<uintptr_t> g_LevelIsAscent{0};
std::atomic<uintptr_t> g_ItemPoolGetCollectible{0};
std::atomic<uintptr_t> g_MusicGetCurrentMusicId{0};
std::atomic<uintptr_t> g_MusicPause{0};
std::atomic<uintptr_t> g_MusicResume{0};
std::atomic<uintptr_t> g_RngSetSeed{0};
std::atomic<uintptr_t> g_RngNext{0};
std::atomic<uintptr_t> g_InputIsActionPressed{0};
std::atomic<uintptr_t> g_InputIsActionTriggered{0};
std::atomic<uintptr_t> g_InputGetActionValue{0};
// Font entry points (`KAGE::Graphics::Font`). The Font object these are called on is allocated
// and released by the `Font` family unit, not by the game.
std::atomic<uintptr_t> g_FontCtor{0};
std::atomic<uintptr_t> g_FontDestructor{0};
std::atomic<uintptr_t> g_FontLoad{0};
std::atomic<uintptr_t> g_FontUnload{0};
std::atomic<uintptr_t> g_FontIsLoaded{0};
std::atomic<uintptr_t> g_FontGetStringWidth{0};
std::atomic<uintptr_t> g_FontGetStringWidthUTF8{0};
std::atomic<uintptr_t> g_FontGetLineHeight{0};
std::atomic<uintptr_t> g_FontGetBaselineHeight{0};
std::atomic<uintptr_t> g_FontGetCharacterWidth{0};
std::atomic<uintptr_t> g_FontSetMissingCharacter{0};
std::atomic<uintptr_t> g_FontDrawString{0};
std::atomic<uintptr_t> g_FontDrawStringScaled{0};
std::atomic<uintptr_t> g_FontDrawStringUTF8{0};
std::atomic<uintptr_t> g_FontDrawStringScaledUTF8{0};
std::atomic<uintptr_t> g_SpriteCtor{0};
std::atomic<uintptr_t> g_SpriteDestructor{0};
std::atomic<uintptr_t> g_SpritePlay{0};
std::atomic<uintptr_t> g_SpriteSetAnimation{0};
std::atomic<uintptr_t> g_SpriteSetFrameNamed{0};
std::atomic<uintptr_t> g_SpriteSetFrame{0};
std::atomic<uintptr_t> g_SpriteGetFrame{0};
std::atomic<uintptr_t> g_SpriteSetLayerFrame{0};
std::atomic<uintptr_t> g_SpriteGetLayerFrame{0};
std::atomic<uintptr_t> g_SpriteGetTexel{0};
std::atomic<uintptr_t> g_SpriteUpdate{0};
std::atomic<uintptr_t> g_SpriteIsPlaying{0};
std::atomic<uintptr_t> g_SpriteIsFinished{0};
std::atomic<uintptr_t> g_SpriteRender{0};
std::atomic<uintptr_t> g_SpriteRenderLayer{0};
std::atomic<uintptr_t> g_SpritePlayRandom{0};
std::atomic<uintptr_t> g_SpriteLoad{0};
std::atomic<uintptr_t> g_SpriteLoadGraphics{0};
std::atomic<uintptr_t> g_SpriteReplaceSpritesheet{0};
std::atomic<uintptr_t> g_LibcxxStringAssign{0};
std::atomic<uintptr_t> g_GameOperatorDelete{0};
#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 13
GameFileReader::Bindings g_Stage13Bindings{};
std::array<char, kStage13ModRootCapacity> g_Stage13ModRoot{};
std::array<char, kRomfsModScriptMaximumLength> g_Stage13RequireSource{};
std::size_t g_Stage13ModRootLength = 0;
std::size_t g_Stage13RequireDepth = 0;
int g_RequireCacheRef = LUA_NOREF;
int g_ModObjectRef = LUA_NOREF;
bool g_Stage13Mode = false;
// `require` 是否可用：由 `SetStage13Context` 置位（生产与 stage13 都经过它）。
bool g_RequireAvailable = false;
std::atomic<u32> g_RequireFailureDetail{0};
std::atomic<u64> g_RequireErrorTail{0};
#endif

// `require` 失败的快照量（第一次/最后一次的代码与模块名、总次数）。
//
// **刻意放在上面那个守卫之外**：`RequireFailureSnapshot()` 定义在通用区（探针在所有构建里都调它），
// 而这些量原先写在 `#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 13` 里，
// 于是 stage7/12 这类诊断构建编译 `RequireFailureSnapshot` 时它们"未声明"（宿主 harness 直接编不过）。
std::atomic<u32> g_RequireFirstFailureCode{0};
std::atomic<u64> g_RequireFirstFailureName{0};
std::atomic<u32> g_RequireLastFailureCode{0};
std::atomic<u64> g_RequireLastFailureName{0};
std::atomic<u32> g_RequireFailureTotal{0};

#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 13
// 当前正在处理的模块名（`Require` 一拿到名字就填），供失败记录打包成 8 字节进诊断。
// 为什么需要"名字"：真机报告 `01789202186` 只告诉我们 `eid_mcm.lua:81` 的
// `EID.descriptions[lang]` 是 nil，而**哪一个** `require` 失败、失败在哪一类
// （名字被拒 19 / 打不开 20 / 读失败 21 / 编译失败 22）完全看不到 —— 而这些失败被 EID 的
// `pcall` 静默吞掉（它只在自己认为"文件不存在"时放过，其余只写一条看不见的日志）。
std::array<char, 64> g_RequireCurrentModule{};
std::size_t g_RequireCurrentModuleLength = 0;
u64 PackModuleNameHead() {
    u64 head = 0;
    const std::size_t length =
        g_RequireCurrentModuleLength < sizeof(head) ? g_RequireCurrentModuleLength : sizeof(head);
    std::memcpy(&head, g_RequireCurrentModule.data(), length);
    return head;
}

void SetRequireCurrentModule(const char* name, std::size_t length) {
    g_RequireCurrentModule.fill('\0');
    if (name == nullptr) {
        g_RequireCurrentModuleLength = 0;
        return;
    }
    const std::size_t limit = g_RequireCurrentModule.size() - 1;
    const std::size_t copy = length < limit ? length : limit;
    std::memcpy(g_RequireCurrentModule.data(), name, copy);
    g_RequireCurrentModuleLength = copy;
}

void RecordRequireFailure(u32 detail) {
    const u64 nameHead = PackModuleNameHead();
    u32 expected = 0;
    if (g_RequireFailureDetail.compare_exchange_strong(expected, detail, std::memory_order_release,
                                                       std::memory_order_relaxed)) {
        // 第一次失败：把名字与代码一起留下（第一次失败才是"最先挡住 Mod 的那一个"）。
        g_RequireFirstFailureName.store(nameHead, std::memory_order_release);
        g_RequireFirstFailureCode.store(detail, std::memory_order_release);
    }
    g_RequireLastFailureName.store(nameHead, std::memory_order_release);
    g_RequireLastFailureCode.store(detail, std::memory_order_release);
    g_RequireFailureTotal.fetch_add(1, std::memory_order_acq_rel);
}

int RaiseRequireFailure(lua_State* state, u32 detail, const char* message) {
    RecordRequireFailure(detail);
    // `"%s"` 而不是直接当格式串：批次 3 起这条消息里会**拼进 Mod 自己给的模块名**
    // （PC 形状的 "no file '<路径>.lua'"），名字里出现 `%` 时直接当格式串是未定义行为。
    return luaL_error(state, "%s", message);
}

// PC 形状的 `require` 失败文本（批次 3）。
//
// 为什么必须长成这样：EID 的 `EID:GetCurrentModPath()`（`main.lua:147`）在**没有 debug 库**时
// 用 `require("")` 的失败文本反推自己的 mod 路径：
//
//   local _, err = pcall(require, "")
//   local _, basePathStart = string.find(err, "no file '", 1)     -- 第一条的结尾
//   local _, modPathStart  = string.find(err, "no file '", basePathStart)  -- 第二条的结尾
//   local modPathEnd = string.find(err, ".lua'", modPathStart)
//   modPath = string.sub(err, modPathStart+1, modPathEnd-1)
//
// 也就是"从**第二条** `no file '` 的单引号之后，取到下一个 `.lua'` 之前"。所以：
//   * 至少要有**两条** `no file '…lua'`（只有一条时第二次 `string.find` 返回 nil，
//     `string.sub` 直接报 "bad argument #3 to 'find' (number expected, got nil)"，
//     EID 的 `main.lua` 就断在那里 —— 这正是本批次要修的加载期硬阻断之一）；
//   * **本 Runtime 真正尝试的那条路径必须在第二条**，否则 EID 取到的是别的字符串。
//
// 因此文本固定为（`<rel>` = 模块名里的 `.` 换成 `/`）：
//
//   module '<名>' not found:
//   \tno file '<mod 根>/<rel>/init.lua'
//   \tno file '<mod 根>/<rel>.lua'
//
// `require("")` 时 `<rel>` 为空，第二条是 `<mod 根>/.lua`，EID 于是取到 `<mod 根>/`
// ——正是它要的"mod 目录 + 结尾斜杠"（它随后自己拼 `resources/font/...`）。
// 注意 EID 还会对结果做 `gsub(":/", ":\\")`，所以它最终拿到的是 `rom:\isaac_mods/...`；
// 这是 EID 自己为 Windows 写的转换，我们不改（真机能否被文件层接受见批次报告）。
//
// 名字里的 `'`、控制字符与 `%` 会破坏上面那个形状（也影响后面 `luaL_error` 的处理），
// 所以先做一遍替换。
std::size_t AppendRequireText(char* buffer, std::size_t capacity, std::size_t length,
                              const char* text, std::size_t textLength) {
    if (buffer == nullptr || capacity == 0 || text == nullptr) {
        return length;
    }
    for (std::size_t index = 0; index < textLength && length + 1 < capacity; ++index) {
        buffer[length++] = text[index];
    }
    buffer[length] = '\0';
    return length;
}

// 字面量的重载：长度由数组大小推出，避免手写长度时数错一个字节（读越界）。
template <std::size_t Size>
std::size_t AppendRequireText(char* buffer, std::size_t capacity, std::size_t length,
                              const char (&text)[Size]) {
    return AppendRequireText(buffer, capacity, length, text, Size - 1);
}

const char* BuildRequireNotFoundMessage(std::array<char, kStage13RequirePathCapacity * 2>* buffer,
                                        const char* modRoot, std::size_t modRootLength,
                                        const char* moduleName, std::size_t moduleLength) {
    if (buffer == nullptr) {
        return "module not found";
    }
    char* text = buffer->data();
    const std::size_t capacity = buffer->size();
    text[0] = '\0';
    std::size_t length = 0;
    length = AppendRequireText(text, capacity, length, "module '");
    for (std::size_t index = 0; index < moduleLength; ++index) {
        const unsigned char value = static_cast<unsigned char>(moduleName[index]);
        const char safe = (value >= 0x20 && value < 0x7F && value != '\'' && value != '%')
                              ? static_cast<char>(value)
                              : '?';
        length = AppendRequireText(text, capacity, length, &safe, 1);
    }
    length = AppendRequireText(text, capacity, length, "' not found:");
    for (int entry = 0; entry < 2; ++entry) {
        const bool directoryForm = entry == 0;
        std::size_t lineLength = AppendRequireText(text, capacity, length, "\n\tno file '");
        lineLength = AppendRequireText(text, capacity, lineLength, modRoot, modRootLength);
        // 根与模块名之间的分隔符：与 `Require` 真正拼路径时一样（`root + "/" + rel + ".lua"`）。
        // 少了它，空名（`require("")`）会拼成 `<根>.lua`，EID 提取出来的 mod 路径就没有结尾
        // 的斜杠，它随后拼 `resources/font/...` 会连成一个不存在的路径。
        lineLength = AppendRequireText(text, capacity, lineLength, "/");
        // 模块名逐字符翻译：`.` → `/`（与 `Require` 的成功路径同一条规则）。
        for (std::size_t index = 0; index < moduleLength; ++index) {
            const char translated = moduleName[index] == '.' ? '/' : moduleName[index];
            lineLength = AppendRequireText(text, capacity, lineLength, &translated, 1);
        }
        if (directoryForm) {
            lineLength = AppendRequireText(text, capacity, lineLength, "/init.lua'");
        } else {
            lineLength = AppendRequireText(text, capacity, lineLength, ".lua'");
        }
        length = lineLength;
    }
    return text;
}

bool ContainsAscii(const char* value, std::size_t valueLength, const char* needle) {
    if (value == nullptr || needle == nullptr) {
        return false;
    }
    const std::size_t needleLength = std::strlen(needle);
    if (needleLength == 0 || needleLength > valueLength) {
        return false;
    }
    for (std::size_t index = 0; index <= valueLength - needleLength; ++index) {
        if (std::memcmp(value + index, needle, needleLength) == 0) {
            return true;
        }
    }
    return false;
}

u32 ClassifyRequireExecutionFailure(lua_State* state) {
    std::size_t errorLength = 0;
    const char* error = lua_tolstring(state, -1, &errorLength);
    if (ContainsAscii(error, errorLength, "RegisterMod accepts exactly one Mod")) {
        return 26;
    }
    return 27;
}

void RecordRequireErrorTail(lua_State* state) {
    std::size_t errorLength = 0;
    const char* error = lua_tolstring(state, -1, &errorLength);
    u64 tail = 0;
    const std::size_t tailLength = errorLength > sizeof(tail) ? sizeof(tail) : errorLength;
    if (error != nullptr) {
        for (std::size_t index = errorLength - tailLength; index < errorLength; ++index) {
            tail = (tail << 8) | static_cast<unsigned char>(error[index]);
        }
    }
    g_RequireErrorTail.store(tail, std::memory_order_release);
}

// 模块名里允许哪些字符。
//
// 这里曾经是一张"字母数字 + `_` + `-` + `.`"的白名单，2026-09-12 真机报告 `01789201969`
// 证明那张白名单少了一个 `+`：
//   `EID:LoadLanguagePacks("ab+")` 会 `require("descriptions.ab+.en_us")`，
//   白名单把它判成"不安全的模块名" → 抛 PC 形状的 not-found → EID 的 `pcall` 把它
//   当"文件不存在"轻轻放过（只是不写警告）→ **ab+ 的 4 个语言包（`pt`/`bul`/`nl_nl`/`el_gr`）
//   永远没被加载**（它们只存在于 `descriptions/ab+/`，`rep/` 里没有）→
//   `EID.descriptions[lang]` 为 nil → `features/eid_mcm.lua:81` 直接崩：
//   "attempt to index a nil value (field '?')"。
//   EID 还用 `features.eid_xmldata_rep+`、`descriptions.rep+.*` 这些同样带 `+` 的名字。
//
// PC 的 `require` 只把 `.` 当层级分隔（换成 `/`），名字里其余字符一律按文件名原样使用。
// 所以这里改成"只挡路径分隔符与不可打印字符"：`..`（路径穿越）由调用点单独挡。
bool IsAsciiModuleCharacter(char value) {
    return value >= 0x20 && value < 0x7F && value != '/' && value != '\\';
}

bool SetStage13Context(const char* modRoot, const GameFileReader::Bindings& bindings) {
    if (modRoot == nullptr) {
        return false;
    }

    std::size_t length = 0;
    while (length < g_Stage13ModRoot.size() && modRoot[length] != '\0') {
        ++length;
    }
    constexpr std::size_t prefixLength = sizeof(kStage13ModRootPrefix) - 1;
    if (length == g_Stage13ModRoot.size() || length <= prefixLength ||
        std::memcmp(modRoot, kStage13ModRootPrefix, prefixLength) != 0) {
        return false;
    }
    const char* directory = modRoot + prefixLength;
    if ((length - prefixLength == 1 && directory[0] == '.') ||
        (length - prefixLength == 2 && directory[0] == '.' && directory[1] == '.')) {
        return false;
    }
    for (std::size_t index = prefixLength; index < length; ++index) {
        if (modRoot[index] == '/' || modRoot[index] == '\\') {
            return false;
        }
    }

    std::memcpy(g_Stage13ModRoot.data(), modRoot, length + 1);
    g_Stage13ModRootLength = length;
    g_Stage13Bindings = bindings;
    g_Stage13Mode = true;
    // 生产路径（清单 Mod 加载）也走这个函数设置 Mod 根目录与文件绑定，所以
    // `require` 从这里开始可用；stage13 只是同一套机制的另一个调用方。
    g_RequireAvailable = true;
    return true;
}

void ResetStage13Context() {
    g_Stage13Bindings = {};
    g_Stage13ModRoot.fill('\0');
    g_Stage13RequireSource.fill('\0');
    g_Stage13ModRootLength = 0;
    g_Stage13RequireDepth = 0;
    g_RequireCacheRef = LUA_NOREF;
    g_ModObjectRef = LUA_NOREF;
    g_Stage13Mode = false;
    g_RequireAvailable = false;
}

int Require(lua_State* state) {
    if (!g_RequireAvailable || lua_gettop(state) != 1 || lua_type(state, 1) != LUA_TSTRING) {
        return RaiseRequireFailure(state, 19, "invalid require module name");
    }

    std::size_t moduleLength = 0;
    const char* moduleName = lua_tolstring(state, 1, &moduleLength);
    // 先登记模块名：下面每一条失败路径（名字被拒 / 打不开 / 读失败 / 编译失败 / 执行失败）
    // 都要把"是谁"记进诊断，否则真机上只能看到"某个 require 挂了"。
    SetRequireCurrentModule(moduleName, moduleName != nullptr ? moduleLength : 0);
    // 失败文本要按 PC 的形状给出"尝试过的路径"（见 `BuildRequireNotFoundMessage`），
    // 所以先把这块缓冲准备好：模块名不安全/太长时同样要能拼出文本。
    std::array<char, kStage13RequirePathCapacity * 2> notFoundText{};
    const char* const notFound = BuildRequireNotFoundMessage(
        &notFoundText, g_Stage13ModRoot.data(), g_Stage13ModRootLength,
        moduleName != nullptr ? moduleName : "", moduleName != nullptr ? moduleLength : 0);
    if (moduleName == nullptr || moduleLength == 0 || moduleName[0] == '.' ||
        moduleName[moduleLength - 1] == '.' ||
        g_Stage13ModRootLength + moduleLength + 6 > kStage13RequirePathCapacity) {
        // `require("")` 就落在这里（EID 的 `EID:GetCurrentModPath()` 用的正是空名），
        // 所以这一条必须带 PC 形状的 "no file '…lua'" 文本。
        return RaiseRequireFailure(state, 19, notFound);
    }

    std::array<char, kStage13RequirePathCapacity> path{};
    std::memcpy(path.data(), g_Stage13ModRoot.data(), g_Stage13ModRootLength);
    std::size_t pathLength = g_Stage13ModRootLength;
    path[pathLength++] = '/';
    for (std::size_t index = 0; index < moduleLength; ++index) {
        if (!IsAsciiModuleCharacter(moduleName[index]) ||
            moduleName[index] == '/' || moduleName[index] == '\\' ||
            (moduleName[index] == '.' && index + 1 < moduleLength && moduleName[index + 1] == '.')) {
            return RaiseRequireFailure(state, 19, notFound);
        }
        path[pathLength++] = moduleName[index] == '.' ? '/' : moduleName[index];
    }
    std::memcpy(path.data() + pathLength, ".lua", 5);
    pathLength += 4;

    lua_rawgeti(state, LUA_REGISTRYINDEX, g_RequireCacheRef);
    const int cacheIndex = lua_gettop(state);
    lua_pushlstring(state, moduleName, moduleLength);
    lua_rawget(state, cacheIndex);
    if (!lua_isnil(state, -1)) {
        return 1;
    }
    lua_pop(state, 1);

    // 内建模块优先于磁盘查找：`json` 是运行时提供的（PC 引擎自带，Mod 无条件 require 它）。
    if (moduleLength == 4 && std::memcmp(moduleName, "json", 4) == 0) {
        lua_getglobal(state, "json");
        if (lua_istable(state, -1)) {
            lua_pushlstring(state, moduleName, moduleLength);
            lua_pushvalue(state, -2);
            lua_rawset(state, cacheIndex);
            return 1;
        }
        lua_pop(state, 1);
    }

    if (g_Stage13RequireDepth >= kStage13RequireMaximumDepth) {
        return RaiseRequireFailure(state, 19, "require nesting exceeds maximum depth");
    }
    ++g_Stage13RequireDepth;

    std::size_t length = 0;
    const auto readResult = GameFileReader::ReadTextFile(
        g_Stage13Bindings, path.data(), reinterpret_cast<u8*>(g_Stage13RequireSource.data()),
        g_Stage13RequireSource.size(), &length);
    if (readResult != GameFileReader::TextReadResult::Success) {
        --g_Stage13RequireDepth;
        if (readResult == GameFileReader::TextReadResult::OpenFailed) {
            // "文件不存在"同样是 PC 形状的 "no file '…lua'"（模块名已拼进文本里）。
            return RaiseRequireFailure(state, 20, notFound);
        }
        if (readResult == GameFileReader::TextReadResult::LengthOutOfRange) {
            // 与"读不出来"分开报：这是**缓冲区装不下**，PC 上不存在这种失败，所以文本必须能一眼
            // 认出来（错误信息会进崩溃报告的诊断字 [13..15]）。2026-09-12 的 EID 轮次里，
            // 16 KiB 的 `kRomfsModScriptMaximumLength` 就是这样吞掉了 EID 的 `require`。
            return RaiseRequireFailure(state, 23, "required module is larger than the script buffer");
        }
        return RaiseRequireFailure(state, 21, "required module could not be read");
    }

    std::array<char, kStage13RequirePathCapacity + 1> chunkNameStorage{};
    chunkNameStorage[0] = '@';
    std::memcpy(chunkNameStorage.data() + 1, path.data(), pathLength + 1);
    const char* chunkName = chunkNameStorage.data();
    auto& source = g_Stage13RequireSource;
    if (luaL_loadbufferx(state, source.data(), length, chunkName, "t") != LUA_OK) {
        --g_Stage13RequireDepth;
        return RaiseRequireFailure(state, 22, "required module could not be compiled");
    }
    if (lua_pcallk(state, 0, 1, 0, 0, nullptr) != LUA_OK) {
        --g_Stage13RequireDepth;
        const u32 detail = ClassifyRequireExecutionFailure(state);
        if (detail == 27 && g_RequireFailureDetail.load(std::memory_order_acquire) == 0) {
            RecordRequireErrorTail(state);
        }
        // 结构化失败码照记（26 = `RegisterMod` 被调用两次、27 = 其它执行失败），但**消息不再替换**：
        // 把内层错误**原样抛出去**，与 PC 的 `require` 一致。
        //
        // 为什么必须改（2026-09-12 第四轮真机）：EID 的 `main.lua:120` 是
        // `require("features.eid_mcm")`，我们原来把内层消息换成自己的
        // "required module could not be executed"，于是真机报告里**完全看不出**
        // `eid_mcm.lua` 到底哪一行、为什么失败 —— 而真机一轮只能问一个问题。
        // PC 的 require 传播原始错误（带出错的 chunk 名与行号），我们也照做。
        RecordRequireFailure(detail);
        if (lua_type(state, -1) == LUA_TSTRING) {
            return lua_error(state);
        }
        lua_pop(state, 1);
        return RaiseRequireFailure(state, detail, "required module could not be executed");
    }
    --g_Stage13RequireDepth;

    if (lua_isnil(state, -1)) {
        lua_pop(state, 1);
        lua_pushboolean(state, 1);
    }
    lua_pushlstring(state, moduleName, moduleLength);
    lua_pushvalue(state, -2);
    lua_rawset(state, cacheIndex);
    return 1;
}

void RegisterStage13Require(lua_State* state) {
    lua_pushnil(state);
    lua_setglobal(state, "load");
    lua_pushnil(state);
    lua_setglobal(state, "loadfile");
    lua_pushnil(state);
    lua_setglobal(state, "dofile");

    lua_newtable(state);
    g_RequireCacheRef = luaL_ref(state, LUA_REGISTRYINDEX);
    lua_pushcfunction(state, Require);
    lua_setglobal(state, "require");
}
#endif

void OpenLibrary(lua_State* state, u32 step, const char* name, lua_CFunction open) {
    g_PreparationStep.store(step, std::memory_order_relaxed);
    luaL_requiref(state, name, open, 1);
    lua_pop(state, 1);
}

int ModIndex(lua_State* state) {
    luaL_checkudata(state, 1, kModMetatable);
    if (lua_gettop(state) != 2) {
        return luaL_error(state, "Mod field lookup expects one key");
    }
    lua_getmetatable(state, 1);
    lua_getfield(state, -1, kModMethodsField);
    lua_pushvalue(state, 2);
    lua_rawget(state, -2);
    if (!lua_isnil(state, -1)) return 1;
    lua_pop(state, 3);
    lua_getuservalue(state, 1);
    lua_pushvalue(state, 2);
    lua_rawget(state, -2);
    return 1;
}

int ModNewIndex(lua_State* state) {
    luaL_checkudata(state, 1, kModMetatable);
    if (lua_gettop(state) != 3) {
        return luaL_error(state, "Mod field assignment expects one key and value");
    }
    lua_getuservalue(state, 1);
    lua_pushvalue(state, 2);
    lua_pushvalue(state, 3);
    lua_rawset(state, -3);
    return 0;
}

int RegisterMod(lua_State* state) {
    if (lua_gettop(state) != 2 || g_ModRegistered) {
        return luaL_error(state, "RegisterMod accepts exactly one Mod");
    }
    const char* name = luaL_checkstring(state, 1);
    luaL_checkinteger(state, 2);
    g_ModRegistered = true;

    auto* mod = static_cast<ModHandle*>(lua_newuserdata(state, sizeof(ModHandle)));
    mod->persistenceNamespace = ModPersistence::HashModNamespace(name, std::strlen(name));
    lua_newtable(state);
    lua_setuservalue(state, -2);
    luaL_getmetatable(state, kModMetatable);
    lua_setmetatable(state, -2);
#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 13
    if (g_Stage13Mode) {
        g_ModObjectRef = lua_gettop(state);
        lua_pushvalue(state, g_ModObjectRef);
        g_ModObjectRef = luaL_ref(state, LUA_REGISTRYINDEX);
    }
#endif
    return 1;
}


void SetIntegerConstants(lua_State* state, const LuaIntegerConstant* constants, std::size_t count) {
    lua_newtable(state);
    for (std::size_t index = 0; index < count; ++index) {
        lua_pushinteger(state, constants[index].value);
        lua_setfield(state, -2, constants[index].name);
    }
}

void RegisterPcLuaEnumTables(lua_State* state) {
    std::size_t tableCount = 0;
    const PcLuaEnumData::Table* tables = PcLuaEnumData::Tables(&tableCount);
    if (tables == nullptr) return;
    for (std::size_t tableIndex = 0; tableIndex < tableCount; ++tableIndex) {
        lua_newtable(state);
        for (std::size_t valueIndex = 0; valueIndex < tables[tableIndex].count; ++valueIndex) {
            lua_pushinteger(state, tables[tableIndex].values[valueIndex].value);
            lua_setfield(state, -2, tables[tableIndex].values[valueIndex].key);
        }
        lua_setglobal(state, tables[tableIndex].name);
    }
}


void RegisterNeutralInputApi(lua_State* state) {
    lua_newtable(state);
    static_cast<void>(isaac::runtime::AttachInputMethods(state));
    lua_setglobal(state, "Input");

    SetIntegerConstants(state, kKeyboardConstants.data(), kKeyboardConstants.size());
    lua_setglobal(state, "Keyboard");
    SetIntegerConstants(state, kButtonActionConstants.data(), kButtonActionConstants.size());
    lua_setglobal(state, "ButtonAction");
    constexpr std::array<LuaIntegerConstant, 3> inputHooks{{
        {"IS_ACTION_PRESSED", 0}, {"IS_ACTION_TRIGGERED", 1}, {"GET_ACTION_VALUE", 2},
    }};
    SetIntegerConstants(state, inputHooks.data(), inputHooks.size());
    lua_setglobal(state, "InputHook");
}


int MarkPostUpdate(lua_State* state) {
    if (lua_gettop(state) != 0) {
        return luaL_error(state, "MarkPostUpdate accepts no arguments");
    }
    g_PostUpdateCount.fetch_add(1, std::memory_order_relaxed);
    return 0;
}

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 15
int MarkPostRender(lua_State* state) {
    if (lua_gettop(state) != 0) {
        return luaL_error(state, "MarkPostRender accepts no arguments");
    }
    g_PostRenderCount.fetch_add(1, std::memory_order_relaxed);
    return 0;
}

int MarkPostRenderPaused(lua_State* state) {
    if (lua_gettop(state) != 0) {
        return luaL_error(state, "MarkPostRenderPaused accepts no arguments");
    }
    g_PostRenderPausedCount.fetch_add(1, std::memory_order_relaxed);
    return 0;
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && (EXL_DIAGNOSTIC_STAGE == 16 || EXL_DIAGNOSTIC_STAGE == 17)
int MarkMusicDiagnosticCycleCompleted(lua_State* state) {
    if (lua_gettop(state) != 0) {
        return luaL_error(state, "MarkMusicDiagnosticCycleCompleted accepts no arguments");
    }
    g_MusicDiagnosticCycleCompleted.store(true, std::memory_order_release);
    return 0;
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 45
int MarkMusicDiagnosticCurrentId(lua_State* state) {
    if (lua_gettop(state) != 1 || !lua_isinteger(state, 1)) {
        return luaL_error(state, "MarkMusicDiagnosticCurrentId accepts one integer");
    }
    const lua_Integer value = lua_tointegerx(state, 1, nullptr);
    if (value < INT32_MIN || value > INT32_MAX) {
        return luaL_error(state, "Music ID is outside int32 range");
    }
    g_MusicDiagnosticCurrentId.store(static_cast<u32>(static_cast<s32>(value)),
                                     std::memory_order_release);
    g_MusicDiagnosticCurrentIdReady.store(true, std::memory_order_release);
    return 0;
}
#endif

int CreateGameHandle(lua_State* state) {
    if (lua_gettop(state) != 0) {
        return luaL_error(state, "Game accepts no arguments");
    }
    auto* game = static_cast<GameHandle*>(lua_newuserdata(state, sizeof(GameHandle)));
    game->reserved = 0;
    luaL_getmetatable(state, kGameMetatable);
    lua_setmetatable(state, -2);
    return 1;
}


bool ValidateMusicMethod(uintptr_t address, const u8* expected, std::size_t expectedLength);

bool ValidateItemPoolGetCollectibleMethod(uintptr_t address) {
    if (address == 0 || (address & 3) != 0) return false;
#if defined(__SWITCH__)
    constexpr std::size_t entrySize = kPreGetCollectibleRelayExpectedEntry.size();
    constexpr std::size_t totalSize = kPreGetCollectibleRelayExpectedOriginal.size();
    MemoryInfo info{};
    u32 pageInfo = 0;
    if (address > UINTPTR_MAX - totalSize ||
        R_FAILED(svcQueryMemory(&info, &pageInfo, address)) || info.size == 0 ||
        info.addr > UINTPTR_MAX - info.size) {
        return false;
    }
    const uintptr_t end = address + totalSize;
    if (!(address >= info.addr && end <= info.addr + info.size &&
          (info.type & MemState_Type) == MemType_ModuleCodeStatic && info.perm == Perm_Rx)) {
        return false;
    }
    // 形态一（旧 IPS 中继）：入口前 4 字节是补丁写入的分支，其余仍是被调用函数的原序言。
    if (std::memcmp(reinterpret_cast<const void*>(address), kPreGetCollectibleRelayExpectedEntry.data(),
                    entrySize) == 0 &&
        std::memcmp(reinterpret_cast<const void*>(address + entrySize),
                    kPreGetCollectibleRelayExpectedOriginal.data() + entrySize,
                    totalSize - entrySize) == 0) {
        return true;
    }
    // 形态二（零占洞入口中继）：入口 16 字节整体被改写成 `ldr x16, #8; br x16; .quad 槽地址`。
    // 两个目标都是小端，整字比较与逐字节比较等价；槽地址只做"非零且 8 字节对齐"的轻量核对
    // （它落在我们自己的模块里，这个函数看不到那边的边界）。
    if (std::memcmp(reinterpret_cast<const void*>(address), &kEntryRelayEntryStubWord0,
                    sizeof(kEntryRelayEntryStubWord0)) != 0 ||
        std::memcmp(reinterpret_cast<const void*>(address + sizeof(kEntryRelayEntryStubWord0)),
                    &kEntryRelayEntryStubWord1, sizeof(kEntryRelayEntryStubWord1)) != 0) {
        return false;
    }
    std::uintptr_t slot = 0;
    std::memcpy(&slot, reinterpret_cast<const void*>(address + 8), sizeof(slot));
    return slot != 0 && (slot & (alignof(std::uintptr_t) - 1)) == 0;
#else
    return true;
#endif
}



bool IsReadableMusicObject(uintptr_t address) {
#if defined(__SWITCH__)
    MemoryInfo info{};
    u32 pageInfo = 0;
    if (R_FAILED(svcQueryMemory(&info, &pageInfo, address)) || info.size == 0 ||
        info.addr > UINTPTR_MAX - info.size || address > UINTPTR_MAX - sizeof(uintptr_t)) {
        return false;
    }
    const uintptr_t end = address + sizeof(uintptr_t);
    return address >= info.addr && end <= info.addr + info.size &&
           (info.perm & Perm_R) != 0 && (info.perm & Perm_X) == 0;
#else
    return address != 0 && (address & (alignof(uintptr_t) - 1)) == 0;
#endif
}

bool ValidateMusicMethod(uintptr_t address, const u8* expected, std::size_t expectedLength) {
    if (address == 0 || expected == nullptr || expectedLength == 0 || (address & 3) != 0) {
        return false;
    }
#if defined(__SWITCH__)
    MemoryInfo info{};
    u32 pageInfo = 0;
    if (address > UINTPTR_MAX - expectedLength ||
        R_FAILED(svcQueryMemory(&info, &pageInfo, address)) || info.size == 0 ||
        info.addr > UINTPTR_MAX - info.size) {
        return false;
    }
    const uintptr_t end = address + expectedLength;
    return address >= info.addr && end <= info.addr + info.size &&
           (info.type & MemState_Type) == MemType_ModuleCodeStatic && info.perm == Perm_Rx &&
           std::memcmp(reinterpret_cast<const void*>(address), expected, expectedLength) == 0;
#else
    return true;
#endif
}

bool ResolveMusicForManager(uintptr_t manager, uintptr_t method, const u8* expected,
                            std::size_t expectedLength, uintptr_t* music) {
    if (manager == 0 || manager > UINTPTR_MAX - kManagerMusicOffset) {
        return false;
    }
    const uintptr_t candidate = manager + kManagerMusicOffset;
    if (!IsReadableMusicObject(candidate) || !ValidateMusicMethod(method, expected, expectedLength)) {
        return false;
    }
    *music = candidate;
    return true;
}

bool ResolveMusicForCall(uintptr_t method, const u8* expected, std::size_t expectedLength,
                         uintptr_t* music) {
    if (!g_InManagedCallbackDispatch.load(std::memory_order_acquire)) {
        return false;
    }
    return ResolveMusicForManager(g_CurrentCallbackManager.load(std::memory_order_acquire), method,
                                  expected, expectedLength, music);
}

int CreateMusicHandle(lua_State* state) {
    if (lua_gettop(state) != 0) {
        return luaL_error(state, "MusicManager accepts no arguments");
    }
    auto* music = static_cast<MusicHandle*>(lua_newuserdata(state, sizeof(MusicHandle)));
    music->reserved = 0;
    luaL_getmetatable(state, kMusicMetatable);
    lua_setmetatable(state, -2);
    return 1;
}


#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC push_options
#pragma GCC optimize ("Os")
#endif

int CreateRngHandle(lua_State* state) {
    if (lua_gettop(state) != 0) {
        return luaL_error(state, "RNG accepts no arguments");
    }
    auto* rng = static_cast<RngHandle*>(lua_newuserdata(state, sizeof(RngHandle)));
    *rng = {};
    luaL_getmetatable(state, kRngMetatable);
    lua_setmetatable(state, -2);
    return 1;
}


#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC pop_options
#endif

void RegisterGameApi(lua_State* state) {
    luaL_newmetatable(state, kGameMetatable);
    lua_newtable(state);
    // The `Game` family owns its binding table in
    // `interfaces/lua/game_api.cpp`; every owner registers from its own family
    // unit in layered builds.
    static_cast<void>(isaac::runtime::AttachGameMethods(state));
    lua_setfield(state, -2, "__index");
    lua_pop(state, 1);

    luaL_newmetatable(state, kRoomMetatable);
    lua_newtable(state);
    static_cast<void>(isaac::runtime::AttachRoomMethods(state));
    lua_setfield(state, -2, "__index");
    lua_pop(state, 1);

    luaL_newmetatable(state, kItemPoolMetatable);
    lua_newtable(state);
    static_cast<void>(isaac::runtime::AttachItemPoolMethods(state));
    lua_setfield(state, -2, "__index");
    lua_pop(state, 1);

    luaL_newmetatable(state, kLevelMetatable);
    lua_newtable(state);
    static_cast<void>(isaac::runtime::AttachLevelMethods(state));
    lua_setfield(state, -2, "__index");
    lua_pop(state, 1);

    luaL_newmetatable(state, kSeedsMetatable);
    lua_newtable(state);
    static_cast<void>(isaac::runtime::AttachSeedsMethods(state));
    lua_setfield(state, -2, "__index");
    lua_pop(state, 1);

    lua_pushcfunction(state, CreateGameHandle);
    lua_setglobal(state, "Game");
}

void RegisterMusicApi(lua_State* state) {
    luaL_newmetatable(state, kMusicMetatable);
    lua_newtable(state);
    static_cast<void>(isaac::runtime::AttachMusicMethods(state));
    lua_setfield(state, -2, "__index");
    lua_pop(state, 1);

    lua_pushcfunction(state, CreateMusicHandle);
    lua_setglobal(state, "MusicManager");
    lua_newtable(state);
    lua_pushinteger(state, 0);
    lua_setfield(state, -2, "MUSIC_NULL");
    lua_setglobal(state, "Music");
}

#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC push_options
#pragma GCC optimize ("Os")
#endif

void RegisterRngApi(lua_State* state) {
    luaL_newmetatable(state, kRngMetatable);
    lua_newtable(state);
    static_cast<void>(isaac::runtime::AttachRngMethods(state));
    lua_setfield(state, -2, "__index");
    lua_pop(state, 1);

    lua_pushcfunction(state, CreateRngHandle);
    lua_setglobal(state, "RNG");
}

// `KColor` is the colour value type the render-facing APIs take. Its metatable needs an
// `__index` function rather than a method table, because PC Mods read the components
// (`c.R`, `c.G`, ...) instead of calling accessors.
void RegisterColorApi(lua_State* state) {
    luaL_newmetatable(state, LuaRuntime::kColorMetatable);
    lua_pushcfunction(state, isaac::runtime::ColorIndex);
    lua_setfield(state, -2, "__index");
    // 真实 Mod 会写颜色字段：EID 的彩虹/闪烁/淡出效果就是 `color.Alpha = ...` 与
    // `color = func(color)`，所以 KColor 必须可写，否则那些效果会在赋值处报错。
    lua_pushcfunction(state, isaac::runtime::ColorNewIndex);
    lua_setfield(state, -2, "__newindex");
    lua_pop(state, 1);

    // 全局 `KColor` 是可调用的**类表**：`__call` 是构造器，`__index` 提供文档化的常量
    // （`KColor.White` 等）。与 `Vector` 同一形态，理由相同：常量必须挂在类上。
    lua_newtable(state);
    lua_newtable(state);
    lua_pushcfunction(state, isaac::runtime::CreateColorHandle);
    lua_setfield(state, -2, "__call");
    lua_pushcfunction(state, isaac::runtime::KColorClassIndex);
    lua_setfield(state, -2, "__index");
    lua_setmetatable(state, -2);
    lua_setglobal(state, "KColor");

    // 全局 `Color`（批次 3）：PC 的 `Color` 与 `KColor` 是**同一个类**（`KColor` 是别名之一），
    // 所以这里把上面那张类表**原样**再挂一个全局名 —— 两者恒等（`Color == KColor`），
    // 构造器（含七参 `Color(r,g,b,a,ro,go,bo)` 重载）与常量完全共用，不需要第二份实现。
    // EID 的 `EID:renderIcon`（`features/eid_api.lua:1369`）与 `EID:renderIndicator`
    // （`main.lua:960`）用的就是 `Color(...)`，而 `EID:getTextColor()` 那一路用的是 `KColor(...)`。
    lua_getglobal(state, "KColor");
    lua_setglobal(state, "Color");
}

// `Vector` = `KAGE::Math::Vector2`（8 字节 `{float X, float Y}` 值类型，无虚表）。比 `KColor`
// 多一层：它既要 userdata 元表（字段 + 方法 + 运算符），又要一个**可调用的类表**来承载
// `Vector.Zero`/`Vector.One`/`Vector.FromAngle`。方法表仍来自家族 TU 的 `AttachVectorMethods`。
// `Sprite` = `IsaacRepentance::ANM2`。与 `Font` 同一形态：元表带 `__gc`（我们分配的对象必须
// 由我们释放），方法表来自家族 TU。第一步只注册不收 `std::string` 的那一半。
//
// 批次 3 起 `__index` 是**函数**（`SpriteIndex`）：`Sprite` 多了三个可写属性
// （`Scale`/`Color`/`FlipX`，EID 在绘制路径上直接赋值），而 `__index` 必须是函数才能同时
// 服务字段与方法。方法表因此挪到元表的 `__methods` 上（与 `Vector` 同一形态）；
// `__newindex` 只处理那三个字段，其它字段名报错。
void RegisterSpriteApi(lua_State* state) {
    luaL_newmetatable(state, LuaRuntime::kSpriteMetatable);
    lua_pushcfunction(state, isaac::runtime::DestroySpriteHandle);
    lua_setfield(state, -2, "__gc");
    lua_pushcfunction(state, isaac::runtime::SpriteIndex);
    lua_setfield(state, -2, "__index");
    lua_pushcfunction(state, isaac::runtime::SpriteNewIndex);
    lua_setfield(state, -2, "__newindex");
    lua_newtable(state);
    static_cast<void>(isaac::runtime::AttachSpriteMethods(state));
    lua_setfield(state, -2, "__methods");
    lua_pop(state, 1);

    lua_pushcfunction(state, isaac::runtime::CreateSpriteHandle);
    lua_setglobal(state, "Sprite");
}

void RegisterVectorApi(lua_State* state) {
    luaL_newmetatable(state, LuaRuntime::kVectorMetatable);
    // `__index` 必须是函数：它同时服务 `v.X`/`v.Y` 字段与方法查找（方法表挂在元表的
    // `__methods` 上，见 `interfaces/lua/vector_api.cpp`）。
    lua_pushcfunction(state, isaac::runtime::VectorIndex);
    lua_setfield(state, -2, "__index");
    // `X`/`Y` 在 PC 里是可写变量，Mod 会直接赋值。
    lua_pushcfunction(state, isaac::runtime::VectorNewIndex);
    lua_setfield(state, -2, "__newindex");
    lua_pushcfunction(state, isaac::runtime::VectorAdd);
    lua_setfield(state, -2, "__add");
    lua_pushcfunction(state, isaac::runtime::VectorSub);
    lua_setfield(state, -2, "__sub");
    lua_pushcfunction(state, isaac::runtime::VectorMul);
    lua_setfield(state, -2, "__mul");
    lua_pushcfunction(state, isaac::runtime::VectorDiv);
    lua_setfield(state, -2, "__div");
    lua_pushcfunction(state, isaac::runtime::VectorUnaryMinus);
    lua_setfield(state, -2, "__unm");
    lua_pushcfunction(state, isaac::runtime::VectorEqual);
    lua_setfield(state, -2, "__eq");
    lua_pushcfunction(state, isaac::runtime::VectorToString);
    lua_setfield(state, -2, "__tostring");
    lua_newtable(state);
    static_cast<void>(isaac::runtime::AttachVectorMethods(state));
    lua_setfield(state, -2, "__methods");
    lua_pop(state, 1);

    lua_newtable(state);
    lua_newtable(state);
    lua_pushcfunction(state, isaac::runtime::CreateVectorHandle);
    lua_setfield(state, -2, "__call");
    lua_pushcfunction(state, isaac::runtime::VectorClassIndex);
    lua_setfield(state, -2, "__index");
    lua_setmetatable(state, -2);
    lua_setglobal(state, "Vector");
}

void RegisterFontApi(lua_State* state) {
    luaL_newmetatable(state, kFontMetatable);
    // Font is the first family that owns its native object, so its metatable needs a `__gc`:
    // it pairs `~Font()` (which only releases the Font's internal buffers) with the release of
    // the block this module allocated for it. The family unit owns both halves.
    lua_pushcfunction(state, isaac::runtime::DestroyFontHandle);
    lua_setfield(state, -2, "__gc");
    lua_newtable(state);
    static_cast<void>(isaac::runtime::AttachFontMethods(state));
    lua_setfield(state, -2, "__index");
    lua_pop(state, 1);

    lua_pushcfunction(state, isaac::runtime::CreateFontHandle);
    lua_setglobal(state, "Font");
}

#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC pop_options
#endif

// --- 最小 `debug` 库（批次 3）--------------------------------------------------
//
// 只有 `debug.getinfo`，因为它是 EID 用来**直接拿到自己真实 mod 路径**的那一条：
//
//   EID:GetCurrentModPath()（main.lua:147）
//     if debug then return string.sub(debug.getinfo(EID.GetCurrentModPath).source,2) .. "/../" end
//     -- 否则退回 require("") 的 "no file '...'" 错误文本 hack
//
// `source` 就是 Lua 自己的 chunk 名：模块脚本由 `Require` 用 `"@" + 路径` 作为 chunk 名加载
// （见 `Require` 里的 `chunkNameStorage`），所以 `source` 是 `@rom:/isaac_mods/mods/<mod>/main.lua`，
// 与 PC 上"文件 chunk"的形状完全一致（PC 也是 `@路径`）。`sub(source, 2)` 去掉那个 `@`。
//
// **为什么不直接开标准 `debug`**：标准库的 `debug.sethook`/`debug.setlocal`/`debug.upvaluejoin`
// 之类能任意改写运行时状态，而 Mod 是在游戏进程里跑的；本 Runtime 一直只开
// base/coroutine/table/string/math/utf8（见 `OpenSafeLibraries`），所以这里只提供读取函数位置
// 所需的那一个入口（`getinfo` 本身是只读的）。
//
// 兼容性提醒：**存在 `debug` 这个全局会让 EID 走上面那条分支**（它只判 `if debug then`），
// 于是 `EID.modPath` 变成 `<mod 根>/main.lua/../`（而不是 require hack 算出来的
// `<mod 根>/`）。两条路径都交给引擎的文件层去解析，最终哪一条能被引擎接受要等真机验证。
int DebugGetInfo(lua_State* state) {
    const int argumentCount = lua_gettop(state);
    if (argumentCount < 1 || argumentCount > 2) {
        return luaL_error(state, "debug.getinfo accepts a function or a stack level");
    }
    lua_Debug information{};
    const bool byFunction = lua_isfunction(state, 1);
    if (byFunction) {
        // `>` 前缀让 `lua_getinfo` 从栈顶取函数（并且弹出它）。
        lua_pushvalue(state, 1);
        if (lua_getinfo(state, ">Su", &information) == 0) {
            lua_pushnil(state);
            return 1;
        }
    } else {
        if (!lua_isinteger(state, 1)) {
            // PC/Lua 的默认形态是 `debug.getinfo(level)`；缺失或 nil 视作第 1 层（调用者）。
            if (!lua_isnoneornil(state, 1)) {
                return luaL_error(state, "debug.getinfo expects a function or a stack level");
            }
        }
        const lua_Integer level =
            lua_isinteger(state, 1) ? lua_tointegerx(state, 1, nullptr) : 1;
        if (level < 0 || !lua_getstack(state, static_cast<int>(level), &information)) {
            lua_pushnil(state);  // 层级越界：Lua 的 `getinfo` 同样返回 nil
            return 1;
        }
        if (lua_getinfo(state, "Slu", &information) == 0) {
            lua_pushnil(state);
            return 1;
        }
    }
    lua_newtable(state);
    lua_pushstring(state, information.source != nullptr ? information.source : "=?");
    lua_setfield(state, -2, "source");
    lua_pushstring(state, information.short_src);
    lua_setfield(state, -2, "short_src");
    lua_pushstring(state, information.what);
    lua_setfield(state, -2, "what");
    lua_pushinteger(state, information.linedefined);
    lua_setfield(state, -2, "linedefined");
    lua_pushinteger(state, information.lastlinedefined);
    lua_setfield(state, -2, "lastlinedefined");
    lua_pushinteger(state, information.currentline);
    lua_setfield(state, -2, "currentline");
    lua_pushinteger(state, information.nups);
    lua_setfield(state, -2, "nups");
    if (!byFunction) {
        lua_pushstring(state, information.name != nullptr ? information.name : "");
        lua_setfield(state, -2, "name");
        lua_pushstring(state, information.namewhat);
        lua_setfield(state, -2, "namewhat");
    }
    return 1;
}

// 把最小 `debug` 表挂成全局。由 `PrepareRuntime` 在 Mod 脚本运行前调用一次。
void RegisterDebugLibrary(lua_State* state) {
    lua_newtable(state);
    lua_pushcfunction(state, DebugGetInfo);
    lua_setfield(state, -2, "getinfo");
    lua_setglobal(state, "debug");
}

// `os` 与 `debug` 一起注册：PC 的 Lua 环境两样都有，Mod 会无条件用（见 `RegisterOsLibrary`）。

// --- 最小 `os` 库（PC Mod 会用 `os.date` / `os.time` / `os.clock`）------------------
//
// 为什么必须补（真机报告 `01789203211`）：EID 的 `features/eid_tmtrainer.lua:199` 有一个
// 愚人节彩蛋 `if (debug and os.date("%m/%d") == "04/01") then … end`。我们提供了 `debug`
// （真值），却没有 `os`，于是 `os.date` 在索引 nil 时直接抛错 —— 整包加载死在 200 行，
// 而 EID 已经登记过回调（诊断字的种类掩码非零）、堆也用掉了 18 MiB，说明它离加载成功只差这一点。
// PC 上 `os` 与 `debug` 都在，所以这段代码在 PC 上从不出错：这是"我们的库比 PC 少"的缺口。
//
// 只实现有据可依、且无害的子集：`clock`/`date`/`difftime`/`getenv`/`setlocale`/`time`。
// **刻意不提供** `remove`/`rename`/`tmpname`/`exit`：前三个是文件系统写操作（本项目的写通道
// 尚未打通，给了只会骗调用方），`exit` 会直接把玩家的游戏结束掉。
int OsClock(lua_State* state) {
    lua_pushnumber(state, static_cast<lua_Number>(std::clock()) / CLOCKS_PER_SEC);
    return 1;
}

// `*t` 形态（`os.date("*t")`）要返回字段表；字段名与顺序照 Lua 5.3 的手册。
int PushDateTable(lua_State* state, const std::tm& value) {
    lua_createtable(state, 0, 9);
    lua_pushinteger(state, value.tm_year + 1900);
    lua_setfield(state, -2, "year");
    lua_pushinteger(state, value.tm_mon + 1);
    lua_setfield(state, -2, "month");
    lua_pushinteger(state, value.tm_mday);
    lua_setfield(state, -2, "day");
    lua_pushinteger(state, value.tm_hour);
    lua_setfield(state, -2, "hour");
    lua_pushinteger(state, value.tm_min);
    lua_setfield(state, -2, "min");
    lua_pushinteger(state, value.tm_sec);
    lua_setfield(state, -2, "sec");
    lua_pushinteger(state, value.tm_wday + 1);
    lua_setfield(state, -2, "wday");
    lua_pushinteger(state, value.tm_yday + 1);
    lua_setfield(state, -2, "yday");
    lua_pushboolean(state, value.tm_isdst > 0);
    lua_setfield(state, -2, "isdst");
    return 1;
}

int OsDate(lua_State* state) {
    const char* format = luaL_optstring(state, 1, "%c");
    const std::time_t when = static_cast<std::time_t>(
        luaL_optinteger(state, 2, static_cast<lua_Integer>(std::time(nullptr))));
    bool utc = false;
    if (format[0] == '!') {
        utc = true;
        ++format;
    }
    std::tm broken{};
    const bool ok = utc ? gmtime_r(&when, &broken) != nullptr : localtime_r(&when, &broken) != nullptr;
    if (!ok) {
        // 设备时钟读不出来时也不能报错：Mod 只是拿不到日期，不该因此整包加载失败。
        lua_pushliteral(state, "");
        return 1;
    }
    if (std::strcmp(format, "*t") == 0) {
        return PushDateTable(state, broken);
    }
    std::array<char, 256> buffer{};
    const std::size_t written = std::strftime(buffer.data(), buffer.size(), format, &broken);
    lua_pushlstring(state, buffer.data(), written);
    return 1;
}

int OsTime(lua_State* state) {
    if (lua_istable(state, 1)) {
        std::tm broken{};
        broken.tm_year = static_cast<int>(luaL_checkinteger(state, 1)) - 1900;
        const char* names[6] = {"month", "day", "hour", "min", "sec", nullptr};
        int* fields[6] = {&broken.tm_mon, &broken.tm_mday, &broken.tm_hour, &broken.tm_min,
                          &broken.tm_sec, nullptr};
        // 表里只给 year 也要能算（Lua 5.3 手册允许省略字段，缺的按 1 月 0 时 0 分 0 秒算）。
        lua_getfield(state, 1, "year");
        if (!lua_isnumber(state, -1)) {
            lua_pop(state, 1);
            return luaL_error(state, "field 'year' missing in date table");
        }
        broken.tm_year = static_cast<int>(lua_tointeger(state, -1)) - 1900;
        lua_pop(state, 1);
        for (std::size_t index = 0; names[index] != nullptr; ++index) {
            lua_getfield(state, 1, names[index]);
            *fields[index] = lua_isnumber(state, -1) ? static_cast<int>(lua_tointeger(state, -1)) : 0;
            lua_pop(state, 1);
        }
        broken.tm_mon -= 1;  // Lua 的月份从 1 开始，`std::tm` 从 0
        lua_pushinteger(state, static_cast<lua_Integer>(std::mktime(&broken)));
        return 1;
    }
    lua_pushinteger(state, static_cast<lua_Integer>(std::time(nullptr)));
    return 1;
}

int OsDifftime(lua_State* state) {
    const std::time_t later = static_cast<std::time_t>(luaL_checkinteger(state, 1));
    const std::time_t earlier = static_cast<std::time_t>(luaL_checkinteger(state, 2));
    lua_pushnumber(state, static_cast<lua_Number>(std::difftime(later, earlier)));
    return 1;
}

int OsGetenv(lua_State* state) {
    // Switch 上没有进程环境变量。返回 nil 是安全降级（Mod 拿到 nil 会走自己的默认分支）。
    luaL_checkstring(state, 1);
    lua_pushnil(state);
    return 1;
}

int OsSetlocale(lua_State* state) {
    // 引擎不做 locale 切换：只认 "C"，并把当前值报回去（与 PC 的调用形状一致）。
    luaL_optstring(state, 1, "C");
    lua_pushliteral(state, "C");
    return 1;
}

// 把最小 `os` 表挂成全局。由 `PrepareRuntime` 在 Mod 脚本运行前调用一次。
void RegisterOsLibrary(lua_State* state) {
    lua_newtable(state);
    lua_pushcfunction(state, OsClock);
    lua_setfield(state, -2, "clock");
    lua_pushcfunction(state, OsDate);
    lua_setfield(state, -2, "date");
    lua_pushcfunction(state, OsDifftime);
    lua_setfield(state, -2, "difftime");
    lua_pushcfunction(state, OsGetenv);
    lua_setfield(state, -2, "getenv");
    lua_pushcfunction(state, OsSetlocale);
    lua_setfield(state, -2, "setlocale");
    lua_pushcfunction(state, OsTime);
    lua_setfield(state, -2, "time");
    lua_setglobal(state, "os");
}

void RegisterModApi(lua_State* state) {
    g_PreparationStep.store(8, std::memory_order_relaxed);
    luaL_newmetatable(state, kModMetatable);
    lua_newtable(state);
    // Catalog-driven registration: adding an API means adding a descriptor and
    // one binding row, not another push/setfield pair. The `Mod` owner now
    // registers from its own translation unit (`interfaces/lua/mod_api.cpp`).
    // Reachability of cross-TU registration is covered by the symbol gate; see
    // the problem log for the two experiments that cleared the old suspicion.
    static_cast<void>(isaac::runtime::AttachModMethods(state));
    lua_setfield(state, -2, kModMethodsField);
    lua_pushcfunction(state, ModIndex);
    lua_setfield(state, -2, "__index");
    lua_pushcfunction(state, ModNewIndex);
    lua_setfield(state, -2, "__newindex");
    lua_pop(state, 1);

    lua_pushcfunction(state, RegisterMod);
    lua_setglobal(state, "RegisterMod");

    RegisterGameApi(state);
    RegisterMusicApi(state);
    RegisterRngApi(state);
    RegisterFontApi(state);
    RegisterColorApi(state);
    RegisterVectorApi(state);
    RegisterSpriteApi(state);
    RegisterNeutralInputApi(state);
    RegisterPcLuaEnumTables(state);
    // `Isaac` 门面与 `Options` 表必须早于 Mod 脚本存在：EID 加载的第一句就是
    // `Isaac.GetItemConfig()`，随后立刻读 `Options.HUDOffset`/`Options.Language`。
    // 两族都由 `interfaces/lua/isaac_api.cpp` 自己建立表并挂成全局（见该文件的注释：
    // 这一轮只保证"存在且被调用不报错"，引擎数据要等偏移定位后再接）。
    static_cast<void>(isaac::runtime::RegisterIsaacApi(state));
    static_cast<void>(isaac::runtime::RegisterOptionsTable(state));

    // `ModCallbacks` 表：生产构建直接用生成的 PC 表（`RegisterPcLuaEnumTables` 已把它
    // 注册成全局），因为 PC Mod 会在加载阶段注册十几种回调，缺一个名字就整包失败。
    // stage13 诊断要的是"只有 POST_UPDATE"的最小表，所以那条路径覆盖回去。
    // `g_Stage13Mode` 本身只在“非诊断构建或 stage13 构建”里声明（见文件上方它自己的
    // 守卫），所以这里必须用同一个条件包起来，否则 stage14+ 的宿主 harness 编译不过。
#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 13
    if (g_Stage13Mode) {
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 13
        lua_newtable(state);
        lua_pushinteger(state, kStage13PostUpdateCallback);
        lua_setfield(state, -2, "MC_POST_UPDATE");
        lua_setglobal(state, "ModCallbacks");
#endif
    }
#endif

    // 版本判定全局：PC 引擎在忏悔版里定义 `REPENTANCE`，Mod 用它选代码分支
    // （EID 就是靠它决定走 Repentance 还是 AB+ 路径）。Switch 1.7.9b 就是忏悔版。
    // `REPENTANCE_PLUS` 刻意不定义：本体是 PC 专属更新，Mod 会按"非 Plus"处理。
    lua_pushboolean(state, 1);
    lua_setglobal(state, "REPENTANCE");

    // `json` 模块：EID 在加载阶段无条件 `require("json")`（PC 引擎自带这个模块）。
    isaac::runtime::RegisterJsonModule(state);

    // 最小 `debug` 库：EID 的 `EID:GetCurrentModPath()` 用 `debug.getinfo(fn).source` 直接
    // 取真实 mod 路径（见 `DebugGetInfo` 的注释）。必须在 Mod 脚本运行前挂好 —— EID 在
    // `main.lua:148` 的加载期就会读它。
    RegisterDebugLibrary(state);
    RegisterOsLibrary(state);

    lua_newtable(state);
    lua_pushcfunction(state, MarkPostUpdate);
    lua_setfield(state, -2, "MarkPostUpdate");
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 15
    lua_pushcfunction(state, MarkPostRender);
    lua_setfield(state, -2, "MarkPostRender");
    lua_pushcfunction(state, MarkPostRenderPaused);
    lua_setfield(state, -2, "MarkPostRenderPaused");
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && (EXL_DIAGNOSTIC_STAGE == 16 || EXL_DIAGNOSTIC_STAGE == 17)
    lua_pushcfunction(state, MarkMusicDiagnosticCycleCompleted);
    lua_setfield(state, -2, "MarkMusicDiagnosticCycleCompleted");
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 45
    lua_pushcfunction(state, MarkMusicDiagnosticCurrentId);
    lua_setfield(state, -2, "MarkMusicDiagnosticCurrentId");
#endif
    lua_setglobal(state, "RuntimeTest");
}

void OpenSafeLibraries(lua_State* state) {
    OpenLibrary(state, 1, "_G", luaopen_base);
    OpenLibrary(state, 2, LUA_COLIBNAME, luaopen_coroutine);
    OpenLibrary(state, 3, LUA_TABLIBNAME, luaopen_table);
    OpenLibrary(state, 4, LUA_STRLIBNAME, luaopen_string);
    OpenLibrary(state, 5, LUA_MATHLIBNAME, luaopen_math);
    OpenLibrary(state, 6, LUA_UTF8LIBNAME, luaopen_utf8);
}

int PrepareRuntime(lua_State* state) {
    OpenSafeLibraries(state);
#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 13
    if (g_Stage13Mode) {
        g_PreparationStep.store(7, std::memory_order_relaxed);
        RegisterStage13Require(state);
    }
#endif
    RegisterModApi(state);
    return 0;
}

LuaInitResult ResetAfterFailure(lua_State* state, LuaInitResult result) {
    lua_close(state);
    g_LuaState = nullptr;
    g_CallbackRegistry.Reset();
    g_ModRegistered = false;
    g_InManagedCallbackDispatch.store(false, std::memory_order_release);
#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 13
    ResetStage13Context();
#endif
    return result;
}

LuaInitResult InitializeScript(const char* script, std::size_t length, const char* chunkName) {
    if (script == nullptr || length == 0 || chunkName == nullptr) {
        return LuaInitResult::ScriptLoadFailed;
    }
    g_PreparationStep.store(0, std::memory_order_relaxed);
    g_PreparationStatus.store(0, std::memory_order_relaxed);
    g_PostUpdateCount.store(0, std::memory_order_release);
    g_CallbackError.store(false, std::memory_order_release);
#if defined(EXL_PERSISTENCE_TRACE) || defined(EXL_PERSISTENCE_EVENT_DIAGNOSTIC)
        g_CallbackOperation.store(static_cast<u8>(CallbackOperation::None), std::memory_order_release);
    g_CallbackFailureDetail.store(0, std::memory_order_release);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 15
    g_PostRenderCount.store(0, std::memory_order_release);
    g_PostRenderPausedCount.store(0, std::memory_order_release);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && (EXL_DIAGNOSTIC_STAGE == 16 || EXL_DIAGNOSTIC_STAGE == 17)
    g_MusicDiagnosticCycleCompleted.store(false, std::memory_order_release);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 45
    g_MusicDiagnosticCurrentIdReady.store(false, std::memory_order_release);
    g_MusicDiagnosticCurrentId.store(0, std::memory_order_release);
#endif
    lua_State* state = luaL_newstate();
    if (state == nullptr) {
        return LuaInitResult::StateCreateFailed;
    }
    lua_pushcfunction(state, PrepareRuntime);
    const int preparationStatus = lua_pcallk(state, 0, 0, 0, 0, nullptr);
    if (preparationStatus != LUA_OK) {
        g_PreparationStatus.store(static_cast<u32>(preparationStatus), std::memory_order_release);
        CaptureLuaErrorTop(state);
        lua_pop(state, 1);
        return ResetAfterFailure(state, preparationStatus == LUA_ERRMEM
            ? LuaInitResult::RuntimePreparationMemoryFailed
            : LuaInitResult::RuntimePreparationFailed);
    }
    if (luaL_loadbufferx(state, script, length, chunkName, "t") != LUA_OK) {
        CaptureLuaErrorTop(state);
        lua_pop(state, 1);
        return ResetAfterFailure(state, LuaInitResult::ScriptLoadFailed);
    }
    if (lua_pcallk(state, 0, 0, 0, 0, nullptr) != LUA_OK) {
        CaptureLuaErrorTop(state);
        lua_pop(state, 1);
        return ResetAfterFailure(state, LuaInitResult::ScriptRunFailed);
    }
    if (g_CallbackRegistry.Count() == 0) {
        return ResetAfterFailure(state, LuaInitResult::MissingPostUpdateCallback);
    }
    g_LuaState = state;
    g_Ready.store(true, std::memory_order_release);
    return LuaInitResult::Success;
}

} // namespace
// 把**任意** Lua 值记成可读文本（2026-09-12，见头文件里的长注释）。
//
// 与下面 `CaptureLuaErrorTop` 的区别：那个只在"栈顶是字符串"时成功，而这个**保证**产出文本
// ——`luaL_tolstring` 接受任意类型（表/函数/userdata 会走 `__tostring` 或类型名），并且它
// 自己把结果压栈、由我们弹掉。为什么需要它：真机的 `errorLength` 与内容长期不一致
// （`= 1` 却读出上一条消息的残渣），根因就是错误值不是字符串时旧通道直接放弃记录。
void RecordLuaErrorValue(lua_State* state) noexcept {
    if (state == nullptr || lua_gettop(state) == 0) {
        return;
    }
    if (lua_type(state, -1) == LUA_TSTRING) {
        CaptureLuaErrorTop(state);
        return;
    }
    // `luaL_tolstring` 把任意值转成字符串并压栈；失败（内存不足等）时返回 nullptr。
    const char* text = luaL_tolstring(state, -1, nullptr);
    if (text != nullptr) {
        const std::size_t length = std::strlen(text);
        if (length != 0) {
            RecordLuaErrorText(text, length);
        }
    }
    lua_pop(state, 1);  // 弹掉 `luaL_tolstring` 压入的字符串
}

// `luaL_error` 的替代实现：**先记录整段消息，再真正抛错**。
//
// 为什么要经过它：`luaL_error` 生成的消息就是最终文本（`luaL_where` 的 `file:line:` 前缀
// + 格式化后的消息），在抛错**之前**把它记下来，报告侧读到的就是消息本体；而旧通道依赖
// 事后从栈上 `lua_tostring`，对非字符串错误值会静默失败。
int ReportAndRecordLuaError(lua_State* state, const char* format, ...) {
    char message[256] = {};
    va_list args;
    va_start(args, format);
    const int written = std::vsnprintf(message, sizeof(message), format, args);
    va_end(args);
    const std::size_t messageLength =
        written > 0 ? (static_cast<std::size_t>(written) < sizeof(message)
                           ? static_cast<std::size_t>(written)
                           : sizeof(message) - 1)
                    : 0;
    // ★★ **必须把 `luaL_where` 的位置前缀也记上**（2026-09-12 第九稿，这是"以前能拿到完整
    // 报错、现在拿不到"的真正原因）。
    //
    // 最早那几轮报告里，错误文本是 `eid_api.lua:2606: attempt to call a nil value (method
    // 'GetPill')` —— **带 `file:line:` 前缀**。那是因为那时的捕获发生在 `luaL_error`
    // **已经构造好完整文本之后**（从栈上取）。而本函数改成"抛错前记录"时，只记了
    // `message`（也就是格式化后的消息本体），**前缀被漏掉了**；遇到本项目的调用点
    // （消息都很简短）时，记下来的就只剩几个字节。
    //
    // `luaL_error` 内部就是这么拼的：`luaL_where(L, 1)` + 消息。这里照抄同样的形状，
    // 记录的内容才与引擎真正抛出的文本一致。
    char full[320] = {};
    luaL_where(state, 1);
    const char* where = lua_tostring(state, -1);
    const std::size_t whereLength = where != nullptr ? std::strlen(where) : 0;
    const std::size_t copyWhere = whereLength < sizeof(full) ? whereLength : sizeof(full) - 1;
    if (copyWhere != 0) {
        std::memcpy(full, where, copyWhere);
    }
    lua_pop(state, 1);  // 弹掉 `luaL_where` 压入的字符串
    const std::size_t remaining = sizeof(full) - 1 - copyWhere;
    const std::size_t copyMessage = messageLength < remaining ? messageLength : remaining;
    if (copyMessage != 0) {
        std::memcpy(full + copyWhere, message, copyMessage);
    }
    if (copyWhere + copyMessage != 0) {
        RecordLuaErrorText(full, copyWhere + copyMessage);
    }
    // 再交给真正的 `luaL_error`（它会自己再加一次位置前缀并 `lua_error` 长跳）。
    // 这里**不能**用宏（否则递归），所以要 `#undef` 后再调。
#pragma push_macro("luaL_error")
#undef luaL_error
    return luaL_error(state, "%s", message);
#pragma pop_macro("luaL_error")
}


void RecordCallbackOperation(CallbackOperation operation) noexcept {
#if defined(EXL_PERSISTENCE_TRACE) || defined(EXL_PERSISTENCE_EVENT_DIAGNOSTIC)
    if (g_InManagedCallbackDispatch.load(std::memory_order_acquire)) {
        g_CallbackOperation.store(static_cast<u8>(operation), std::memory_order_release);
    }
#else
    static_cast<void>(operation);
#endif
}

bool CallbackErrorPending() noexcept {
    return g_CallbackError.load(std::memory_order_acquire);
}

std::uint64_t LastLuaErrorHead(std::uint32_t* length) noexcept {
    const std::uint32_t stored = g_LastLuaErrorLength.load(std::memory_order_acquire);
    if (length != nullptr) {
        *length = stored;
    }
    std::uint64_t head = 0;
    const std::size_t copyLength = stored < 8 ? stored : 8;
    std::memcpy(&head, g_LastLuaErrorText.data(), copyLength);
    return head;
}

// 把最近一次捕获的 Lua 错误文本**整段**拷给调用方（诊断用，探针要的就是它）。
//
// 为什么需要它：真机报告 `01789201566` 只说"main.lua 运行期出错"（诊断字 `[11] = 0x515`
// = `LuaInit` + detail 5 = `ScriptRunFailed`）并给出了错误信息的**前 8 字节**
// （`"..._mods"`——Lua 5.3.3 的 `luaO_chunkid` 把 66 字符的入口路径截成 "...+末 57 字符"，
// 于是信息头正好落在这个位置），但真正要的是**行号 + 消息**，那在后面的 94 个字节里。
// 只带 8 字节等于又白跑一轮，所以补上整段文本的读取口。
// 模块的假堆已经用了多少字节（诊断用）。
//
// `EXL_USE_FAKEHEAP` 让 newlib 的 `fake_heap_start/end` 指向 `init.cpp` 里那块 .bss 数组
// （大小 = `exl::setting::HeapSize`）—— **整个模块的 malloc 只有这一个来源**，Lua 状态也在里面。
// `sbrk(0)` 给出当前堆顶，减掉数组起点就是"已经向堆要过多少"（堆只增不减，所以它同时是峰值）。
//
// 为什么要读它：真机报告 `01789202408` 里 Lua 抛 `LUA_ERRMEM`（"not enough memory"），
// 而当时堆只有 2 MiB。把用量报出来，才能判断"32 MiB 够不够"，而不是靠猜。
std::size_t HeapUsedBytes() noexcept {
#if defined(__SWITCH__) && defined(EXL_USE_FAKEHEAP)
    const auto top = reinterpret_cast<std::uintptr_t>(sbrk(0));
    const auto base = reinterpret_cast<std::uintptr_t>(__fake_heap);
    return top > base ? static_cast<std::size_t>(top - base) : 0;
#else
    return 0;
#endif
}

void RequireFailureSnapshot(RequireFailureView* view) noexcept {
    if (view == nullptr) {
        return;
    }
    view->firstCode = g_RequireFirstFailureCode.load(std::memory_order_acquire);
    view->firstNameHead = g_RequireFirstFailureName.load(std::memory_order_acquire);
    view->lastCode = g_RequireLastFailureCode.load(std::memory_order_acquire);
    view->lastNameHead = g_RequireLastFailureName.load(std::memory_order_acquire);
    view->total = g_RequireFailureTotal.load(std::memory_order_acquire);
}

std::size_t CopyLastLuaErrorText(char* output, std::size_t capacity) noexcept {
    if (output == nullptr || capacity == 0) {
        return 0;
    }
    const std::uint32_t stored = g_LastLuaErrorLength.load(std::memory_order_acquire);
    std::size_t copyLength = stored;
    if (copyLength > g_LastLuaErrorText.size()) {
        copyLength = g_LastLuaErrorText.size();
    }
    if (copyLength > capacity) {
        copyLength = capacity;
    }
    // ★ **先把缓冲区清零再拷**（2026-09-12，真机报告 `01789225234`：`errorLength = 1`，
    // 但报告里读到的 16 字节却是**上一次**残留的 `file name string` —— 因为调用方只拷
    // `copyLength` 字节，剩下的位置留着旧消息，而报告侧的"错误文本尾部"会把它们当成消息）。
    // 清零之后，凡是超出 `errorLength` 的位置都是 0，报告侧一眼就能看出"这段没有内容"。
    std::memset(output, 0, capacity);
    if (copyLength != 0) {
        std::memcpy(output, g_LastLuaErrorText.data(), copyLength);
    }
    return copyLength;
}

void RecordLuaErrorText(const char* text, std::size_t length) noexcept {
    if (text == nullptr || length == 0) {
        return;
    }
    if (length > g_LastLuaErrorText.size()) {
        length = g_LastLuaErrorText.size();
    }
    std::memcpy(g_LastLuaErrorText.data(), text, length);
    g_LastLuaErrorLength.store(static_cast<std::uint32_t>(length), std::memory_order_release);
}

bool InManagedCallbackScope() noexcept {
    return g_InManagedCallbackDispatch.load(std::memory_order_acquire);
}

std::uintptr_t GameOwnerSlot() noexcept {
    return g_GameOwnerSlot.load(std::memory_order_acquire);
}

std::uintptr_t EngineModuleBase() noexcept {
    return g_EngineModuleBase.load(std::memory_order_acquire);
}

std::uintptr_t GameIsPausedThunk() noexcept {
    return g_GameIsPausedThunk.load(std::memory_order_acquire);
}

std::uintptr_t GameIsGreedModeThunk() noexcept {
    return g_GameIsGreedMode.load(std::memory_order_acquire);
}

std::uintptr_t LevelIsAscentThunk() noexcept {
    return g_LevelIsAscent.load(std::memory_order_acquire);
}

std::uintptr_t ItemPoolGetCollectibleThunk() noexcept {
    return g_ItemPoolGetCollectible.load(std::memory_order_acquire);
}

std::uintptr_t MusicGetCurrentMusicIdThunk() noexcept {
    return g_MusicGetCurrentMusicId.load(std::memory_order_acquire);
}

std::uintptr_t MusicPauseThunk() noexcept {
    return g_MusicPause.load(std::memory_order_acquire);
}

std::uintptr_t MusicResumeThunk() noexcept {
    return g_MusicResume.load(std::memory_order_acquire);
}

std::uintptr_t RngSetSeedThunk() noexcept {
    return g_RngSetSeed.load(std::memory_order_acquire);
}

std::uintptr_t InputIsActionPressedThunk() noexcept {
    return g_InputIsActionPressed.load(std::memory_order_acquire);
}

std::uintptr_t InputIsActionTriggeredThunk() noexcept {
    return g_InputIsActionTriggered.load(std::memory_order_acquire);
}

std::uintptr_t InputGetActionValueThunk() noexcept {
    return g_InputGetActionValue.load(std::memory_order_acquire);
}

std::uintptr_t RngNextThunk() noexcept {
    return g_RngNext.load(std::memory_order_acquire);
}

std::uintptr_t FontCtorThunk() noexcept {
    return g_FontCtor.load(std::memory_order_acquire);
}

std::uintptr_t FontDestructorThunk() noexcept {
    return g_FontDestructor.load(std::memory_order_acquire);
}

std::uintptr_t FontLoadThunk() noexcept {
    return g_FontLoad.load(std::memory_order_acquire);
}

std::uintptr_t FontUnloadThunk() noexcept {
    return g_FontUnload.load(std::memory_order_acquire);
}

std::uintptr_t FontIsLoadedThunk() noexcept {
    return g_FontIsLoaded.load(std::memory_order_acquire);
}

std::uintptr_t FontGetStringWidthThunk() noexcept {
    return g_FontGetStringWidth.load(std::memory_order_acquire);
}

std::uintptr_t FontGetStringWidthUTF8Thunk() noexcept {
    return g_FontGetStringWidthUTF8.load(std::memory_order_acquire);
}

std::uintptr_t FontGetLineHeightThunk() noexcept {
    return g_FontGetLineHeight.load(std::memory_order_acquire);
}

std::uintptr_t FontGetBaselineHeightThunk() noexcept {
    return g_FontGetBaselineHeight.load(std::memory_order_acquire);
}

std::uintptr_t FontGetCharacterWidthThunk() noexcept {
    return g_FontGetCharacterWidth.load(std::memory_order_acquire);
}

std::uintptr_t FontSetMissingCharacterThunk() noexcept {
    return g_FontSetMissingCharacter.load(std::memory_order_acquire);
}

std::uintptr_t FontDrawStringScaledThunk() noexcept {
    return g_FontDrawStringScaled.load(std::memory_order_acquire);
}

std::uintptr_t FontDrawStringUTF8Thunk() noexcept {
    return g_FontDrawStringUTF8.load(std::memory_order_acquire);
}

std::uintptr_t FontDrawStringScaledUTF8Thunk() noexcept {
    return g_FontDrawStringScaledUTF8.load(std::memory_order_acquire);
}

std::uintptr_t FontDrawStringThunk() noexcept {
    return g_FontDrawString.load(std::memory_order_acquire);
}

std::uintptr_t SpriteCtorThunk() noexcept {
    return g_SpriteCtor.load(std::memory_order_acquire);
}
std::uintptr_t SpriteDestructorThunk() noexcept {
    return g_SpriteDestructor.load(std::memory_order_acquire);
}
std::uintptr_t SpritePlayThunk() noexcept {
    return g_SpritePlay.load(std::memory_order_acquire);
}
std::uintptr_t SpriteSetAnimationThunk() noexcept {
    return g_SpriteSetAnimation.load(std::memory_order_acquire);
}
std::uintptr_t SpriteSetFrameNamedThunk() noexcept {
    return g_SpriteSetFrameNamed.load(std::memory_order_acquire);
}
std::uintptr_t SpriteSetFrameThunk() noexcept {
    return g_SpriteSetFrame.load(std::memory_order_acquire);
}
std::uintptr_t SpriteGetFrameThunk() noexcept {
    return g_SpriteGetFrame.load(std::memory_order_acquire);
}
std::uintptr_t SpriteSetLayerFrameThunk() noexcept {
    return g_SpriteSetLayerFrame.load(std::memory_order_acquire);
}
std::uintptr_t SpriteGetLayerFrameThunk() noexcept {
    return g_SpriteGetLayerFrame.load(std::memory_order_acquire);
}
std::uintptr_t SpriteGetTexelThunk() noexcept {
    return g_SpriteGetTexel.load(std::memory_order_acquire);
}
std::uintptr_t SpriteUpdateThunk() noexcept {
    return g_SpriteUpdate.load(std::memory_order_acquire);
}
std::uintptr_t SpriteIsPlayingThunk() noexcept {
    return g_SpriteIsPlaying.load(std::memory_order_acquire);
}
std::uintptr_t SpriteIsFinishedThunk() noexcept {
    return g_SpriteIsFinished.load(std::memory_order_acquire);
}
std::uintptr_t SpriteRenderThunk() noexcept {
    return g_SpriteRender.load(std::memory_order_acquire);
}
std::uintptr_t SpriteRenderLayerThunk() noexcept {
    return g_SpriteRenderLayer.load(std::memory_order_acquire);
}
std::uintptr_t SpritePlayRandomThunk() noexcept {
    return g_SpritePlayRandom.load(std::memory_order_acquire);
}

std::uintptr_t SpriteLoadThunk() noexcept {
    return g_SpriteLoad.load(std::memory_order_acquire);
}

std::uintptr_t SpriteLoadGraphicsThunk() noexcept {
    return g_SpriteLoadGraphics.load(std::memory_order_acquire);
}

std::uintptr_t SpriteReplaceSpritesheetThunk() noexcept {
    return g_SpriteReplaceSpritesheet.load(std::memory_order_acquire);
}

std::uintptr_t LibcxxStringAssignThunk() noexcept {
    return g_LibcxxStringAssign.load(std::memory_order_acquire);
}

std::uintptr_t GameOperatorDeleteThunk() noexcept {
    return g_GameOperatorDelete.load(std::memory_order_acquire);
}

bool ValidateItemPoolGetCollectibleBinding(std::uintptr_t address) noexcept {
    return ValidateItemPoolGetCollectibleMethod(address);
}

bool ResolveMusicGetCurrentMusicId(std::uintptr_t method, std::uintptr_t* music) noexcept {
    return ResolveMusicForCall(method, kMusicGetCurrentMusicIdExpectedBytes.data(),
                               kMusicGetCurrentMusicIdExpectedBytes.size(), music);
}

bool ResolveMusicPause(std::uintptr_t method, std::uintptr_t* music) noexcept {
    return ResolveMusicForCall(method, kMusicPauseExpectedBytes.data(),
                               kMusicPauseExpectedBytes.size(), music);
}

bool ResolveMusicResume(std::uintptr_t method, std::uintptr_t* music) noexcept {
    return ResolveMusicForCall(method, kMusicResumeExpectedBytes.data(),
                               kMusicResumeExpectedBytes.size(), music);
}

bool ValidateRngSetSeedBinding(std::uintptr_t address) noexcept {
    return ValidateMusicMethod(address, kRngSetSeedExpectedBytes.data(),
                               kRngSetSeedExpectedBytes.size());
}

bool ValidateRngNextBinding(std::uintptr_t address) noexcept {
    return ValidateMusicMethod(address, kRngNextExpectedBytes.data(),
                               kRngNextExpectedBytes.size());
}

isaac::runtime::CallbackRegistry& ManagedCallbackRegistry() noexcept {
    return g_CallbackRegistry;
}

isaac::runtime::ModHandle RuntimeOwnerHandle() noexcept {
    return kRuntimeOwner;
}

// Read by `interfaces/lua/mod_api.cpp` when it maps a Lua `ModCallbacks` value
// onto a catalog callback id: the Stage13 diagnostic only allows its own
// POST_UPDATE phase.
bool IsStage13CallbackMode() noexcept {
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 13
    return g_Stage13Mode;
#else
    return false;
#endif
}

LuaInitResult InitializeFromBuffer(const char* script, std::size_t length, const char* chunkName) {
    return InitializeScript(script, length, chunkName);
}

#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 13
LuaInitResult InitializeManifestMod(const char* entry, std::size_t entryLength,
                                    const char* chunkName, const char* modRoot,
                                    const GameFileReader::Bindings& bindings) {
    g_RequireFailureDetail.store(0, std::memory_order_release);
    g_RequireErrorTail.store(0, std::memory_order_release);
    g_RequireFirstFailureCode.store(0, std::memory_order_release);
    g_RequireFirstFailureName.store(0, std::memory_order_release);
    g_RequireLastFailureCode.store(0, std::memory_order_release);
    g_RequireLastFailureName.store(0, std::memory_order_release);
    g_RequireFailureTotal.store(0, std::memory_order_release);
    if (entry == nullptr || entryLength == 0 || chunkName == nullptr ||
        !SetStage13Context(modRoot, bindings)) {
        RecordRequireFailure(19);
        ResetStage13Context();
        return LuaInitResult::ScriptLoadFailed;
    }

    g_Ready.store(false, std::memory_order_release);
    g_CallbackError.store(false, std::memory_order_release);
    g_PostUpdateCount.store(0, std::memory_order_release);
    const LuaInitResult result = InitializeScript(entry, entryLength, chunkName);
    if (result != LuaInitResult::Success) {
        ResetStage13Context();
    }
    return result;
}

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 13
LuaInitResult InitializeStage13Mod(const char* entry, std::size_t entryLength,
                                   const char* chunkName, const char* modRoot,
                                   const GameFileReader::Bindings& bindings) {
    return InitializeManifestMod(entry, entryLength, chunkName, modRoot, bindings);
}

u32 RequireFailureDetail() {
    return g_RequireFailureDetail.load(std::memory_order_acquire);
}

u64 RequireErrorTail() {
    return g_RequireErrorTail.load(std::memory_order_acquire);
}
#endif
#endif

LuaInitResult Initialize() {
    return InitializeScript(kEmbeddedLuaTestScript, std::strlen(kEmbeddedLuaTestScript), "@runtime_test.lua");
}

u32 PreparationFailureDetail() {
    const u32 step = g_PreparationStep.load(std::memory_order_acquire);
    const u32 status = g_PreparationStatus.load(std::memory_order_acquire);
    return (step << 8) | (status & 0xFF);
}

void SetGameBindings(uintptr_t ownerSlot, uintptr_t isPausedThunk) {
    g_GameOwnerSlot.store(ownerSlot, std::memory_order_release);
    g_GameIsPausedThunk.store(isPausedThunk, std::memory_order_release);
}

// 游戏本体模块基址：与 `SetGameBindings` 同一种发布方式（Hook 安装阶段调用一次）。
void SetEngineModuleBase(uintptr_t base) noexcept {
    g_EngineModuleBase.store(base, std::memory_order_release);
}

void SetInputBindings(uintptr_t isActionPressed, uintptr_t isActionTriggered,
                      uintptr_t getActionValue) {
    g_InputIsActionPressed.store(isActionPressed, std::memory_order_release);
    g_InputIsActionTriggered.store(isActionTriggered, std::memory_order_release);
    g_InputGetActionValue.store(getActionValue, std::memory_order_release);
}

void SetGameIsGreedModeBinding(uintptr_t method) {
    g_GameIsGreedMode.store(method, std::memory_order_release);
}

void SetLevelIsAscentBinding(uintptr_t method) {
    g_LevelIsAscent.store(method, std::memory_order_release);
}

void SetItemPoolGetCollectibleBinding(uintptr_t method) {
    g_ItemPoolGetCollectible.store(method, std::memory_order_release);
}

void SetMusicBindings(uintptr_t getCurrentMusicId, uintptr_t pause, uintptr_t resume) {
    g_MusicGetCurrentMusicId.store(getCurrentMusicId, std::memory_order_release);
    g_MusicPause.store(pause, std::memory_order_release);
    g_MusicResume.store(resume, std::memory_order_release);
}

#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC push_options
#pragma GCC optimize ("Os")
#endif

void SetRngBindings(uintptr_t setSeed, uintptr_t next) {
    g_RngSetSeed.store(setSeed, std::memory_order_release);
    g_RngNext.store(next, std::memory_order_release);
}

// Published as one record and stored field by field: the handlers read each entry point with its
// own acquire load, so a partially verified record never mixes a verified and an unverified
// address inside a single call.
void SetFontBindings(const LuaFontBindings& bindings) {
    g_FontCtor.store(bindings.ctor, std::memory_order_release);
    g_FontDestructor.store(bindings.destructor_, std::memory_order_release);
    g_FontLoad.store(bindings.load, std::memory_order_release);
    g_FontUnload.store(bindings.unload, std::memory_order_release);
    g_FontIsLoaded.store(bindings.isLoaded, std::memory_order_release);
    g_FontGetStringWidth.store(bindings.getStringWidth, std::memory_order_release);
    g_FontGetStringWidthUTF8.store(bindings.getStringWidthUTF8, std::memory_order_release);
    g_FontGetLineHeight.store(bindings.getLineHeight, std::memory_order_release);
    g_FontGetBaselineHeight.store(bindings.getBaselineHeight, std::memory_order_release);
    g_FontGetCharacterWidth.store(bindings.getCharacterWidth, std::memory_order_release);
    g_FontSetMissingCharacter.store(bindings.setMissingCharacter, std::memory_order_release);
    g_FontDrawString.store(bindings.drawString, std::memory_order_release);
    g_FontDrawStringScaled.store(bindings.drawStringScaled, std::memory_order_release);
    g_FontDrawStringUTF8.store(bindings.drawStringUTF8, std::memory_order_release);
    g_FontDrawStringScaledUTF8.store(bindings.drawStringScaledUTF8, std::memory_order_release);
}

void SetSpriteBindings(const LuaSpriteBindings& bindings) {
    g_SpriteCtor.store(bindings.ctor, std::memory_order_release);
    g_SpriteDestructor.store(bindings.destructor_, std::memory_order_release);
    g_SpritePlay.store(bindings.play, std::memory_order_release);
    g_SpriteSetAnimation.store(bindings.setAnimation, std::memory_order_release);
    g_SpriteSetFrameNamed.store(bindings.setFrameNamed, std::memory_order_release);
    g_SpriteSetFrame.store(bindings.setFrame, std::memory_order_release);
    g_SpriteGetFrame.store(bindings.getFrame, std::memory_order_release);
    g_SpriteSetLayerFrame.store(bindings.setLayerFrame, std::memory_order_release);
    g_SpriteGetLayerFrame.store(bindings.getLayerFrame, std::memory_order_release);
    g_SpriteGetTexel.store(bindings.getTexel, std::memory_order_release);
    g_SpriteUpdate.store(bindings.update, std::memory_order_release);
    g_SpriteIsPlaying.store(bindings.isPlaying, std::memory_order_release);
    g_SpriteIsFinished.store(bindings.isFinished, std::memory_order_release);
    g_SpriteRender.store(bindings.render, std::memory_order_release);
    g_SpriteRenderLayer.store(bindings.renderLayer, std::memory_order_release);
    g_SpritePlayRandom.store(bindings.playRandom, std::memory_order_release);
    g_SpriteLoad.store(bindings.load, std::memory_order_release);
    g_SpriteLoadGraphics.store(bindings.loadGraphics, std::memory_order_release);
    g_SpriteReplaceSpritesheet.store(bindings.replaceSpritesheet, std::memory_order_release);
    g_LibcxxStringAssign.store(bindings.libcxxStringAssign, std::memory_order_release);
    g_GameOperatorDelete.store(bindings.gameOperatorDelete, std::memory_order_release);
}

#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC pop_options
#endif

#if !defined(EXL_DIAGNOSTIC_STAGE)
bool IsMusicReadyForDefaultCallback(uintptr_t manager) {
    const uintptr_t method = g_MusicGetCurrentMusicId.load(std::memory_order_acquire);
    uintptr_t music = 0;
    if (!ResolveMusicForManager(manager, method, kMusicGetCurrentMusicIdExpectedBytes.data(),
                                kMusicGetCurrentMusicIdExpectedBytes.size(), &music)) {
        return false;
    }
    using GetCurrentMusicIdFn = int (*)(const void*);
    return reinterpret_cast<GetCurrentMusicIdFn>(method)(reinterpret_cast<const void*>(music)) != 0;
}
#endif

// Invokes one registered callback with the same context the legacy single-slot
// path used: the Mod userdata is the only argument. A Lua error is recorded and
// isolated, but the callback remains registered so one missing API cannot
// disable an entire Mod for the rest of the session.
struct LuaCallbackInvoker final : isaac::runtime::ICallbackInvoker {
    std::uintptr_t manager{0};
    bool hasManager{false};
    bool postUpdate{false};
    // 可选的第二个实参。目前只有 `MC_POST_GAME_STARTED` 用（EID 的处理函数签名是
    // `(mod, isSave)`）；其余回调仍然只收到 Mod 对象一个参数，行为不变。
    std::int64_t secondArgument{0};
    bool hasSecondArgument{false};

    LuaCallbackInvoker(std::uintptr_t managerValue, bool withManager, bool updatePhase,
                       std::int64_t extra = 0, bool withExtra = false) noexcept
        : manager(managerValue), hasManager(withManager), postUpdate(updatePhase),
          secondArgument(extra), hasSecondArgument(withExtra) {}

    isaac::runtime::Status Invoke(
        const isaac::runtime::CallbackDescriptor& descriptor) noexcept override {
        if (g_LuaState == nullptr) {
            return isaac::runtime::Status{isaac::runtime::StatusCode::InvalidState};
        }
        lua_rawgeti(g_LuaState, LUA_REGISTRYINDEX, descriptor.luaReference);
        lua_rawgeti(g_LuaState, LUA_REGISTRYINDEX, descriptor.modReference);
#if defined(EXL_PERSISTENCE_TRACE) || defined(EXL_PERSISTENCE_EVENT_DIAGNOSTIC)
        if (postUpdate) {
            g_CallbackOperation.store(static_cast<u8>(CallbackOperation::None),
                                      std::memory_order_release);
        }
#endif
        if (hasManager) {
            g_CurrentCallbackManager.store(manager, std::memory_order_release);
        }
        g_InManagedCallbackDispatch.store(true, std::memory_order_release);
        if (hasSecondArgument) {
            lua_pushinteger(g_LuaState, static_cast<lua_Integer>(secondArgument));
        }
        const int callbackStatus =
            lua_pcallk(g_LuaState, hasSecondArgument ? 2 : 1, 0, 0, 0, nullptr);
        g_InManagedCallbackDispatch.store(false, std::memory_order_release);
        if (hasManager) {
            g_CurrentCallbackManager.store(0, std::memory_order_release);
        }
        if (callbackStatus != LUA_OK) {
#if defined(EXL_PERSISTENCE_TRACE) || defined(EXL_PERSISTENCE_EVENT_DIAGNOSTIC)
            const u64 detail =
                (CallbackErrorTail(g_LuaState) << 16) |
                (static_cast<u64>(g_CallbackOperation.load(std::memory_order_acquire)) << 8) |
                static_cast<u8>(callbackStatus);
            g_CallbackFailureDetail.store(detail, std::memory_order_release);
#endif
            CaptureLuaErrorTop(g_LuaState);
            lua_pop(g_LuaState, 1);
            g_CallbackError.store(true, std::memory_order_release);
            return isaac::runtime::Status{isaac::runtime::StatusCode::Rejected};
        }
        return isaac::runtime::Status::Ok();
    }
};

namespace {
// 引擎侧的开局中继记下的"刚开了一局"。用原子变量跨线程/跨帧传递，见 `NoteGameStarted`。
// 取值：0 = 没有待派发的开局事件；1 = 待派发，且是**读档**进游戏；2 = 待派发，且是**新开一局**。
// 刻意用整型而不是 bool 类型的原子变量：本项目有一条门禁，运行时代码里不允许出现 bool 原子
// （`runtime/tests/test_manager_update_hook_audit.py::test_runtime_source_contains_no_bool_atomics`
// 做的是纯字符串匹配，所以这里连写法都不能照抄）。
std::atomic<std::uint32_t> g_PendingGameStarted{0};
constexpr std::uint32_t kGameStartedFromSave = 1;
constexpr std::uint32_t kGameStartedFresh = 2;
}  // namespace

void NoteGameStarted(bool isSave) noexcept {
    g_PendingGameStarted.store(isSave ? kGameStartedFromSave : kGameStartedFresh,
                               std::memory_order_release);
}

// 返回"是否真的派发出去了"。**调用方必须据此决定能否清掉待派发标志** ——
// 早先的写法无条件清标志，于是在"当前不允许派发"（例如刚开局那几帧）时把开局事件永久吞掉：
// 真机症状就是套装计数一直 (0/3)。这是 2026-09-14 定位到的真 bug。
bool DispatchPostGameStarted(bool isSave) {
    if (!IsReady() || g_LuaState == nullptr ||
        !isaac::runtime::ShouldDispatchManagedCallbacks()) {
        return false;
    }
    isaac::runtime::EngineGuardEnterDispatch();
    LuaCallbackInvoker invoker{0, false, false, isSave ? 1 : 0, true};
    isaac::runtime::CallbackDispatcher dispatcher{g_CallbackRegistry, invoker};
    static_cast<void>(dispatcher.Dispatch(isaac::runtime::kCallbackPostGameStarted,
                                          isaac::runtime::ThreadAffinity::Any));
    isaac::runtime::EngineGuardLeaveDispatch();
    return true;
}

void DispatchPostUpdate() {
    // 开局事件先派发：PC 上它发生在这一局的第一帧之前，放到 POST_UPDATE 之前最接近原语义。
    // 这里才是真正跑模组 Lua 的地方 —— 中继本身只记账（见 `NoteGameStarted` 的注释）。
    if (const std::uint32_t pending = g_PendingGameStarted.load(std::memory_order_acquire);
        pending != 0 && IsReady()) {
        // **只有真的派发出去才清标志**：清早了就把开局事件永久吞掉（真机症状 = 计数一直 0/3）。
        // 没派发成就留到下一帧再试 —— 它只是"晚到"，语义上仍然是"这一局的开局事件"。
        if (DispatchPostGameStarted(pending == kGameStartedFromSave)) {
            g_PendingGameStarted.store(0, std::memory_order_release);
        }
    }
    if (!IsReady() || g_LuaState == nullptr ||
        !isaac::runtime::ShouldDispatchManagedCallbacks() ||
        g_CallbackRegistry.CountOf(isaac::runtime::kCallbackPostUpdate) == 0) {
        return;
    }
    // 引擎内存可读性缓存只在派发期间有效（见 `engine_memory_guard.hpp`）：游戏线程正在执行
    // 我们的 Lua，这段时间里没有东西会改页表，所以这一层缓存不可能读到过期结论；
    // 出了派发就关闭，任何非派发路径仍然每次都做真正的 `svcQueryMemory`。
    isaac::runtime::EngineGuardEnterDispatch();
    LuaCallbackInvoker invoker{0, false, true};
    isaac::runtime::CallbackDispatcher dispatcher{g_CallbackRegistry, invoker};
    static_cast<void>(dispatcher.Dispatch(isaac::runtime::kCallbackPostUpdate,
                                          isaac::runtime::ThreadAffinity::Any));
    isaac::runtime::EngineGuardLeaveDispatch();
}

void DispatchPostRender(uintptr_t manager) {
    if (!IsReady() || manager == 0 || g_LuaState == nullptr ||
        !isaac::runtime::ShouldDispatchManagedCallbacks() ||
        g_CallbackRegistry.CountOf(isaac::runtime::kCallbackPostRender) == 0) {
        return;
    }
    // 帧归因探针的帧边界就定在这里：这条路径已经要求房间与玩家都成立，所以只有"在局内、
    // EID 正在工作"的帧才进样本（生产构建里这几个调用是空操作）。
    isaac::runtime::EngineFrameProbeOnManagedDispatch();
    isaac::runtime::EngineGuardEnterDispatch();
    isaac::runtime::EngineFrameProbeOnDispatchBegin();
    LuaCallbackInvoker invoker{manager, true, false};
    isaac::runtime::CallbackDispatcher dispatcher{g_CallbackRegistry, invoker};
    static_cast<void>(dispatcher.Dispatch(isaac::runtime::kCallbackPostRender,
                                          isaac::runtime::ThreadAffinity::Any));
    isaac::runtime::EngineFrameProbeOnDispatchEnd();
    isaac::runtime::EngineGuardLeaveDispatch();
}

std::uint64_t DispatchPreGetCollectible(void* itemPool, std::uint32_t itemPoolType,
                                        std::uint32_t seed, std::uint32_t noDecrease,
                                        std::uint32_t lastCollectibleType) {
    (void)itemPool;
    (void)lastCollectibleType;
    // The collectible callback returns a value to the game, so it keeps the
    // legacy single-callback semantics; storage still comes from the shared
    // registry, so there is only one place that owns registrations.
    const isaac::runtime::CallbackDescriptor* descriptor =
        g_CallbackRegistry.At(isaac::runtime::kCallbackPreGetCollectible, 0);
    if (!IsReady() || g_InPreGetCollectibleDispatch || descriptor == nullptr ||
        g_LuaState == nullptr) {
        return 0;
    }
    const int functionRef = descriptor->luaReference;
    const int modRef = descriptor->modReference;

    lua_rawgeti(g_LuaState, LUA_REGISTRYINDEX, functionRef);
    lua_rawgeti(g_LuaState, LUA_REGISTRYINDEX, modRef);
    lua_pushinteger(g_LuaState, static_cast<lua_Integer>(itemPoolType));
    lua_pushboolean(g_LuaState, noDecrease == 0);
    lua_pushinteger(g_LuaState, static_cast<lua_Integer>(seed));
    g_InPreGetCollectibleDispatch = true;
    g_InManagedCallbackDispatch.store(true, std::memory_order_release);
    const int callbackStatus = lua_pcallk(g_LuaState, 4, 1, 0, 0, nullptr);
    g_InManagedCallbackDispatch.store(false, std::memory_order_release);
    g_InPreGetCollectibleDispatch = false;

    const auto recordError = [&]() {
        g_CallbackError.store(true, std::memory_order_release);
    };

    if (callbackStatus != LUA_OK) {
        lua_pop(g_LuaState, 1);
        recordError();
        return 0;
    }
    if (lua_isnil(g_LuaState, -1)) {
        lua_pop(g_LuaState, 1);
        return 0;
    }
    if (!lua_isinteger(g_LuaState, -1)) {
        lua_pop(g_LuaState, 1);
        recordError();
        return 0;
    }
    const lua_Integer result = lua_tointegerx(g_LuaState, -1, nullptr);
    lua_pop(g_LuaState, 1);
    if (result < 0 ||
        static_cast<std::uint64_t>(result) > std::numeric_limits<std::uint32_t>::max()) {
        recordError();
        return 0;
    }
    return (std::uint64_t{1} << 32) | static_cast<std::uint32_t>(result);
}

bool IsReady() {
    return g_Ready.load(std::memory_order_acquire);
}

u32 PostUpdateCount() {
    return g_PostUpdateCount.load(std::memory_order_acquire);
}

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 15
u32 PostRenderCount() {
    return g_PostRenderCount.load(std::memory_order_acquire);
}

u32 PostRenderPausedCount() {
    return g_PostRenderPausedCount.load(std::memory_order_acquire);
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && (EXL_DIAGNOSTIC_STAGE == 16 || EXL_DIAGNOSTIC_STAGE == 17)
bool MusicDiagnosticCycleCompleted() {
    return g_MusicDiagnosticCycleCompleted.load(std::memory_order_acquire);
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 45
bool MusicDiagnosticCurrentId(u32* value) {
    if (value == nullptr || !g_MusicDiagnosticCurrentIdReady.load(std::memory_order_acquire)) {
        return false;
    }
    *value = g_MusicDiagnosticCurrentId.load(std::memory_order_acquire);
    return true;
}
#endif

bool ReadLuaGlobalNumber(const char* name, double* value) {
    if (!IsReady() || g_LuaState == nullptr || name == nullptr || value == nullptr) {
        return false;
    }
    lua_getglobal(g_LuaState, name);
    const bool isNumber = lua_isnumber(g_LuaState, -1) != 0;
    if (isNumber) {
        *value = static_cast<double>(lua_tonumber(g_LuaState, -1));
    }
    lua_pop(g_LuaState, 1);
    return isNumber;
}

std::uint32_t ReadLuaTableNumber(const char* globalName, const char* fieldName,
                                 double* value) {
    if (!IsReady() || g_LuaState == nullptr || globalName == nullptr || fieldName == nullptr ||
        value == nullptr) {
        return 1;
    }
    lua_getglobal(g_LuaState, globalName);
    if (lua_istable(g_LuaState, -1) == 0) {
        lua_pop(g_LuaState, 1);
        return 1;  // 全局不是表：`EID` 还没建出来
    }
    lua_getfield(g_LuaState, -1, fieldName);
    if (lua_isnil(g_LuaState, -1) != 0) {
        lua_pop(g_LuaState, 2);
        return 2;  // 表在，字段是 nil
    }
    if (lua_isnumber(g_LuaState, -1) == 0) {
        lua_pop(g_LuaState, 2);
        return 3;  // 字段在但不是 number
    }
    *value = static_cast<double>(lua_tonumber(g_LuaState, -1));
    lua_pop(g_LuaState, 2);
    return 0;
}

bool ReadLuaTableBoolean(const char* globalName, const char* fieldName, bool* value) {
    if (!IsReady() || g_LuaState == nullptr || globalName == nullptr || fieldName == nullptr ||
        value == nullptr) {
        return false;
    }
    lua_getglobal(g_LuaState, globalName);
    if (lua_istable(g_LuaState, -1) == 0) {
        lua_pop(g_LuaState, 1);
        return false;
    }
    lua_getfield(g_LuaState, -1, fieldName);
    const bool isBoolean = lua_type(g_LuaState, -1) == LUA_TBOOLEAN;
    if (isBoolean) {
        *value = lua_toboolean(g_LuaState, -1) != 0;
    }
    lua_pop(g_LuaState, 2);
    return isBoolean;
}

bool LuaTableFieldIsTable(const char* globalName, const char* fieldName) {
    if (!IsReady() || g_LuaState == nullptr || globalName == nullptr || fieldName == nullptr) {
        return false;
    }
    lua_getglobal(g_LuaState, globalName);
    if (lua_istable(g_LuaState, -1) == 0) {
        lua_pop(g_LuaState, 1);
        return false;
    }
    lua_getfield(g_LuaState, -1, fieldName);
    const bool isTable = lua_istable(g_LuaState, -1) != 0;
    lua_pop(g_LuaState, 2);
    return isTable;
}

std::uint32_t ReadLuaNestedNumber(const char* globalName, const char* tableName,
                                  const char* fieldName, double* value) {
    if (!IsReady() || g_LuaState == nullptr || globalName == nullptr || tableName == nullptr ||
        fieldName == nullptr || value == nullptr) {
        return 1;
    }
    lua_getglobal(g_LuaState, globalName);
    if (lua_istable(g_LuaState, -1) == 0) {
        lua_pop(g_LuaState, 1);
        return 1;
    }
    lua_getfield(g_LuaState, -1, tableName);
    if (lua_istable(g_LuaState, -1) == 0) {
        lua_pop(g_LuaState, 2);
        return 1;
    }
    lua_getfield(g_LuaState, -1, fieldName);
    if (lua_isnil(g_LuaState, -1) != 0) {
        lua_pop(g_LuaState, 3);
        return 2;
    }
    if (lua_isnumber(g_LuaState, -1) == 0) {
        lua_pop(g_LuaState, 3);
        return 3;
    }
    *value = static_cast<double>(lua_tonumber(g_LuaState, -1));
    lua_pop(g_LuaState, 3);
    return 0;
}

bool ReadLuaNestedBoolean(const char* globalName, const char* tableName, const char* fieldName,
                          bool* value) {
    if (!IsReady() || g_LuaState == nullptr || globalName == nullptr || tableName == nullptr ||
        fieldName == nullptr || value == nullptr) {
        return false;
    }
    lua_getglobal(g_LuaState, globalName);
    if (lua_istable(g_LuaState, -1) == 0) {
        lua_pop(g_LuaState, 1);
        return false;
    }
    lua_getfield(g_LuaState, -1, tableName);
    if (lua_istable(g_LuaState, -1) == 0) {
        lua_pop(g_LuaState, 2);
        return false;
    }
    lua_getfield(g_LuaState, -1, fieldName);
    const bool isBoolean = lua_type(g_LuaState, -1) == LUA_TBOOLEAN;
    if (isBoolean) {
        *value = lua_toboolean(g_LuaState, -1) != 0;
    }
    lua_pop(g_LuaState, 3);
    return isBoolean;
}

std::uint32_t RegisteredCallbackCount(std::uint32_t callbackId) {
    return static_cast<std::uint32_t>(g_CallbackRegistry.CountOf(callbackId));
}

bool TakeCallbackError() {
    return g_CallbackError.exchange(false, std::memory_order_acq_rel);
}

#if defined(EXL_PERSISTENCE_TRACE) || defined(EXL_PERSISTENCE_EVENT_DIAGNOSTIC)
u64 CallbackFailureDetail() {
    return g_CallbackFailureDetail.load(std::memory_order_acquire);
}
#endif

} // namespace LuaRuntime
