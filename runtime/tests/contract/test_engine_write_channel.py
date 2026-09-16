"""引擎内存**写通道**的契约测试（2026-09-16 新增）。

## 为什么需要它

在 2026-09-16 之前，运行时对引擎对象是**只读**的：四个 API 族的可读性检查
（`IsEngineMemoryReadable`）加 `ReadEngine<T>`，没有任何写入口。这一批第一次开了写：

* `Sprite.Scale` / `Sprite.Color` / `Sprite.FlipX` 写进原生 `ANM2`
  （`sprite_api.cpp` 的 `ApplyCachedSpriteProperties`，绘制路径上每帧应用一次）；
* `EntityPlayer.ControlsCooldown` 写进 `Entity_Player+0x404`
  （`isaac_api.cpp` 的 `EntityNewIndex`，挂在 `EntityPlayer` 元表的 `__newindex` 上）。

写引擎内存与读是**两件危险等级完全不同**的事：读错地址最坏是"读到垃圾"，写错地址是
**静默破坏别人的内存**（症状是随机崩溃 / 存档损坏，而且事后无从归因）。所以这里把写通道的
三条纪律钉成门禁：

1. **唯一入口**：`runtime/src/interfaces/lua/` 下任何"往引擎地址写"都必须经过该 TU 里的
   `WriteEngine<T>`，不许出现裸指针赋值或裸 `memcpy` —— 这样"写之前先确认可写"就不可能被绕过；
2. **可读 ≠ 可写**：写路径必须查**写**缓存（`EngineGuardLookupWritable` /
   `EngineGuardRememberWritable`），且必须按 `Perm_W` 判定；拿读缓存给写路径放行会让
   `Sprite.Scale = 2` 去写只读页（真机 = `Data Abort`）；
3. **偏移有出处**：写点引用的必须是 `runtime_constants.hpp` 里的具名常量（且常量注释里要写出
   证据函数），不许在写点写裸字面量偏移。

另外钉住"写入口不滥开"：`EntityPlayer` 目前只放行 `ControlsCooldown`，别的字段名必须报错
（PC 里那些字段是否可写、写了之后引擎会不会崩，我们**没有**证据）。
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
LUA_DIR = ROOT / "runtime" / "src" / "interfaces" / "lua"
CONSTANTS = ROOT / "runtime" / "source" / "runtime_constants.hpp"
GUARD = LUA_DIR / "engine_memory_guard.hpp"

SPRITE = LUA_DIR / "sprite_api.cpp"
ISAAC = LUA_DIR / "isaac_api.cpp"

#: 允许定义 `WriteEngine` / `IsEngineMemoryWritable` 的编译单元（每个族一份私有实现，
#: 与既有的 `IsEngineMemoryReadable` 同例）。
WRITE_CAPABLE_SOURCES = (SPRITE, ISAAC)


def function_body(text: str, signature: str) -> str:
    """从 `signature` 起按大括号配平截出函数体（够用即可：这些文件里没有花括号字面量干扰）。"""
    start = text.find(signature)
    if start < 0:
        raise AssertionError(f"源码里找不到：{signature}")
    brace = text.find("{", start)
    if brace < 0:
        raise AssertionError(f"{signature} 后面没有函数体")
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[brace:index + 1]
    raise AssertionError(f"{signature} 的函数体没有闭合")


def code_lines(text: str):
    """只留下**代码**行（丢掉 `//` 与 `*` 开头的注释行）。

    注释里出现 `*reinterpret_cast<float*>(address) = value` 这种**反面例子**是正常的
    （`sprite_api.cpp` 的 `WriteEngine` 上方就有一句"为什么不这么写"）——
    门禁判的是代码，不是文字。`--` 之前项目里有过"注释把契约门禁判红"的先例。
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
            continue
        yield line


class EngineWriteChannelContractTests(unittest.TestCase):
    def setUp(self):
        self.sprite = SPRITE.read_text(encoding="utf-8")
        self.isaac = ISAAC.read_text(encoding="utf-8")
        self.guard = GUARD.read_text(encoding="utf-8")
        self.constants = CONSTANTS.read_text(encoding="utf-8")

    # ------------------------------------------------------------------ 唯一入口
    def test_engine_writes_go_through_write_engine_only(self):
        """裸写引擎内存的写法一律不许出现 —— 写必须经 `WriteEngine<T>`。

        `WriteEngine` 里那一句 `memcpy(reinterpret_cast<void*>…, …)` 是**唯一**允许的裸写；
        每个编译单元各有一份实现，所以允许出现次数 == 定义个数。
        """
        raw_writes = re.findall(r"memcpy\s*\(\s*reinterpret_cast<void\s*\*>", self.sprite + self.isaac)
        definitions = len(re.findall(r"bool\s+WriteEngine\s*\(", self.sprite + self.isaac))
        self.assertEqual(definitions, len(WRITE_CAPABLE_SOURCES),
                         "每个可写编译单元应当有且只有一份 `WriteEngine<T>` 定义")
        self.assertEqual(len(raw_writes), definitions,
                         "有绕过 `WriteEngine` 的裸写：先把可写性检查加进 `WriteEngine`，再调用它")

        # 反向对照：整个 lua 目录里，除了 `WriteEngine` 的函数体，不该再有裸写形态。
        for path in sorted(LUA_DIR.glob("*.cpp")):
            text = path.read_text(encoding="utf-8")
            body = function_body(text, "bool WriteEngine(") if "bool WriteEngine(" in text else ""
            others = [line.strip() for line in code_lines(text)
                      if re.search(r"memcpy\s*\(\s*reinterpret_cast<void\s*\*>", line)
                      and line.strip() not in body]
            self.assertEqual(others, [], f"{path.name} 里有 `WriteEngine` 之外的裸写：{others}")

        # 直接对引擎指针做赋值（`*reinterpret_cast<float*>(address) = …`）同样不许出现。
        for name, text in (("sprite_api.cpp", self.sprite), ("isaac_api.cpp", self.isaac)):
            offenders = [line.strip() for line in code_lines(text)
                         if re.search(r"\*\s*reinterpret_cast<[^>]*\*>\s*\([^)]*\)\s*=", line)]
            self.assertEqual(offenders, [], f"{name} 里出现了裸指针赋值：{offenders}")

    # ------------------------------------------------------------------ 可读 ≠ 可写
    def test_write_path_uses_the_writable_cache_and_perm_w(self):
        """写路径必须查写缓存、按 `Perm_W` 判定，且**不碰**读缓存。"""
        for path in WRITE_CAPABLE_SOURCES:
            text = path.read_text(encoding="utf-8")
            body = function_body(text, "bool IsEngineMemoryWritable(")
            with self.subTest(source=path.name):
                self.assertIn("EngineGuardLookupWritable", body,
                              "写路径没有查写缓存（每次都进内核会拖慢绘制，且写法与读侧不一致）")
                self.assertIn("EngineGuardRememberWritable", body,
                              "写路径没有登记写区间（同一页会被反复问内核）")
                self.assertIn("Perm_W", body, "写路径必须按 `Perm_W` 判定，不能只看可读")
                self.assertNotIn("EngineGuardLookupCached", body,
                                 "写路径查了**读**缓存：可读不等于可写，模块映像的只读段会因此被放行")
                self.assertNotIn("Perm_R", body, "写路径不许用 `Perm_R` 判定")

    def test_writable_cache_is_separate_and_cleared_with_the_scope(self):
        """写缓存必须与读缓存**分家**，并且随派发作用域一起清空（否则会命中过期区间）。"""
        self.assertIn("EngineGuardWritableRegionCache()", self.guard)
        self.assertNotEqual(self.guard.find("EngineGuardWritableRegionCache()"),
                            self.guard.find("EngineGuardRegionCache() {"),
                            "写缓存不能就是读缓存那一个实例")
        for scope in ("inline void EngineGuardSetCacheEnabled(", "inline void EngineGuardEnterDispatch()",
                      "inline void EngineGuardLeaveDispatch()"):
            body = function_body(self.guard, scope)
            with self.subTest(scope=scope):
                self.assertIn("EngineGuardWritableRegionCache().Clear()", body,
                              f"{scope} 没有清写缓存：换局/换相之后会命中过期区间")

    # ------------------------------------------------------------------ 偏移有出处
    def test_write_sites_reference_named_constants(self):
        """写点必须引用具名常量，常量注释里要写出证据（函数 + 指令形态）。"""
        for name in ("kSpriteScaleOffset", "kSpriteColorOffset", "kSpriteFlipXOffset"):
            with self.subTest(constant=name):
                self.assertIn(f"inline constexpr uintptr_t {name} =", self.constants,
                              f"{name} 没在 `runtime_constants.hpp` 里声明")
                self.assertIn(name, self.sprite, f"{name} 没有被写点引用（写了字面量偏移？）")
        self.assertIn("kEntityPlayerControlsCooldownOffset", self.isaac)
        self.assertIn("inline constexpr uintptr_t kEntityPlayerControlsCooldownOffset =",
                      self.constants)

        # 证据：两条互不相同的证人都要出现在常量注释里（见常量块上方的说明）。
        for witness in ("ANM2::Reset", "GetDestQuad"):
            with self.subTest(witness=witness):
                self.assertIn(witness, self.constants,
                              f"ANM2 字段偏移的证据里缺了 {witness} —— 偏移必须有第二证人")

    def test_sprite_write_covers_the_three_pc_properties(self):
        """三个属性的落点与宽度要与 PC 一致（Scale 两个 float、Color 四元组、FlipX 一个字节）。"""
        body = function_body(self.sprite, "void ApplyCachedSpriteProperties(")
        self.assertIn("kSpriteScaleOffset", body)
        self.assertIn("kSpriteScaleOffset + sizeof(float)", body)
        self.assertIn("kColorModTintOffset", body)
        self.assertIn("kColorModShiftOffset", body)
        self.assertIn("WriteEngine<std::uint8_t>(base + kSpriteFlipXOffset", body)
        # 只写"Mod 确实赋过值"的属性：没赋过值就不该动引擎原有的值。
        for flag in ("hasScale", "hasColor", "hasFlipX"):
            with self.subTest(flag=flag):
                self.assertIn(f"handle->{flag} != 0", body,
                              f"缺少 `{flag}` 门槛：Mod 没写过这个属性也会去改引擎对象")

    # ------------------------------------------------------------------ 写入口不滥开
    def test_player_newindex_only_allows_controls_cooldown(self):
        """`EntityPlayer` 的写入口只放行 `ControlsCooldown`，别的字段名报错。"""
        body = function_body(self.isaac, "int EntityNewIndex(")
        self.assertIn('"ControlsCooldown"', body)
        self.assertIn("kEntityPlayerControlsCooldownOffset", body)
        self.assertIn("luaL_error(state, \"EntityPlayer fields are read-only except ControlsCooldown\")",
                      body, "未知字段名必须报错，不能静默吞掉（拼错的字段名会因此查不出来）")
        # 基类 `Entity` 的元表不许有 `__newindex`：PC 里基类没有可写成员，开了就是纯粹的放松。
        registration = self.isaac[self.isaac.find("void RegisterViewMetatables("):]
        base_block = registration[:registration.find("kEntityPlayerMetatable")]
        self.assertNotIn("__newindex", base_block,
                         "基类 `Entity` 的元表被加上 `__newindex` 了 —— 本批只放行玩家字段")
        self.assertIn('lua_setfield(state, -2, "__newindex")', registration)


if __name__ == "__main__":
    unittest.main()
