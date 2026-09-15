local mod = RegisterMod("RomfsRuntimeProbe", 1)
mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function()
    RuntimeTest.MarkPostUpdate()
end)
