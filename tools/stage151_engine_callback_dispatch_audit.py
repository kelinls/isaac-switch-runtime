#!/usr/bin/env python3
"""Stage 151: 本体（``Repentance.nro``）是否自带 Mod 回调派发？—— 只读静态审计。

回答一个工程问题：**我们把 ``MC_POST_RENDER`` 派发挂在 ``Manager::Render``
(``0x3F9684``) 入口中继、在「原函数跑完返回之后」才回调 Lua，此时引擎的绘制
批次还成不成立？本体里有没有一个「PC 同款」的、时机更正确的派发点？**

本工具只读 ``Repentance.nro``，不写 ``runtime/``，不联网。它做四件事：

1. 把每条结论涉及的**偏移**从真实镜像里抄 16 字节，并逐字节断言（脚本内声明的
   ``declared`` 必须等于文件里的实际字节，否则直接失败退出，避免把命令输出的
   记忆错误带进证据）。
2. 全量筛动态符号表，回答「本体有没有 AddCallback / RunCallback / Lua 运行时」。
3. 用 ``.jmprel`` → PLT 桩 → ``BL``/``B`` 的三段式交叉引用，给出「谁调用了谁」，
   特别是 ``ModManager::RunModScripts`` / ``RunModScript`` / ``apply_frame_images``
   这类关键函数的调用者个数。
4. 给出 ``Manager::Update`` / ``Manager::Render`` 的完整调用序列，以及
   ``run_repentance`` 的帧循环结构，用来判定我们中继点的**时机先后**。

关键机制（沿用 stage148 的口径）：该 NRO 模块内调用也走 PLT（default visibility
+ PIC），所以「调用者」可以完全由重定位表 + 扫描复现，不依赖任何猜测。

Usage::

    python3 tools/stage151_engine_callback_dispatch_audit.py [--out <json>] [--print]
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import struct
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import nro_symbols  # noqa: E402

NRO = os.path.join(
    REPO_ROOT,
    "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]",
    "Program #0",
    "1",
    ".nro",
    "Repentance.nro",
)
DEFAULT_OUT = os.path.join(
    REPO_ROOT, "analysis", "stage151-engine-callback-dispatch",
    "91C73FDD575061318D68886316AFEAC72388B2AB.json",
)

TOOLING = [
    "python3 tools/nro_symbols.py 等价调用：parse_dynamic_symbols / parse_dynamic_relocations（读 .dynsym / .rela / .jmprel）",
    "python3 tools/nro_disasm.py 0x3F9684 0x4D8（Manager::Render 全函数）",
    "python3 tools/nro_disasm.py 0x3F8DB8 0x364（Manager::Update 全函数）",
    "python3 tools/nro_disasm.py 0x4C0840 0x2A0（run_repentance 帧循环）",
    "python3 tools/nro_disasm.py 0x3B3820 0x40（IsaacRender）",
    "python3 tools/nro_disasm.py 0x41FB04 0x8（ModManager::TryRedirectPath）",
    "python3 tools/nro_disasm.py 0x421838 0x8（ModManager::RunModScripts）",
    "python3 tools/nro_disasm.py 0x42183C 0x8（ModManager::RunModScript）",
    "python3 tools/nro_disasm.py 0x4EF7DC 0x60（KAGE::Graphics::ManagerBase::Present）",
    "python3 tools/nro_disasm.py 0x4F3014 0x48（KAGE::Graphics::Manager::Present）",
    "python3 tools/nro_disasm.py 0x4F388C 0x60 / 0x4F3A5C 0x60（begin_scene / end_scene）",
    "python3 tools/nro_disasm.py 0x4D1368 0x120（ImageBase::get_batch，绘制取批次路径）",
    "python3 tools/nro_disasm.py 0x4D0A40 0x80（ImageBase::apply_data 的 add_frame_image 调用点）",
    "python3 tools/nro_disasm.py 0x4F3644 0x48（Manager::get_predefined_shader，读 [this+0x18]）",
    "python3 tools/nro_disasm.py 0x4F177C 0x8（ShaderBase::GetState = add x0,x0,#0x38; ret）",
    "本脚本内置：PLT 桩扫描 + BL/B 目标扫描 + 动态符号筛选 + 原始字节串搜索",
]

# ---------------------------------------------------------------------------
# 记录表。
#
# ``declared`` 是从真实 Repentance.nro 抄下来的 16 字节（偏移处开始的 16 字节，
# 不足 16 字节的按实际长度）。脚本会逐字节与文件比对，不一致就抛错。
# ---------------------------------------------------------------------------
RECORDS = (
    # ---- 我们的中继点 -------------------------------------------------
    dict(
        key="manager_update_entry",
        category="relay_site",
        symbol="IsaacRepentance::Manager::Update()",
        mangled="_ZN15IsaacRepentance7Manager6UpdateEv",
        offset=0x3F8DB8,
        declared="ff 43 01 d1 fd 7b 01 a9 fd 43 00 91 f7 13 00 f9",
        inst="sub sp, sp, #0x50（函数入口，与既有结论一致）",
        role="我们在入口做 IPS 中继的 Update 派发点（本阶段只做旁证）",
        evidence=".dynsym 符号 _ZN15IsaacRepentance7Manager6UpdateEv = 0x3F8DB8；"
                 "下一符号 _ZN15IsaacRepentance7Manager25execute_show_gameselectorEv = 0x3F9120，"
                 "故函数体为 [0x3F8DB8, 0x3F911C)",
        confidence="verified",
    ),
    dict(
        key="manager_render_entry",
        category="relay_site",
        symbol="IsaacRepentance::Manager::Render()",
        mangled="_ZN15IsaacRepentance7Manager6RenderEv",
        offset=0x3F9684,
        declared="ff c3 02 d1 e8 3b 00 fd fd 7b 08 a9 fd 03 02 91",
        inst="sub sp, sp, #0xb0（函数入口，与既有结论一致）",
        role="我们在入口做 IPS 中继、并在 Orig 返回后派发 MC_POST_RENDER 的那个函数",
        evidence=".dynsym 符号 _ZN15IsaacRepentance7Manager6RenderEv = 0x3F9684；"
                 "下一符号 _ZN15IsaacRepentance7Manager15LockRenderMutexEv = 0x3F9B5C，"
                 "故函数体为 [0x3F9684, 0x3F9B58]",
        confidence="verified",
    ),
    dict(
        key="manager_render_final_present",
        category="timing",
        symbol="IsaacRepentance::Manager::Render() 末尾：bl KAGE::Graphics::Manager::Present()",
        mangled=None,
        offset=0x3F9B40,
        declared="c0 db 09 94 e8 3b 40 fd f4 4f 4a a9 f6 57 49 a9",
        inst="0x3F9B40: bl 0x670A40（PLT 桩 → _ZN4KAGE8Graphics7Manager7PresentEv）；"
             "后接 epilogue（ldr d8 / ldp / add sp,#0xb0）",
        role="Manager::Render 的最后一次真正调用就是 Present —— 这是判「我们的派发点是否在 Present 之后」的直接证据",
        evidence="反汇编 0x3F9B40 的 bl 目标 0x670A40，其 .jmprel 槽 = 0xAA4B18 → 符号 "
                 "_ZN4KAGE8Graphics7Manager7PresentEv；同一函数的 epilogue 起点 0x3F9B44，ret 在 0x3F9B58",
        confidence="verified",
    ),
    dict(
        key="manager_render_ret",
        category="timing",
        symbol="IsaacRepentance::Manager::Render() 的 ret",
        mangled=None,
        offset=0x3F9B58,
        declared="c0 03 5f d6 c0 03 5f d6 c0 03 5f d6 e0 03 1f 2a",
        inst="0x3F9B58: ret；0x3F9B5C 起是下一个符号 LockRenderMutex（它自身又以 ret 开头）",
        role="确认 Present 与 ret 之间没有任何恢复绘制状态的代码（只有 4 条 epilogue 指令）",
        evidence="反汇编 0x3F9B44..0x3F9B58 共 6 条指令：ldr d8 / ldp x20,x19 / ldp x22,x21 / "
                 "ldp x29,x30 / add sp,sp,#0xb0 / ret，无 bl、无 str 到 g_Manager",
        confidence="verified",
    ),
    dict(
        key="isaac_render_call_site",
        category="timing",
        symbol="IsaacRepentance::IsaacRender() → Manager::Render",
        mangled="_ZN15IsaacRepentance11IsaacRenderEv",
        offset=0x3B3840,
        declared="cc 1f 0b 94 60 02 40 f9 f3 0b 40 f9 fd 7b c2 a8",
        inst="0x3B3840: bl 0x67B770（→ _ZN15IsaacRepentance7Manager6RenderEv）",
        role="我们的中继位于这条调用里；调用者持有 render mutex（+0x18 Lock、+0x30 Unlock）",
        evidence="反汇编 0x3B3820..0x3B3850：0x3B3838 bl LockRenderMutex / 0x3B3840 bl Manager::Render / "
                 "0x3B3850 b UnlockRenderMutex；调用者扫描给出唯一调用者 _Z14run_repentancev @ 0x4C08E0",
        confidence="verified",
    ),
    dict(
        key="isaac_render_lock_mutex",
        category="timing",
        symbol="IsaacRender() 的 LockRenderMutex 调用点",
        mangled=None,
        offset=0x3B3838,
        declared="12 f5 0a 94 60 02 40 f9 cc 1f 0b 94 60 02 40 f9",
        inst="0x3B3838: bl 0x670C80（→ _ZN15IsaacRepentance7Manager15LockRenderMutexEv）",
        role="证明我们的 MC_POST_RENDER 派发仍处于「已 LockRenderMutex」的临界区（muter 在 Render 之外持有）",
        evidence="PLT 槽 → 符号 _ZN15IsaacRepentance7Manager15LockRenderMutexEv；"
                 "对应 Unlock 在 0x3B3850 的尾调用",
        confidence="verified",
    ),
    dict(
        key="frame_loop_render_call",
        category="timing",
        symbol="run_repentance() 帧循环里的 IsaacRender 调用",
        mangled="_Z14run_repentancev",
        offset=0x4C08E0,
        declared="20 fc 06 94 a8 b3 00 d1 29 00 80 52 ff 0f 00 b9",
        inst="0x4C08E0: bl 0x67F960（→ _ZN15IsaacRepentance11IsaacRenderEv）",
        role="run_repentance 的循环体：先 IsaacRender 再 IsaacUpdate，循环内没有 Present / swap",
        evidence="反汇编 0x4C0840..0x4C0A64：0x4C08E0 bl IsaacRender，0x4C0A0C bl IsaacUpdate，"
                 "0x4C0A1C b.gt 0x4C08E0 回跳；整段无 begin_scene/end_scene/Present 调用",
        confidence="verified",
    ),
    dict(
        key="frame_loop_update_call",
        category="timing",
        symbol="run_repentance() 帧循环里的 IsaacUpdate 调用",
        mangled="_Z14run_repentancev",
        offset=0x4C0A0C,
        declared="d9 fb 06 94 40 39 69 1e e9 0f 40 b9 3f 0d 00 71",
        inst="0x4C0A0C: bl 0x67F970（→ _ZN15IsaacRepentance11IsaacUpdateEv）",
        role="同上：帧循环里 Render 与 Update 相邻，且都在 Present 之外（Present 在 Render 内部）",
        evidence="反汇编 0x4C0A0C；PLT 槽 → _ZN15IsaacRepentance11IsaacUpdateEv",
        confidence="verified",
    ),

    # ---- 本体 mod 脚本执行路径（否定证据 1：函数被桩化） -----------------
    dict(
        key="run_mod_scripts_stub",
        category="engine_mod_dispatch",
        symbol="IsaacRepentance::ModManager::RunModScripts()",
        mangled="_ZN15IsaacRepentance10ModManager13RunModScriptsEv",
        offset=0x421838,
        declared="c0 03 5f d6 e0 03 1f 2a c0 03 5f d6 c0 03 5f d6",
        inst="0x421838: ret（0xD65F03C0）—— 空函数体",
        role="本体「运行全部 mod Lua 脚本」的入口；在 Switch 移植里被裁成空桩",
        evidence="反汇编 0x421838 = 单条 ret；下一符号 RunModScript = 0x42183C，说明该函数只有 4 字节；"
                 "调用者扫描（.jmprel → PLT 桩 → BL/B）结果 0 个 —— 全镜像无人调用它",
        confidence="verified",
    ),
    dict(
        key="run_mod_script_stub",
        category="engine_mod_dispatch",
        symbol="IsaacRepentance::ModManager::RunModScript(std::string&)",
        mangled="_ZN15IsaacRepentance10ModManager12RunModScriptERNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEE",
        offset=0x42183C,
        declared="e0 03 1f 2a c0 03 5f d6 c0 03 5f d6 c0 03 5f d6",
        inst="0x42183C: mov w0, wzr; 0x421840: ret —— 恒定 return false 的桩",
        role="本体「运行单个 mod Lua 脚本」的入口；同样被桩化，返回 false 表示失败",
        evidence="反汇编 0x42183C = mov w0,wzr + ret；下一符号 _ZN15IsaacRepentance10ModManager13ReloadShadersEv "
                 "= 0x421844，故函数体 = [0x42183C, 0x421844)；调用者扫描 0 个",
        confidence="verified",
    ),
    dict(
        key="try_redirect_path_identity",
        category="engine_mod_dispatch",
        symbol="IsaacRepentance::ModManager::TryRedirectPath(std::string const&)",
        mangled="_ZN15IsaacRepentance10ModManager15TryRedirectPathERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEE",
        offset=0x41FB04,
        declared="e0 03 08 aa 8e 41 09 14 fd 7b ba a9 fc 6f 01 a9",
        inst="0x41FB04: mov x0, x8（x8 = sret 返回串）；0x41FB08: b 0x670140 —— "
             "尾部跳转 std::string 的拷贝构造（x1 = 入参），即「原样返回 path」的恒等桩",
        role="本体「把 mod 里的文件路径重定向到 isaac_mods」的入口；在移植版里不重定向，直接返回入参副本",
        evidence="0x670140 是 PLT 桩，其 .jmprel 槽 = 0xA9E008 → 符号 "
                 "_ZNSt3__112basic_stringIcNS_11char_traitsIcEENS_9allocatorIcEEEC1ERKS5_；"
                 "下一符号 ListMods = 0x41FB0C，故函数体 = [0x41FB04, 0x41FB0C) 共 8 字节",
        confidence="verified",
    ),
    dict(
        key="mod_manager_reset_no_caller",
        category="engine_mod_dispatch",
        symbol="IsaacRepentance::ModManager::Reset()",
        mangled="_ZN15IsaacRepentance10ModManager5ResetEv",
        offset=0x422440,
        declared="fd 7b be a9 f4 4f 01 a9 fd 03 00 91 f3 03 00 aa",
        inst="函数入口（stp x29,x30 / str x19 / mov x29,sp ...）",
        role="本体 ModManager 的重置入口（内部会 UnloadMods + LoadConfigs + ListMods）。它本身 0 调用者 ⇒ 该生命周期从不发生",
        evidence="调用者扫描（BL+B）0 个，且该类无 vtable 符号 _ZTVN15IsaacRepentance10ModManagerE，"
                 "排除虚函数间接派发；反汇编 0x422450 bl UnloadMods、0x422458 bl ListMods、0x422470 bl LoadConfigs",
        confidence="verified",
    ),
    dict(
        key="mod_manager_list_mods_only_caller",
        category="engine_mod_dispatch",
        symbol="IsaacRepentance::ModManager::ListMods()",
        mangled="_ZN15IsaacRepentance10ModManager8ListModsEv",
        offset=0x41FB0C,
        declared="fd 7b ba a9 fc 6f 01 a9 fd 03 00 91 fa 67 02 a9",
        inst="函数入口（大栈帧：sub sp,sp,#0x10000+0x110）",
        role="本体唯一枚举 mod 目录、解析 metadata.xml 的函数；它的 2 个调用者本身都没有调用者（死路径）",
        evidence="调用者扫描（BL+B）：0x422458 bl（_ZN15IsaacRepentance10ModManager5ResetEv +0x18）与 "
                 "0x422740 b（_ZN15IsaacRepentance10ModManager17PreLanguageSwitchEv +0x20，尾调用）；"
                 "而 Reset 与 PreLanguageSwitch 各自的调用者都是 0；"
                 "函数体内使用 g_ModdingDataPath 全局与字面量 \"/metadata.xml\"（0x8AA385）",
        confidence="verified",
    ),
    dict(
        key="mod_manager_pre_language_switch_no_caller",
        category="engine_mod_dispatch",
        symbol="IsaacRepentance::ModManager::PreLanguageSwitch()",
        mangled="_ZN15IsaacRepentance10ModManager17PreLanguageSwitchEv",
        offset=0x422720,
        declared="fd 7b be a9 f3 0b 00 f9 fd 03 00 91 f3 03 00 aa",
        inst="0x422730 bl ModManager::UnloadMods；0x422740 b ModManager::ListMods（尾调用）",
        role="本体「重新加载 mod 列表」的另一条入口；它自己 0 调用者 ⇒ 与 Reset 一样是死路径",
        evidence="调用者扫描（BL+B）0 个；对照 .dynsym 中同类生命周期函数 "
                 "_ZN15IsaacRepentance10ModManager18PostLanguageSwitchEv = 0x422744 同样 0 调用者",
        confidence="verified",
    ),

    # ---- 本体 mod 目录根（g_ModdingDataPath） ---------------------------
    dict(
        key="mod_entry_load_custom_resources",
        category="engine_mod_dispatch",
        symbol="IsaacRepentance::ModEntry::LoadCustomResources()",
        mangled="_ZN15IsaacRepentance8ModEntry19LoadCustomResourcesEv",
        offset=0x41EDB0,
        declared=None,  # 仅作符号存在性证据，不做字节断言
        inst=None,
        role="本体 ModEntry 只加载「自定义内容」（items/entities/资源），没有任何脚本字段或 Lua 相关方法",
        evidence=".dynsym 中 ModEntry 的全部符号共 20 条，方法只有 C2/D2/LoadCustomResources/"
                 "LoadCustomResource/GetContentPath/WriteMetadata，以及各 Config::Load(..., ModEntry*)；"
                 "无 AddCallback/RunScript/Lua 相关符号",
        confidence="verified",
    ),

    dict(
        key="mod_manager_list_mods_modding_data_path",
        category="engine_mod_dispatch",
        symbol="ModManager::ListMods() 读取 g_ModdingDataPath 并拼 \"/metadata.xml\"",
        mangled=None,
        offset=0x41FBE0,
        declared="60 34 00 d0 00 cc 40 f9 f2 3f 09 94 1f 40 00 b1",
        inst="0x41FBE0: adrp x0, 0xAAD000；0x41FBE4: ldr x0,[x0,#0x198]（= _ZN15IsaacRepentance17g_ModdingDataPathE）；"
             "0x41FBE8: bl strlen；随后 memcpy/append/lower 与 CreateCleanPath(…, \"/metadata.xml\")",
        role="mod 目录根是运行期全局 g_ModdingDataPath；本体只把它当成「内容覆盖目录」，没有任何 Lua 语义",
        evidence="反汇编 0x41FB0C（ListMods）内 0x41FBE4 的 ldr 被注解为 _ZN15IsaacRepentance17g_ModdingDataPathE"
                 "（该注解来自 .rela 数据重定位）；0x41FCB4-0x41FCC4 以字面量 0x8AA385 \"/metadata.xml\" 调 "
                 "KAGE::Util::Path::CreateCleanPath；0x41FB60-0x41FB6C 以字面量 0x8903B7 \"begin list mods\\n\" 调 KAGE_LogMessage",
        confidence="verified",
    ),
    dict(
        key="pc_source_tree_path_string",
        category="engine_mod_dispatch",
        symbol="镜像内残留的 PC 源码路径字符串",
        mangled=None,
        offset=0x68D017,
        declared="69 73 61 61 63 2d 6e 67 4d 6f 64 44 4c 43 5c 50",
        inst="ASCII：\"isaac-ngModDLC\\Platforms\\NX\\dlls\"（前文为 \"D:\\Projekte\\P4\\\"，0x68D00C 附近）",
        role="证明本 NRO 是从 PC 版 isaac-ng 源码树的 NX 平台分支构建出来的移植产物",
        evidence="原始字节搜索 b\"isaac\" 命中 0x68D017，上下文为 b\"D:\\\\Projekte\\\\P4\\\\isaac-ngModDLC\\\\Platforms\\\\NX\\\\dlls\"；"
                 "这是 __FILE__ 风格的编译期路径常量",
        confidence="verified",
    ),
    dict(
        key="isaac_mods_string_absent",
        category="engine_mod_dispatch",
        symbol="（否定证据）本体不含 \"isaac_mods\" 字面量",
        mangled=None,
        offset=0x8903B7,
        declared="6d 6f 64 73 0a 00 74 61 67 00 23 4d 4f 44 5f 55",
        inst="ASCII：\"mods\\n\\0tag\\0#MOD_U...\"",
        role="全镜像里唯一与 mod 目录有关的字面量是日志文本 \"begin list mods\\n\"；"
             "\"isaac_mods\" 整个 token 在 11,210,752 字节里出现 0 次",
        evidence="原始字节搜索 b\"isaac_mods\" = 0 命中；b\"_mods\" = 0 命中；b\"mods\" 唯一命中 0x8903B7（即本条），"
                 "b\"Mods\" 的命中全部是 C++ 符号名（ModsMenu、PostLoadMods、Menu_Mods 等）",
        confidence="verified",
    ),

    # ---- KAGE 帧边界（判据） -------------------------------------------
    dict(
        key="kage_manager_present",
        category="kage_frame_boundary",
        symbol="KAGE::Graphics::Manager::Present()",
        mangled="_ZN4KAGE8Graphics7Manager7PresentEv",
        offset=0x4F3014,
        declared="fd 7b be a9 f3 0b 00 f9 fd 03 00 91 08 00 40 f9",
        inst="0x4F3014: stp x29,x30；0x4F3020 ldr x8,[x0]（vptr）；"
             "0x4F3030 blr [vptr+0xF8]（Manager::set_predefined_shader，w1=0）；"
             "0x4F3038 bl ManagerBase::Present；0x4F3054 br [vptr+0x100]（尾调用）",
        role="帧结束/提交的公开入口；Manager::Render 的最后一次调用就是它",
        evidence="反汇编 0x4F3014..0x4F3054；vtable 槽由 .rela 解析：_ZTVN4KAGE8Graphics7ManagerE+0xF8 "
                 "= _ZN4KAGE8Graphics7Manager21set_predefined_shaderENS0_17ePredefinedShaderE，"
                 "+0x100 = _ZN4KAGE8Graphics7Manager21get_predefined_shaderEv",
        confidence="verified",
    ),
    dict(
        key="kage_manager_present_no_swap",
        category="kage_frame_boundary",
        symbol="KAGE::Graphics::Manager::PresentWithoutSwap()",
        mangled="_ZN4KAGE8Graphics7Manager18PresentWithoutSwapEv",
        offset=0x4F2FD0,
        declared="fd 7b be a9 f3 0b 00 f9 fd 03 00 91 08 00 40 f9",
        inst="与 Present 同构，但最后一条尾调用 vptr+0x100 传 w1=0",
        role="对照项：证明 Present 系列的共同主体是 ManagerBase::Present",
        evidence="反汇编 0x4F2FD0..0x4F3010；调用者扫描（BL/B）0 个 ⇒ 本体自身不使用它",
        confidence="verified",
    ),
    dict(
        key="kage_manager_base_present",
        category="kage_frame_boundary",
        symbol="KAGE::Graphics::ManagerBase::Present()",
        mangled="_ZN4KAGE8Graphics11ManagerBase7PresentEv",
        offset=0x4EF7DC,
        declared="fd 7b be a9 f4 4f 01 a9 fd 03 00 91 1f 0c 00 f9",
        inst="0x4EF7E8 str xzr,[x0,#0x18]（当前 shader 指针清零）；"
             "0x4EF7FC bl ImageManager::apply_frame_images（提交本帧全部批次）；"
             "0x4EF804 bl ImageManager::clear_frame_images（清空本帧图像队列）；"
             "0x4EF814 s_DepthValue = -128.0f；0x4EF818 再次 str xzr,[x19,#0x18]；"
             "0x4EF830 br [vptr+0xD8]（尾调用）",
        role="**帧提交点本体**：本帧积压的绘制在这里被 apply 并随后 clear",
        evidence="反汇编 0x4EF7DC..0x4EF830；两条 bl 的 PLT 槽分别解析为 "
                 "_ZN4KAGE8Graphics12ImageManager18apply_frame_imagesEv / "
                 "_ZN4KAGE8Graphics12ImageManager18clear_frame_imagesEv",
        confidence="verified",
    ),
    dict(
        key="kage_manager_base_present_clear_shader",
        category="kage_frame_boundary",
        symbol="ManagerBase::Present 里清当前 shader 的那条 str",
        mangled=None,
        offset=0x4EF7E8,
        declared="1f 0c 00 f9 f4 2d 00 b0 94 ae 43 f9 f3 03 00 aa",
        inst="0x4EF7E8: str xzr, [x0, #0x18] —— 把 g_Manager+0x18 置 0",
        role="Present 之后「当前 shader」为空：这是判定「Present 后绘制上下文已不完整」的关键字面量证据",
        evidence="字段语义由 0x4EF8A8 ManagerBase::SetShader（str x1,[x0,#0x18]; ret）与 "
                 "0x4EF8B0 ManagerBase::GetShader（ldr x0,[x0,#0x18]; ret）确认；"
                 "0x4F3644 Manager::get_predefined_shader 也读同一个 +0x18",
        confidence="verified",
    ),
    dict(
        key="kage_apply_frame_images_entry",
        category="kage_frame_boundary",
        symbol="KAGE::Graphics::ImageManager::apply_frame_images()",
        mangled="_ZN4KAGE8Graphics12ImageManager18apply_frame_imagesEv",
        offset=0x4D3740,
        declared="ff c3 01 d1 fd 7b 01 a9 fd 43 00 91 fc 6f 02 a9",
        inst="函数入口（sub sp,sp,#0x70）",
        role="「本帧绘制真正上屏」的唯一落点",
        evidence="调用者扫描（BL/B）**只有 1 个**：0x4EF7FC，位于 _ZN4KAGE8Graphics11ManagerBase7PresentEv +0x20",
        confidence="verified",
    ),
    dict(
        key="kage_clear_frame_images_entry",
        category="kage_frame_boundary",
        symbol="KAGE::Graphics::ImageManager::clear_frame_images()",
        mangled="_ZN4KAGE8Graphics12ImageManager18clear_frame_imagesEv",
        offset=0x4D2BDC,
        declared="fd 7b bd a9 f5 0b 00 f9 fd 03 00 91 f4 4f 02 a9",
        inst="函数入口",
        role="帧队列清空；清空后图像上的「已入队」标志被复位，之后的绘制会重新入队（进入下一帧）",
        evidence="调用者扫描（BL/B）**只有 1 个**：0x4EF804，位于 ManagerBase::Present +0x28；"
                 "函数体内对图像字段做位清零（0x4D2C10 and x11,x11,#0xfffffffffffffffb 等）",
        confidence="verified",
    ),
    dict(
        key="kage_add_frame_image_entry",
        category="kage_frame_boundary",
        symbol="KAGE::Graphics::ImageManager::add_frame_image(ImageBase*)",
        mangled="_ZN4KAGE8Graphics12ImageManager15add_frame_imageEPNS0_9ImageBaseE",
        offset=0x4D3640,
        declared="fd 7b bb a9 f9 0b 00 f9 fd 03 00 91 f8 5f 02 a9",
        inst="函数入口",
        role="绘制入队点：绘制不是立即提交，而是把图像挂到「本帧待提交列表」",
        evidence="调用者扫描（BL/B）只有 1 个：0x4D0A90，位于 _ZN4KAGE8Graphics9ImageBase10apply_dataERKNS0_10SourceQuadERKNS0_15DestinationQuadERKNS0_5ColorESA_SA_SA_ +0x57C",
        confidence="verified",
    ),
    dict(
        key="kage_apply_data_add_frame_image_site",
        category="kage_frame_boundary",
        symbol="ImageBase::apply_data(...) 里的 add_frame_image 调用点",
        mangled=None,
        offset=0x4D0A90,
        declared="20 be 06 94 68 42 40 39 f4 07 40 f9 c8 01 28 37",
        inst="0x4D0A90: bl 0x680310（→ ImageManager::add_frame_image）；"
             "前置判断 0x4D0A6C ldrb w8,[x19,#0xAD] / 0x4D0A70 cbz w8 → 只有「本帧还没入队」才调用",
        role="证明「画一个 Sprite」在 KAGE 里只是入队；真正提交发生在 Present",
        evidence="反汇编 0x4D0A6C..0x4D0A94；PLT 槽 → _ZN4KAGE8Graphics12ImageManager15add_frame_imageEPNS0_9ImageBaseE",
        confidence="verified",
    ),
    dict(
        key="kage_get_batch_entry",
        category="kage_frame_boundary",
        symbol="KAGE::Graphics::ImageBase::get_batch(bool)",
        mangled="_ZN4KAGE8Graphics9ImageBase9get_batchEb",
        offset=0x4D1368,
        declared="ff c3 01 d1 fd 7b 01 a9 fd 43 00 91 fb 13 00 f9",
        inst="函数入口；内部 0x4D139C bl GetBlendMode、0x4D13A8 bl GetShader（都作用于 g_Manager），"
             "新批次路径 0x4D1450 str x23,[x8,#0x10]（batch->shader = 当前 shader），"
             "0x4D1454 bl ShaderBase::GetState（对「当前 shader」解引用）",
        role="绘制取批次的路径：批次会记录「当前 shader」指针与其 state；若当前 shader 为 NULL，"
             "则 NULL 被写进批次、并对 NULL 调 GetState",
        evidence="反汇编 0x4D1368..0x4D1484；GetShader/SetShader 的字段 +0x18 语义见上；"
                 "ShaderBase::GetState = 0x4F177C `add x0,x0,#0x38; ret`（无解引用），"
                 "故 NULL shader 会得到 state 指针 0x38，后续 State::eq 会读 0x38",
        confidence="inferred",
    ),
    dict(
        key="kage_shader_pointer_clear_again",
        category="kage_frame_boundary",
        symbol="ManagerBase::Present 尾部的第二次 str xzr,[x19,#0x18]",
        mangled=None,
        offset=0x4EF818,
        declared="7f 0e 00 f9 68 02 40 f9 01 6d 40 f9 e0 03 13 aa",
        inst="0x4EF818: str xzr, [x19, #0x18]（x19 = this，与入口同一次清零重复）",
        role="即便 Present 期间有人重绑 shader，返回前也会再清一次 ⇒ 我们的派发点必然看到 shader = NULL",
        evidence="反汇编 0x4EF808..0x4EF830",
        confidence="verified",
    ),
    dict(
        key="kage_begin_scene_entry",
        category="kage_frame_boundary",
        symbol="KAGE::Graphics::Manager::begin_scene(bool)",
        mangled="_ZN4KAGE8Graphics7Manager11begin_sceneEb",
        offset=0x4F388C,
        declared="ff 83 01 d1 fd 7b 02 a9 fd 83 00 91 f7 1b 00 f9",
        inst="函数入口；+0xC4 处 bl CommandBuffer::Begin（0x4F40C8）",
        role="「批次开始」判据：每个批次提交前由 ImagePlatformBase::RenderTexturedTriangles 调用",
        evidence="调用者扫描唯一：0x4F56D8，位于 _ZN4KAGE8Graphics17ImagePlatformBase23RenderTexturedTrianglesENS0_9BlendModeEPNS0_10ShaderBaseEPvijPti（函数体 [0x4F5684,0x4F5774)）",
        confidence="verified",
    ),
    dict(
        key="kage_end_scene_entry",
        category="kage_frame_boundary",
        symbol="KAGE::Graphics::Manager::end_scene(bool)",
        mangled="_ZN4KAGE8Graphics7Manager9end_sceneEb",
        offset=0x4F3A5C,
        declared="ff c3 01 d1 fd 7b 03 a9 fd c3 00 91 f7 23 00 f9",
        inst="函数入口；+0x90 处 bl CommandBuffer::End（0x4F4164）",
        role="「批次结束」判据；但在本移植版里 **全镜像无人调用**（既无 BL 也无 B）",
        evidence="调用者扫描（BL + B）0 个；其唯一的外部参照来自 vtable 槽 "
                 "_ZTVN4KAGE8Graphics7ManagerE+0x110；对照项 RenderTexturedTriangles 只调 begin_scene "
                 "后尾调用 render_vertices，不调用 end_scene",
        confidence="verified",
    ),
    dict(
        key="kage_command_buffer_begin",
        category="kage_frame_boundary",
        symbol="KAGE::Graphics::CommandBuffer::Begin(nn::gfx::TColorTargetView<...>)",
        mangled="_ZN4KAGE8Graphics13CommandBuffer5BeginEPN2nn3gfx16TColorTargetViewINS3_12ApiVariationINS3_7ApiTypeILi4EEENS3_10ApiVersionILi8EEEEEEE",
        offset=0x4F40C8,
        declared="fd 7b bd a9 f5 0b 00 f9 fd 03 00 91 f4 4f 02 a9",
        inst="函数入口",
        role="nngfx 命令缓冲开始；唯一调用者 begin_scene",
        evidence="调用者扫描唯一：0x4F3950，位于 Manager::begin_scene +0xC4",
        confidence="verified",
    ),
    dict(
        key="kage_command_buffer_end",
        category="kage_frame_boundary",
        symbol="KAGE::Graphics::CommandBuffer::End()",
        mangled="_ZN4KAGE8Graphics13CommandBuffer3EndEv",
        offset=0x4F4164,
        declared="fd 7b be a9 f3 0b 00 f9 fd 03 00 91 f3 03 00 aa",
        inst="函数入口",
        role="nngfx 命令缓冲结束；唯一调用者 end_scene（而 end_scene 自身无人调用）",
        evidence="调用者扫描唯一：0x4F3AEC，位于 Manager::end_scene +0x90",
        confidence="verified",
    ),
    dict(
        key="kage_image_platform_render_triangles",
        category="kage_frame_boundary",
        symbol="KAGE::Graphics::ImagePlatformBase::RenderTexturedTriangles(...)",
        mangled="_ZN4KAGE8Graphics17ImagePlatformBase23RenderTexturedTrianglesENS0_9BlendModeEPNS0_10ShaderBaseEPvijPti",
        offset=0x4F5684,
        declared="ff c3 01 d1 fd 7b 01 a9 fd 43 00 91 fc 6f 02 a9",
        inst="+0x54 bl begin_scene；随后 GetOffset/VerifySize/bind_image/apply_blend_mode/bind_shader；"
             "尾部 `b Manager::render_vertices`（0x4F3518）",
        role="真正的 GPU 提交函数：批次在这里被 begin_scene 包住并 draw",
        evidence="反汇编 0x4F5684..0x4F5770；下一符号 _ZN4KAGE8Graphics17ImagePlatformBase28swap_graphics_buffer_objectsEv = 0x4F5774",
        confidence="verified",
    ),
    dict(
        key="kage_manager_base_clear",
        category="kage_frame_boundary",
        symbol="KAGE::Graphics::ManagerBase::Clear()",
        mangled="_ZN4KAGE8Graphics11ManagerBase5ClearEv",
        offset=0x4EF738,
        declared="ff c3 00 d1 fd 7b 01 a9 fd 43 00 91 f3 13 00 f9",
        inst="函数入口；+0x50 处调用 ImageManager::purge_old_vertex_buffers",
        role="清屏兼回收顶点缓冲；被 Manager::Render 的色修正分支调用（0x3F989C / 0x3F99BC）",
        evidence="调用者扫描 purge_old_vertex_buffers 唯一：0x4EF788，位于 ManagerBase::Clear +0x50",
        confidence="verified",
    ),
    dict(
        key="kage_get_current_render_target",
        category="kage_frame_boundary",
        symbol="KAGE::Graphics::ManagerBase::GetCurrentRenderTarget()",
        mangled="_ZN4KAGE8Graphics11ManagerBase22GetCurrentRenderTargetEv",
        offset=0x4EF71C,
        declared="00 10 40 f9 c0 03 5f d6 08 10 40 f9 28 00 00 f9",
        inst="ldr x0,[x0,#0x20]; ret（渲染目标指针在 +0x20；可见 Present 里清的 +0x18 不是渲染目标）",
        role="运行期可读的「当前渲染目标」判据（+0x20）；Present 不清它",
        evidence="反汇编 0x4EF71C；对照 SetRenderTargetScreen 0x4EF70C（str xzr,[x0,#0x20] 且 [x0,#0x28]=1）"
                 "与 SetRenderTargetTexture 0x4EF6FC（str x1,[x0,#0x20]）",
        confidence="verified",
    ),
)

# ---------------------------------------------------------------------------
# 需要现场扫描「谁调用了谁」的目标符号
# ---------------------------------------------------------------------------
CALLER_TARGETS = (
    "_ZN15IsaacRepentance10ModManager13RunModScriptsEv",
    "_ZN15IsaacRepentance10ModManager12RunModScriptERNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEE",
    "_ZN15IsaacRepentance10ModManager5ResetEv",
    "_ZN15IsaacRepentance10ModManager8ListModsEv",
    "_ZN15IsaacRepentance10ModManager11LoadConfigsEv",
    "_ZN15IsaacRepentance10ModManager10UnloadModsEv",
    "_ZN15IsaacRepentance10ModManager17PreLanguageSwitchEv",
    "_ZN15IsaacRepentance10ModManager18PostLanguageSwitchEv",
    "_ZN15IsaacRepentance10ModManager15TryRedirectPathERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEE",
    "_ZN15IsaacRepentance7Manager6UpdateEv",
    "_ZN15IsaacRepentance7Manager6RenderEv",
    "_ZN15IsaacRepentance11IsaacRenderEv",
    "_ZN15IsaacRepentance11IsaacUpdateEv",
    "_ZN4KAGE8Graphics12ImageManager18apply_frame_imagesEv",
    "_ZN4KAGE8Graphics12ImageManager18clear_frame_imagesEv",
    "_ZN4KAGE8Graphics7Manager7PresentEv",
    "_ZN4KAGE8Graphics11ManagerBase7PresentEv",
    "_ZN4KAGE8Graphics12ImageManager15add_frame_imageEPNS0_9ImageBaseE",
    "_ZN4KAGE8Graphics7Manager11begin_sceneEb",
    "_ZN4KAGE8Graphics7Manager9end_sceneEb",
    "_ZN4KAGE8Graphics13CommandBuffer5BeginEPN2nn3gfx16TColorTargetViewINS3_12ApiVariationINS3_7ApiTypeILi4EEENS3_10ApiVersionILi8EEEEEEE",
    "_ZN4KAGE8Graphics13CommandBuffer3EndEv",
    "_ZN4KAGE8Graphics7Manager21set_predefined_shaderENS0_17ePredefinedShaderE",
    "_ZN15IsaacRepentance10LoadShaderENS_7eShaderE",
)

# 需要在整份镜像里做原始字节搜索的 token（用来证明「本体没有 Lua / 没有回调名」）
STRING_TOKENS = (
    b"MC_POST_RENDER", b"MC_POST_UPDATE", b"MC_PRE_RENDER", b"ModCallbacks",
    b"AddCallback", b"RunCallback", b"CallCallback", b"ExecuteCallback",
    b"CallbackList", b"LuaEngine", b"lua_pcall", b"luaL_newstate", b"luaL_register",
    b"attempt to index", b"stack traceback", b"bad argument #",
    b"Lua 5.3", b"Lua 5.1", b"LuaJIT", b"LUAJIT",
    b"main.lua", b".lua", b"isaac_mods", b"metadata.xml",
)

# 需要现场枚举「完整调用序列」的函数区间
DECODE_RANGES = (
    ("manager_update_calls", 0x3F8DB8, 0x3F911C),
    ("manager_render_calls", 0x3F9684, 0x3F9B58),
    ("run_repentance_calls", 0x4C0840, 0x4C0AE0),
    ("isaac_render_calls", 0x3B3820, 0x3B3854),
    ("manager_base_present_calls", 0x4EF7DC, 0x4EF834),
    ("apply_frame_images_calls", 0x4D3740, 0x4D3940),
)

VTABLE_TARGETS = (
    "_ZTVN4KAGE8Graphics7ManagerE",
    "_ZTVN4KAGE8Graphics11ManagerBaseE",
)


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _hex16(data: bytes, offset: int) -> str:
    return data[offset:offset + 16].hex(" ")


def _scan_plt_stubs(data: bytes) -> dict[int, int]:
    """``adrp x16; ldr x17,[x16,#imm]; add x16,x16,#imm; br x17`` → {GOT 槽: 桩地址}。"""
    stubs: dict[int, int] = {}
    for index in range(len(data) // 4 - 3):
        pc = index * 4
        w0, w1, w2, w3 = struct.unpack_from("<4I", data, pc)
        if (w0 & 0x9F000000) != 0x90000000 or (w0 & 0x1F) != 16:
            continue
        if (w1 & 0xFFC00000) != 0xF9400000 or (w1 & 0x1F) != 17 or ((w1 >> 5) & 0x1F) != 16:
            continue
        if (w2 & 0xFFC00000) != 0x91000000 or (w2 & 0x1F) != 16 or ((w2 >> 5) & 0x1F) != 16:
            continue
        if w3 != 0xD61F0220:
            continue
        immhi = (w0 >> 5) & 0x7FFFF
        immlo = (w0 >> 29) & 3
        imm = (immhi << 2) | immlo
        if imm & (1 << 20):
            imm -= 1 << 21
        page = (pc & ~0xFFF) + (imm << 12)
        stubs.setdefault(page + ((w1 >> 10) & 0xFFF) * 8, pc)
    return stubs


def _branch_targets(data: bytes) -> tuple[dict[int, list[int]], dict[int, list[int]]]:
    bl: dict[int, list[int]] = {}
    b: dict[int, list[int]] = {}
    for index in range(len(data) // 4):
        word = struct.unpack_from("<I", data, index * 4)[0]
        pc = index * 4
        if (word & 0xFC000000) == 0x94000000:
            imm = word & 0x03FFFFFF
            if imm & 0x02000000:
                imm -= 0x04000000
            bl.setdefault(pc + imm * 4, []).append(pc)
        elif (word & 0xFC000000) == 0x14000000:
            imm = word & 0x03FFFFFF
            if imm & 0x02000000:
                imm -= 0x04000000
            b.setdefault(pc + imm * 4, []).append(pc)
    return bl, b


class Nro:
    def __init__(self, path: str) -> None:
        with open(path, "rb") as handle:
            self.data = handle.read()
        self.build_id, self.symbols = nro_symbols.parse_dynamic_symbols(self.data)
        _, self.relocations = nro_symbols.parse_dynamic_relocations(self.data)
        self.size = len(self.data)
        self.sha256 = hashlib.sha256(self.data).hexdigest()
        self.stubs = _scan_plt_stubs(self.data)
        self.bl, self.b = _branch_targets(self.data)
        self._slot_name: dict[int, str] = {}
        for name, items in self.relocations.items():
            for relocation in items:
                if relocation.table == "jmprel":
                    self._slot_name.setdefault(relocation.offset, name)
        self._functions = sorted(
            (s.file_offset, n) for n, s in self.symbols.items()
            if s.file_offset is not None and s.file_offset < 0x800000
        )
        self._function_offsets = [o for o, _ in self._functions]

    # -- 查询 ---------------------------------------------------------
    def symbol_at(self, offset: int) -> str | None:
        for name, sym in self.symbols.items():
            if sym.file_offset == offset:
                return name
        return None

    def containing_function(self, address: int) -> tuple[int | None, str | None]:
        index = bisect.bisect_right(self._function_offsets, address) - 1
        if index < 0:
            return None, None
        return self._functions[index]

    def callers(self, name: str, want_branch: bool = True) -> list[dict]:
        out: list[dict] = []
        for relocation in self.relocations.get(name, []):
            if relocation.table != "jmprel":
                continue
            stub = self.stubs.get(relocation.offset)
            if stub is None:
                continue
            for site in self.bl.get(stub, []):
                out.append(self._caller_record("bl", site))
            if want_branch:
                for site in self.b.get(stub, []):
                    out.append(self._caller_record("b", site))
        out.sort(key=lambda item: item["site"])
        return out

    def _caller_record(self, kind: str, site: int) -> dict:
        offset, function = self.containing_function(site)
        return {
            "kind": kind,
            "site": f"0x{site:X}",
            "function_offset": f"0x{offset:X}" if offset is not None else None,
            "function": function,
            "delta_in_function": f"+0x{site - offset:X}" if offset is not None else None,
        }

    def decode_calls(self, start: int, end: int) -> list[dict]:
        rows: list[dict] = []
        for site in range(start, end, 4):
            word = struct.unpack_from("<I", self.data, site)[0]
            if (word & 0xFC000000) == 0x94000000:
                kind = "bl"
            elif (word & 0xFC000000) == 0x14000000:
                kind = "b"
            else:
                continue
            imm = word & 0x03FFFFFF
            if imm & 0x02000000:
                imm -= 0x04000000
            target = site + imm * 4
            name = self._slot_name.get(self._stub_slot(target)) if target in set(self.stubs.values()) else None
            rows.append({
                "site": f"0x{site:X}",
                "kind": kind,
                "target": f"0x{target:X}",
                "symbol": name,
            })
        return rows

    def _stub_slot(self, stub_address: int) -> int | None:
        for slot, stub in self.stubs.items():
            if stub == stub_address:
                return slot
        return None

    def vtable_slots(self, symbol: str) -> list[dict]:
        base = self.symbols[symbol].file_offset
        if base is None:
            return []
        slot_names: dict[int, str] = {}
        for name, items in self.relocations.items():
            for relocation in items:
                if relocation.table == "rela":
                    slot_names.setdefault(relocation.offset, name)
        rows: list[dict] = []
        for index in range(0, 0x130 // 8):
            offset = base + index * 8
            name = slot_names.get(offset)
            if name:
                rows.append({"vtable_byte_offset": f"0x{index * 8:X}", "target_symbol": name})
        return rows


def build(nro: Nro, printed: bool) -> dict:
    # ---- 1. 记录表 + 逐字节比对 -------------------------------------
    records: list[dict] = []
    mismatches: list[str] = []
    for spec in RECORDS:
        offset = spec["offset"]
        actual = _hex16(nro.data, offset)
        declared = spec["declared"]
        entry = {
            "key": spec["key"],
            "category": spec["category"],
            "symbol": spec["symbol"],
            "mangled": spec["mangled"],
            "native_offset": f"0x{offset:X}",
            "expected_bytes": actual,
            "instruction": spec["inst"],
            "role": spec["role"],
            "evidence": spec["evidence"],
            "confidence": spec["confidence"],
        }
        if declared is None:
            entry["byte_check"] = {
                "declared_bytes": None,
                "actual_bytes": actual,
                "match": None,
                "note": "纯符号存在性证据，未做 16 字节断言",
            }
        else:
            match = declared.strip() == actual.strip()
            entry["byte_check"] = {
                "declared_bytes": declared,
                "actual_bytes": actual,
                "match": match,
            }
            if not match:
                mismatches.append(spec["key"])
        records.append(entry)

    if mismatches:
        raise SystemExit(f"字节比对失败，声明与真实 NRO 不一致: {mismatches}")

    # ---- 2. 符号表现场筛选 -----------------------------------------
    callback_like = {}
    for name in nro.symbols:
        lowered = name.lower()
        if "callback" in lowered or "addcallback" in lowered or "runcallback" in lowered:
            callback_like[name] = (
                f"0x{nro.symbols[name].file_offset:X}" if nro.symbols[name].file_offset else None
            )
    lua_symbols = sorted(
        name for name in nro.symbols
        if name.startswith("lua") or name.startswith("luaL") or name.startswith("lua_")
    )
    mod_symbols = sorted(
        (nro.symbols[name].file_offset, name)
        for name in nro.symbols
        if name.startswith("_ZN15IsaacRepentance10ModManager") and nro.symbols[name].file_offset
    )
    mod_symbols_all = sorted(
        nro.symbols[name].file_offset for name in nro.symbols
        if "10ModManager" in name and nro.symbols[name].file_offset
    )
    mod_entry_symbols = sorted(
        (nro.symbols[name].file_offset, name)
        for name in nro.symbols
        if "ModEntry" in name and nro.symbols[name].file_offset
    )

    symbol_screen = {
        "callback_like_lines_total": len(callback_like),
        "callback_like_in_isaac_repentance": sorted(
            n for n in callback_like
            if n.startswith("_ZN15IsaacRepentance") and "Menu_DailyChallenge" not in n
        ),
        "callback_like_in_kage": sorted(n for n in callback_like if n.startswith("_ZN4KAGE")),
        "callback_like_note": "命中项全部是 nn::nex / KAGE 系统回调与本项目既有的 Menu_DailyChallenge 排行榜回调；"
                              "没有任何 mod 回调注册/派发符号",
        "lua_c_api_symbols": lua_symbols,
        "mod_manager_method_count": len(mod_symbols),
        "mod_manager_symbol_count_including_const": len(mod_symbols_all),
        "mod_manager_methods": [{"offset": f"0x{o:X}", "symbol": n} for o, n in mod_symbols],
        "mod_entry_symbols": [{"offset": f"0x{o:X}", "symbol": n} for o, n in mod_entry_symbols],
        "dynsym_total": len(nro.symbols),
    }

    # PLT/交叉引用完备性：每个 .jmprel 槽都必须能还原到唯一一个桩，否则「0 个调用者」不可信
    jmprel_slots = {
        r.offset for items in nro.relocations.values() for r in items if r.table == "jmprel"
    }
    stub_slots = set(nro.stubs)
    plt_integrity = {
        "jmprel_slot_count": len(jmprel_slots),
        "plt_stub_count": len(set(nro.stubs.values())),
        "slots_without_stub": len(jmprel_slots - stub_slots),
        "stub_slots_without_reloc": len(stub_slots - jmprel_slots),
        "stub_addresses_are_distinct": len(set(nro.stubs.values())) == len(stub_slots),
        "every_jmprel_slot_has_its_own_stub": (
            len(jmprel_slots - stub_slots) == 0
            and len(set(nro.stubs.values())) == len(stub_slots)
        ),
        "note": "7442 个 jmprel 槽全部有桩，且桩地址互不共享（7443 个桩里多出的 1 个没有 jmprel 记录）；"
                "因此「把槽还原成桩、再扫 BL/B」得到的调用者数是完备计数，不是抽样",
    }

    # 运行期探针会需要的 GOT 槽（数据符号，`.rela` GLOB_DAT 给出）
    data_slots = {}
    for name in (
        "_ZN15IsaacRepentance9g_ManagerE",
        "_ZN4KAGE8Graphics9g_ManagerE",
        "_ZN4KAGE8Graphics14g_ImageManagerE",
        "_ZN15IsaacRepentance17g_ModdingDataPathE",
    ):
        entries = [
            {"table": r.table, "slot": f"0x{r.offset:X}", "type": r.relocation_type}
            for r in nro.relocations.get(name, []) if r.table == "rela"
        ]
        data_slots[name] = entries

    # ---- 3. 原始字节搜索（本体有没有 Lua / 有没有回调名） -------------
    token_hits = {}
    for token in STRING_TOKENS:
        positions = []
        start = 0
        while True:
            found = nro.data.find(token, start)
            if found == -1:
                break
            positions.append(f"0x{found:X}")
            start = found + 1
            if len(positions) >= 8:
                break
        token_hits[token.decode("ascii", "replace")] = {
            "count_capped_at_8": len(positions),
            "first_offsets": positions,
        }

    # ---- 4. 交叉引用 -------------------------------------------------
    callers = {}
    for name in CALLER_TARGETS:
        sym = nro.symbols.get(name)
        callers[name] = {
            "offset": f"0x{sym.file_offset:X}" if sym and sym.file_offset else None,
            "callers": nro.callers(name),
        }

    # ---- 5. 调用序列 -------------------------------------------------
    decoded = {
        label: nro.decode_calls(start, end)
        for label, start, end in DECODE_RANGES
    }

    # ---- 6. vtable ---------------------------------------------------
    vtables = {name: nro.vtable_slots(name) for name in VTABLE_TARGETS}

    json_records = records
    if printed:
        for entry in json_records:
            print(f"{entry['native_offset']}  {entry['expected_bytes']}  {entry['symbol']}")

    xref_summary = {
        name: {
            "offset": payload_callers["offset"],
            "caller_count": len(payload_callers["callers"]),
            "callers": payload_callers["callers"],
        }
        for name, payload_callers in callers.items()
    }

    return {
        "stage": 151,
        "topic": "engine-owned mod callback dispatch & KAGE frame boundary (read-only audit)",
        "build_id": nro.build_id,
        "nro": os.path.relpath(NRO, REPO_ROOT),
        "nro_sha256": nro.sha256,
        "nro_size": nro.size,
        "module_offset_equals_file_offset": True,
        "tooling": TOOLING,
        "record_count": len(json_records),
        "records": json_records,
        "byte_comparison": {
            "records_compared": sum(1 for r in json_records if r["byte_check"]["match"] is not None),
            "records_matched": sum(1 for r in json_records if r["byte_check"]["match"] is True),
            "mismatches": [],
            "note": "每条记录的 expected_bytes 都是从真实 Repentance.nro 抄下的 16 字节，"
                    "脚本逐字节比对；不一致会直接退出。",
        },
        "symbol_screen": symbol_screen,
        "plt_integrity": plt_integrity,
        "data_slots": data_slots,
        "string_screen": token_hits,
        "cross_references": callers,
        "cross_reference_summary": xref_summary,
        "decoded_calls": decoded,
        "vtables": vtables,
        "answers": {
            "engine_owns_mod_callback_dispatch": False,
            "engine_mod_script_runner": "IsaacRepentance::ModManager::RunModScripts (0x421838) = ret；"
                                        "RunModScript (0x42183C) = return false；两者调用者均为 0",
            "engine_mod_path_redirect": "IsaacRepentance::ModManager::TryRedirectPath (0x41FB04) = "
                                        "把入参 std::string 复制一份返回（恒等桩），不读任何 mod 目录",
            "relay_timing_vs_frame_flush": "我们的 MC_POST_RENDER 派发点在 Manager::Render 的 ret 之后，"
                                           "而 Manager::Render 的最后一次调用是 Graphics::Manager::Present "
                                           "(0x3F9B40)，Present 内部执行 apply_frame_images + clear_frame_images："
                                           "派发时本帧绘制已提交并已清空队列",
            "current_shader_after_present": "g_Manager+0x18（ManagerBase 的当前 shader）在 "
                                            "ManagerBase::Present 被两次清零（0x4EF7E8 / 0x4EF818），"
                                            "故我们的派发点必然读到 shader = NULL",
        },
        "open_questions": [
            "「Present 之后一次绘制是否必然崩」目前是 inferred：ImageBase::get_batch 会把 g_Manager+0x18 "
            "（可能为 NULL）写进批次并对它调 ShaderBase::GetState（= 0x4F177C `add x0,x0,#0x38; ret`，"
            "返回 0x38），随后 State::eq 会访问 0x38。缺少的证据是「真机上在该点调用一次 Sprite:Render 的行为」"
            "（崩溃报告 or 探针读 g_Manager+0x18）。",
            "本体是否在别处（例如未导出的局部函数）派发 mod 回调：本审计的调用者扫描是完备的"
            "（每个 .jmprel 槽都能还原到 PLT 桩，BL/B 全镜像扫描），但未导出函数若通过函数指针数组调用则无法覆盖；"
            "已用「本体无 Lua C API 符号 + 无 Lua 错误字符串」作为反向证据。",
            "ModManager::Reset 0 调用者 ⇒ 本体从不枚举 mod；因此 g_ModdingDataPath 下的 mod 目录在本体流程里"
            "实际只被内容加载器（EntityConfig/ItemConfig/Music/... 的 Load(..., ModEntry*)）间接使用。"
            "需要真机读 g_ModdingDataPath 与其使用者以确认运行时取值。",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--nro", default=NRO)
    parser.add_argument("--print", action="store_true", dest="printed")
    args = parser.parse_args()

    nro = Nro(args.nro)
    payload = build(nro, args.printed)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"wrote {args.out}")
    print(f"build_id={payload['build_id']} size={payload['nro_size']} sha256={payload['nro_sha256']}")
    print(f"records={payload['record_count']} "
          f"byte_matched={payload['byte_comparison']['records_matched']}/"
          f"{payload['byte_comparison']['records_compared']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
