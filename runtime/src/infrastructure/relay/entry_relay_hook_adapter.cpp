#include "infrastructure/relay/entry_relay_hook_adapter.hpp"

#include "relay/entry_relay.hpp"       // 侵入核心（source/relay/entry_relay.hpp）
#include "runtime_constants.hpp"       // M0 台账

namespace isaac::runtime {
namespace {

// 台账里的"入口已被入口中继改写"形态常量，必须与编码器逐字一致
// （别的子系统用它来识别入口形态，例如 `ValidateItemPoolGetCollectibleMethod`）。
static_assert(kEntryRelayEntryStubWord0 == relay::kEntryLdrX16Word,
              "kEntryRelayEntryStubWord0 必须等于编码器的 `ldr x16, #8`");
static_assert(kEntryRelayEntryStubWord1 == relay::kEntryBrX16Word,
              "kEntryRelayEntryStubWord1 必须等于编码器的 `br x16`");

// 回调指针表：由 `RegisterEntryRelayCallbacks()` 接上真实回调。初值全为 nullptr 是刻意的 ——
// 空回调会被判定拒绝（见下），否则会装上一个"跳进空地址"的中继。
void* g_hookCallbacks[kHookIdCount] = {};

// 每个挂点一份回退入口存储（安装后写入，回调靠它跑原函数）。
std::uintptr_t g_fallbackEntries[kHookIdCount] = {};

// 真实回调在 hook_manager.cpp 里定义（与各自的钩子体同 TU），这里只持声明。
// 各路的形状不同，见 `RegisterEntryRelayCallbacks` 的注释。
extern "C" void EntryRelayManagerRenderCallback(void* self);
extern "C" void EntryRelayManagerUpdateCallback(void* self);
// 取道具前挂点：目标 ABI 是 `this + poolType + seed + noDecrease + defaultItem`（五个都要透传，
// 旧 IPS 中继桩同样收 x0..x4）。
extern "C" std::uint32_t EntryRelayPreGetCollectibleCallback(void* itemPool, std::uint32_t itemPoolType,
                                                             std::uint32_t seed,
                                                             std::uint32_t noDecrease,
                                                             std::uint32_t defaultItem);
// 挂载点重建：目标函数无参数（`_ZN...RebuildContentMountPointsEv`）。
extern "C" void EntryRelayRebuildMountPointsCallback();

// `RegisterEntryRelayCallbacks` 定义在匿名命名空间之外（外部链接）：调用点在
// hook_manager.cpp，而 g_hookCallbacks 在本 TU 的匿名命名空间里。

EntryRelayBinding BindingOf(HookId id) noexcept {
    const auto index = static_cast<std::size_t>(id);
    void* callback = index < kHookIdCount ? g_hookCallbacks[index] : nullptr;
    switch (id) {
        case HookId::ManagerUpdate:
            return {"Manager::Update", kManagerUpdateFileOffset,
                    kManagerUpdateExpectedBytes.data(), callback, &g_fallbackEntries[index]};
        case HookId::ManagerRender:
            return {"Manager::Render", kManagerRenderFileOffset,
                    kManagerRenderExpectedBytes.data(), callback, &g_fallbackEntries[index]};
        case HookId::PreGetCollectible:
            return {"ItemPool::GetCollectible", kPreGetCollectibleRelayOffset,
                    kPreGetCollectibleRelayExpectedOriginal.data(), callback,
                    &g_fallbackEntries[index]};
        case HookId::RebuildMountPoints:
            return {"RebuildContentMountPoints", kRebuildContentMountPointsOffset,
                    kRebuildContentMountPointsExpectedBytes.data(), callback,
                    &g_fallbackEntries[index]};
        case HookId::ManagerPresent:
        case HookId::GameStart:
        case HookId::Count:
            // ManagerPresent 的靶点是 PLT 调用点（16 字节含 bl），入口中继无法回放：
            // 它走 GOT 槽改写（spec §4.5），在 M2b 单独实现。
            // GameStart 的靶点是"代码洞里已经放好的中继 + 一个回调槽"，也不走入口中继，
            // 而是 `RelaySlot` 后端（见 `infrastructure/relayslot/`）。
            return {};
    }
    return {};
}

}  // namespace

// 把已实现的回调接进绑定表。必须在安装前调用一次。
// 外部链接：调用点在 hook_manager.cpp，而 g_hookCallbacks 在上面的匿名命名空间里。
//
// 四路回调的形状不同，且都是刻意的：
//   * `ManagerRender`：钩子体已抽成两条后端共用的自由函数，中继回调自己读回退入口；
//   * `ManagerUpdate`：钩子体仍是蹦床里的那个 `Callback`（约 300 行、诊断构建下还有十几处
//     按源码切片断言它的测试），所以中继回调复用同一个 `Callback`，由钩子管理器把回退入口
//     发布进蹦床的 `Orig`（见 hook_manager.cpp 的 `PublishEntryRelayOriginals`）。
//     两条后端跑的是同一个函数，行为一致由构造保证，且不必改写那批守护测试。
//   * `PreGetCollectible` / `RebuildMountPoints`：回调体与旧后端共用同一个派发函数，
//     差别只在"没有覆盖时谁来跑原函数"（旧后端的 IPS 桩自己判断，中继桩不做判断，
//     所以回调自己判 / 自己调回退入口）。
void RegisterEntryRelayCallbacks() noexcept {
    g_hookCallbacks[static_cast<std::size_t>(HookId::ManagerUpdate)] =
        reinterpret_cast<void*>(&EntryRelayManagerUpdateCallback);
    g_hookCallbacks[static_cast<std::size_t>(HookId::ManagerRender)] =
        reinterpret_cast<void*>(&EntryRelayManagerRenderCallback);
    g_hookCallbacks[static_cast<std::size_t>(HookId::PreGetCollectible)] =
        reinterpret_cast<void*>(&EntryRelayPreGetCollectibleCallback);
    g_hookCallbacks[static_cast<std::size_t>(HookId::RebuildMountPoints)] =
        reinterpret_cast<void*>(&EntryRelayRebuildMountPointsCallback);
}

BindingVerdict EvaluateEntryRelayBinding(const EntryRelayBinding& binding,
                                         const HookTarget& target,
                                         std::uintptr_t* outTarget) noexcept {
    if (outTarget == nullptr || binding.offset == 0 || binding.expectedEntry16 == nullptr ||
        binding.callback == nullptr || binding.fallbackEntry == nullptr) {
        return BindingVerdict::Rejected;                  // 含"回调未接线"，必须响亮失败
    }
    // 地址判定复用头文件里的 constexpr 版（同一份恒等逻辑，编译期已自证）。
    if (!IsHookableEntryAddress(target.base, target.codeSize, binding.offset, outTarget)) {
        return BindingVerdict::Rejected;                  // 空/回绕/越界/对齐/跨页
    }
    return BindingVerdict::Ok;
}

std::uintptr_t EntryRelayFallbackEntry(HookId id) noexcept {
    const auto index = static_cast<std::size_t>(id);
    return index < kHookIdCount ? g_fallbackEntries[index] : 0;
}

Status EntryRelayHookAdapter::Install(HookId id, const HookTarget& target) noexcept {
    // 顶部清零：下面两条早退（InvalidArgument / InvalidState）不是 entry_relay 失败，
    // 不清零会返回上一次成功安装留下的陈旧失败码；Task 4 的路由要转发它、Task 5 要读它。
    lastFailure_ = 0;
    const auto index = static_cast<std::size_t>(id);
    if (index >= kHookIdCount) {
        return Status{StatusCode::InvalidArgument};
    }
    if (installed_[index]) {
        return Status{StatusCode::InvalidState};
    }
    const EntryRelayBinding binding = BindingOf(id);
    std::uintptr_t address = 0;
    if (EvaluateEntryRelayBinding(binding, target, &address) != BindingVerdict::Ok) {
        lastFailure_ = 1U;  // 纯判定拒绝（地址/边界/对齐/回调未接线）
        return Status{StatusCode::Rejected};
    }
    std::uintptr_t originalEntry = 0;
    if (!isaac::runtime::relay::TryInstallEntryRelay(
            address, binding.expectedEntry16,
            reinterpret_cast<std::uintptr_t>(binding.callback), &originalEntry)) {
        const std::uint32_t failure = isaac::runtime::relay::LastFailure();
        lastFailure_ = failure;
        // 失败分两类，处置口径不同（`entry_relay` 的契约）：
        //   * `EntryRelayFailure` 的 1/2/3/5（InvalidAddress/EntryMismatch/ArenaExhausted/
        //     PcRelativeInstruction）都在**任何写入之前**返回，目标字节保持原样；
        //   * `EntryRelayFailure::WriteVerifyFailed`(4) 表示**已经写过**但用只读别名读回不一致：
        //     不可恢复，绝不允许重试（重试等于把一个可能已被改写的入口再写一次）。
        // 两者都报 Rejected，由调用方（Task 4 的路由）决定是否停机。
        return Status{StatusCode::Rejected};
    }
    *binding.fallbackEntry = originalEntry;
    installed_[index] = true;
    lastFailure_ = 0;
    return Status::Ok();
}

bool EntryRelayHookAdapter::IsInstalled(HookId id) const noexcept {
    const auto index = static_cast<std::size_t>(id);
    return index < kHookIdCount && installed_[index];
}

std::uint32_t EntryRelayHookAdapter::LastFailureCode() const noexcept { return lastFailure_; }

}  // namespace isaac::runtime
