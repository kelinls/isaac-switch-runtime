#include "domain/mod/mod_toggle_state.hpp"

#include <cstring>

namespace isaac::runtime {
namespace {

constexpr std::string_view kHeader{"isaac-switch-mods 1"};
constexpr std::string_view kDisabledPrefix{"off "};

// 目录名是否"看起来像"一个模组目录名。与自动发现的口径一致：空、`.`、`..`、带斜杠的名字
// 都说明这份文件被写坏了（自动发现那边遇到同样的名字会整批拒绝，理由相同）。
bool PlausibleName(std::string_view name) noexcept {
    if (name.empty() || name == "." || name == "..") {
        return false;
    }
    if (name.find('/') != std::string_view::npos ||
        name.find('\\') != std::string_view::npos) {
        return false;
    }
    return name.size() < kModToggleNameCapacity;
}

std::string_view Trim(std::string_view text) noexcept {
    while (!text.empty() && (text.back() == ' ' || text.back() == '\t' || text.back() == '\r')) {
        text.remove_suffix(1);
    }
    while (!text.empty() && (text.front() == ' ' || text.front() == '\t')) {
        text.remove_prefix(1);
    }
    return text;
}

} // namespace

bool ModToggleState::IsDisabled(std::string_view directory) const noexcept {
    for (std::size_t index = 0; index < disabledCount; ++index) {
        if (directory == std::string_view(disabled[index].data())) {
            return true;
        }
    }
    return false;
}

Status ModToggleState::Parse(std::string_view text) noexcept {
    disabledCount = 0;
    bool headerSeen = false;
    std::size_t cursor = 0;
    while (cursor <= text.size()) {
        const std::size_t newline = text.find('\n', cursor);
        const std::size_t end = newline == std::string_view::npos ? text.size() : newline;
        const std::string_view raw = text.substr(cursor, end - cursor);
        cursor = newline == std::string_view::npos ? text.size() + 1 : newline + 1;

        const std::string_view line = Trim(raw);
        if (line.empty() || line.front() == '#') {
            continue;
        }
        if (!headerSeen) {
            // 第一行必须是抬头：它同时是"这份文件确实是我们写的"的最低限度校验。
            if (line != kHeader) {
                return Status{StatusCode::Corrupted};
            }
            headerSeen = true;
            continue;
        }
        if (line.rfind(kDisabledPrefix, 0) != 0) {
            // 整行只有 `off`（含被截断的 `off `，`Trim` 之后就是它）说明这份文件坏了，
            // 而不是"不认识的字段" —— 安静忽略会让"某个模组仍然被禁用"变成查不出的现象。
            if (line == "off") {
                return Status{StatusCode::Corrupted};
            }
            continue;   // 不认识的整行忽略（向后兼容）
        }
        const std::string_view name = Trim(line.substr(kDisabledPrefix.size()));
        if (!PlausibleName(name)) {
            return Status{StatusCode::Corrupted};
        }
        if (IsDisabled(name)) {
            continue;   // 重复条目按一条算
        }
        if (disabledCount >= kMaximumDisabledMods) {
            return Status{StatusCode::CapacityExceeded};
        }
        std::memcpy(disabled[disabledCount].data(), name.data(), name.size());
        disabled[disabledCount][name.size()] = '\0';
        ++disabledCount;
    }
    if (!headerSeen && !text.empty()) {
        return Status{StatusCode::Corrupted};
    }
    return Status::Ok();
}

std::size_t ModToggleState::Serialize(char* out, std::size_t capacity) const noexcept {
    if (out == nullptr) {
        return 0;
    }
    std::size_t written = 0;
    const auto append = [&](std::string_view piece) {
        if (written + piece.size() + 1 > capacity) {
            return false;
        }
        std::memcpy(out + written, piece.data(), piece.size());
        written += piece.size();
        return true;
    };
    if (!append(kHeader) || !append("\n")) {
        return 0;
    }
    for (std::size_t index = 0; index < disabledCount; ++index) {
        if (!append(kDisabledPrefix) ||
            !append(std::string_view(disabled[index].data())) || !append("\n")) {
            return 0;
        }
    }
    out[written] = '\0';
    return written;
}

Status ModToggleState::SetEnabled(std::string_view directory, bool enabled) noexcept {
    if (!PlausibleName(directory)) {
        return Status{StatusCode::InvalidArgument};
    }
    for (std::size_t index = 0; index < disabledCount; ++index) {
        if (directory != std::string_view(disabled[index].data())) {
            continue;
        }
        if (enabled) {
            for (std::size_t move = index + 1; move < disabledCount; ++move) {
                disabled[move - 1] = disabled[move];
            }
            disabled[--disabledCount] = ModToggleName{};
        }
        return Status::Ok();
    }
    if (enabled) {
        return Status::Ok();   // 本来就没被关掉
    }
    if (disabledCount >= kMaximumDisabledMods) {
        return Status{StatusCode::CapacityExceeded};
    }
    std::memcpy(disabled[disabledCount].data(), directory.data(), directory.size());
    disabled[disabledCount][directory.size()] = '\0';
    ++disabledCount;
    return Status::Ok();
}

} // namespace isaac::runtime
