#pragma once

#include "domain/runtime/status.hpp"
#include "interfaces/lua/api_descriptor.hpp"

#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// Single source of truth for "which Lua APIs does this Runtime expose". The
// legacy runtime registered APIs inline; the catalog makes the list enumerable,
// comparable against the implementation and safe to generate docs from.
class ApiCatalog {
public:
    // 上界，不是"当前条数"：`Validate()` 在 `count_ > kCapacity` 时报
    // `CapacityExceeded`。批次 3 加了 8 条（`Game.GetNumPlayers`/`GetFrameCount`、
    // `EntityPlayer` 的 4 个、`ItemConfig_Item.IsCollectible`、全局 `Color` 与
    // `debug.getinfo`），把原有的 127 条推到了 135 条，所以这里从 128 提到 160
    // （留 ~25 条余量给后续批次）。`sizeof(kDefaultApis)` 仍由编译器核对，改大这里不会有
    // 任何运行期代价。
    //
    // 批次 6（2026-09-12）再加 34 条，把 120 推到 154，超过原来的 160 之前的安全水位：
    //   * `Game.GetSeeds`/`Game.GetVictoryLap`（2 条）与 `Seeds.IsCustomRun`/`GetStartSeed`
    //     （2 条）—— EID 的 `features/eid_api.lua:2046` 直接 `game:GetSeeds():IsCustomRun()`；
    //   * `EntityPlayer` 的 30 个成员 —— EID 逐帧调用（`eid_api.lua:2606` 的 `player:GetPill(0)`），
    //     缺一个就是"调 nil"、整条回调被静默摘除。
    // 160 → 192（留 ~38 条余量）。`Validate()` 在 `count_ > kCapacity` 时直接失败，所以这个
    // 数字必须跟着条数一起改，否则整张 Catalog 会在真机上被判为无效（门禁测试会先拦下来）。
    // 2026-09-12 再 +1（`Sprite.GetTexel`）后为 193，故 192 → 224。
    //
    // 2026-09-16：224 → 256。**这一次是提前抬，不是撞线后补救** ——
    //   现状 214 条、只剩 10 个位置，而"接口面台账"（`tools/` 下的 `api_surface_ledger`）里
    //   还排着 21 条 planned 的字段缺口 + 方法侧的 `IsAvailable` 一族，一批就要用掉十几个位置；
    //   一旦 `count_ > kCapacity`，`Validate()` 直接把**整张目录**判为无效（真机上等于 API 全不挂）。
    //   注意：**字段走 `__index` 分派，不占这张目录**（本次新加的 `Quality` 等就一个位置都没占）
    //   —— 所以这次抬容量是给"方法批次"留的余量，不是给字段。
    //   （这里刻意不写台账的完整文件名：`runtime/tests` 里有一条门禁把本文件当**纯文本**
    //     查某个模块名，写全路径会撞上那个词 —— 那条判据偏粗，已在交接文档里记为待办。）
    static constexpr std::size_t kCapacity = 256;

    // The catalog is a view over an immutable table; building one never
    // allocates.
    constexpr ApiCatalog(const LuaApiDescriptor* entries, std::size_t count) noexcept
        : entries_(entries), count_(count) {}

    [[nodiscard]] static const ApiCatalog& Default() noexcept;

    [[nodiscard]] std::size_t Count() const noexcept { return count_; }
    [[nodiscard]] const LuaApiDescriptor* At(std::size_t index) const noexcept;
    [[nodiscard]] const LuaApiDescriptor* Find(std::uint32_t id) const noexcept;
    [[nodiscard]] const LuaApiDescriptor* Find(const char* owner, const char* name) const noexcept;

    // Rejects duplicate ids, duplicate owner+name pairs, empty names, overflow
    // of the capacity and ids whose domain byte does not match the domain.
    [[nodiscard]] Status Validate() const noexcept;

private:
    const LuaApiDescriptor* entries_{nullptr};
    std::size_t count_{0};
};

} // namespace isaac::runtime
