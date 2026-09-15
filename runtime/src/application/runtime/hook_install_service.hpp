#pragma once

#include "domain/runtime/status.hpp"
#include "ports/hook_port.hpp"
#include "ports/module_scanner_port.hpp"

#include <array>
#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

enum class HookOutcome : std::uint8_t {
    Pending = 0,
    Installed,
    Skipped,
    Failed,
};

struct HookInstallReport {
    std::array<HookOutcome, kHookIdCount> outcomes{};

    [[nodiscard]] HookOutcome OutcomeOf(HookId id) const noexcept {
        const auto index = static_cast<std::size_t>(id);
        return index < outcomes.size() ? outcomes[index] : HookOutcome::Failed;
    }

    void Set(HookId id, HookOutcome outcome) noexcept {
        const auto index = static_cast<std::size_t>(id);
        if (index < outcomes.size()) {
            outcomes[index] = outcome;
        }
    }

    // 必需点**全部**装上才算 ready；"哪些点是必需的"由登记表
    // （`domain/runtime/hook_catalog.hpp`）定义，这里不再写死某个 `HookId`。
    [[nodiscard]] bool productionReady() const noexcept {
        for (const HookDescriptor& point : kHookCatalog) {
            if (point.required && OutcomeOf(point.id) != HookOutcome::Installed) {
                return false;
            }
        }
        return true;
    }

    // 回读口径（设备侧只需三个 32 位字段）：
    //   InstalledCount  -> 已安装数（outcomes 里 Installed 的个数）
    //   FirstFailureSlot-> 第一个 `Failed` 的挂点 index+1；0 表示没有任何挂点装失败
    //
    // `FirstFailureSlot` 只看 `Failed`，**不把 `Pending` 当失败**：`Pending` 的语义是"这个点
    // 根本没被尝试过"，把它读成"装失败"会误导（例如 `InstallProductionHooks` 在 `!module.valid`
    // 时先把报告清零再返回 `InvalidArgument`，此时五个槽全是 `Pending`）。
    //
    // 因此设备侧读者必须配合 `InstalledCount` 才能区分两种"0 失败"：
    //   报告从未被填充（例如上面那条早退路径）：`InstalledCount()==0` 且 `FirstFailureSlot()==0`，
    //                                        五个槽都是 `Pending`；
    //   全部成功且 0 个（理论情形，当前安装流程至少会装上 Update）：`InstalledCount()>0`
    //                                        且 `FirstFailureSlot()==0`。
    // `InstallProductionHooks` 正常返回（含必需项失败）时不会留下 `Pending`：必需项失败会把四个
    // 可选点一律记为 `Skipped`。
    [[nodiscard]] std::uint32_t InstalledCount() const noexcept;
    [[nodiscard]] std::uint32_t FirstFailureSlot() const noexcept;
};

class HookInstallService {
public:
    explicit HookInstallService(IHookPort& hooks) noexcept : hooks_(hooks) {}

    // 装全部挂点：**顺序与"必需/可选"都来自登记表**（`kHookCatalog`）——
    // 先装必需点（当前只有 `ManagerUpdate`，它是加载 Mod 的前置条件），再逐个装可选点。
    // 某个可选点装不上（例如对应的中继/IPS 不在位）只记 `Skipped`，既不阻断 Mod 加载，
    // 也不影响其它挂点。
    [[nodiscard]] Status InstallProductionHooks(const ModuleInfo& module,
                                                HookInstallReport* report) noexcept;

private:
    IHookPort& hooks_;
};

} // namespace isaac::runtime
