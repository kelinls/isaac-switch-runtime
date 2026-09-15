#pragma once

#include <cstdint>

namespace IsaacRepentance {
struct Game;
struct Level;
}

enum class GameOwnerObservation : std::uint32_t {
    OwnerNull,
    OwnerUnreadable,
    GameNull,
    GameUnreadable,
    Success,
};

enum class GameIsPausedObservation : std::uint32_t {
    OwnerNull,
    OwnerUnreadable,
    GameNull,
    GameUnreadable,
    ThunkUnavailable,
    PausedFalse,
    PausedTrue,
};

enum class GameLevelStageObservation : std::uint32_t {
    OwnerNull,
    OwnerUnreadable,
    GameNull,
    GameUnreadable,
    Success,
};

enum class GameItemPoolObservation : std::uint32_t {
    OwnerNull,
    OwnerUnreadable,
    GameNull,
    GameUnreadable,
    ItemPoolUnreadable,
    Success,
};

enum class GameRoomObservation : std::uint32_t {
    OwnerNull,
    OwnerUnreadable,
    GameNull,
    GameUnreadable,
    RoomNull,
    RoomUnreadable,
    Success,
};

struct GameRoomKey {
    std::uint32_t roomIndex;
    std::uint32_t dimension;

    constexpr bool operator==(const GameRoomKey& other) const {
        return roomIndex == other.roomIndex && dimension == other.dimension;
    }
};

struct GameRoomDescriptorSnapshot {
    std::uint32_t candidateWord0c;
    std::uint32_t candidateWord50;

    constexpr bool operator==(const GameRoomDescriptorSnapshot& other) const {
        return candidateWord0c == other.candidateWord0c && candidateWord50 == other.candidateWord50;
    }
};

enum class GameIsGreedModeObservation : std::uint32_t {
    OwnerNull,
    OwnerUnreadable,
    GameNull,
    GameUnreadable,
    MethodUnavailable,
    GreedFalse,
    GreedTrue,
};

enum class GameIsAscentObservation : std::uint32_t {
    OwnerNull,
    OwnerUnreadable,
    GameNull,
    GameUnreadable,
    MethodUnavailable,
    AscentFalse,
    AscentTrue,
};

GameOwnerObservation ObserveGameOwnerChain(uintptr_t ownerSlot);
GameOwnerObservation DeepestGameOwnerObservation();
GameIsPausedObservation ObserveGameIsPaused(uintptr_t ownerSlot, uintptr_t isPausedAddress);
GameLevelStageObservation ReadCurrentGameLevelStage(uintptr_t ownerSlot, std::uint32_t* stage);
GameItemPoolObservation ReadCurrentGameItemPool(uintptr_t ownerSlot, void** itemPool);
GameRoomObservation ReadCurrentGameRoom(uintptr_t ownerSlot, void** room);
GameRoomObservation ReadCurrentGameRoomType(uintptr_t ownerSlot, std::uint32_t* roomType);
GameRoomObservation ReadGameRoomTypeFromGame(const IsaacRepentance::Game* game, std::uint32_t* roomType);
GameRoomObservation ReadGameCurrentRoomKeyFromGame(const IsaacRepentance::Game* game, GameRoomKey* key);
GameRoomObservation ReadGameRoomDescriptorSnapshotFromGame(
    const IsaacRepentance::Game* game, GameRoomDescriptorSnapshot* snapshot);
GameIsGreedModeObservation ObserveGameIsGreedMode(uintptr_t ownerSlot, uintptr_t methodAddress);
GameIsAscentObservation ObserveLevelIsAscent(uintptr_t ownerSlot, uintptr_t methodAddress);

void ObserveGameFrame(IsaacRepentance::Game* game);
bool HasObservedGameFrame();
void ObserveGameUpdateFrame(IsaacRepentance::Game* game);
bool HasEnteredGameUpdateObserverRelay();
bool HasObservedGameUpdateFrame();
void ObserveGameState2Frame(IsaacRepentance::Game* game);
bool HasEnteredGameState2ObserverRelay();
bool HasObservedGameState2Frame();
void ObserveGameRenderFrame(IsaacRepentance::Game* game);
bool HasEnteredGameRenderObserverRelay();
bool HasObservedGameRenderFrame();
