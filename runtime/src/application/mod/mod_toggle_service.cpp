#include "application/mod/mod_toggle_service.hpp"

#include <array>
#include <cstring>

namespace isaac::runtime {
namespace {

// 用户手工标记文件的名字（放在模组目录里）。卡上的覆盖层是只读的，所以这条路径只能**读**：
// 它给用户一个"不改存档也能证明性地禁用某个模组"的保底手段。
constexpr char kUserDisableMarker[] = "/disable.it";

constexpr std::size_t kMarkerPathCapacity = kModRootPathCapacity + sizeof(kUserDisableMarker);

// 模组根的**目录名**（`rom:/isaac_mods/mods/SomeMod` ⇒ `SomeMod`）。
// 状态文件里存的、以及发现服务产出的，都是这个短名字 —— 这样两种加载路径（清单/自动发现）
// 用同一把钥匙，用户改清单也不会让开关失效。
std::string_view LeafName(std::string_view path) noexcept {
    if (path.empty()) {
        return {};
    }
    while (!path.empty() && path.back() == '/') {
        path.remove_suffix(1);
    }
    const std::size_t slash = path.rfind('/');
    return slash == std::string_view::npos ? path : path.substr(slash + 1);
}

} // namespace

Status ModToggleService::Load() noexcept {
    std::array<char, kModToggleStateMaximumBytes> buffer{};
    std::size_t size = 0;
    const Status read = files_.Read(kModToggleStateFileName, buffer.data(), buffer.size(), &size);
    if (read.code() == StatusCode::NotFound) {
        state_ = ModToggleState{};   // 第一次运行：没有状态就是"全都开着"
        return Status::Ok();
    }
    if (!read.ok()) {
        return read;
    }
    return state_.Parse(std::string_view(buffer.data(), size));
}

Status ModToggleService::Save() noexcept {
    std::array<char, kModToggleStateMaximumBytes> buffer{};
    const std::size_t size = state_.Serialize(buffer.data(), buffer.size());
    if (size == 0) {
        return Status{StatusCode::CapacityExceeded};
    }
    return files_.Write(kModToggleStateFileName, std::string_view(buffer.data(), size));
}

bool ModToggleService::HasUserDisableMarker(const char* modRoot) noexcept {
    if (modRoot == nullptr || modRoot[0] == '\0') {
        return false;
    }
    char path[kMarkerPathCapacity] = {};
    const std::size_t rootLength = std::strlen(modRoot);
    if (rootLength + sizeof(kUserDisableMarker) > sizeof(path)) {
        return false;
    }
    std::memcpy(path, modRoot, rootLength);
    std::memcpy(path + rootLength, kUserDisableMarker, sizeof(kUserDisableMarker));
    bool exists = false;
    if (!files_.Exists(path, &exists).ok()) {
        return false;   // 查不了 ⇒ 不当成"被禁用"（宁可多加载，也不要静默少加载）
    }
    return exists;
}

std::size_t ModToggleService::ApplyToBatch(ResolvedManifestModBatch* batch) noexcept {
    if (batch == nullptr) {
        return 0;
    }
    std::size_t kept = 0;
    std::size_t removed = 0;
    for (std::size_t index = 0; index < batch->count; ++index) {
        const ResolvedManifestMod& source = batch->mods[index];
        const char* root = source.modRoot == nullptr ? "" : source.modRoot;
        const std::string_view directory = LeafName(root);
        const bool disabled = !directory.empty() && !IsEnabled(directory);
        if (disabled || HasUserDisableMarker(root)) {
            ++removed;
            continue;
        }
        if (kept != index) {
            batch->paths[kept] = batch->paths[index];
            ResolvedManifestMod moved = source;
            // 指针是**指进缓冲**的（`ResolvedManifestMod` 只存指针），搬动之后必须重新指向
            // 新位置，否则它们会指向已经作废的那一份（症状是"加载了错误的模组路径"）。
            moved.modRoot = batch->paths[kept].modRoot.data();
            moved.entryPath = source.hasEntry ? batch->paths[kept].entryPath.data() : "";
            moved.chunkName = source.hasEntry ? batch->paths[kept].chunkName.data() : "";
            batch->mods[kept] = moved;
        }
        ++kept;
    }
    batch->count = kept;
    return removed;
}

} // namespace isaac::runtime
