-- Stage149 渲染胶水验收 Mod（v18：Mod 贴图 PNG 桥的真机验收）。
--
-- 这一版验的是本轮新做的"图片加载中继"：Switch 版 `Manager::LoadImage` 把 `.png` 改写成 `.pcx`
-- 才去找文件，而 `ImageManager::LoadImage` **按扩展名**挑解码器。中继在加载器入口做一件事：
-- 请求的 `.pcx` 解析不到、而同名 `.png` 能解析到时，把路径换回 `.png`，让引擎走它自带的 PNG 分支。
--
-- 三路（y=30，x=60/180/300，全部完整可见）：
--   1 pcxbase   `gfx/draw149.anm2`       真 `.pcx` 贴图（基准，应出品红方块）
--   2 realpng   `gfx/realpng149.anm2`    贴图是**真 PNG**、没有 `.pcx` 对应物
--                                        → **中继生效才会画出来**（本轮验收点）
--   3 pngaspcx  `gfx/draw149pngascpx.anm2` 贴图路径 `.png`、磁盘上那个 `.pcx` 里装的是 PNG 字节
--                                        → `.pcx` 确实存在，中继不改写 → 仍解不开（负对照）
--
-- 屏幕编码（红色 `|`）：金丝雀 y=90 x=180 六格全亮（pcall 之外；看不到 = 不是这一版）；
--   A 行 y=105 八格：bit0..2 = 三路已加载，bit6..7 = 出错步骤（1 加载/2 Update/3 Render/4 查询）；
--   B 行 y=120：bit0 = 有 Render 抛错；指纹 y=150 七格 = `STAGE149_ERR` 的 7 位哈希；
--   文字 y=200：`M<mark>`。
--
-- 历史：v8 崩在 `<Anm2>` 根元素；v9..v14 把问题锁到"自带 .png 加载不了"；v15 补 `resources/gfx`
-- 挂载点；v16 补 `.pcx`（Sprite 通过）；v17 证明解码器按扩展名分派 → 本轮做 PNG 桥。
local mod = RegisterMod("Stage149 Draw Test", 1)

local font = nil
local frame = 0
local draws = 0
local updates = 0

-- 五路并列的尝试：谁加载得起来、谁画得出来、谁在播 "Idle"，全部单独记录。
local attempts = {
  { name = "pcxbase",   path = "gfx/draw149.anm2",       x = 60 },
  { name = "realpng",   path = "gfx/realpng149.anm2",    x = 180 },
  { name = "pngaspcx",  path = "gfx/draw149pngascpx.anm2", x = 300 },
}

local errorStep = 0       -- 0 无 / 1 加载 / 2 Update / 3 Render / 4 查询
local errorHash = 0
local queryError = 0
local renderError = 0

local function setMark(value)
  _G["STAGE149_MARK"] = value
  _G["STAGE148_MARK"] = value
end

setMark(1)

local function ensureFont()
  if font ~= nil then
    return true
  end
  local candidate = Font()
  setMark(2)
  if not candidate:Load("font/eid_default.fnt") then
    setMark(3)
    _G["STAGE148_FONT"] = 2
    return false
  end
  setMark(4)
  _G["STAGE148_FONT"] = 1
  _G["STAGE148_WIDTH"] = candidate:GetStringWidth("Stage148")
  font = candidate
  return true
end

-- 只在真正进了房间以后绘制：开局阶段 Game():GetRoom() 还不可用。
local function roomReady()
  local ok, ready = pcall(function()
    return Game():GetRoom() ~= nil
  end)
  return ok and ready == true
end

-- 条形码：位 i 为 1 就在 (x + i*24, y) 画一个红色 `|`。只判"有没有"，字形不重要 —— 这样
-- 崩溃报告里 Atmosphere 抓的那张截图可以机器化判读。
local function drawCode(bits, x, y, slots)
  for index = 0, slots - 1 do
    if math.floor(bits / (2 ^ index)) % 2 == 1 then
      font:DrawString("|", x + index * 24, y, KColor(1, 0, 0, 1), 0, false)
    end
  end
end

-- 错误字符串的 7 位指纹：本机可以用同样的算法把候选错误消息算出来比对。
local function hashError(text)
  local hash = 0
  for index = 1, #text do
    hash = (hash * 31 + text:byte(index)) % 128
  end
  return hash
end

-- 每个冒险步骤各自 pcall：失败只记录，不打断其它步骤，也不影响下一帧的诊断。
local function step(stepId, body)
  local ok, err = pcall(body)
  if not ok then
    errorStep = stepId
    errorHash = hashError(tostring(err))
    _G["STAGE149_ERR"] = tostring(err)
  end
  return ok
end

function mod:draw()
  if not roomReady() then
    return
  end
  if not ensureFont() then
    return
  end
  frame = frame + 1
  draws = draws + 1
  _G["STAGE149_FRAME"] = frame
  _G["STAGE149_DRAWS"] = draws

  -- **金丝雀**：画在 pcall 之外、别的判断之前 —— 只要设备上跑的是这一版，这一排 6 个红竖杠必然
  -- 出现，与"冒险块有没有抛错"无关。看不到它 = 设备上根本不是这一版（先排查有没有真正重启游戏）。
  drawCode(63, 180, 90, 6)

  -- 整个函数体都在 pcall 里：回调里的 Lua 报错会被派发器**静默吞掉并摘除该回调**（本运行时
  -- 与 PC 的已知差异），所以任何一步报错都必须由自己捕获并写进标记。
  local ok, err = pcall(function()
    -- === 诊断编码：全部来自**上一帧**记录的结果，画在任何冒险调用之前 ===
    local rowA = (errorStep % 4) * 64
    local rowB = (renderError == 1 and 1 or 0)
    for index, attempt in ipairs(attempts) do
      if attempt.loaded == 1 then
        rowA = rowA + (2 ^ (index - 1))
      end
    end
    drawCode(rowA, 180, 105, 8)
    drawCode(rowB, 180, 120, 7)
    drawCode(errorHash % 128, 180, 150, 7)
    font:DrawString("M" .. _G["STAGE149_MARK"], 180, 200, KColor(1, 1, 1, 1), 0, false)

    -- === 冒险步骤：五路并列，先"画"后"问"，每步独立 pcall ===
    for index, attempt in ipairs(attempts) do
      if attempt.sprite == nil then
        step(1, function()
          attempt.sprite = Sprite()
        end)
      end
      if attempt.sprite ~= nil and attempt.loaded == nil then
        step(1, function()
          attempt.sprite:Load(attempt.path, true)
          if attempt.sprite:IsLoaded() then
            attempt.loaded = 1
            attempt.sprite:Play("Idle", true)
          else
            attempt.loaded = 2
          end
        end)
      end
      if attempt.loaded == 1 then
        step(2, function()
          attempt.sprite:Update()
        end)
        local drawn = step(3, function()
          attempt.sprite:Render(Vector(attempt.x, 30))
        end)
        attempt.render = drawn and 1 or 2
        if not drawn then
          renderError = 1
        end
        -- 查询放在绘制之后：查询抛错也不能影响"这一帧有没有把方块画出去"
        step(4, function()
          attempt.playingAny = attempt.sprite:IsPlaying() and 1 or 0
        end)
        step(4, function()
          attempt.playingIdle = attempt.sprite:IsPlaying("Idle") and 1 or 0
        end)
        step(4, function()
          attempt.frame = attempt.sprite:GetFrame() or 0
        end)
        if errorStep == 4 then
          queryError = 1
        end
      end
      if index == 1 then
        _G["STAGE149_SPRS"] = attempt.loaded or 0
        _G["STAGE149_SPRP"] = attempt.playingIdle or 0
        _G["STAGE149_SPRF"] = attempt.frame or 0
      end
    end

    _G["STAGE149_CTRL"] = (attempts[2].loaded or 0)
    _G["STAGE149_SPQR"] = queryError

    -- 类表常量（KColor.White 等）也要能取到，取不到就直接报错暴露出来。
    local white = KColor.White
    if math.abs(white.Red - 1) > 0.001 or math.abs(white.Alpha - 1) > 0.001 then
      error('KColor.White is not (1,1,1,1)')
    end
  end)
  if ok then
    setMark(5)
  else
    _G["STAGE149_ERR"] = tostring(err)
    setMark(6)
  end
end

function mod:update()
  updates = updates + 1
  _G["STAGE149_UPDATE"] = updates
end

mod:AddCallback(ModCallbacks.MC_POST_RENDER, mod.draw)
mod:AddCallback(ModCallbacks.MC_POST_UPDATE, mod.update)
