#include "game_observer.hpp"

#include <atomic>

#include "lib/nx/nx.h"

#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC optimize ("Os")
#endif

namespace {
std::atomic<u32> g_GameObserved{false};
std::atomic<u32> g_GameUpdateObserverEntered{false};
std::atomic<u32> g_GameUpdateObserved{false};
std::atomic<u32> g_GameState2ObserverEntered{false};
std::atomic<u32> g_GameState2Observed{false};
std::atomic<u32> g_GameRenderObserverEntered{false};
std::atomic<u32> g_GameRenderObserved{false};
std::atomic<GameOwnerObservation> g_DeepestGameOwnerObservation{
    GameOwnerObservation::OwnerNull};
constexpr size_t kGameIsPausedThunkWindowSize = 16;
constexpr uintptr_t kGameItemPoolOffset = 0x242C0;
constexpr uintptr_t kLevelCurrentRoomOffset = 0x21550;
constexpr uintptr_t kLevelCurrentRoomIndexOffset = 0x21558;
constexpr uintptr_t kLevelCurrentRoomDimensionOffset = 0x21560;
constexpr uintptr_t kRoomTypeOffset = 0x10;
constexpr uintptr_t kRoomDescriptorOffset = 0x8;
constexpr uintptr_t kDescriptorCandidateWord0cOffset = 0xc;
constexpr uintptr_t kDescriptorCandidateWord50Offset = 0x50;

bool IsReadableNonExecutableMapping(uintptr_t address, size_t length) {
    if (length == 0 || address > UINTPTR_MAX - length) return false;
    MemoryInfo info{};
    u32 pageInfo = 0;
    if (R_FAILED(svcQueryMemory(&info, &pageInfo, address)) || info.size == 0 ||
        info.addr > UINTPTR_MAX - info.size) {
        return false;
    }
    const uintptr_t end = address + length;
    return address >= info.addr && end <= info.addr + info.size &&
           (info.perm & Perm_R) != 0 && (info.perm & Perm_X) == 0;
}

bool IsMappedRxModuleCodeWindow(uintptr_t address, size_t length) {
    if (length == 0 || address > UINTPTR_MAX - length) return false;
    MemoryInfo info{};
    u32 pageInfo = 0;
    if (R_FAILED(svcQueryMemory(&info, &pageInfo, address)) || info.size == 0 ||
        info.addr > UINTPTR_MAX - info.size) {
        return false;
    }
    const uintptr_t end = address + length;
    return address >= info.addr && end <= info.addr + info.size &&
           (info.type & MemState_Type) == MemType_ModuleCodeStatic && info.perm == Perm_Rx;
}

void RecordGameOwnerObservation(GameOwnerObservation observation) {
    auto current = g_DeepestGameOwnerObservation.load(std::memory_order_relaxed);
    while (current < observation &&
           !g_DeepestGameOwnerObservation.compare_exchange_weak(
               current, observation, std::memory_order_release, std::memory_order_relaxed)) {}
}
}

GameOwnerObservation ObserveGameOwnerChain(uintptr_t ownerSlot) {
    if (ownerSlot == 0) {
        RecordGameOwnerObservation(GameOwnerObservation::OwnerUnreadable);
        return GameOwnerObservation::OwnerUnreadable;
    }
    if ((ownerSlot & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(ownerSlot, sizeof(uintptr_t))) {
        RecordGameOwnerObservation(GameOwnerObservation::OwnerUnreadable);
        return GameOwnerObservation::OwnerUnreadable;
    }
    const uintptr_t owner = *reinterpret_cast<const uintptr_t*>(ownerSlot);
    if (owner == 0) {
        RecordGameOwnerObservation(GameOwnerObservation::OwnerNull);
        return GameOwnerObservation::OwnerNull;
    }
    if ((owner & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(owner, sizeof(uintptr_t))) {
        RecordGameOwnerObservation(GameOwnerObservation::OwnerUnreadable);
        return GameOwnerObservation::OwnerUnreadable;
    }
    const uintptr_t game = *reinterpret_cast<const uintptr_t*>(owner);
    if (game == 0) {
        RecordGameOwnerObservation(GameOwnerObservation::GameNull);
        return GameOwnerObservation::GameNull;
    }
    if ((game & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(game, sizeof(uintptr_t))) {
        RecordGameOwnerObservation(GameOwnerObservation::GameUnreadable);
        return GameOwnerObservation::GameUnreadable;
    }
    RecordGameOwnerObservation(GameOwnerObservation::Success);
    return GameOwnerObservation::Success;
}

GameOwnerObservation DeepestGameOwnerObservation() {
    return g_DeepestGameOwnerObservation.load(std::memory_order_acquire);
}

GameIsPausedObservation ObserveGameIsPaused(uintptr_t ownerSlot, uintptr_t isPausedAddress) {
    if (isPausedAddress == 0 || (isPausedAddress & 3) != 0 ||
        !IsMappedRxModuleCodeWindow(isPausedAddress, kGameIsPausedThunkWindowSize)) {
        return GameIsPausedObservation::ThunkUnavailable;
    }
    if (ownerSlot == 0 || (ownerSlot & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(ownerSlot, sizeof(uintptr_t))) {
        RecordGameOwnerObservation(GameOwnerObservation::OwnerUnreadable);
        return GameIsPausedObservation::OwnerUnreadable;
    }
    const uintptr_t owner = *reinterpret_cast<const uintptr_t*>(ownerSlot);
    if (owner == 0) {
        RecordGameOwnerObservation(GameOwnerObservation::OwnerNull);
        return GameIsPausedObservation::OwnerNull;
    }
    if ((owner & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(owner, sizeof(uintptr_t))) {
        RecordGameOwnerObservation(GameOwnerObservation::OwnerUnreadable);
        return GameIsPausedObservation::OwnerUnreadable;
    }
    const uintptr_t gameAddress = *reinterpret_cast<const uintptr_t*>(owner);
    if (gameAddress == 0) {
        RecordGameOwnerObservation(GameOwnerObservation::GameNull);
        return GameIsPausedObservation::GameNull;
    }
    if ((gameAddress & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(gameAddress, sizeof(uintptr_t))) {
        RecordGameOwnerObservation(GameOwnerObservation::GameUnreadable);
        return GameIsPausedObservation::GameUnreadable;
    }

    using GameIsPausedFunction = bool (*)(const IsaacRepentance::Game*);
    const auto* game = reinterpret_cast<const IsaacRepentance::Game*>(gameAddress);
    const auto isPaused = reinterpret_cast<GameIsPausedFunction>(isPausedAddress);
    RecordGameOwnerObservation(GameOwnerObservation::Success);
    return isPaused(game) ? GameIsPausedObservation::PausedTrue
                          : GameIsPausedObservation::PausedFalse;
}

GameLevelStageObservation ReadCurrentGameLevelStage(uintptr_t ownerSlot, std::uint32_t* stage) {
    if (stage == nullptr || ownerSlot == 0 ||
        (ownerSlot & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(ownerSlot, sizeof(uintptr_t))) {
        return GameLevelStageObservation::OwnerUnreadable;
    }
    const uintptr_t owner = *reinterpret_cast<const uintptr_t*>(ownerSlot);
    if (owner == 0) return GameLevelStageObservation::OwnerNull;
    if ((owner & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(owner, sizeof(uintptr_t))) {
        return GameLevelStageObservation::OwnerUnreadable;
    }
    const uintptr_t game = *reinterpret_cast<const uintptr_t*>(owner);
    if (game == 0) return GameLevelStageObservation::GameNull;
    if ((game & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(game, sizeof(std::uint32_t))) {
        return GameLevelStageObservation::GameUnreadable;
    }
    *stage = *reinterpret_cast<const std::uint32_t*>(game);
    return GameLevelStageObservation::Success;
}

GameItemPoolObservation ReadCurrentGameItemPool(uintptr_t ownerSlot, void** itemPool) {
    if (itemPool == nullptr || ownerSlot == 0 ||
        (ownerSlot & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(ownerSlot, sizeof(uintptr_t))) {
        return GameItemPoolObservation::OwnerUnreadable;
    }
    const uintptr_t owner = *reinterpret_cast<const uintptr_t*>(ownerSlot);
    if (owner == 0) return GameItemPoolObservation::OwnerNull;
    if ((owner & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(owner, sizeof(uintptr_t))) {
        return GameItemPoolObservation::OwnerUnreadable;
    }
    const uintptr_t game = *reinterpret_cast<const uintptr_t*>(owner);
    if (game == 0) return GameItemPoolObservation::GameNull;
    if ((game & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(game, sizeof(uintptr_t))) {
        return GameItemPoolObservation::GameUnreadable;
    }
    if (game > UINTPTR_MAX - kGameItemPoolOffset) {
        return GameItemPoolObservation::ItemPoolUnreadable;
    }
    const uintptr_t candidate = game + kGameItemPoolOffset;
    if (!IsReadableNonExecutableMapping(candidate, sizeof(uintptr_t))) {
        return GameItemPoolObservation::ItemPoolUnreadable;
    }
    *itemPool = reinterpret_cast<void*>(candidate);
    return GameItemPoolObservation::Success;
}

GameRoomObservation ReadCurrentGameRoom(uintptr_t ownerSlot, void** room) {
    if (room == nullptr || ownerSlot == 0 ||
        (ownerSlot & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(ownerSlot, sizeof(uintptr_t))) {
        return GameRoomObservation::OwnerUnreadable;
    }
    const uintptr_t owner = *reinterpret_cast<const uintptr_t*>(ownerSlot);
    if (owner == 0) return GameRoomObservation::OwnerNull;
    if ((owner & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(owner, sizeof(uintptr_t))) {
        return GameRoomObservation::OwnerUnreadable;
    }
    const uintptr_t game = *reinterpret_cast<const uintptr_t*>(owner);
    if (game == 0) return GameRoomObservation::GameNull;
    if ((game & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(game, sizeof(uintptr_t)) ||
        game > UINTPTR_MAX - kLevelCurrentRoomOffset) {
        return GameRoomObservation::GameUnreadable;
    }
    const uintptr_t roomSlot = game + kLevelCurrentRoomOffset;
    if ((roomSlot & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(roomSlot, sizeof(uintptr_t))) {
        return GameRoomObservation::RoomUnreadable;
    }
    const uintptr_t currentRoom = *reinterpret_cast<const uintptr_t*>(roomSlot);
    if (currentRoom == 0) return GameRoomObservation::RoomNull;
    if ((currentRoom & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(currentRoom, sizeof(uintptr_t))) {
        return GameRoomObservation::RoomUnreadable;
    }
    *room = reinterpret_cast<void*>(currentRoom);
    return GameRoomObservation::Success;
}

GameRoomObservation ReadCurrentGameRoomType(uintptr_t ownerSlot, std::uint32_t* roomType) {
    if (roomType == nullptr) return GameRoomObservation::RoomUnreadable;
    void* room = nullptr;
    const GameRoomObservation result = ReadCurrentGameRoom(ownerSlot, &room);
    if (result != GameRoomObservation::Success) return result;
    const uintptr_t address = reinterpret_cast<uintptr_t>(room);
    if (address > UINTPTR_MAX - kRoomTypeOffset ||
        !IsReadableNonExecutableMapping(address + kRoomTypeOffset, sizeof(std::uint32_t))) {
        return GameRoomObservation::RoomUnreadable;
    }
    *roomType = *reinterpret_cast<const std::uint32_t*>(address + kRoomTypeOffset);
    return GameRoomObservation::Success;
}

GameRoomObservation ReadGameRoomTypeFromGame(const IsaacRepentance::Game* game, std::uint32_t* roomType) {
    if (game == nullptr || roomType == nullptr) return GameRoomObservation::GameNull;
    const uintptr_t gameAddress = reinterpret_cast<uintptr_t>(game);
    if ((gameAddress & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(gameAddress, sizeof(uintptr_t)) ||
        gameAddress > UINTPTR_MAX - kLevelCurrentRoomOffset) {
        return GameRoomObservation::GameUnreadable;
    }
    const uintptr_t roomSlot = gameAddress + kLevelCurrentRoomOffset;
    if ((roomSlot & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(roomSlot, sizeof(uintptr_t))) {
        return GameRoomObservation::RoomUnreadable;
    }
    const uintptr_t room = *reinterpret_cast<const uintptr_t*>(roomSlot);
    if (room == 0) return GameRoomObservation::RoomNull;
    if ((room & (alignof(uintptr_t) - 1)) != 0 ||
        room > UINTPTR_MAX - kRoomTypeOffset ||
        !IsReadableNonExecutableMapping(room + kRoomTypeOffset, sizeof(std::uint32_t))) {
        return GameRoomObservation::RoomUnreadable;
    }
    *roomType = *reinterpret_cast<const std::uint32_t*>(room + kRoomTypeOffset);
    return GameRoomObservation::Success;
}

GameRoomObservation ReadGameCurrentRoomKeyFromGame(const IsaacRepentance::Game* game, GameRoomKey* key) {
    if (game == nullptr || key == nullptr) return GameRoomObservation::GameNull;
    const uintptr_t gameAddress = reinterpret_cast<uintptr_t>(game);
    if ((gameAddress & (alignof(std::uint32_t) - 1)) != 0 ||
        gameAddress > UINTPTR_MAX - kLevelCurrentRoomDimensionOffset ||
        !IsReadableNonExecutableMapping(gameAddress + kLevelCurrentRoomIndexOffset, sizeof(std::uint32_t)) ||
        !IsReadableNonExecutableMapping(gameAddress + kLevelCurrentRoomDimensionOffset, sizeof(std::uint32_t))) {
        return GameRoomObservation::GameUnreadable;
    }
    key->roomIndex = *reinterpret_cast<const std::uint32_t*>(gameAddress + kLevelCurrentRoomIndexOffset);
    key->dimension = *reinterpret_cast<const std::uint32_t*>(gameAddress + kLevelCurrentRoomDimensionOffset);
    return GameRoomObservation::Success;
}

GameRoomObservation ReadGameRoomDescriptorSnapshotFromGame(
    const IsaacRepentance::Game* game, GameRoomDescriptorSnapshot* snapshot) {
    if (game == nullptr || snapshot == nullptr) return GameRoomObservation::GameNull;
    const uintptr_t gameAddress = reinterpret_cast<uintptr_t>(game);
    if ((gameAddress & (alignof(uintptr_t) - 1)) != 0 ||
        gameAddress > UINTPTR_MAX - kLevelCurrentRoomOffset ||
        !IsReadableNonExecutableMapping(gameAddress + kLevelCurrentRoomOffset, sizeof(uintptr_t))) {
        return GameRoomObservation::GameUnreadable;
    }
    const uintptr_t room = *reinterpret_cast<const uintptr_t*>(gameAddress + kLevelCurrentRoomOffset);
    if (room == 0) return GameRoomObservation::RoomNull;
    if ((room & (alignof(uintptr_t) - 1)) != 0 || room > UINTPTR_MAX - kRoomDescriptorOffset ||
        !IsReadableNonExecutableMapping(room + kRoomDescriptorOffset, sizeof(uintptr_t))) {
        return GameRoomObservation::RoomUnreadable;
    }
    const uintptr_t descriptor = *reinterpret_cast<const uintptr_t*>(room + kRoomDescriptorOffset);
    if (descriptor == 0) return GameRoomObservation::RoomNull;
    if ((descriptor & (alignof(std::uint32_t) - 1)) != 0 ||
        descriptor > UINTPTR_MAX - kDescriptorCandidateWord50Offset ||
        !IsReadableNonExecutableMapping(descriptor + kDescriptorCandidateWord0cOffset, sizeof(std::uint32_t)) ||
        !IsReadableNonExecutableMapping(descriptor + kDescriptorCandidateWord50Offset, sizeof(std::uint32_t))) {
        return GameRoomObservation::RoomUnreadable;
    }
    snapshot->candidateWord0c = *reinterpret_cast<const std::uint32_t*>(
        descriptor + kDescriptorCandidateWord0cOffset);
    snapshot->candidateWord50 = *reinterpret_cast<const std::uint32_t*>(
        descriptor + kDescriptorCandidateWord50Offset);
    return GameRoomObservation::Success;
}

GameIsGreedModeObservation ObserveGameIsGreedMode(uintptr_t ownerSlot, uintptr_t methodAddress) {
    if (methodAddress == 0 || (methodAddress & 3) != 0 ||
        !IsMappedRxModuleCodeWindow(methodAddress, kGameIsPausedThunkWindowSize)) {
        return GameIsGreedModeObservation::MethodUnavailable;
    }
    if (ownerSlot == 0 || (ownerSlot & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(ownerSlot, sizeof(uintptr_t))) {
        RecordGameOwnerObservation(GameOwnerObservation::OwnerUnreadable);
        return GameIsGreedModeObservation::OwnerUnreadable;
    }
    const uintptr_t owner = *reinterpret_cast<const uintptr_t*>(ownerSlot);
    if (owner == 0) return GameIsGreedModeObservation::OwnerNull;
    if ((owner & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(owner, sizeof(uintptr_t))) {
        RecordGameOwnerObservation(GameOwnerObservation::OwnerUnreadable);
        return GameIsGreedModeObservation::OwnerUnreadable;
    }
    const uintptr_t gameAddress = *reinterpret_cast<const uintptr_t*>(owner);
    if (gameAddress == 0) return GameIsGreedModeObservation::GameNull;
    if ((gameAddress & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(gameAddress, sizeof(uintptr_t))) {
        RecordGameOwnerObservation(GameOwnerObservation::GameUnreadable);
        return GameIsGreedModeObservation::GameUnreadable;
    }
    using GameIsGreedModeFunction = bool (*)(const IsaacRepentance::Game*);
    const auto method = reinterpret_cast<GameIsGreedModeFunction>(methodAddress);
    const auto* game = reinterpret_cast<const IsaacRepentance::Game*>(gameAddress);
    RecordGameOwnerObservation(GameOwnerObservation::Success);
    return method(game) ? GameIsGreedModeObservation::GreedTrue
                        : GameIsGreedModeObservation::GreedFalse;
}

GameIsAscentObservation ObserveLevelIsAscent(uintptr_t ownerSlot, uintptr_t methodAddress) {
    if (methodAddress == 0 || (methodAddress & 3) != 0 ||
        !IsMappedRxModuleCodeWindow(methodAddress, kGameIsPausedThunkWindowSize)) {
        return GameIsAscentObservation::MethodUnavailable;
    }
    if (ownerSlot == 0 || (ownerSlot & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(ownerSlot, sizeof(uintptr_t))) {
        return GameIsAscentObservation::OwnerUnreadable;
    }
    const uintptr_t owner = *reinterpret_cast<const uintptr_t*>(ownerSlot);
    if (owner == 0) return GameIsAscentObservation::OwnerNull;
    if ((owner & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(owner, sizeof(uintptr_t))) {
        return GameIsAscentObservation::OwnerUnreadable;
    }
    const uintptr_t gameAddress = *reinterpret_cast<const uintptr_t*>(owner);
    if (gameAddress == 0) return GameIsAscentObservation::GameNull;
    if ((gameAddress & (alignof(uintptr_t) - 1)) != 0 ||
        !IsReadableNonExecutableMapping(gameAddress, sizeof(uintptr_t))) {
        return GameIsAscentObservation::GameUnreadable;
    }
    using LevelIsAscentFunction = bool (*)(const IsaacRepentance::Level*);
    const auto method = reinterpret_cast<LevelIsAscentFunction>(methodAddress);
    const auto* level = reinterpret_cast<const IsaacRepentance::Level*>(gameAddress);
    RecordGameOwnerObservation(GameOwnerObservation::Success);
    return method(level) ? GameIsAscentObservation::AscentTrue : GameIsAscentObservation::AscentFalse;
}

void ObserveGameFrame(IsaacRepentance::Game* game) {
    if (game != nullptr) {
        g_GameObserved.store(true, std::memory_order_release);
    }
}

bool HasObservedGameFrame() {
    return g_GameObserved.load(std::memory_order_acquire);
}

void ObserveGameUpdateFrame(IsaacRepentance::Game* game) {
    g_GameUpdateObserverEntered.store(true, std::memory_order_release);
    if (game != nullptr) {
        g_GameUpdateObserved.store(true, std::memory_order_release);
    }
}

bool HasEnteredGameUpdateObserverRelay() {
    return g_GameUpdateObserverEntered.load(std::memory_order_acquire);
}

bool HasObservedGameUpdateFrame() {
    return g_GameUpdateObserved.load(std::memory_order_acquire);
}

void ObserveGameState2Frame(IsaacRepentance::Game* game) {
    g_GameState2ObserverEntered.store(true, std::memory_order_release);
    if (game != nullptr) {
        g_GameState2Observed.store(true, std::memory_order_release);
    }
}

bool HasEnteredGameState2ObserverRelay() {
    return g_GameState2ObserverEntered.load(std::memory_order_acquire);
}

bool HasObservedGameState2Frame() {
    return g_GameState2Observed.load(std::memory_order_acquire);
}

void ObserveGameRenderFrame(IsaacRepentance::Game* game) {
    g_GameRenderObserverEntered.store(true, std::memory_order_release);
    if (game != nullptr) g_GameRenderObserved.store(true, std::memory_order_release);
}

bool HasEnteredGameRenderObserverRelay() {
    return g_GameRenderObserverEntered.load(std::memory_order_acquire);
}

bool HasObservedGameRenderFrame() {
    return g_GameRenderObserved.load(std::memory_order_acquire);
}
