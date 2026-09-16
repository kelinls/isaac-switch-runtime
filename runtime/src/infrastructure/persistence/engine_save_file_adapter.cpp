#include "infrastructure/persistence/engine_save_file_adapter.hpp"

#include "module_finder.hpp"
#include "runtime_constants.hpp"

// `svcQueryMemory` 必须走 `lib/nx/nx.h`（它把 libnx 整套头包在 `extern "C"` 里）：
// 只包含 `lib/nx/kernel/svc.h` 会拿到 C++ 链接的声明，而 `svc.s` 里的定义是汇编未修饰名
// ⇒ 链接期未定义符号（真机上 PLT 槽为 0 会直接跳到地址 0）。
#include "lib/nx/nx.h"

#include <cstring>
#include <optional>

namespace isaac::runtime {

// 所有入口都在这里解析一次并缓存。任何一个守卫不过 ⇒ 整条通道视为不可用
// （宁可让上层看到 `Unsupported`，也不要拿一个"地址可能错了"的函数去写玩家的存档）。
struct EngineSaveFileAdapter::Calls {
    bool valid{false};
    std::uint32_t (*openFile)(std::uint64_t*, const char*, int){nullptr};
    std::uint32_t (*createFile)(const char*, std::int64_t){nullptr};
    std::uint32_t (*writeFile)(std::uint64_t, std::int64_t, const void*, std::size_t,
                               const std::uint32_t*){nullptr};
    std::uint32_t (*readFile)(std::uint64_t*, std::uint64_t, std::int64_t, void*,
                              std::size_t){nullptr};
    std::uint32_t (*setFileSize)(std::uint64_t, std::int64_t){nullptr};
    std::uint32_t (*flushFile)(std::uint64_t){nullptr};
    std::uint32_t (*getFileSize)(std::int64_t*, std::uint64_t){nullptr};
    std::uint32_t (*closeFile)(std::uint64_t){nullptr};
    std::uint32_t (*deleteFile)(const char*){nullptr};
    std::uint32_t (*commitSaveData)(const char*){nullptr};
    const char* (*getMountPoint)(const void*){nullptr};
};

namespace {

constexpr std::size_t kPathCapacity = 320;

bool GuardedCode(const TargetModule& module, std::uintptr_t offset,
                 const std::array<u8, 16>& expected) {
    if (!module.Contains(module.base + offset, expected.size())) {
        return false;
    }
    return std::memcmp(reinterpret_cast<const void*>(module.base + offset), expected.data(),
                       expected.size()) == 0;
}

template <typename Fn>
Fn Guarded(const TargetModule& module, std::uintptr_t offset,
           const std::array<u8, 16>& expected) {
    return GuardedCode(module, offset, expected)
               ? reinterpret_cast<Fn>(module.base + offset)
               : nullptr;
}

bool RangeReadable(std::uint64_t address, std::size_t size) noexcept {
    MemoryInfo info{};
    u32 pageInfo = 0;
    if (address > UINTPTR_MAX - size) {
        return false;
    }
    if (R_FAILED(svcQueryMemory(&info, &pageInfo, address)) || info.size == 0) {
        return false;
    }
    return (info.perm & Perm_R) != 0 && address >= info.addr &&
           address + size <= info.addr + info.size;
}

// `ams::fs` 的结果码（module = 2）：0 = 成功。区分"现在读不到这个文件"与其他失败很重要 ——
// 前者对开关状态来说是正常情况（第一次运行），后者要如实报错。
constexpr std::uint32_t kResultPathNotFound = 0x00000202;   // 1
constexpr std::uint32_t kResultTargetNotFound = 0x0002EE02; // 1002
constexpr std::uint32_t kResultNotMounted = 0x0035F202;     // 6905
// `PathAlreadyExists` = 2：`CreateFile` 在文件已存在时返回它，对"覆盖写"来说不是错误。
constexpr std::uint32_t kResultPathAlreadyExists = 0x00000402;

Status StatusFromResult(std::uint32_t result, bool missingIsNotFound) noexcept {
    if (result == 0) {
        return Status::Ok();
    }
    if (missingIsNotFound && (result == kResultPathNotFound || result == kResultTargetNotFound ||
                              result == kResultNotMounted)) {
        return Status{StatusCode::NotFound};
    }
    return Status{StatusCode::IoFailure};
}

} // namespace

// 挂载点只在第一次用到时解析：`GetMountPoint()` 是个返回字面量的小函数（忽略 `this`，
// 见 `runtime_constants.hpp` 的证据），但**返回值指向游戏模块的只读段**，所以解引用前
// 先用内核问一次"这一页可读吗"。
bool EngineSaveFileAdapter::ResolveMount(const Calls& calls, std::array<char, 32>& mountPoint,
                                        std::array<char, 32>& mountName) noexcept {
    const char* point = calls.getMountPoint(nullptr);
    if (point == nullptr ||
        !RangeReadable(reinterpret_cast<std::uint64_t>(point), mountName.size())) {
        return false;
    }
    std::size_t length = 0;
    while (point[length] != '\0' && length + 1 < mountPoint.size()) {
        mountPoint[length] = point[length];
        ++length;
    }
    mountPoint[length] = '\0';
    // 挂载名 = 截到 ':' 之前（与游戏 `SaveDataManager::Initialize` 的算法一致）。
    std::size_t nameLength = 0;
    while (mountPoint[nameLength] != '\0' && mountPoint[nameLength] != ':' &&
           nameLength + 1 < mountName.size()) {
        mountName[nameLength] = mountPoint[nameLength];
        ++nameLength;
    }
    mountName[nameLength] = '\0';
    return length != 0 && nameLength != 0;
}

alignas(0x1000) char EngineSaveFileAdapter::buffer_[EngineSaveFileAdapter::kMaximumFileBytes];

const EngineSaveFileAdapter::Calls* EngineSaveFileAdapter::Resolve() noexcept {
    static Calls calls{};
    static bool resolved = false;
    if (resolved) {
        return mountValid_ ? &calls : nullptr;
    }
    resolved = true;
    const std::optional<TargetModule> module = FindTargetModule();
    if (!module.has_value()) {
        return nullptr;
    }
    calls.openFile = Guarded<decltype(calls.openFile)>(*module, kFilesysOpenFileThunkOffset,
                                                      kFilesysOpenFileThunkExpectedBytes);
    calls.createFile = Guarded<decltype(calls.createFile)>(*module, kFilesysCreateFileThunkOffset,
                                                           kFilesysCreateFileThunkExpectedBytes);
    calls.writeFile = Guarded<decltype(calls.writeFile)>(*module, kFilesysWriteFileThunkOffset,
                                                         kFilesysWriteFileThunkExpectedBytes);
    calls.readFile = Guarded<decltype(calls.readFile)>(*module, kFilesysReadFileThunkOffset,
                                                       kFilesysReadFileThunkExpectedBytes);
    calls.setFileSize = Guarded<decltype(calls.setFileSize)>(*module,
                                                             kFilesysSetFileSizeThunkOffset,
                                                             kFilesysSetFileSizeThunkExpectedBytes);
    calls.flushFile = Guarded<decltype(calls.flushFile)>(*module, kFilesysFlushFileThunkOffset,
                                                         kFilesysFlushFileThunkExpectedBytes);
    calls.getFileSize = Guarded<decltype(calls.getFileSize)>(*module,
                                                             kFilesysGetFileSizeThunkOffset,
                                                             kFilesysGetFileSizeThunkExpectedBytes);
    calls.closeFile = Guarded<decltype(calls.closeFile)>(*module, kFilesysCloseFileThunkOffset,
                                                         kFilesysCloseFileThunkExpectedBytes);
    calls.deleteFile = Guarded<decltype(calls.deleteFile)>(*module, kFilesysDeleteFileThunkOffset,
                                                           kFilesysDeleteFileThunkExpectedBytes);
    calls.commitSaveData = Guarded<decltype(calls.commitSaveData)>(
        *module, kFilesysCommitSaveDataThunkOffset, kFilesysCommitSaveDataThunkExpectedBytes);
    calls.getMountPoint = Guarded<decltype(calls.getMountPoint)>(
        *module, kSaveDataManagerGetMountPointOffset, kSaveDataManagerGetMountPointExpectedBytes);
    calls.valid = calls.openFile != nullptr && calls.createFile != nullptr &&
                  calls.writeFile != nullptr && calls.readFile != nullptr &&
                  calls.setFileSize != nullptr && calls.flushFile != nullptr &&
                  calls.getFileSize != nullptr && calls.closeFile != nullptr &&
                  calls.deleteFile != nullptr && calls.commitSaveData != nullptr &&
                  calls.getMountPoint != nullptr;
    mountResolved_ = true;
    mountValid_ = calls.valid && ResolveMount(calls, mountPoint_, mountName_);
    return mountValid_ ? &calls : nullptr;
}

bool EngineSaveFileAdapter::BuildRelativePath(std::string_view name, char* out,
                                              std::size_t capacity) const noexcept {
    if (name.empty() || capacity == 0 || !mountValid_) {
        return false;
    }
    const std::size_t mountLength = std::strlen(mountPoint_.data());
    if (mountLength + name.size() + 1 > capacity) {
        return false;
    }
    std::memcpy(out, mountPoint_.data(), mountLength);
    std::memcpy(out + mountLength, name.data(), name.size());
    out[mountLength + name.size()] = '\0';
    return true;
}

Status EngineSaveFileAdapter::Read(std::string_view name, char* out, std::size_t capacity,
                                   std::size_t* size) noexcept {
    if (out == nullptr || size == nullptr || capacity == 0 || capacity > kMaximumFileBytes) {
        return Status{StatusCode::InvalidArgument};
    }
    *size = 0;
    const Calls* calls = Resolve();
    if (calls == nullptr) {
        return Status{StatusCode::Unsupported};
    }
    char path[kPathCapacity] = {};
    if (!BuildRelativePath(name, path, sizeof(path))) {
        return Status{StatusCode::InvalidArgument};
    }
    std::uint64_t handle = 0;
    const Status opened = StatusFromResult(calls->openFile(&handle, path, kVfsOpenModeRead), true);
    if (!opened.ok()) {
        return opened;
    }
    std::int64_t fileSize = 0;
    if (const std::uint32_t result = calls->getFileSize(&fileSize, handle); result != 0) {
        (void)calls->closeFile(handle);
        return StatusFromResult(result, false);
    }
    if (fileSize < 0 || static_cast<std::uint64_t>(fileSize) > capacity) {
        (void)calls->closeFile(handle);
        return Status{StatusCode::CapacityExceeded};
    }
    std::uint64_t readSize = 0;
    const std::uint32_t result = calls->readFile(&readSize, handle, 0, buffer_,
                                                 static_cast<std::size_t>(fileSize));
    (void)calls->closeFile(handle);
    if (result != 0) {
        return StatusFromResult(result, false);
    }
    if (readSize > capacity) {
        return Status{StatusCode::CapacityExceeded};
    }
    std::memcpy(out, buffer_, static_cast<std::size_t>(readSize));
    *size = static_cast<std::size_t>(readSize);
    return Status::Ok();
}

Status EngineSaveFileAdapter::Write(std::string_view name, std::string_view data) noexcept {
    if (name.empty()) {
        return Status{StatusCode::InvalidArgument};
    }
    if (data.size() > kMaximumFileBytes) {
        return Status{StatusCode::CapacityExceeded};
    }
    const Calls* calls = Resolve();
    if (calls == nullptr) {
        return Status{StatusCode::Unsupported};
    }
    char path[kPathCapacity] = {};
    if (!BuildRelativePath(name, path, sizeof(path))) {
        return Status{StatusCode::InvalidArgument};
    }
    // 先把内容搬进对齐缓冲：写与读共用这一个缓冲，所以**写之前必须拷完**
    // （上一次读的内容还在里面）。
    std::memcpy(buffer_, data.data(), data.size());

    const std::uint32_t created = calls->createFile(path, static_cast<std::int64_t>(data.size()));
    if (created != 0 && created != kResultPathAlreadyExists) {
        return StatusFromResult(created, false);
    }
    std::uint64_t handle = 0;
    const std::uint32_t opened = calls->openFile(&handle, path, kVfsOpenModeWrite);
    if (opened != 0) {
        return StatusFromResult(opened, false);
    }
    // 与引擎自己的调用点同形：`x4` 指向一个清零的 4 字节 `WriteOption`（不是空指针）。
    const std::uint32_t writeOption = 0;
    Status status =
        StatusFromResult(calls->writeFile(handle, 0, buffer_, data.size(), &writeOption), false);
    if (status.ok()) {
        // 引擎自己写完也这么干（`write_stream_data` 的尾部）：把长度设成写入位置。
        status = StatusFromResult(calls->setFileSize(handle, static_cast<std::int64_t>(data.size())),
                                  false);
    }
    if (status.ok()) {
        status = StatusFromResult(calls->flushFile(handle), false);
    }
    (void)calls->closeFile(handle);
    if (!status.ok()) {
        return status;
    }
    // 存档是"提交式"的：写完不提交，重启后可能什么都看不到。
    return StatusFromResult(calls->commitSaveData(mountName_.data()), false);
}

Status EngineSaveFileAdapter::Remove(std::string_view name) noexcept {
    const Calls* calls = Resolve();
    if (calls == nullptr) {
        return Status{StatusCode::Unsupported};
    }
    char path[kPathCapacity] = {};
    if (!BuildRelativePath(name, path, sizeof(path))) {
        return Status{StatusCode::InvalidArgument};
    }
    const Status removed = StatusFromResult(calls->deleteFile(path), true);
    if (!removed.ok()) {
        return removed;
    }
    return StatusFromResult(calls->commitSaveData(mountName_.data()), false);
}

Status EngineSaveFileAdapter::Exists(const char* absolutePath, bool* exists) noexcept {
    if (absolutePath == nullptr || exists == nullptr) {
        return Status{StatusCode::InvalidArgument};
    }
    *exists = false;
    const Calls* calls = Resolve();
    if (calls == nullptr) {
        return Status{StatusCode::Unsupported};
    }
    std::uint64_t handle = 0;
    const std::uint32_t opened = calls->openFile(&handle, absolutePath, kVfsOpenModeRead);
    if (opened != 0) {
        return StatusFromResult(opened, true);
    }
    (void)calls->closeFile(handle);
    *exists = true;
    return Status::Ok();
}

} // namespace isaac::runtime
