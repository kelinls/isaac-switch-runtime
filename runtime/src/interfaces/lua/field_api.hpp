#pragma once

// ============================================================================
// 字段读取型 API 的"数据行"机制（门禁三期：成本压缩）
// ============================================================================
//
// **问题**：一个"读引擎字段并返回"的方法，写成一个独立 `lua_CFunction` 要 236–312 字节代码
// （反汇编实测 `EntityPlayer:GetSoulHearts` 312 B、`EntityPlayer:GetPlayerType` 320 B）。
// 这些代码里**绝大部分是每次重复的同一套流程**：
//
//   1. 检查参数个数（`lua_gettop`）并为不符的情况报错；
//   2. 从 Lua 栈上第 1 个参数解出接收者（句柄 → 校验 vptr → 引擎对象地址）；
//   3. 校验后再读字段（带可读性检查）；
//   4. 读不到时按族约定降级（0 / nil / false）；
//   5. 把值压回 Lua 栈。
//
// 而 PC 侧的 API 契约里**绝大多数就是这个形状**（`GetXxx()` 读一个成员），我们后面要补的
// API 有成百上千个。所以把"不变的流程"提成一个**共享处理器**，每个 API 只剩一行数据
// （`FieldApiRow`，16 字节只读数据、**0 字节代码**）。
//
// **怎么绑定**：`AttachOwnerMethods` 支持"带数据行的闭包"——把这一行作为上值
// （`lua_pushcclosure`）交给共享处理器，于是同一个 `lua_CFunction` 能服务成百上千个方法，
// 不需要为每个 API 生成一个 thunk。
//
// **什么时候不能用它**（宁可手写，别硬塞）：
//   * 返回值需要计算/查表/多字段组合（除非正好是已支持的那几种 `FieldKind`）；
//   * 需要传参数（例如 `GetTrinketMultiplier(id)`）；
//   * 接收者不是"句柄 + 固定偏移"的形状（例如要走容器、要走引擎函数）。
//
// **纪律**：`offset` 必须来自**有证据的**布局表（`tools/layout_tables/*.json` →
// `runtime/source/*_layout.hpp`），不能在实现里现编一个偏移 —— 这条与地基二期一致，
// 门禁 `runtime/tests/test_field_api_rows.py` 会把"行里的 id/族/偏移"逐条核对。

#include <cstdint>

extern "C" {
#include <lua.h>
}

namespace isaac::runtime {

//: 接收者从哪个族的 Lua 参数上解出来（决定用哪套句柄校验）。
enum class FieldReceipt : std::uint8_t {
    Entity = 0,          // `Entity`/`EntityPlayer`/`EntityPickup` 三张元表都接受
    EntityPlayer = 1,    // 必须是精确的 `Entity_Player` vptr
    ItemConfigItem = 2,  // `ItemConfig_Item`
};

//: 读什么、怎么读。
enum class FieldKind : std::uint8_t {
    U32 = 0,      // 无符号 32 位整数，按整数压回
    I32 = 1,      // 有符号 32 位整数（例如 `ControllerIndex`：`-1` 表示键鼠）
    Bool = 2,     // 读一个字节，按布尔压回
    Sum2U32 = 3,  // 两个 `u32` 字段相加（例如 `GetEffectiveMaxHearts` = 红心容器 + 骨心）
};

//: 读不到（或接收者无效）时返回什么。按各族 PC 语义与既有实现选定，**不许随手挑**：
//: 读不出来却编一个假值，会让 Mod 走进错误的判断分支。
enum class FieldMissing : std::uint8_t {
    Zero = 0,      // 压 0（计数器类：PC 上"没有"就是 0）
    Nil = 1,       // 压 nil（枚举/句柄类：`x == SomeEnum` 在 nil 上自然为假）
    False = 2,     // 压 false（布尔类）
    MinusOne = 3,  // 压 -1（"没有"在 PC 上就是 -1 的字段，例如 `GetBabySkin()` 非婴儿 = -1）
};

//: 一行 = 一个 API。字段顺序按"对齐后最小"排：4+4+4+1+1+1+1 = 16 字节。
struct FieldApiRow {
    std::uint32_t id;        //: catalog id（必须与 `api_catalog.cpp` 里那条对得上）
    std::uint32_t offset;    //: 相对接收者对象的偏移（`Sum2U32` 时是第一个）
    std::uint32_t offset2;   //: `Sum2U32` 的第二个偏移，其余 kind 忽略
    FieldKind kind;
    FieldMissing missing;
    FieldReceipt receipt;
    std::uint8_t probeBit;   //: `api_sequence_probe.hpp` 的位号；`0xFF` = 不记
};

//: 一行必须正好 16 字节（4+4+4+1+1+1+1，按对齐排好）。这是这套机制的**成本前提**：
//: 行变大就会削弱"每个 API 只花一行数据"的结论。改结构体先看这条断言。
static_assert(sizeof(FieldApiRow) == 16, "FieldApiRow 必须保持 16 字节（见成本说明）");

//: 字段读取型 API 的**共享处理器**。行通过闭包上值传入（见 `AttachOwnerMethods`）。
//:
//: 错误消息按 catalog 里的 `owner`/`name` 现拼（例如 `"EntityPlayer:GetSoulHearts accepts
//: no arguments"`），与手写处理器逐字一致；拼消息只在出错路径上做，稳态零成本。
[[nodiscard]] int FieldApiHandler(lua_State* state);

} // namespace isaac::runtime
