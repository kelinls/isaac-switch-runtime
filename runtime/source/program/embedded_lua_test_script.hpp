#pragma once

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 48
inline constexpr char kEmbeddedLuaTestScript[] = R"lua(
local game = Game()
local music = MusicManager()
local mod = RegisterMod("EmbeddedRuntimeMusicReplayProbe", 1)
local pauseCalled = false
local wasPaused = true
local seenReadyUnpaused = false
mod:AddCallback(ModCallbacks.MC_POST_RENDER, function()
    if pauseCalled then return end
    local isPaused = game:IsPaused()
    if not isPaused then
        local musicId = music:GetCurrentMusicID()
        if type(musicId) ~= "number" then error("Music:GetCurrentMusicID type") end
        if musicId ~= Music.MUSIC_NULL then seenReadyUnpaused = true end
    elseif seenReadyUnpaused and not wasPaused then
        local musicId = music:GetCurrentMusicID()
        if type(musicId) ~= "number" then error("Music:GetCurrentMusicID type") end
        if musicId ~= Music.MUSIC_NULL then
            music:Pause()
            pauseCalled = true
        end
    end
    wasPaused = isPaused
end)
)lua";
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 45
inline constexpr char kEmbeddedLuaTestScript[] = R"lua(
local game = Game()
local music = MusicManager()
local mod = RegisterMod("EmbeddedRuntimeMusicIdProbe", 1)
local reported = false
mod:AddCallback(ModCallbacks.MC_POST_RENDER, function()
    if reported or not game:IsPaused() then return end
    local musicId = music:GetCurrentMusicID()
    if type(musicId) ~= "number" then error("Music:GetCurrentMusicID type") end
    RuntimeTest.MarkMusicDiagnosticCurrentId(musicId)
    reported = true
end)
)lua";
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 17
inline constexpr char kEmbeddedLuaTestScript[] = R"lua(
local game = Game()
local music = MusicManager()
local mod = RegisterMod("EmbeddedRuntimeLongMusicPauseProbe", 1)
local pausedFrames = 0
local pauseCalled = false
local resumeCalled = false
mod:AddCallback(ModCallbacks.MC_POST_RENDER, function()
    if not game:IsPaused() then return end
    pausedFrames = pausedFrames + 1
    if not pauseCalled then
        local musicId = music:GetCurrentMusicID()
        if type(musicId) ~= "number" then error("Music:GetCurrentMusicID type") end
        music:Pause()
        pauseCalled = true
    elseif not resumeCalled and pausedFrames >= 600 then
        music:Resume()
        resumeCalled = true
    elseif resumeCalled and pausedFrames >= 900 then
        RuntimeTest.MarkMusicDiagnosticCycleCompleted()
    end
end)
)lua";
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 16
inline constexpr char kEmbeddedLuaTestScript[] = R"lua(
local game = Game()
local music = MusicManager()
local mod = RegisterMod("EmbeddedRuntimeMusicPauseProbe", 1)
local pausedFrames = 0
local pauseCalled = false
local resumeCalled = false
mod:AddCallback(ModCallbacks.MC_POST_RENDER, function()
    if not game:IsPaused() then return end
    pausedFrames = pausedFrames + 1
    if not pauseCalled then
        local musicId = music:GetCurrentMusicID()
        if type(musicId) ~= "number" then error("Music:GetCurrentMusicID type") end
        music:Pause()
        pauseCalled = true
    elseif not resumeCalled and pausedFrames >= 120 then
        music:Resume()
        resumeCalled = true
    elseif resumeCalled and pausedFrames >= 180 then
        RuntimeTest.MarkMusicDiagnosticCycleCompleted()
    end
end)
)lua";
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 15
inline constexpr char kEmbeddedLuaTestScript[] = R"lua(
local game = Game()
local mod = RegisterMod("EmbeddedRuntimeRenderPauseProbe", 1)
mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function()
    local paused = game:IsPaused()
    if type(paused) ~= "boolean" then error("Game:IsPaused update type") end
    if paused then RuntimeTest.MarkPostUpdate() end
end)
mod:AddCallback(ModCallbacks.MC_POST_RENDER, function()
    local paused = game:IsPaused()
    if type(paused) ~= "boolean" then error("Game:IsPaused render type") end
    RuntimeTest.MarkPostRender()
    if paused then RuntimeTest.MarkPostRenderPaused() end
end)
)lua";
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 14
inline constexpr char kEmbeddedLuaTestScript[] = R"lua(
local game = Game()
local mod = RegisterMod("EmbeddedRuntimeGameProbe", 1)
mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function()
    local paused = game:IsPaused()
    if type(paused) ~= "boolean" then
        RuntimeTest.MarkPostUpdate()
        RuntimeTest.MarkPostUpdate()
        RuntimeTest.MarkPostUpdate()
        error("Game:IsPaused() did not return a boolean")
    end
    RuntimeTest.MarkPostUpdate()
    if paused then
        RuntimeTest.MarkPostUpdate()
    end
end)
)lua";
#else
inline constexpr char kEmbeddedLuaTestScript[] = R"lua(
local mod = RegisterMod("EmbeddedRuntimeTest", 1)
mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function()
    RuntimeTest.MarkPostUpdate()
end)
)lua";
#endif
