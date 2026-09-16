#pragma once

#include "ports/save_file_port.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace isaac::runtime {

// 走游戏自己 fs 会话的存档文件适配器（2026-09-16，B1 真机验收之后）。
//
// 为什么是**直连 `nn::fs`**（而不是引擎的 `KAGE::Filesys::File` 类）：
//   * 真机对照过两条路（`docs/问题与解决记录.md` 续三十四）：两条都能写，但引擎那条的
//     `read_stream_data` 稳定读回 0 字节、`File::Close()` 的返回类型还是 `void`；
//   * 直连的每一步都返回 `Result` 码（0 = 成功），没有缓冲层与流模式语义要照顾；
//   * 全部入口都是游戏模块自己的**导入跳板**，地址来自它的 `.got`，每个跳板都有 16 字节守卫。
//
// 具体入口与偏移写在 `runtime_constants.hpp`（含逐条证据）；这里只负责编排与把
// `Result` 翻成项目的 `Status`。
class EngineSaveFileAdapter final : public ISaveFilePort {
public:
    EngineSaveFileAdapter() noexcept = default;

    [[nodiscard]] Status Read(std::string_view name, char* out, std::size_t capacity,
                              std::size_t* size) noexcept override;
    [[nodiscard]] Status Write(std::string_view name, std::string_view data) noexcept override;
    [[nodiscard]] Status Remove(std::string_view name) noexcept override;
    [[nodiscard]] Status Exists(const char* absolutePath, bool* exists) noexcept override;

    // 单次读写的上限：缓冲区是**一个 4 KiB 对齐的静态缓冲**，因为 `nn::fs::ReadFile` 对
    // 缓冲区对齐有要求（真机第三轮探针就是靠这一点把"读回 0 字节"排除掉的）。
    static constexpr std::size_t kMaximumFileBytes = 0x1000;

private:
    struct Calls;

    [[nodiscard]] const Calls* Resolve() noexcept;
    [[nodiscard]] static bool ResolveMount(const Calls& calls, std::array<char, 32>& mountPoint,
                                          std::array<char, 32>& mountName) noexcept;
    [[nodiscard]] bool BuildRelativePath(std::string_view name, char* out,
                                         std::size_t capacity) const noexcept;

    std::array<char, 32> mountPoint_{};
    std::array<char, 32> mountName_{};
    bool mountResolved_{false};
    bool mountValid_{false};
    // 4 KiB 对齐：`nn::fs::ReadFile` 对缓冲区对齐有要求；放在静态存储里（不进游戏线程栈）。
    alignas(0x1000) static char buffer_[kMaximumFileBytes];
};

} // namespace isaac::runtime
