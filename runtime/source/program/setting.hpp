#pragma once

#include "common.hpp"

#define EXL_MODULE_NAME "exlaunch"

#define EXL_DEBUG
#define EXL_USE_FAKEHEAP

/*
#define EXL_SUPPORTS_REBOOTPAYLOAD
*/

namespace exl::setting {
    /* How large the fake .bss heap will be.
     *
     * 32 MiB（2026-09-12：0x200000 → 0x2000000）。这是**整个模块 malloc 的唯一来源**
     * （`EXL_USE_FAKEHEAP` ⇒ newlib 的 `fake_heap_start/end` 指向这块 .bss 数组），
     * 所以它同时是 Lua 状态的上限。真机报告 `01789202408` 里 Lua 报的
     * `LUA_ERRMEM`（"not enough memory"）就是它：EID 的 20 个语言包 × 两个版本
     * （ab+/rep）+ names/modular 一共约 10 MB 的源码，展开成 Lua 表与字符串后远超 2 MiB。
     *
     * 历史：0x40000 时 Font API 一装就满（一个 `KAGE::Graphics::Font` 是 0x20050 = 128 KiB），
     * 于是提到 2 MiB 并保留"至少 8 个 Font"的余量（`runtime/tests/test_runtime_constants.py`）。
     * 现在按 Lua Mod 的真实需求再上一个量级；`runtime/tests/test_runtime_constants.py`
     * 会同时断言"至少 8 个 Font"和"不小于 16 MiB"。 */
    constexpr size_t HeapSize = 0x2000000;

    /* How large the JIT area will be for hooks. */
    constexpr size_t JitSize = 0x1000;

    /* How large the area will be inline hook pool. */
    constexpr size_t InlinePoolSize = 0x1000;

    /* How large the formatting buffer should be for logging. The buffer will be on the stack. */
    constexpr size_t LogBufferSize = 512;

    /* Sanity checks. */
    static_assert(ALIGN_UP(JitSize, PAGE_SIZE) == JitSize, "");
    static_assert(ALIGN_UP(InlinePoolSize, PAGE_SIZE) == InlinePoolSize, "");
}
