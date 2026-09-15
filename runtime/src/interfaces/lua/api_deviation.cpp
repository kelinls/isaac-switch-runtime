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
     "`Room` 的网格尺寸字段未定位。宽高返回标准房间边长 13（只为让 EID 的下标计算不除零），"
     "`GetGridPath` 恒报'不可走'、`GetGridEntity` 返回 nil。偏离后果：EID 的寻路判定恒为"
     "'没有可达路径' —— 刻意选择'少画提示'而不是'用假数据画出错误提示'。"},
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
    {0x04010007,
     ApiDeviationKind::Placeholder,
     "`Room:GetGridEntity()`：网格实体容器未定位 ⇒ 恒返回 nil。"
     "偏离后果：依赖'某个格子上有什么东西'的少数描述不生效（EID 对此有 nil 判断，不会报错）。"},

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
