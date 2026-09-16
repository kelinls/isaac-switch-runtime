#include "interfaces/lua/api_deviation.hpp"

namespace isaac::runtime {
namespace {

// 每条都对应实现处的一段注释（"占位/未定位/语义偏差/恒假/常量实现"），这里只做汇总与索引。
// 顺序按 catalog id，方便与 `api_catalog.cpp` 对照。
constexpr ApiDeviation kApiDeviations[] = {
    // ---- `Options`（值型条目）------------------------------------------------
    {0x00020001,
     ApiDeviationKind::Placeholder,
     "PC 的 `Options.HUDOffset` 从引擎选项结构里读；该偏移尚未定位，所以这里固定 1.0"
     "（与 EID 中文默认配置一致）。偏离后果：玩家在游戏里调过 HUD 偏移时，EID 描述的位置"
     "与 PC 不一致。"},

    // ---- `Game` / `Seeds`（字段直读，偏移未定位）------------------------------
    {0x02010008,
     ApiDeviationKind::Placeholder,
     "PC 的 `Game:GetSeeds()` 返回引擎的种子对象；Switch 的 `Game` 类没有 `GetSeeds` 方法，"
     "字段偏移尚未定位，所以这里返回一个值自洽的替代对象。偏离后果：EID 用它做"
     "`IsCustomRun()` 与缓存键判断，拿到的是自洽值而非引擎真值。"},
    {0x02010009,
     ApiDeviationKind::Placeholder,
     "同 `Game:GetSeeds`：PC 是字段直读，偏移未定位 ⇒ 这里返回固定值。"
     "偏离后果：EID 只在判断'胜利后'的少数分支上用到它。"},
    {0x0F010001,
     ApiDeviationKind::Placeholder,
     "`Seeds:IsCustomRun()`（挑战局或带种子的局）在 Switch 上没有可 thunk 的访问器，"
     "字段偏移未定位 ⇒ 返回保守值。偏离后果：带种子/挑战局的少数描述分支可能不生效。"},
    {0x0F010002,
     ApiDeviationKind::Placeholder,
     "`Seeds:GetStartSeed()` 是 EID 的缓存键，偏移未定位 ⇒ 返回自洽值。"
     "偏离后果：缓存键可能与 PC 不同（只影响缓存命中，不影响显示内容正确性）。"},

    // ---- `Level` -------------------------------------------------------------
    {0x03010003,
     ApiDeviationKind::Partial,
     "PC 的 `Level:GetCurses()` 是 `原始诅咒位 | 永久诅咒 & ~禁用诅咒` 三步合成；"
     "后两步的两个函数地址本轮未定位，所以只返回 `Level + 0x0C` 的原始位掩码。"
     "偏离后果：本局临时诅咒（`AddCurse`）读得到，而'永久诅咒'与'禁用诅咒'两侧的修正读不到。"},

    // ---- `Room` 网格/寻路（保守占位）-----------------------------------------
    {0x04010002,
     ApiDeviationKind::Placeholder,
     "`Room` 的网格尺寸字段未定位。宽高返回标准房间边长 13（只为让 EID 的下标计算不除零）。"
     "偏离后果：EID 的寻路判定恒为'没有可达路径' —— 刻意选择'少画提示'而不是'用假数据画出"
     "错误提示'。**注意**：这条文本在 2026-09-16 更新过一次，删掉了'GetGridEntity 恒返回 nil'"
     "那半句 —— 网格实体表已定位（`Room + 0x30`），`GetGridEntity` 现在是真的，"
     "别把已经消掉的偏离继续挂在这条上。"},
    {0x04010003,
     ApiDeviationKind::Placeholder,
     "`Room:GetGridHeight()`：网格高度字段未定位 ⇒ 返回标准房间边长 13。"
     "偏离后果：非标准尺寸的房间（例如某些大房间）算出的行数会与 PC 不同。"},
    {0x04010004,
     ApiDeviationKind::Placeholder,
     "`Room:GetGridSize()`：PC 返回格子总数（宽 × 高），这里只能用两个兜底值相乘（13×13）。"
     "偏离后果：非标准房间的格子总数与 PC 不同。"},
    {0x04010005,
     ApiDeviationKind::Placeholder,
     "`Room:GetGridIndex(x, y)`：PC 用真实宽度做 `x * width + y`，这里用兜底宽度 13。"
     "偏离后果：非标准房间下算出的下标会偏，EID 据此查网格实体会查错格子（查不到时按'无'处理）。"},
    {0x04010006,
     ApiDeviationKind::Placeholder,
     "`Room:GetGridPath()`：网格与寻路数据未定位 ⇒ 恒返回'不可走'（1000，高于 EID 的 900 阈值）。"
     "偏离后果：EID 的寻路判定恒为'没有可达路径'，相关提示不显示 —— 刻意选它，"
     "而不是用假数据画出并不存在的路径。"},
    // 0x04010007 `Room:GetGridEntity()` 的偏离**已于 2026-09-16 解除**：网格实体表定位到
    // `Room + 0x30`（证据见 `runtime_constants.hpp` 的 `kRoomGridEntityTableOffset`），
    // 现在返回真实的 `GridEntity` 句柄（`GridEntity:GetVariant()`/`GetType()` 同批落地）。
    // 记录保留在此，是为了"曾经登记过的偏离"有迹可循 —— 条目本身已从表里移除。

    // ---- `Sprite` ------------------------------------------------------------
    {0x0D010012,
     ApiDeviationKind::NotInEngine,
     "Switch 的 `ANM2` 没有 `GetTexel`，真实像素链路未定位 ⇒ 常量实现。"
     "偏离后果：EID 的 `IsAltChoice()` 逐像素比对拿不到真值（该分支退化为不启用）。"},

    // ---- `Isaac` 门面 --------------------------------------------------------
    {0x0E010002,
     ApiDeviationKind::Partial,
     "PC 的 `Isaac.GetTime()` 返回引擎自己的游戏内计时（暂停、过场、加载都由引擎扣减）；"
     "引擎时钟偏移尚未定位 ⇒ 这里返回 `帧数 / 30` 的整数秒，不声称与 PC 逐值一致。"
     "偏离后果：与计时相关的少数显示数值会与 PC 有秒级差异。"},

    // ---- `Entity` / `EntityPlayer` 的占位成员 --------------------------------
    {0x0E010017,
     ApiDeviationKind::Placeholder,
     "`EntityPlayer:GetData()`：`ModData` 结构没有证据 ⇒ 恒 nil（不是 stub 告警，是明确的'暂缺'）。"
     "偏离后果：依赖 mod 侧自定义数据的少数显示缺失，而不会给出错误内容。"},
    {0x0E010025,
     ApiDeviationKind::Placeholder,
     "`Entity:GetData()`：`ModData` 结构没有证据 ⇒ 恒 nil。"
     "偏离后果：依赖 mod 自定义数据的少数显示缺失，而不会给出错误内容。"},
    {0x0E01001D,
     ApiDeviationKind::Placeholder,
     "`ItemConfig:HasTags()`：`Item` 的 tags 字段偏移未定位 ⇒ 恒 false。"
     "偏离后果：靠 tags 分支的少数条目描述不出现。"},
    {0x0E01001E,
     ApiDeviationKind::Placeholder,
     "`ItemConfig_Item:HasTags()`：`Item` 的 tags 字段偏移未定位 ⇒ 恒 false。"
     "偏离后果：靠 tags 分支的少数条目描述不出现。"},
    {0x0E010029,
     ApiDeviationKind::Placeholder,
     "`EntityPickup:IsShopItem()`：'商店商品'的判据没有证据（只有 `ShopItemId` 这个候选字段）"
     "⇒ 恒 false。偏离后果：商店里卡片/药丸相关的一条描述判断会偏保守。"},
    {0x0E010030,
     ApiDeviationKind::Placeholder,
     "`EntityPlayer:GetPill()`：按槽位读口袋的引擎字段（口袋数组偏移）未定位 ⇒ 恒 0"
     "（PC 上'空口袋'本来就是 0，EID 按 `id ~= 0` 判）。"
     "偏离后果：口袋里有药丸时缺一条口袋描述，但不会造成错误结论。"},
    {0x0E010031,
     ApiDeviationKind::Placeholder,
     "`EntityPlayer:GetCard()`：口袋数组偏移未定位 ⇒ 恒 0。"
     "偏离后果：口袋里有卡牌时缺一条口袋描述，但不会造成错误结论。"},
    {0x0E010048,
     ApiDeviationKind::Placeholder,
     "`EntityPlayer:GetName()`：名字来自存档、偏移未定位 ⇒ 恒 nil"
     "（EID 只做字符串拼接，nil 会被 `tostring` 兜住）。"},
    // 批次 13（2026-09-16）新增。
    {0x0E010054,
     ApiDeviationKind::Partial,
     "`GridEntity:GetRNG()`：PC 返回引擎里那个 RNG 的**引用**（从返回对象上 `SetSeed`/`Next` "
     "会改到实体自己），这里返回的是**取到那一刻的 16 字节快照**。"
     "为什么这样选：网格实体随时会被销毁（石头被炸、尖刺被消耗、换房间重建），而本句柄只存实体"
     "地址、拿不到网格下标，做不到'每次访问重新解析并校验'；让 Lua 长期持有'引擎对象内部成员'"
     "的指针，等于把写已释放内存的口子开给模组。"
     "偏离后果：读语义与 PC 逐值相同（`GetSeed`/`Next`/`RandomInt` 在取到的那一刻完全一致，"
     "EID 的 `spikes:GetRNG():GetSeed()` 正属此类）；只有在'从返回的 RNG 上改状态、并期待实体"
     "跟着变'这种用法上才与 PC 不同 —— 那类用法目前没有任何模组在用。"},


    // ---- `ItemConfig_Item:IsAvailable()` 的 flags（2026-09-16，兼容层定义）------------------
    {0x0E010055,
     ApiDeviationKind::Partial,
     "`Item::IsAvailable(long flags, uint)` 的 `flags` 由我们定：bit1（成就解锁）+ bit2（tags/"
     "当前局阻挡）+ bit3（模组提供的物品）= `0xE`。原因：引擎里 `Item`/`Card`/`PillEffect` 三个 "
     "`IsAvailable` 在镜像中**没有任何调用点**（也不在虚表里），是 Switch 版编译掉 Lua API 后留下的"
     "死代码，参数无法从游戏自己的用法学到。取舍依据：PC 文档 `ItemConfig_Item.md:35-38` 把"
     "『没解锁』与『被 tags 挡掉』写在同一句里，所以这两条检查都要跑到；bit3 只在装了添加物品的"
     "模组时才有影响，打开它是为了不把模组道具误判成不可用。"
     "**未覆盖的 PC 语义**：bit0（按 `Type` 分流的主动/跟班/饰品专用检查）、第三个参数（反汇编里"
     "四个分支都没读到它，这里传 0）、以及各分支内部还有若干与楼层/贪心模式/特定道具 id 相关的"
     "特例条件 —— 我们没有逐条复现，只保证「这两类检查跑到」。"
     "偏离后果：极少数依赖『类型专用检查』或特例道具的道具，可用性判断与 PC 可能不同；"
     "EID 的用途（Spindown Dice / 背包合成跳过未解锁道具）不受影响。"},
};

constexpr std::size_t kApiDeviationCount = sizeof(kApiDeviations) / sizeof(kApiDeviations[0]);

} // namespace

const ApiDeviation* FindApiDeviation(std::uint32_t id) noexcept {
    for (std::size_t index = 0; index < kApiDeviationCount; ++index) {
        if (kApiDeviations[index].id == id) {
            return &kApiDeviations[index];
        }
    }
    return nullptr;
}

std::size_t ApiDeviationCount() noexcept {
    return kApiDeviationCount;
}

const char* ToString(ApiDeviationKind kind) noexcept {
    switch (kind) {
    case ApiDeviationKind::Partial: return "partial";
    case ApiDeviationKind::Placeholder: return "placeholder";
    case ApiDeviationKind::NotInEngine: return "not-in-engine";
    }
    return "unknown";
}

} // namespace isaac::runtime
