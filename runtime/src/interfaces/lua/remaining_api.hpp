#pragma once

#include <cstddef>
#include <cstdint>

extern "C" {
#include <lua.h>
}

// Migration container for the Lua API owners that have not been split into
// their own translation unit yet: Level, Room and ItemPool. Each owner already
// owns its `descriptor id -> handler` table here and registers through the
// shared `AttachOwnerMethods` loop, so a domain can move to its own file (and
// grow past three methods) without touching the registration mechanics.
//
// The file must stay a container: as soon as one owner reaches three methods or
// 200 lines it moves to `<owner>_api.cpp`, per the design's size rules.
// MusicManager, RNG and Input already moved that way.
namespace isaac::runtime {

[[nodiscard]] std::size_t AttachLevelMethods(lua_State* state) noexcept;
[[nodiscard]] std::size_t AttachRoomMethods(lua_State* state) noexcept;
//: `GridEntity` 族（批次 12，2026-09-16）：`Room:GetGridEntity()` 的返回值。
[[nodiscard]] std::size_t AttachGridEntityMethods(lua_State* state) noexcept;
// 地基二期（2026-09-15）：`RoomDescriptor` 的字段访问与列表的 `Size`/`Get`。
// 两者的 `__index` 都是函数（同时服务字段与方法），由 `lua_runtime.cpp` 注册元表时接线。
int RoomDescriptorIndex(lua_State* state);
int RoomDescriptorListIndex(lua_State* state);
[[nodiscard]] std::size_t AttachRoomDescriptorListMethods(lua_State* state) noexcept;
[[nodiscard]] std::size_t AttachItemPoolMethods(lua_State* state) noexcept;

// --- `Level:GetCurses()` 的探针读数（2026-09-12，动作三）----------------------------
//
// EID 的 `hasCurseBlind()`（`eid_api.lua:597`）是"隐瞒分支"四条成因之一，它的全部输入
// 就是 `Level:GetCurses()`。反汇编已证明地址链没错、但语义只做了三步里的第一步
// （`curses | permanent` 再 `& banned` 都没做），所以真机要同时看到：
//   * `raw`       —— `Level+0xC` 的原始位（现在交给 Mod 的那个值）；
//   * `permanent` —— `Game::GetSpecialSeedPermanentCurses()` 的返回值；
//   * `banned`    —— `Game::GetSpecialSeedBannedCurses()` 的返回值；
//   * `flags`     —— bit0 原始位读到了、bit1 permanent 访问器被调到（守卫通过）、
//                     bit2 banned 访问器被调到。
struct GameCursesProbe {
    std::uint32_t raw{0};
    std::uint32_t permanent{0};
    std::uint32_t banned{0};
    std::uint32_t flags{0};
    // `Level:GetCurses()` 被调用过的次数（0 = Mod 压根没走这条判定）。
    std::uint32_t reads{0};
};

[[nodiscard]] GameCursesProbe GameCursesProbeSnapshot() noexcept;

} // namespace isaac::runtime
