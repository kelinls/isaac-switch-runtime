// 宿主 harness 的**共享弱默认桩**（地基一期，2026-09-15）。
//
// 为什么要它：`game_observer.hpp` 里的这些入口在宿主上没有实现，每个 hand-written harness
// 过去都要自己抄一份同样的桩。后果是"运行时**新增**一个观测函数"会让所有 harness 同时
// 链接失败（2026-09-15 实测：本来可以给 `Level` 家族加观测函数，最后只能绕开写）。
//
// 做法：这里的默认定义全部标 `__attribute__((weak))`，并且**被加入
// `test_support.layered_lua_runtime_sources()`** —— 也就是说每个 harness 都会自动带上它们。
// 测试自己的 TU 里再定义同名函数就是强符号，链接器优先用强符号：
//   * 老 harness 不动一行也照旧工作（它们自己的定义是强符号，覆盖这里）；
//   * 新增观测函数只需在这里补一个默认，**不需要动任何测试文件**；
//   * 默认行为一律是"读不到"，对应的都是各 handler 已有的降级分支。
//
// 签名必须与头文件逐字一致；`ReadTextFile` 那条还带与头文件相同的条件编译守卫。

#include "game_observer.hpp"
#include "game_file_reader.hpp"

#include <cstddef>
#include <cstdint>

// 弱符号：测试自己的 TU 里定义同名函数即可覆盖（强符号优先）。
#define ISAAC_TEST_WEAK __attribute__((weak))

namespace {

// 宿主上没有引擎，默认一律"读不到"。参数不命名以免 `-Wunused-parameter`。
constexpr std::uintptr_t kUnused = 0;
static_assert(kUnused == 0, "");

}  // namespace

ISAAC_TEST_WEAK GameIsPausedObservation ObserveGameIsPaused(uintptr_t, uintptr_t) {
    return GameIsPausedObservation::OwnerUnreadable;
}
ISAAC_TEST_WEAK GameLevelStageObservation ReadCurrentGameLevelStage(uintptr_t, std::uint32_t*) {
    return GameLevelStageObservation::GameUnreadable;
}
ISAAC_TEST_WEAK GameItemPoolObservation ReadCurrentGameItemPool(uintptr_t, void**) {
    return GameItemPoolObservation::GameUnreadable;
}
ISAAC_TEST_WEAK GameRoomObservation ReadCurrentGameRoom(uintptr_t, void**) {
    return GameRoomObservation::RoomUnreadable;
}
ISAAC_TEST_WEAK GameRoomObservation ReadCurrentGameRoomType(uintptr_t, std::uint32_t*) {
    return GameRoomObservation::RoomUnreadable;
}
ISAAC_TEST_WEAK GameIsGreedModeObservation ObserveGameIsGreedMode(uintptr_t, uintptr_t) {
    return GameIsGreedModeObservation::MethodUnavailable;
}
ISAAC_TEST_WEAK GameIsAscentObservation ObserveLevelIsAscent(uintptr_t, uintptr_t) {
    return GameIsAscentObservation::MethodUnavailable;
}
ISAAC_TEST_WEAK GameOwnerObservation ObserveGameOwnerChain(uintptr_t) {
    return GameOwnerObservation::OwnerUnreadable;
}
ISAAC_TEST_WEAK GameOwnerObservation DeepestGameOwnerObservation() {
    return GameOwnerObservation::OwnerUnreadable;
}
ISAAC_TEST_WEAK GameRoomObservation ReadGameRoomTypeFromGame(const IsaacRepentance::Game*,
                                                             std::uint32_t*) {
    return GameRoomObservation::GameUnreadable;
}
ISAAC_TEST_WEAK GameRoomObservation ReadGameCurrentRoomKeyFromGame(const IsaacRepentance::Game*,
                                                                   GameRoomKey*) {
    return GameRoomObservation::GameUnreadable;
}
ISAAC_TEST_WEAK GameRoomObservation ReadGameRoomDescriptorSnapshotFromGame(
    const IsaacRepentance::Game*, GameRoomDescriptorSnapshot*) {
    return GameRoomObservation::GameUnreadable;
}
ISAAC_TEST_WEAK void ObserveGameFrame(IsaacRepentance::Game*) {}
ISAAC_TEST_WEAK void ObserveGameUpdateFrame(IsaacRepentance::Game*) {}
ISAAC_TEST_WEAK void ObserveGameState2Frame(IsaacRepentance::Game*) {}
ISAAC_TEST_WEAK void ObserveGameRenderFrame(IsaacRepentance::Game*) {}

// `require` 链要用的文件读取：默认"打不开"，需要真读文件的测试自己覆盖。
// **守卫必须与 `game_file_reader.hpp` 里那条声明逐字一致**：`ReadTextFile` 只在
// "非诊断构建或 stage13" 下声明，别的构建里连类型都不存在（照抄签名会编译不过）。
#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 13
ISAAC_TEST_WEAK GameFileReader::TextReadResult GameFileReader::ReadTextFile(
    const GameFileReader::Bindings&, const char*, std::uint8_t*, std::size_t, std::size_t*) {
    return GameFileReader::TextReadResult::OpenFailed;
}
#endif

