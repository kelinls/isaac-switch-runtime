#pragma once

#include "domain/mod/mod_manifest.hpp"
#include "domain/runtime/status.hpp"

#include <array>
#include <cstddef>
#include <string_view>

namespace isaac::runtime {

// 同一次加载里最多记几个"被关掉"的模组。
//
// 为什么只记"关掉的"而不是"每个模组的状态"：新装的模组应当**默认开着**（PC 就是这个语义：
// 把文件夹丢进 `mods/` 就生效）。只记例外，文件就永远只有几十字节，也不会因为"模组换名字"
// 留下过期条目。
inline constexpr std::size_t kMaximumDisabledMods = 8;

// 一个模组目录名（与自动发现用的容量一致：它来自同一批字符串）。
inline constexpr std::size_t kModToggleNameCapacity = kModDirectoryCapacity;

using ModToggleName = std::array<char, kModToggleNameCapacity>;

// 模组开关状态：**纯值对象**（没有文件句柄、没有指针），因此可以直接被单测覆盖，
// 也可以在栈上传递。序列化格式是纯文本，便于用户理解与排查：
//
//     isaac-switch-mods 1
//     off SomeModDirectory
//     off AnotherMod
//
// 解析纪律（与清单解析器同口径）：不认识的**整行**忽略（未来加字段不会让老版本解析失败），
// 但"明显被写坏"的条目（空名字、名字里带 `/`）整份拒绝 —— 拿可疑名字去拼路径、去比目录，
// 只会让"某个模组没生效"变成查不出来的问题。
struct ModToggleState {
    std::array<ModToggleName, kMaximumDisabledMods> disabled{};
    std::size_t disabledCount{0};

    [[nodiscard]] bool IsDisabled(std::string_view directory) const noexcept;
    [[nodiscard]] bool Empty() const noexcept { return disabledCount == 0; }

    // 解析一份状态文本。`NotFound` 由调用方处理（文件不存在 = 空状态），这里只管文本。
    [[nodiscard]] Status Parse(std::string_view text) noexcept;

    // 序列化到 `out`；放不下返回 0（调用方按 `CapacityExceeded` 处理，不写半截）。
    [[nodiscard]] std::size_t Serialize(char* out, std::size_t capacity) const noexcept;

    // 设置某个模组的开关。关掉时追加；打开时从表里删掉（"只记例外"）。
    // 表满 ⇒ `CapacityExceeded`（不静默丢弃）。
    [[nodiscard]] Status SetEnabled(std::string_view directory, bool enabled) noexcept;
};

//: 状态文件名（相对存档根）。改名等于让所有用户的开关归零，所以它是一个具名常量。
inline constexpr char kModToggleStateFileName[] = "isaac-switch-mods-state.txt";
//: 序列化后的最大字节数：头一行 + 每行 `off ` + 名字 + '\n'。
inline constexpr std::size_t kModToggleStateMaximumBytes =
    32 + kMaximumDisabledMods * (4 + kModToggleNameCapacity);

} // namespace isaac::runtime
