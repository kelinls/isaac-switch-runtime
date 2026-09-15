#pragma once

#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// `LuaApiDescriptor::maturity` 记的是"我们验证到什么程度"，**不是**"跟 PC 语义差多少"。
// 这两件事必须分开：一条 API 可以既标 `Experimental`（没上机）又语义完全正确；
// 也可以上机验证过（调用链确实通）却**返回值与 PC 不同**——后者才是这里的"偏离"。
//
// 为什么要单列这张表：本项目的验收基准是三维（① 存在 ② 语义 ③ 派发时机），
// 而"语义不满足"必须**被标记出来**，不能只写在实现处的注释里——注释不会被任何
// 报告、清单或验收读数带出来，于是"实现正确"与"实现但有已知偏离"就分不清了。
enum class ApiDeviationKind : std::uint8_t {
    // 只实现了 PC 语义的一部分：缺失的那部分有明确证据说明，返回值在缺的那部分上会偏。
    Partial = 0,
    // 用保守值或固定值代替引擎真值（字段/调用链未定位）：刻意选"不会造成错误结论"的那个值，
    // 但拿不到真值 —— 例如缺一条描述，而不是显示错误的内容。
    Placeholder,
    // 引擎里没有对应能力（不是"没定位"，而是确实不存在），只能给常量。
    NotInEngine,
};

struct ApiDeviation {
    // catalog 里的稳定 id（`ApiDescriptor.hpp` 的 `MakeId`）。
    std::uint32_t id{0};
    ApiDeviationKind kind{ApiDeviationKind::Partial};
    // 差在哪、为什么、后果。**必须能回答这三件事**，否则不算记录（契约测试会拦住空文本）。
    const char* text{nullptr};
};

// 收表口径（避免这张表将来变成"想写什么写什么"）：
//   * 只收**代码注释里已经明确承认**的偏离（"占位/未定位/语义偏差/恒假/常量实现"这类原话）；
//   * 逐条写出"差在哪 + 为什么 + 后果"；后果是"缺功能"还是"可能给出错误结论"必须能分辨；
//   * 新增或删除条目都要同步契约测试里那张表的条数（强制人工过一遍）。
// 本表**不追求一次收全**：批量期由 `tools/eid_api_gap_report.py` 与语义差分工具共同发现，
// 发现一条补一条；漏收的风险是"偏离没被标记"，不会让实现变错。
[[nodiscard]] const ApiDeviation* FindApiDeviation(std::uint32_t id) noexcept;
[[nodiscard]] std::size_t ApiDeviationCount() noexcept;
[[nodiscard]] const char* ToString(ApiDeviationKind kind) noexcept;

} // namespace isaac::runtime
