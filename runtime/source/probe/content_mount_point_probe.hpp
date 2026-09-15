#pragma once

#include <cstdint>

// Stage 147 probe: can a mod's `resources/` directory be registered as a KAGE
// content mount point, and does the engine then resolve files that only exist in
// the Atmosphere SD overlay tree?
//
// Stage-146 static evidence (`analysis/stage146-content-mount-point/`) decoded the
// whole path: `g_ContentManager->AddMountPoint(ContentMountPointPath("dir"))`
// allocates a `ContentMountPoint`, `strdup`s the path and calls
// `ContentMountPoint::Build("")`, which tail-calls `FileMap::build(path, "")`;
// that enumerates the directory through `ContentManager::get_directory_entries`
// (`nn::fs::OpenDirectory`/`ReadDirectory`) and inserts every file under its path
// *relative to the mount point*, recursing into subdirectories.
//
// Three things can still only be answered on hardware, and this probe answers all
// three in one session:
//
//   1. does `nn::fs::OpenDirectory` succeed for a `rom:/…` path that exists only
//      in the SD overlay (LayeredFS directory merging)?
//   2. does the appended mount point actually answer lookups for a *new* file name?
//   3. is the mount point consulted before the base `resources` mount point for a
//      same-named file?
//
// Payload (`ISAACMP1` in x1), one shot per session:
//   x2  status bitmask, bits 0..6 guard checks for the called functions
//       (bit0 path ctor, bit1 AddMountPoint, bit2 path dtor,
//        bit3 DoesMountPointExist, bit4 GetFileMountPoint,
//        bit5 GetMountedFilePath, bit6 GetMountPoints),
//       bit7 the rebuild function's guard matched,
//       bit8 module found, bit9 manager non-null,
//       bit10 baseline file resolves before registration,
//       bit11 test mount point absent before registration,
//       bit12 test mount point present after registration,
//       bit13 test file resolves after registration,
//       bit14 the resolved mount point path names our mod directory,
//       bit15 a same-named base-game file is answered by our mount point after
//             registration (priority),
//       bit16 the engine's own rebuild dropped the mount point (expected),
//       bit17 re-registering restored it (the restoring strategy the relay uses),
//       bit18 the new file resolves again after the rebuild + re-register
//   x3  game module base of the copy that ran (compare with the crash report's module)
//   x4  the `g_ContentManager` pointer we used
//   x5  first 8 bytes of the resolved mount point path for the test file
//   x6  first 8 bytes of the resolved mount point path for the baseline file
//   x7  first 8 bytes of `GetMountedFilePath`'s allocated result for the test file
//   x8  first 8 bytes of the mount point path that answers the same-named file
//       (after registration), or 0
//   x9  mount points reported by `GetMountPoints` before / after the rebuild
//       (low 32 bits before, high 32 bits after)
//   x15 已登记 ModCallbacks 种类掩码（id 0..63；2026-09-12 起）
//   x16 同上（id 64..127）
//   x17 bits 0..31 有派发点的登记次数，bits 32.. 无派发点（登记了但不会触发）的登记次数
//   x18 bits 0..31 Lua 错误信息前 4 字节，bits 32.. 错误信息长度
//   x19 bits 0..31 清单 Mod 加载失败字（打包格式见 `kModLoadProbeMagic` 段），
//       bits 32.. 回调 Lua 错误标志（只读快照）
namespace isaac::runtime {
constexpr std::uint64_t kContentMountProbeMagic = 0x31504D4341415349ULL;  // "ISAACMP1"
// Mod（EID）加载失败的探针报错魔数。成功不报错，失败必须报错：
//   x1 "ISAACLDF"
//   x2 bit0 回调 Lua 错误标志（只读快照），bits 8..15 无派发点登记次数，
//      bits 32..47 清单 Mod 加载失败字（**打包格式**，见下）
//   x3 已登记回调种类掩码（id 0..63）  x4 同上（id 64..127）
//   x5 有派发点的登记次数              x6 无派发点的登记次数
//   x7 bits 32.. Lua 错误信息长度，bits 0..31 错误信息前 4 字节
//   x8 Lua 错误信息前 8 字节
//
// **2026-09-12 起加载失败字升级为打包格式**（`mod_load_step.hpp` 的 `PackModLoadWord`）：
//   bits 0..7   步骤：0 = 带脚本成功，1 = 清单读取，2 = 清单解析，3 = 路径拼接，4 = **入口脚本读取**，
//               5 = 只挂载内容（纯资源 Mod，不算失败），`0x10 + detail` = Lua 初始化失败
//   bits 8..15  失败原因（`StatusCode`：1 参数、2 状态、3 不支持、4 不存在、5 **缓冲区太小**、6 IO、7 损坏）
//   bits 16..47 读取失败时**观测到的文件长度**
// 判定"是不是失败"必须只看低 8 位（`IsModLoadFailureWord`），否则高位的原因/长度会被当成步骤。
// 这次升级的直接原因：真机报告 `01789200504` 只给出"步骤 4"，我们因此在一轮里没能看出
// EID 的 `main.lua`（87,328 字节）撞的是 16 KiB 的脚本缓冲区上限。
constexpr std::uint64_t kModLoadProbeMagic = 0x46444C4341415349ULL;  // "ISAACLDF"
// The existing update-callback break sits at `kProbeBreakAfterEntries` (1500) and a
// break ends the session, so this probe must fire first. Its own question is the open
// one; the older payload's answer is already in hand.
// 触发口径：**测试 Mod 自己画了多少帧**（Lua 全局 `STAGE149_FRAME`，只在房间内增长）。
// 换过两次口径才定下来：update 回调次数在加载阶段每秒上千次；渲染帧数同样被加载阶段猛渲染
// 烧掉大半 —— 两者都会让会话在"进房间后一两秒"就被探针自己结束，Mod 根本来不及画（2026-09-13
// 第四、五轮实测）。Mod 的绘制计数是唯一只在"玩家真的在房间里"时才增长的信号。
constexpr std::uint32_t kContentMountProbeAfterDraws = 600;
// 兜底口径：Mod 一直没画够帧数时（回调被摘除、加载失败、Mod 自己报错……）也要有一次报告，
// 否则现象就是"既不报错也没有显示"——2026-09-13 第七轮正是这样浪费了一轮。阈值按 update 回调
// 次数给（加载阶段每秒上千次，约等于运行二三十秒），刻意放得比绘制口径晚，只有主口径没出现时
// 才会成为先到的那一个。
constexpr std::uint32_t kContentMountProbeFallbackAfterUpdates = 30000;
// --- 引擎 ItemConfig 链探针（批次 2b，2026-09-13 修正）-------------------------
//
// 目的：`Isaac.GetItemConfig()` 与 `ItemConfig` 只读视图在实现之后，必须用一次真机报错确认
// **内嵌对象**这条链路（`IC = Manager + 0x36538`，加法）与 `733 × 8 == 0x16E8` 的长度硬判据。
//
// 上一版（`ISAACGP1` 那一轮）的 ItemConfig 部分读数全废，根因是**探针自己多解引用了一次**：
// 它把 `*(u64*)(M + 0x36538)`（实际是 `collectibles.begin()`）当成了 `ItemConfig*`，于是
// `items[0]/items[1]` 被当成 begin/end，`items[0] == 0` 时的 `readU64(0 + 8)` 又被可读性检查
// 静默拦掉 —— 与"条目指针为 0、第 6 位不亮"逐位吻合。本轮改成读 `IC + 0x00 / IC + 0x08`。
//
// **证据口径 = 只走栈转储**（2026-09-12 第三轮实机证据，2026-09-13 再次确认窗口边界）：
// 崩溃报告的 `Stack Dump` 段**精确等于 `[SP, SP+0x100)`**（256 字节 / 16 行）。所以负载数组
// 只能有 16 个 u64，而且必须落在 `SP + off`（`off + 128 <= 256`）—— 做法是把触发逻辑留在
// 外层、把所有读数和报错放进一个 `noinline` 的小函数 `ReportItemConfigSnapshot`，
// 让 `payload[16]` 成为它**唯一的大局部变量**。核对命令（build 之后在容器里跑）：
//   aarch64-none-elf-objdump -d runtime/<BUILD>/content_mount_point_probe.o
// 在 `ReportItemConfigSnapshot` 里读 `stp x29, x30, [sp, #-N]!`（帧 N 字节）与
// `add x2, sp, #off`（负载起点），判据是 `off + 16*8 <= 0x100`。
//
// 寄存器（`x5`–`x7` 实测不可信，禁止承载判据）：
//   x0 = 2（BreakReason_User）
//   x1 = "ISAACIG2"（`kItemConfigProbeMagic`）
//   x2 = **栈上负载数组的指针**（16 个 u64，字段表见下）
//   x3 = 状态位（9 位，见下）
//   x4 = `IC`（内嵌 `ItemConfig` 的地址 = `Manager + 0x36538`）
//
// 状态位（`x3`，共 9 位）：
//   bit0 `g_Manager` 槽可读且非 0        bit1 `Manager*` 非空
//   bit2 **`E - B == 0x16E8`（最硬）**   bit3 条目数 == 733
//   bit4 `items[0] == 0`（复现旧失败形态）  bit5 `items[1]` 非空
//   bit6 `items[1]` 的 `kind == 1 && id == 1`
//   bit7 `items[1]` 的名字可读（SSO 解析出 size/data，且前 min(size,8) 字节可打印）
//   bit8 `ItemConfig::GetCollectible(IC, 1) == items[1]`（引擎方法与我们自己的向量读一致）
//
// 负载数组（`x2` 指向，崩溃报告的 Stack Dump 里按小端 8 字节一组读出）：
//   [0] `*(u64*)(base+0xAAC648)`（槽内容，期望 `base+0xABCCE0`）
//   [1] `Manager*`                 [2] `IC = Manager + 0x36538`（**加法**）
//   [3] 收藏品向量 begin（`IC+0x00`）  [4] 收藏品向量 end（`IC+0x08`）
//   [5] `E - B`（期望 **0x16E8**）   [6] `(E-B)/8`（期望 **733**）
//   [7] `items[0]`（期望 0）        [8] `items[1]`
//   [9] `kind | id << 32`（`items[1]`，期望 1 | 1<<32）
//   [10] 名字前 8 字节（原始字节，期望可打印 ASCII）
//   [11] 名字 size（低 32 位）| `is_long`（bit32）
//   [12] `GetCollectible(IC, 1)` 的返回值（期望 == [8]）
//   [13] Trinket 向量的 `E - B`（`*(u64*)(IC+0x28) - *(u64*)(IC+0x18)`；预分配 190 项 = 0x5F0）
//   [14] 游戏模块 base（与崩溃报告的 `Address` 段互证"执行的是哪一份映像"）
//   [15] 本函数调用次数（低 32 位）| `players[0]` 非空（bit32）
//        —— 区分"等到了 players[0] 才报"与"兜底次数到了才报"
//
// 触发口径（与上一轮一致）：等到 `players[0]` 非空才报一次；另有 `kEnginePlayerProbeFallbackCalls`
// 调用次数兜底，每会话最多一次。
//
// **2026-09-13 批次 2c 起：本探针在回调错误探针（`ISAACERR`）报错之前不触发。**
// 上一轮它一进房间就崩（`players[0]` 非空即报），于是会话在 EID 的回调错误发生之前就结束了，
// 拿不到"回调报错"这条信息 —— 而本轮最需要的正是那条信息。做法见
// `kEnginePlayerProbeDeferUntilErrorProbe`。探针自身的触发时机（每帧判一次 `players[0]`）没有改，
// 只是在它前面加了一道"错误探针还没轮到就先让路"的前置判断。
constexpr std::uint64_t kItemConfigProbeMagic = 0x3247494341415349ULL;  // "ISAACIG2"
// 兜底：调用这么多次（每帧一次）还没看到玩家数组非空，也报一次，避免"没崩"被误读成"偏移又错了"。
constexpr std::uint32_t kEnginePlayerProbeFallbackCalls = 20000;
// --- 回调 Lua 错误探针（批次 2c，2026-09-13）----------------------------------
//
// 目的：把"回调里的 Lua 错误"变成可读证据。现象是 EID 能加载、游戏不崩、屏幕上什么都不显示；
// 而派发器在回调 `lua_pcallk` 失败时会 `luaL_unref` 并 `registry->Remove(...)` **静默摘除**那个
// 回调（`lua_runtime.cpp`），所以"MC_POST_RENDER 第一帧就报错"与"没有任何显示"是同一件事。
//
// 证据口径仍然是**只走栈转储**：崩溃报告的 `Stack Dump` 段精确等于 `[SP, SP+0x100)`，所以负载
// 数组只有 16 个 u64，且必须是报错那一帧的唯一大局部变量。做法与 `ISAACIG2` 相同：触发判定留在
// `IsaacModRuntime_ProbeCallbackError`（它的局部变量不需要进窗口），读数与 `svcBreak` 全部放进
// `noinline` 的 `ReportCallbackErrorSnapshot`，那里只有一个 `payload[16]`。核对命令：
//   aarch64-none-elf-objdump -d runtime/<BUILD>/content_mount_point_probe.o
// 在 `ReportCallbackErrorSnapshot` 里查 `stp x29, x30, [sp, #-N]!`（帧 N 字节）与负载数组的起点
// —— 实测形态是 `add x0, sp, #0x20` + `bl memset` + `mov x2, x0`（编译器用 `memset` 把数组清零，
// 返回值就是数组起点），等价于 `add x2, sp, #off`；判据是 `off + 16*8 <= 0x100`。
// 2026-09-13 实测：帧 160 字节、数组在 `sp+0x20`，`0x20 + 0x80 = 0xa0 <= 0x100`，整个数组在窗口内。
//
// 寄存器（`x5`–`x7` 实测不可信、`x8` 以上被 `svc` 清掉，所以只用 `x0`–`x4`）：
//   x0 = 2（`BreakReason_User`）
//   x1 = "ISAACERR"（`kCallbackErrorProbeMagic`）
//   x2 = **栈上负载数组的指针**（16 个 u64，字段表见下）
//   x3 = 状态位（8 位，见下；与 `payload[0]` 相同）
//   x4 = Lua 错误信息长度（与 `payload[1]` 相同）
//
// 状态位（`x3`）：
//   bit0 回调错误标志（`CallbackErrorPending()` 或诊断字 `word[12]`）
//   bit1 错误信息长度 > 0
//   bit2 错误信息可取（前 8 字节非零）
//   bit3 update 回调进入次数 > 0        bit4 render 回调进入次数 > 0
//   bit5 回调注册表非空                 bit6 已登记回调种类掩码非零
//   bit7 16 字诊断出口握手成功（返回 `ISAACHR1` 魔数）
//   bit8 **兜底口径**：报错是被 `kCallbackErrorProbeFallbackCalls` 顶出来的，不是"真的看到了
//        回调错误"（探针自己加的一位；bit8 为 1 时 bit0 按"本次会话没有回调错误"解释）
//   bit10 触发原因包含"运行时捕获到过 Lua 错误文本"（加载期失败也算；2026-09-12 第三轮后补：
//        加载期 `ScriptRunFailed` 不是"回调错误"，`CallbackErrorPending()` 一直是 0，
//        探针于是只能等兜底 —— 明明第一帧就有数据）
//   bits 32..63 模块假堆**已用 KiB**（`sbrk(0) - __fake_heap`；2026-09-12 起）。
//        真机报告 `01789202408` 里 Lua 抛 `LUA_ERRMEM`（"not enough memory"）时，模块堆只有
//        2 MiB（`exl::setting::HeapSize`）。有了这个读数才能判断新上限够不够。
//   bit11 **错误文本不可信**：`errorLength` 非 0 但 < 4 字节 —— Lua 允许 `error(任何值)`，
//        此时我们的"取栈顶字符串"通道只能拿到退化表示（真机 `01789218953` 就只拿到一个 `.`）。
//        报告侧看到这一位时，**不要**把那段文本当成错误消息。
//   bit9 **文本布局**：`errorLength != 0` 时置位，此时 `payload[3..15]` 是**错误文本的前
//        104 字节**（不是普查字）。报告侧必须按这一位选解码表。
//        为什么加它：真机报告 `01789201566` 只给出前 8 字节 `"..._mods"`（Lua 5.3.3 的
//        `luaO_chunkid` 把 66 字符的入口路径截成 "...+末 57 字符"，信息头正好落在那里），
//        而定位问题要的是后面的**行号 + 消息**——只带 8 字节等于又白跑一轮。
//
// 负载数组（`x2` 指向，崩溃报告的 Stack Dump 里按小端 8 字节一组读出）。
// **两种布局，按 status 的 bit9 选**：
//   bit9 = 1（`errorLength != 0`，文本布局）：
//     [0] 状态位  [1] 错误信息长度
//     **[1..3] 在"没有错误文本"的会话里被复用为 EID 侧的可疑点读数**（2026-09-12 收尾）：
//     [1] bit0 `EID.isHidden` 为真、bit1 该字段读取失败（`EID` 表不在或字段不是布尔）
//     [2] bits0..15 `Isaac.CountEnemies()` 最后一次返回值、bit16 `Game:IsPaused()` 最后返回值
//     [3] bit0 `EID.Config.HideInBattle`、bits8..15 其读取状态码、bits16..31 `RefreshRate`、
//         bits32..39 `EID.GameRenderCount` 读取状态码（0 成功/1 表不在/2 字段是 nil/3 非数字）、
//         bit40 该字段 > 0
//
//     为什么复用：普查布局（无错误文本）下 `[1]`（错误长度）与 `[2]`（错误信息头）本来就是 0。
//     这几个读数一次性覆盖"描述为什么没画"的全部可疑提前 return 点。

//     **[2]/[3] 改放"字符串键读实体字段"读数**（第九轮）：[2] 次数、[3] 最近一次的键名前 8 字节。
//     EID 的 `getEntityData` 若走 `entity:GetData()` 会点亮 `Entity.GetData` 位；若走别的字段名，
//     这里能看到"确实发生过字符串字段读取"以及它读的是什么。
//     （原先占据 [2]/[3] 的 `isHidden`/`HideInBattle`/`RefreshRate` 等已在前面几轮定案，不再重复。）
//
//     **[6]/[7] 改放 `Entity.Type`/`Variant` 读数**（第九轮主线索）：[6] bits0..31 `Type`
//     被读取的次数、bits32..63 最近一次 Type；[7] bits0..31 最近一次 Variant、bits32..63 SubType。
//     EID 的 `hasDescription` 先用 `entity.Type` 过滤，通过后才调 `Entity:GetData`。
//
//     **[10]/[11] 改放 `Entity.FrameCount` 读数**（第八轮主线索）：[10] bits0..15 读取次数、
//     bits16..31 "结果 > 0" 的次数；[11] bits0..31 引擎帧计数、bits32..63 实体出生帧。
//     EID 的 `main.lua:1473` 判 `entity.FrameCount > 0`，恒 0 就会把所有候选实体滤掉。
//
//     **[8]/[9] 改放过滤器链**（2026-09-12 第八轮）：[8] 过滤器链掩码（`Entity.GetData`/
//     `GetSprite`/`ToPickup`/`ItemConfig.GetCollectible`/`EntityPickup.IsShopItem`/`GetPtrHash`），
//     [9] bits0..23 `ItemConfig:*` 命中次数、bits24..47 未命中次数。EID 的 `main.lua:1473`
//     用 `hasDescription` + `FrameCount` 过滤候选实体，恒未命中就一个字都画不出来。
//
//     另外 **[4]/[5] 改放 API 序列掩码**（低/高 64 位）：它回答"Mod 走到了哪一支" ——
//     `Game.GetLevel` 调过但 `Isaac.FindInRadius` 没调过 ⇒ 卡在选描述之前；
//     `Font.DrawString*` 调过 ⇒ 描述构建成功、问题在绘制。位表见
//     `runtime/src/interfaces/lua/api_sequence_probe.cpp`。
//     [2..12] 错误文本的**前 88 字节**
//     [13] 报错这一帧**现查**的玩家链阶段（低 8 位）| 标志位（bits 8..15）：
//          bit0 向量可读、bit1 向量为空、bit2 首元素非空、
//          bit3 vptr == 模块基址 + `Entity_Player` vtable 偏移、
//          bit4 槽读到、bit5 `Game*` 拿到、bit6 begin 可读、bit7 end 可读
//     [14] 打包：bits0..15 `Font:DrawString*` 调用次数、bits16..31 累计文本字节数、
//          bits32..47 `Isaac.FindInRadius` 调用次数、bits48..63 最后一次查询返回的实体个数
//     [15] 打包：bit0 Room 拿到、bit1 房间容器指纹匹配、bit2 查询有结果、
//          bits8..15 分区掩码、bits16..39 半径、bits40..47 活表命中、bits48..55 玩家命中、
//          bits56..63 效果表命中
//
//     `[14]/[15]` 是"描述到底画没画"的分界判据：都为 0 说明 EID 根本没走到把文字交给引擎
//     那一步（描述没构建出来）；非 0 但屏幕上没有文字，则要查坐标/颜色/字体加载。
//     玩家链的原始 begin/end 已由 `[13]` 的标志位（bit4..7）覆盖。
//
//     `[13..15]` 为什么是"现查"而不是取缓存（2026-09-12 第四轮）：先报过"最近一次查找"的
//     阶段码，读到 `stage = 0`（成功）—— 但 EID 每帧都调 `Isaac.GetPlayer`，出错那次的记录
//     早被后续成功的那次覆盖了。**读数必须与报错同一时刻**才可信。
//     [13] `Isaac.GetPlayer` 解析**阶段码**（0 = 成功；1 = 基址不可用；2 = 槽不可读/为空；
//          3 = `Game*` 为空；4 = 向量区间不可读或不自洽；5 = 元素个数超上限；6 = 下标越界；
//          7 = 元素为空指针；8 = vptr 不是 `Entity_Player`）
//     [14] 向量首元素指针（阶段 8 时它是那个"非空但不是玩家"的指针）
//     [15] 该元素的 vptr（阶段 8 时与"模块基址 + `kEntityPlayerVtableOffset`"比对即可定论）
//
//     说明：`[13..15]` 在 2026-09-12 第四轮之前放的是 `require` 失败快照（第一次/最后一次
//     失败模块名的前 8 字节 + 代码与总次数）。那一快照已确认其结论（8 次 code 20 = EID 用
//     `pcall` 吞掉的 4 个 AB+ 语言包，属预期），而 `player` 为 nil 是当前唯一挡住 EID 的环节，
//     所以这三个字改放它。要复跑 `require` 快照就把这段换回去。
//   bit9 = 0（普查布局，与 2026-09-12 第二轮一致）：
//   [0] 状态位（与 x3 相同）            [1] Lua 错误信息长度
//   [2] 错误信息前 8 字节               [3] update 回调进入次数
//   [4] render 回调进入次数             [5] 已登记回调种类掩码低 64 位
//   [6] 同上高 64 位                    [7] 有派发点的登记次数
//   [8] 无派发点的登记次数              [9] 清单 Mod 加载失败字（打包格式，见下）
//   [10] 诊断出海口 `word[12]`（回调错误标志）
//   [11] 当前注册表里的回调条数（`LuaRuntime::ManagedCallbackRegistry().Count()`）
//   [12] bits0..15 `Font:Load` 调用次数、bit16 最后一次结果、bits17..31 `Font:IsLoaded` 调用次数、
//        bit32 最后一次结果
//   [13] bits0..15 绘制时颜色 alpha（千分比）、bits16..31 绘制时缩放 X（千分比）、
//        bits32..47 `Font:Load` 交给引擎的名字长度、bits48..63 名字前两字节
//
//   —— 下面这条是旧布局（`[12]` 曾单独放 Font:Load 读数），保留说明以免误读 ——
//   [12] `Font:Load` 读数（`bit0` 引擎接受 / `bit1` 名字是内容挂载点相对名 / `bits8..15` 名字长度 /
//        `bits16..47` 名字前 4 字节）。本轮"EID 为什么在 `main.lua:187` 提前 return"完全取决于
//        引擎收到的字体名字：`bit1` 为 0 就说明归一化没把它变成 `font/eid_default.fnt`。
//   [13..15] 预留 0
constexpr std::uint64_t kCallbackErrorProbeMagic = 0x5252454341415349ULL;  // "ISAACERR"
// 负载**自描述标记**（2026-09-12 第五轮）：`payload[2]` 的高 32 位固定写这个值。
//
// 为什么要它：崩溃报告只 dump `[SP, SP+0x100)`，而负载在帧里的位置**实测与反汇编推断不一致**
// （本地反汇编给出 `payload[0]` 在 `sp+0x30`，真机却只有前 48 字节可达）。报告里能读回的
// `payload[2]` 高 32 位是不是这个标记，就**直接告诉报告侧"这批字的布局是新的"**，
// 不必再靠"读出来的数字像不像路径 ASCII"来猜布局。ASCII = "AKP2"。
constexpr std::uint32_t kProbeLayoutTag = 0x32504B41U;
// 错误文本装进负载的容量：负载 16 字 128 字节 = [0] status + [1] 长度 + 11 字文本（88 字节）
// + [13][14] `require` 失败模块名 + [15] `require` 失败代码/次数。
// 88 字节够看到"哪一行 + 什么消息"（真机那条错误 104 字节，尾部的 `(field '?')` 会被截掉，
// 但路径、行号与消息主干都在）；换来的 `require` 失败信息解决的是"哪个模块挂了"，比尾巴更值钱。
constexpr std::size_t kCallbackErrorProbeTextBytes = 176;
// 兜底：本探针自己的调用次数达到这个值也报一次（每帧一次调用）。理由与内容挂载点探针的兜底
// 同源：主口径（真的发生了回调错误）一直没出现时，也必须有一次报告，否则现象又只剩"既不报错也
// 没有数据"。30000 次 update 回调在加载阶段每秒上千次，约等于运行二三十秒；EID 的
// `MC_POST_RENDER` 若在第一帧就报错，主口径会在第一时间命中，兜底只负责"什么都没发生"那种情形。
// 触发口径修正（2026-09-12 实机）：原值 30000 是按“加载阶段每秒上千次 update”估的，
// 但在房间里 update 只有约 60 次/秒 → 要跑 8 分钟以上，等于永远等不到。改成 600 次
// （进房后约 10 秒），一次会话就能拿到“回调计数 + 有无 Lua 错误”的全部判据。
//
// 2026-09-12 第二轮再改 600 → 3600（进房后约 1 分钟）：这一轮 EID 的大脚本终于能读进来了
// （脚本缓冲区 16 KiB → 1 MiB），所以要给玩家**在房间里找一件道具、看一眼有没有描述**的时间，
// 否则兜底会在 10 秒时结束会话，把"EID 到底画没画"这个问题又留到下一轮。主口径（真的发生了
// 回调 Lua 错误）不受影响：它一旦命中就立刻报错，不等这个计数。
// 2026-09-12 第三轮后又改回 900（进房约 15 秒）：探针现在多了一条主口径
// （"运行时捕获到过任何 Lua 错误文本"，见 `capturedError`），加载期失败会在第一帧就报出来，
// 兜底只负责"连错误都没有"那种情形，不需要再让玩家等一分钟。
//
// **2026-09-12 第四轮：900 → 3600**（实测教训）。真机上 900 次 update 落在"主界面还在转场"的
// 时间窗里 —— 玩家还没进房间，会话就被兜底结束了，于是"EID 到底画没画"这个问题又留到下一轮
// （用户原话："进入了主界面，但没来及进房间"）。主口径不受影响：任何一次捕获到的 Lua 错误
// 仍然在第一帧立刻报错；兜底只是把"什么都没有"那种会话的等待从 15 秒放宽到约 1 分钟。
// 2026-09-12 第九轮：3600 → **2700**（进房约 45 秒，用户要求）。
// 实测 3600 次 update ≈ 60 秒（update 约 60 次/秒），按同比例取 2700 ≈ 45 秒：
// 既要让玩家有足够时间进房间并停留观察，又不让每轮取证都等满一分钟。
// ★ 2026-09-12 第九稿：2700（约 45 秒）→ **14400（约 4 分钟）**。
//
// 为什么加长：兜底口径的用途是"即使什么都没错，也留一份读数"，而 45 秒会**打断试玩**
// （用户实测"刚进主界面就被关掉"，因为当时的触发条件里还混进了一个粘性标志，见
// `IsaacModRuntime_ProbeCallbackError` 的注释）。触发条件修正后，兜底只需保证
// "一份会话最终会留下证据"，所以放宽到 4 分钟；期间用户完全可以正常玩。
constexpr std::uint32_t kCallbackErrorProbeFallbackCalls = 14400;
// `IsaacModRuntime_ProbeEnginePlayers`（`ISAACIG2`）的让路开关。
//
// 打开时它只在"回调错误探针已经报错"之后才允许触发。为什么这样就等于本轮不触发、且**一次会话
// 只会崩一次**：错误探针的报错是 `svcBreak`，报错即结束会话，所以那个标志一旦置位就不会再回到
// 这里；反过来，只要错误探针还没报错，本探针就一直让路。两个探针因此不可能各崩一次，也不会让
// `ISAACIG2` 抢在 `ISAACERR` 前面把会话结束掉（上一轮的故障正是如此）。
// 需要复跑 `ISAACIG2` 时把这一个常量改成 `false` 即可，不需要动触发逻辑。
constexpr bool kEnginePlayerProbeDeferUntilErrorProbe = true;

// --- 房间实体容器探针（批次 4 真机确认，2026-09-13）-----------------------------------
//
// 目的：`Room + 0x1950` 是**内嵌** `EntityList` 这条静态结论（批次 4 反汇编定位，见
// `runtime_constants.hpp` 的「房间实体容器」段与 `docs/问题与解决记录.md`）**从未上过真机**。
// 本探针用一次报错同时确认：两级 `Game` 链（槽 → 指针 → `Game*`）、`Room*`、容器指纹
// （三个固定容量值）、三张表的 begin/count、首个实体的全部字段，以及一次自遍历
// （活表元素数 | vptr 命中数 | 分区直方图）。
//
// 证据口径与 `ISAACIG2`/`ISAACERR` 相同（理由见 `content_mount_point_probe.cpp` 里
// `ReportItemConfigSnapshot` 上面的长注释）：崩溃报告的 `Stack Dump` 段精确等于
// `[SP, SP+0x100)`，所以负载数组只能是 16 个 u64，而且必须是报错那一帧的**唯一**大局部变量。
// 核对命令（build 之后）：
//   aarch64-none-elf-objdump -d runtime/<BUILD>/content_mount_point_probe.o
// 在 `ReportEntityListSnapshot` 里查 `sub sp, sp, #N`（帧 N 字节）与负载数组的起点偏移，
// 判据是 `offset + 16*8 <= 0x100`。
//
// 寄存器（`x5`–`x7` 实测不可信、`x8` 以上被 `svc` 清掉，所以只用 `x0`–`x4`）：
//   x0 = 2（`BreakReason_User`）
//   x1 = "ISAACEL1"（`kEntityListProbeMagic`）
//   x2 = **栈上负载数组的指针**（16 个 u64，字段表见下）
//   x3 = `(16 << 32) | status`（高 32 位 = 负载字数，供报告侧自证数组长度；低 32 位 = status）
//   x4 = `payload[3]`（`EL` 地址，冗余上报；与 `ISAACIG2` 用 `x4` 带 `IC` 同一个用意）
//
// status（`x3` 低 32 位，**顺序判定**：从 1 开始第一个不成立的就是它）：
//   0 全通过
//   1 槽为 0（`*(u64*)(base + 0xAAC698) == 0`）
//   2 `Game` 为空（`*(u64*)槽 == 0`）
//   3 `Room` 为空（`*(u64*)(Game + 0x21550) == 0`）
//   4 容器指纹不匹配（`EL+0x50 != 0x4000 || EL+0x80 != 0x800 || EL+0xE0 != 0x8000`），
//     或指纹字段/计数不可读
//   5 活表 `count > cap`（`*(u32*)(EL+0x84) > *(u32*)(EL+0x80)`）
//   6 活表 begin 不可读、为 0，或未 8 字节对齐
//   7 首实体 `e0` 为空、不可读，或它的 vptr 不落在 `[base+0xA34F58, base+0xA38230)`
//   8 遍历中出现空元素或未 8 字节对齐的元素（在 7 之后才判，7 优先）
// 说明：7/8 只有真正解引用才判得出来，所以逐帧的触发判定只算到 6；报错那一帧的 status 可能是
// 7/8（主口径触发后发现首实体异常就是这种情形）。
//
// 负载数组（`x2` 指向，崩溃报告 `Stack Dump` 里按小端 8 字节一组读出）：
//   [0]  `*(u64*)(base+0xAAC698)`（槽的原始值，期望 `base+0xABB448`）
//   [1]  `Game*`                    [2] `Room*`          [3] `EL = Room + 0x1950`
//   [4]  一般表 count | cap << 32（cap 期望 0x4000）
//   [5]  活表 count | cap << 32（cap 期望 0x800）← 触发口径里的 `payload[5]` 低 32 位
//   [6]  一般表 begin（`EL+0x48`）  [7] 活表 begin（`EL+0x78`）
//   [8]  `e0 = *(u64*)(payload[7])`（首个实体指针；活表 count 为 0 时不读，保持 0）
//   [9]  `Type | Variant<<8 | SubType<<16 | 生成帧<<32`（`e0+0x38/0x3C/0x40/0x2F4`）
//   [10] `*(u64*)e0`（vptr）        [11] `Position.x | Position.y<<32`（`e0+0x310/0x314`，
//        两个 float 的**位模式**，按小端 float 解码）
//   [12] `Size | Index<<32`（`e0+0x344/0x30`）  [13] 标志（`e0+0x1B8`）
//   [14] `validVptrCount << 32 | liveCount`（自遍历：报出的 count 与 vptr 落在区间内的个数）
//   [15] 分区直方图位：bit i 置位 = 遍历里至少见过一个分区 i
//        （0=FAMILIAR 1=BULLET 2=TEAR 3=ENEMY 4=PICKUP 5=PLAYER 6=EFFECT，判据同
//        `EntityList::collide()`）
//
// 读法（`validVptrCount < liveCount` 有两种来源，都是"表里存在异常元素"）：空元素/未对齐元素
// （此时 status 会是 8）、或元素本身不可读、vptr 不落在 `Entity` 家族区间内（status 保持 0，
// 因为 8 只表示"空/未对齐"，见上面 status 表的措辞）。
constexpr std::uint64_t kEntityListProbeMagic = 0x314C454341415349ULL;  // "ISAACEL1"
constexpr std::uint32_t kEntityListProbePayloadWords = 16;
// 兜底口径：**已经在局内（`players[0]` 非空）**但主口径始终不成立的帧数上限。没有它，
// status != 0 / 活表为空 这类结果就只剩"既不报错也没有数据"——正是本项目反复吃亏的会话形态
// （见 `kContentMountProbeFallbackAfterUpdates` 的注释）。刻意要求 `players[0]` 非空：否则
// 加载阶段就会把会话结束掉，换来的只是一份"槽还是 0"这种没有信息量的报告。
// `players[0]` 永远不非空的那种情形由既有探针兜底（`ISAACIG2` 的 20000 次口径能报出
// "玩家数组能不能读"），本探针不再重复占用那一次报错机会。
constexpr std::uint32_t kEntityListProbeFallbackCalls = 900;
// 报错前置：既有探针（`ISAACERR` / 内容挂载点）已经置位它们的一次性标志时，本探针让路。
// 正常情况下不需要——`svcBreak` 会直接结束会话，标志一旦置位本函数就不会再被调用；这条判断
// 是为了防"某个探针只置位、还没崩"的窗口里本探针抢先把会话结束掉，导致它的证据丢失。
constexpr bool kEntityListProbeYieldsToOtherProbes = true;
// 本轮开关（2026-09-12 第二轮真机）。
//
// `ISAACEL1` 要问的问题已经在报告 `01789200335` 里以 `status = 0` **全部答完**：两级链
// （槽 → `Game*` → `Room*`）、内嵌 `EntityList` 的三处容量指纹（`0x4000`/`0x800`/`0x8000`）、
// 活表 count/begin、首个实体的 Type/Variant/SubType/生成帧/位置/尺寸/vptr、以及一次自遍历
// （1 个元素、vptr 命中 1、分区直方图 = PLAYER）**全部通过**。而它的触发时机是"进房间的第一帧"，
// 会立刻结束会话，于是 `ISAACERR`（EID 的 Lua 回调错误 / 回调登记普查）永远轮不到 —— 本轮
// 真机报告正是这样（只有 `ISAACEL1`，没有 `ISAACERR`）。
//
// 所以本轮把它关掉，只留 `ISAACERR` 去回答"EID 为什么不显示"。要复跑容器探针把这个常量改回
// `true` 即可，触发逻辑与代码都不用动。
constexpr bool kEntityListProbeArmed = false;
// 内容挂载点探针（`ISAACMP1`）的本轮开关。
//
// 它要问的问题（SD 覆盖树能不能当 KAGE 内容挂载点、只存在于 Mod 的新文件名能不能被解析、
// 同名文件优先级、Font 资源链路、中继发布）已经全部答完，而且纯贴图 Mod 真的把图标画出来了。
// 而它的触发口径是"**Mod 自己画了 600 帧**"——EID 一旦开始正常绘制，它就会抢在 `ISAACERR`
// （回调 Lua 错误 + 回调登记普查字）前面结束会话，那正是本轮最需要的证据。所以关掉它。
// 要复跑挂载点探针把这个常量改回 `true`，触发逻辑与代码都不用动。
constexpr bool kContentMountProbeArmed = false;
}  // namespace isaac::runtime

// 另受 `PROBE_BREAK_CONTENT_MOUNT` 单独控制（2026-09-13 二分定位探针包启动故障）：本 TU 里还
// 定义着 `ProbeCallbackError` / `ProbeEnginePlayers`，更新钩子每帧都调它们，所以它会在加载阶段
// 执行；关掉 `PROBE_BREAK_CONTENT_MOUNT` 后下面走 `#else` 的空实现分支。
#if defined(EXL_PROBE_BREAK) && EXL_PROBE_BREAK_CONTENT_MOUNT
extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_ProbeContentMountPoint();
extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_ProbeEnginePlayers();
// 回调 Lua 错误探针（`ISAACERR`）：每帧检查一次，只在"已经发生过回调 Lua 错误"（或调用次数
// 到达 `kCallbackErrorProbeFallbackCalls`）时报错一次，每会话最多一次。
extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_ProbeCallbackError();
// 房间实体容器探针（`ISAACEL1`）：每帧检查一次，只在"两级链与容器指纹全部成立、活表非空、
// `players[0]` 非空"（或"已经在局内但判据不成立"的兜底）时报错一次，每会话最多一次。
extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_ProbeEntityList();
#else
inline void IsaacModRuntime_ProbeContentMountPoint() {}
inline void IsaacModRuntime_ProbeEnginePlayers() {}
inline void IsaacModRuntime_ProbeCallbackError() {}
inline void IsaacModRuntime_ProbeEntityList() {}
#endif
