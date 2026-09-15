#include "module_finder.hpp"

#include "lib/nx/nx.h"

#include <algorithm>
#include <cstring>
#include <limits>

namespace {
constexpr size_t kPathHeaderSize = 8;
constexpr size_t kPathMax = 0x200;
constexpr size_t kNroHeaderSize = 0x60;
constexpr u32 kNroMagic = 0x304F524E;
constexpr u32 kMod0Magic = 0x30444F4D;

bool Contains(const ModuleRange& range, uintptr_t address, size_t length, bool text) {
    const uintptr_t start = text ? range.textStart : range.rodataStart;
    const size_t size = text ? range.textSize : range.rodataSize;
    if (length > size || address < start) return false;
    const uintptr_t end = address + length;
    const uintptr_t limit = start + size;
    return end >= address && limit >= start && end <= limit;
}

bool ValidRange(const ModuleRange& range) {
    if (range.textSize == 0 || range.rodataSize < kPathHeaderSize || range.dataSize == 0) return false;
    const uintptr_t textEnd = range.textStart + range.textSize;
    const uintptr_t rodataEnd = range.rodataStart + range.rodataSize;
    const uintptr_t dataEnd = range.dataStart + range.dataSize;
    return textEnd >= range.textStart && rodataEnd >= range.rodataStart && dataEnd >= range.dataStart &&
           range.textStart <= range.rodataStart && textEnd <= range.rodataStart &&
           range.rodataStart <= range.dataStart && rodataEnd <= range.dataStart;
}

bool ReadPath(const ModuleRange& range, std::string_view* path) {
    if (path == nullptr || !ValidRange(range) || !Contains(range, range.rodataStart, kPathHeaderSize, false)) return false;
    const auto* header = reinterpret_cast<const u32*>(range.rodataStart);
    const size_t length = header[1];
    if (length == 0 || length > kPathMax || !Contains(range, range.rodataStart + kPathHeaderSize, length, false)) return false;
    const char* chars = reinterpret_cast<const char*>(range.rodataStart + kPathHeaderSize);
    if (std::memchr(chars, '\0', length) != nullptr) return false;
    *path = std::string_view(chars, length);
    return !path->empty();
}

bool HasValidMod0(const ModuleRange& range) {
    if (!ValidRange(range) || range.textSize < kNroHeaderSize) return false;
    const auto* header = reinterpret_cast<const u32*>(range.textStart);
    if (header[4] != kNroMagic) return false;
    const u32 offset = header[1];
    if (offset < 8) return false;
    const uintptr_t mod0Address = range.textStart + static_cast<uintptr_t>(offset);
    if (mod0Address < range.textStart || !Contains(range, mod0Address, sizeof(u32), false)) return false;
    return *reinterpret_cast<const u32*>(mod0Address) == kMod0Magic;
}

} // namespace

bool ReadBuildId(const ModuleRange& range, std::array<u8, 0x20>* out) {
    if (out == nullptr || !HasValidMod0(range) || !Contains(range, range.textStart + 0x40, 0x20, true)) return false;
    std::memcpy(out->data(), reinterpret_cast<const void*>(range.textStart + 0x40), out->size());
    return true;
}

bool IsTargetModule(const ModuleRange& range, std::span<const u8, 0x20> expected) {
    std::string_view path;
    if (!ReadPath(range, &path) || path.substr(path.find_last_of("/\\") + 1) != "Repentance.nrs") return false;
    std::array<u8, 0x20> buildId{};
    return ReadBuildId(range, &buildId) && std::equal(buildId.begin(), buildId.end(), expected.begin());
}

TargetModuleScanResult ScanTargetModule() {
    std::optional<TargetModule> buildMismatch;
    MemoryInfo info{};
    u32 pageInfo = 0;
    uintptr_t cursor = 0;
    for (;;) {
        if (R_FAILED(svcQueryMemory(&info, &pageInfo, cursor)) || info.size == 0 || info.addr + info.size < info.addr) break;
        const uintptr_t next = info.addr + info.size;
        if ((info.type & MemState_Type) == MemType_ModuleCodeStatic && info.perm == Perm_Rx) {
            MemoryInfo ro{}, data{};
            u32 ignored = 0;
            const uintptr_t dataQuery = next;
            if (R_FAILED(svcQueryMemory(&ro, &ignored, dataQuery))) break;
            if (ro.addr + ro.size < ro.addr) break;
            if (ro.addr != dataQuery || ro.size == 0 ||
                (ro.type & MemState_Type) != MemType_ModuleCodeStatic || ro.perm != Perm_R) {
                cursor = next;
                if (cursor == 0) break;
                continue;
            }
            const uintptr_t dataQueryAddress = ro.addr + ro.size;
            if (R_FAILED(svcQueryMemory(&data, &ignored, dataQueryAddress))) break;
            if (data.addr + data.size < data.addr) break;
            if (data.addr != dataQueryAddress || data.size == 0 ||
                (data.type & MemState_Type) != MemType_ModuleCodeMutable || data.perm != Perm_Rw) {
                cursor = next;
                if (cursor == 0) break;
                continue;
            }
            ModuleRange range{static_cast<uintptr_t>(info.addr), static_cast<size_t>(info.size), static_cast<uintptr_t>(ro.addr), static_cast<size_t>(ro.size), static_cast<uintptr_t>(data.addr), static_cast<size_t>(data.size)};
            const uintptr_t end = range.dataStart + range.dataSize;
            std::string_view path;
            if (end >= range.textStart &&
                ReadPath(range, &path) && path.substr(path.find_last_of("/\\") + 1) == "Repentance.nrs") {
                TargetModule target{range.textStart, static_cast<size_t>(end - range.textStart), range.textSize, {}, path};
                if (ReadBuildId(range, &target.buildId)) {
                    if (target.buildId == kTargetBuildId) {
                        return {TargetModuleScanStatus::Found, target};
                    }
                    buildMismatch = target;
                }
            }
            cursor = next;
            if (cursor == 0) break;
            continue;
        }
        cursor = next;
        if (cursor == 0 || cursor < info.addr) break;
    }
    if (buildMismatch.has_value()) {
        return {TargetModuleScanStatus::BuildMismatch, buildMismatch};
    }
    return {};
}

std::optional<TargetModule> FindTargetModule() {
    TargetModuleScanResult result = ScanTargetModule();
    if (result.status != TargetModuleScanStatus::Found || !result.module.has_value() ||
        kManagerUpdateFileOffset % 4 != 0 ||
        kManagerUpdateFileOffset > std::numeric_limits<uintptr_t>::max() - kManagerUpdateExpectedBytes.size()) {
        return std::nullopt;
    }
    const TargetModule* module = &*result.module;
    const uintptr_t target = module->base + kManagerUpdateFileOffset;
    if (target < module->base || !module->Contains(target, kManagerUpdateExpectedBytes.size())) {
        return std::nullopt;
    }
    return result.module;
}
