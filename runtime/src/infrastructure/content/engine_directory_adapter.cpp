#include "infrastructure/content/engine_directory_adapter.hpp"

#include "module_finder.hpp"
#include "runtime_constants.hpp"

#include <array>
#include <cstdint>
#include <cstring>
#include <string_view>

namespace isaac::runtime {
namespace {

// 引擎侧的目录列表项：0x10 字节一项，`+0x00` 标志字节、`+0x08` 名字指针。
// 证据见 `runtime_constants.hpp` 的 `kDirectoryEntrySize` 一带（引擎自己的
// `DirectoryEntry::IsFile/IsDirectory/GetName` 三条反汇编 + 真机读回的原始字）。
struct DirectoryEntryLike {
    std::uint8_t flags;
    std::uint8_t padding[7];   // ⚠️ 引擎不初始化这 7 个字节（真机数据证实是堆块旧内容）
    const char* name;
};
static_assert(sizeof(DirectoryEntryLike) == kDirectoryEntrySize, "条目必须是 0x10 字节");
static_assert(offsetof(DirectoryEntryLike, name) == kDirectoryEntryNameOffset,
              "名字指针必须在 +0x08");

using GetDirectoryEntriesFn = void* (*)(void* contentManager, const char* path,
                                         std::uint32_t* count);
using ArrayDeleteFn = void (*)(void* block);

// 名字长度上限（含结尾 NUL）——与 `ModDirectoryEntry::name` 的容量一致。
constexpr std::size_t kNameCapacity = kModDirectoryCapacity;

// 只允许列这一层：这是**真机唯一被证明安全**的路径（对子目录再调会触发引擎内部断言）。
constexpr std::string_view kAllowedRelativePath = "isaac_mods/mods";

struct ResolvedEngineCalls {
    bool valid{false};
    void* contentManager{nullptr};
    GetDirectoryEntriesFn getDirectoryEntries{nullptr};
    ArrayDeleteFn arrayDelete{nullptr};
};

bool GuardedCode(const TargetModule& module, std::uintptr_t offset,
                 const std::array<u8, 16>& expected) {
    if (!module.Contains(module.base + offset, expected.size())) {
        return false;
    }
    return std::memcmp(reinterpret_cast<const void*>(module.base + offset), expected.data(),
                       expected.size()) == 0;
}

ResolvedEngineCalls ResolveCalls() {
    ResolvedEngineCalls calls{};
    const std::optional<TargetModule> module = FindTargetModule();
    if (!module.has_value()) {
        return calls;
    }
    if (!GuardedCode(*module, kContentGetDirectoryEntriesOffset,
                     kContentGetDirectoryEntriesExpectedBytes)) {
        // 守卫不过绝不调用：拿猜的偏移去调游戏线程上的代码，是最容易随机崩的一种错。
        return calls;
    }
    const auto* contentSlot =
        reinterpret_cast<void* const*>(module->base + kContentManagerSlotOffset);
    if (contentSlot == nullptr || *contentSlot == nullptr) {
        return calls;
    }
    // 释放函数：NRO 的 `R_AARCH64_JUMP_SLOT` 槽里运行期就是 `_ZdaPv` 的真实地址。
    const auto* deleteSlot =
        reinterpret_cast<void* const*>(module->base + kEngineArrayDeleteGotOffset);
    if (deleteSlot == nullptr || *deleteSlot == nullptr) {
        return calls;
    }
    calls.contentManager = *contentSlot;
    calls.arrayDelete = reinterpret_cast<ArrayDeleteFn>(*deleteSlot);
    calls.getDirectoryEntries = reinterpret_cast<GetDirectoryEntriesFn>(
        module->base + kContentGetDirectoryEntriesOffset);
    calls.valid = true;
    return calls;
}

const ResolvedEngineCalls& EngineCalls() {
    // 与 `engine_content_mount_adapter.cpp` 同一形态：解析一次、成功后缓存。
    // 不用带动态初始化的函数内 `static`（本模块 `-nostartfiles`，那个 magic-static 守卫
    // 依赖我们并不运行的启动代码）；这两个 `static` 都是零初始化 POD。
    static ResolvedEngineCalls cached{};
    static bool resolved = false;
    if (!resolved) {
        const ResolvedEngineCalls calls = ResolveCalls();
        if (calls.valid) {
            cached = calls;
            resolved = true;
        }
        return cached;
    }
    return cached;
}

} // namespace

Status EngineDirectoryAdapter::ListSubdirectories(std::string_view relativePath,
                                                  ModDirectoryEntry* out,
                                                  std::size_t capacity,
                                                  std::size_t* count) noexcept {
    if (out == nullptr || count == nullptr || capacity == 0) {
        return Status{StatusCode::InvalidArgument};
    }
    *count = 0;
    if (relativePath != kAllowedRelativePath) {
        // 见头文件：引擎那个入口只对父目录安全，对子目录会断言中止。
        // 这里明确拒绝，而不是"试着调一下看看"。
        return Status{StatusCode::Unsupported};
    }

    const ResolvedEngineCalls& calls = EngineCalls();
    if (!calls.valid) {
        return Status{StatusCode::NotFound};
    }

    char path[kModDirectoryCapacity + 32]{};
    if (relativePath.size() + 1 > sizeof(path)) {
        return Status{StatusCode::CapacityExceeded};
    }
    std::memcpy(path, relativePath.data(), relativePath.size());
    path[relativePath.size()] = '\0';

    std::uint32_t reported = 0;
    void* block = calls.getDirectoryEntries(calls.contentManager, path, &reported);
    if (block == nullptr) {
        return Status{StatusCode::NotFound};
    }

    // 返回的指针是**第一个条目**（条目数经 `reported` 给出）。
    std::size_t directories = 0;
    Status status{StatusCode::Ok};
    for (std::uint32_t index = 0; index < reported; ++index) {
        const auto* entry = reinterpret_cast<const DirectoryEntryLike*>(
            static_cast<const std::uint8_t*>(block) + index * kDirectoryEntrySize);
        if ((entry->flags & (1u << kDirectoryEntryDirectoryBit)) == 0) {
            continue;   // 不是目录（模组目录下的文件在更深一层，这一层不该出现文件）
        }
        if (entry->name == nullptr) {
            continue;
        }
        if (directories >= capacity) {
            // **不截断**：静默丢掉一个模组会让"某个模组没生效"变成查不出来的谜。
            status = Status{StatusCode::CapacityExceeded};
            break;
        }
        ModDirectoryEntry& target = out[directories];
        std::size_t length = 0;
        while (length + 1 < kNameCapacity && entry->name[length] != '\0') {
            target.name[length] = entry->name[length];
            ++length;
        }
        target.name[length] = '\0';
        ++directories;
    }
    calls.arrayDelete(block);

    if (!status.ok()) {
        *count = 0;
        return status;
    }
    *count = directories;
    return Status{StatusCode::Ok};
}

} // namespace isaac::runtime
