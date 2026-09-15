#include "application/callback/callback_registry.hpp"

namespace isaac::runtime {

Status CallbackRegistry::Register(const CallbackDescriptor& descriptor) noexcept {
    if (!descriptor.valid()) {
        return Status{StatusCode::InvalidArgument};
    }
    if (count_ >= kCapacity) {
        return Status{StatusCode::CapacityExceeded};
    }
    // **追加**登记（2026-09-12 修正）。
    //
    // 这里原来对同一 `(id, owner)` 做覆盖，注释写的是"PC 上一个 Mod 只占一个回调槽" —— 那是错的：
    // PC 的 `Mod:AddCallback` 每次都新增一个回调，派发时按登记顺序**全部调用**。EID 重度依赖它
    // （同一 id 登记 5 个 `MC_POST_NEW_ROOM`、10 个 `MC_PRE_USE_ITEM`）。
    // 更糟的是 `owner` 目前是常量 `kRuntimeOwner{1,1}`（`lua_runtime.cpp`），去重键退化成"只看 id"，
    // 于是每种回调永远只剩最后一条 —— 真机报告 `01789203805` 的"注册表 7 条"正是这么来的
    // （宿主机已定量复现：同 id 登记两次，只有后者被调用，且前者既不执行也不释放引用）。
    //
    // 不同 Mod 之间的隔离仍由 `owner` 承担；同一 Mod 的多次登记按登记顺序派发
    // （`CallbackDispatcher::Dispatch` 用 `CountOf(id)` + `At(id, index)` 遍历多条，无需改动）。
    entries_[count_] = descriptor;
    ++count_;
    return Status::Ok();
}

const CallbackDescriptor* CallbackRegistry::Find(CallbackId id, ModHandle owner) const noexcept {
    for (std::size_t index = 0; index < count_; ++index) {
        const CallbackDescriptor& entry = entries_[index];
        if (entry.id == id && entry.owner == owner) {
            return &entry;
        }
    }
    return nullptr;
}

std::uint32_t CallbackRegistry::Remove(CallbackId id, ModHandle owner,
                                       CallbackDescriptor* removed) noexcept {
    for (std::size_t index = 0; index < count_; ++index) {
        const CallbackDescriptor& entry = entries_[index];
        if (entry.id != id || entry.owner != owner) {
            continue;
        }
        if (removed != nullptr) {
            *removed = entry;
        }
        for (std::size_t move = index + 1; move < count_; ++move) {
            entries_[move - 1] = entries_[move];
        }
        --count_;
        return 1;
    }
    return 0;
}

std::uint32_t CallbackRegistry::RemoveOwner(ModHandle owner) noexcept {
    std::size_t write = 0;
    std::uint32_t removed = 0;
    for (std::size_t index = 0; index < count_; ++index) {
        if (entries_[index].owner == owner) {
            ++removed;
            continue;
        }
        entries_[write] = entries_[index];
        ++write;
    }
    count_ = write;
    return removed;
}

std::size_t CallbackRegistry::CountOf(CallbackId id) const noexcept {
    std::size_t total = 0;
    for (std::size_t index = 0; index < count_; ++index) {
        if (entries_[index].id == id) {
            ++total;
        }
    }
    return total;
}

const CallbackDescriptor* CallbackRegistry::At(CallbackId id, std::size_t index) const noexcept {
    std::size_t seen = 0;
    for (std::size_t position = 0; position < count_; ++position) {
        if (entries_[position].id != id) {
            continue;
        }
        if (seen == index) {
            return &entries_[position];
        }
        ++seen;
    }
    return nullptr;
}

const CallbackDescriptor* CallbackRegistry::AtIndex(std::size_t index) const noexcept {
    if (index >= count_) {
        return nullptr;
    }
    return &entries_[index];
}

void CallbackRegistry::Reset() noexcept {
    entries_ = {};
    count_ = 0;
}

} // namespace isaac::runtime
