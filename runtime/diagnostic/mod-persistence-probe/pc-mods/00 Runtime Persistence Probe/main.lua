local mod = RegisterMod("Runtime Persistence Probe", 1)
local completed = false

mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function()
    if completed then
        return
    end
    completed = true

    if not mod:HasData() then
        mod:SaveData("first")
        return
    end

    local value = mod:LoadData()
    if value == "first" then
        mod:SaveData("second")
    elseif value == "second" then
        mod:RemoveData()
        if mod:HasData() or mod:LoadData() ~= "" then
            error("RemoveData did not clear Mod data")
        end
    else
        error("unexpected persisted Mod data")
    end
end)
