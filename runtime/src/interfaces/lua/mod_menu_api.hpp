#pragma once

#include "application/mod/manifest_service.hpp"

struct lua_State;

namespace isaac::runtime {

class ModToggleService;

// 模组开关菜单的控制面（2026-09-16，路线 B）。
//
// 这是**运行时自己的**接口，不是 PC 的 Lua API：PC 的模组开关在游戏自己的菜单里，Lua 侧
// 看不到模组清单，所以我们没有可对齐的 PC 语义，只能自己定义一套最小的。表名 `RuntimeMods`，
// 只有四个成员，全部围绕"读清单 / 改开关 / 落盘"：
//
//   RuntimeMods.List()                     -> { {Directory=, Enabled=, Active=}, ... }
//   RuntimeMods.SetEnabled(directory, on)  -> boolean（只改内存）
//   RuntimeMods.Save()                     -> boolean（写进游戏存档分区）
//   RuntimeMods.Reload()                   -> boolean（从存档重新读）
//
// `Active` = "本次启动真的加载了它"（开机扫描时按开关过滤过）。它存在的意义是让菜单能如实
// 告诉玩家"这一项要重启游戏才生效"：`Enabled ~= Active` 就是需要重启的那种情况。
//
//   RuntimeMods.Report(code)               -> 无返回值；菜单上报"走到哪一步了"
//
// 清单由 `hook_manager` 在扫描/过滤时灌进来（见 `ModMenuPublishDiscovered` /
// `ModMenuMarkActive`），所以这个 TU 不自己去列目录、也不自己读文件。
//
// `Report` 是**给真机排障用的**：菜单是脚本，出问题时"没反应"什么都看不出来，所以脚本每走到
// 一个里程碑就报一次（脚本已加载 / 字体装上了 / 字体失败 / 菜单打开 / update 回调跑过 /
// 切换过 / 存过盘）。C++ 侧把它们记成位图，调试桩一次读数就能分辨到底卡在哪一步。

// 建立 `RuntimeMods` 表并挂成全局（由 `lua_runtime.cpp` 的准备阶段调用）。
int RegisterModMenuApi(lua_State* state) noexcept;

// hook_manager 侧：告诉菜单"发现了哪些模组"（过滤**之前**调用）。
void ModMenuPublishDiscovered(const ResolvedManifestModBatch& batch) noexcept;

// hook_manager 侧：标记"这一批本次真的加载了"（过滤**之后**调用）。
void ModMenuMarkActive(const ResolvedManifestModBatch& batch) noexcept;

// hook_manager 侧：把开关服务交给菜单（读状态、改开关、落盘都通过它）。
void ModMenuAttachToggles(ModToggleService* service) noexcept;

} // namespace isaac::runtime
