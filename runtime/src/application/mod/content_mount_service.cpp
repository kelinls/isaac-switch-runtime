#include "application/mod/content_mount_service.hpp"

#include <cstring>

namespace isaac::runtime {
namespace {

ContentMountService* g_sessionService = nullptr;

std::string_view LeafAt(std::size_t index) noexcept {
    return index < ContentMountService::kLeaves.size()
               ? ContentMountService::kLeaves[index]
               : std::string_view{};
}

}  // namespace

Status ContentMountService::MountLeaves(const Entry& entry) noexcept {
    const std::string_view directory{entry.directory.data(), entry.length};
    for (std::size_t index = 0; index < ContentMountService::kLeaves.size(); ++index) {
        const Status status = port_.MountModDirectory(directory, LeafAt(index));
        if (!status.ok()) {
            // The port fails closed when the engine side is unverified; a missing
            // directory inside a Mod is not a failure there, so any error here means
            // the environment is wrong and the caller should learn about it.
            return status;
        }
    }
    return Status::Ok();
}

const ContentMountService::Entry* ContentMountService::Find(
    std::string_view modDirectory) const noexcept {
    for (std::size_t index = 0; index < count_; ++index) {
        const Entry& entry = entries_[index];
        if (entry.length == modDirectory.size() &&
            std::memcmp(entry.directory.data(), modDirectory.data(), entry.length) == 0) {
            return &entry;
        }
    }
    return nullptr;
}

Status ContentMountService::RegisterMod(std::string_view modDirectory) noexcept {
    if (modDirectory.empty()) {
        return Status{StatusCode::InvalidArgument};
    }
    if (modDirectory.size() >= kMaxDirectoryLength) {
        return Status{StatusCode::CapacityExceeded};
    }
    if (Find(modDirectory) != nullptr) {
        return Status::Ok();
    }
    if (count_ >= kMaxMods) {
        return Status{StatusCode::CapacityExceeded};
    }

    Entry& entry = entries_[count_];
    std::memcpy(entry.directory.data(), modDirectory.data(), modDirectory.size());
    entry.length = modDirectory.size();
    ++count_;

    const Status status = MountLeaves(entry);
    if (!status.ok()) {
        // Do not remember a Mod whose mount failed: the rebuild relay would retry it
        // forever and the caller has already been told.
        --count_;
        entry = Entry{};
        return status;
    }
    return Status::Ok();
}

Status ContentMountService::RemountAll() noexcept {
    for (std::size_t index = 0; index < count_; ++index) {
        const Status status = MountLeaves(entries_[index]);
        if (!status.ok()) {
            return status;
        }
    }
    return Status::Ok();
}

void ContentMountService::Clear() noexcept {
    entries_ = {};
    count_ = 0;
}

std::string_view ContentMountService::mod_directory(std::size_t index) const noexcept {
    if (index >= count_) {
        return std::string_view{};
    }
    return std::string_view{entries_[index].directory.data(), entries_[index].length};
}

void SetSessionContentMountService(ContentMountService* service) noexcept {
    g_sessionService = service;
}

ContentMountService* SessionContentMountService() noexcept {
    return g_sessionService;
}

}  // namespace isaac::runtime
