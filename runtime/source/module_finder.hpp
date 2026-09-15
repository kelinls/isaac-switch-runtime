#pragma once

#include "runtime_constants.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string_view>

struct ModuleRange {
    uintptr_t textStart = 0;
    size_t textSize = 0;
    uintptr_t rodataStart = 0;
    size_t rodataSize = 0;
    uintptr_t dataStart = 0;
    size_t dataSize = 0;
};

struct TargetModule {
    uintptr_t base = 0;
    size_t size = 0;
    size_t textSize = 0;
    std::array<u8, 0x20> buildId{};
    std::string_view path{};

    bool Contains(uintptr_t address, size_t length = 1) const {
        if (length > textSize || address < base) return false;
        const uintptr_t end = address + length;
        const uintptr_t limit = base + textSize;
        return end >= address && limit >= base && end <= limit;
    }
};

enum class TargetModuleScanStatus {
    NotFound,
    Found,
    BuildMismatch,
};

struct TargetModuleScanResult {
    TargetModuleScanStatus status = TargetModuleScanStatus::NotFound;
    std::optional<TargetModule> module;
};

bool ReadBuildId(const ModuleRange& range, std::array<u8, 0x20>* out);
bool IsTargetModule(const ModuleRange& range, std::span<const u8, 0x20> expected);
TargetModuleScanResult ScanTargetModule();
std::optional<TargetModule> FindTargetModule();
