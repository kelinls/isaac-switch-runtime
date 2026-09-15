#pragma once

#include "common.hpp"
#include "lib/util/typed_storage.hpp"
#include "rw_pages.hpp"

#include <span>

// 竞技场必须在 `--gc-sections` 下存活：它是**只被本 TU 引用**的静态数组，只要调用方那一侧
// 还没接线（或将来被临时摘掉），整个 TU 的 section 都会被当作不可达回收，竞技场就凭空消失
// （2026-09-12 实测：`runtime/source/relay/entry_relay.cpp` 的 `.text.EntryRelayArena`
// 在 ELF 里查不到，`nm` 里也没有任何 `EntryRelay*` 符号）。
// 因此给它打上 `SHF_GNU_RETAIN`：section 属性里的 `"axR"` 就是
// `alloc | exec | retain`（`R` 由 GNU as / ld 认，devkitA64 15.2.0 实测可用）。
// 注意不能用变量属性 `__attribute__((retain))`——GCC 对**已经显式指定 section** 的变量
// 会忽略它（报 `'retain' attribute ignored`，本项目 `-Werror` 直接失败）。
#define JIT_CREATE(name, size)                              \
    namespace impl::name {                                  \
        __attribute__((section(".text." #name ", \"axR\""))) \
        alignas(PAGE_SIZE)                                  \
        static const std::array<const u8, size> s_Area {};  \
    }                                                       \
    constinit exl::util::Jit<size> name;

namespace exl::util {

    template<size_t Size>
    class Jit {
        util::TypedStorage<RwPages> m_Pages;

        inline RwPages& GetPages() { return util::GetReference(m_Pages); }

        public:
        constexpr Jit() = default;
        inline void Initialize(std::span<const u8, Size> rx) {
            util::ConstructAt(m_Pages, reinterpret_cast<uintptr_t>(rx.data()), rx.size());
        }

        inline void Flush() {
            GetPages().Flush();
        }

        inline uintptr_t GetRo() { return GetPages().GetRo(); }
        inline uintptr_t GetRw() { return GetPages().GetRw(); }
        inline uintptr_t GetSize() { return GetPages().GetSize(); }
    };
}
