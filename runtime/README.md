# Switch Lua Runtime Probe

这是《以撒的结合：忏悔》Switch 真机 Atmosphere 的 Runtime。它只针对基础 Title ID `010021C000B6A000` 和指定 Build ID，不支持模拟器、任意外部 PC Mod 目录或其他游戏版本。默认包内嵌 Lua 5.3.3，并从 RomFS 的受限清单加载受版本控制的原始 `MuteOnPause` PC Mod（含 `metadata.xml`、`main.lua` 与受限 `require` 路径）；Switch 真机已确认其可随暂停停止背景音乐、随恢复继续播放。该结果不代表已支持多 Mod 自动发现、SD 卡 Mod 读取、资源覆盖或完整 PC Lua API。更新包的 Content ID 为 `010021C000B6A800`，只用于定位原始 NPDM 输入，不能用作 Atmosphere 覆盖目录。

## PC Mod RomFS 清单（仅 PC 端输入）

下面的命令扫描既有 PC `mods/` 目录，保留 Mod 的原始文件和元数据，并自动构造一个
Atmosphere RomFS 预览树。工具只管理输出根目录中的固定
`atmosphere/contents/010021C000B6A000/romfs/isaac_mods` 子树；不要在该子树手工放置文件，
也不要在同步过程中由第二个工具实例、文件管理器或其他进程修改同一输出树。

```sh
python3 -m tools.inspect_pc_mod \
  --mods-root "The Binding of Isaac Rebirth pc/mods" \
  --romfs-output analysis/pc-mod-contract/romfs-preview
```

该命令不会复制任何内容到 SD 卡、不会启动游戏，也不会执行 `main.lua` 或其他 Lua 文件。
生成 `manifest.json` 只证明 PC Mod 发现和 RomFS 输入完整；它不表示 Lua Runtime 已加载，
更不表示游戏内 Mod 菜单、Mod 启停、资源重定向或完整 PC Mod 兼容已经实现。

## 构建入口与源码边界

Runtime、SaltyNX 宿主插件和真机探针是三个不同产物，源码集合互不重叠，因此 `runtime/Makefile`
只暴露三个显式入口。一次构建必须属于其中一个，不允许混编：

| 入口 | 产物 | 编译的源码 | 禁止编译的源码 |
| --- | --- | --- | --- |
| `runtime_module` | `deploy/atmosphere/contents/010021C000B6A000/exefs/subsdk9` | 生产 Runtime | 探针、SaltyNX 宿主插件 |
| `host_plugin` | `deploy-saltynx/stage<N>/SaltySD/plugins/010021C000B6A000/isaac-runtime.elf` | 宿主插件 crt0 与 SaltyNX 能力注册 | 生产 Runtime、探针 |
| `probe` | `deploy-diagnostic/stage<N>/` 或 `deploy-startup-probe/stage<N>/` | 显式选择的探针入口 | 宿主插件 |

```sh
# 生产 Runtime：不接受任何探针或插件变量
make -C runtime runtime_module

# SaltyNX 宿主插件：必须显式给出插件阶段
make -C runtime host_plugin HOST_PLUGIN_STAGE=145

# 真机探针：必须显式选择阶段，否则直接报错退出
make -C runtime probe DIAGNOSTIC_STAGE=13
make -C runtime probe STARTUP_PROBE_STAGE=5
```

源码根目录由 `RUNTIME_SOURCE_ROOT` 控制，当前默认仍是 `source`，并把 `runtime/src/` 作为附加
源码根一起编译。Task 8 第十片后，生产 Runtime、诊断包和启动探针都默认使用分层 Lua 路径
（`LAYERED_RUNTIME=1`），旧 TU 中供非分层构建使用的 handler 副本已经删除。`exl_main` 在
`EXL_LAYERED_RUNTIME` 下先调用 `isaac::runtime::RuntimeBootstrap::Start()` 记录入口边界，其余控制流
保持不变。

诊断与启动探针只需要共享回调注册表和 Lua API 家族 TU，因此构建时把附加源码根收窄为
`src/application/callback` 与 `src/interfaces/lua`；完整 `runtime/src` 只由生产构建编译。这样历史
诊断宏不会误编译不兼容的生产适配器，同时探针和生产包继续使用同一份 Lua API 注册与派发实现。

`runtime/src/application/`（扫描、Hook 安装、回调注册与派发）与 `runtime/src/infrastructure/`（exlaunch
适配器）已经可以随分层构建编译，但在被 Worker 接入之前没有任何调用者，链接器会按分段回收它们：因此
`runtime/build/` 下能看到对应 `.o`，最终 `runtime.elf` 里却暂时没有这些符号。判断是否真正进入产物要看
对象文件与调用链，不能只看 ELF 符号表。

分层构建的 Hook 安装已经由 `HookInstallService` 拥有：`TryInstallDefaultManifestMod` 只负责校验绑定与
武装状态，Update（必需）、Render 与 PreGetCollectible（可选）三个拦截点统一由服务安装。端口层的
`HookTarget` 必须携带 `base`、代码窗口、镜像大小与完整 32 字节 buildId，因为
底层校验会比对 buildId，缺失字段会让 Hook 静默失效。

Lua API 的唯一清单在 `runtime/src/interfaces/lua/api_catalog.cpp`：每个 API 有稳定的
`(domain<<24)|(ownerGroup<<16)|sequence` 编号、owner、名称、版本、能力依赖、线程亲和性与成熟度。
`runtime/tests/contract/test_api_catalog.py` 会双向比对清单、全局注册和
`runtime/src/interfaces/lua/*_api.cpp` 的家族绑定表；漏登记或登记了不存在的 API 都会失败：

```sh
python3 -m unittest runtime.tests.contract.test_api_catalog -v
```

Lua 回调由共享注册表管理：注册存进 `CallbackRegistry`，PostUpdate/PostRender 经
`CallbackDispatcher` 按顺序派发，单个回调失败只移除它自己；PostRender 的 manager 作用域位于
`LuaCallbackInvoker`（仍然只在一次回调期间有效）。生产与探针共用这条路径，旧每阶段单槽实现已删除。

三个入口都不会启动游戏，也不会访问 SD 卡或 FTP。

## 分层源码与宿主测试

`runtime/src/` 是重构后的生产源码根目录，按依赖方向分层：

| 目录 | 层 | 说明 |
| --- | --- | --- |
| `domain/` | Entity / Value Object | 状态机、能力集合、持久化编码等纯 C++ 值类型，不接触平台 |
| `ports/` | Repository / Client 接口 | 文件、内容读取、游戏内存、Hook、线程、Lua、事件和 Host API 契约 |
| `bootstrap/`、`composition/` | main / DI | 启动顺序和依赖装配 |
| `interfaces/` | Controller | Lua API family TU 与 Hook 入口 |
| `application/` | Service / Use Case | 回调注册/派发、Manifest 与 Mod 加载用例 |
| `infrastructure/` | RepositoryImpl | SaltyNX、exlaunch、Switch、RomGame 适配器（按切片迁移） |
| `diagnostics/` | Logging | 结构化事件、缓冲和落盘（Task 7 填充） |
| `host_plugin/` | 独立二进制 | `isaac-runtime.elf` 的 SaltyNX 能力注册（后续任务填充） |

`runtime/src` 已经接入默认分层构建；`runtime/source` 仍承载入口、Hook、观察器和非分层时代共享的
底层实现。Domain/Ports 与分层接线可以分别用宿主工具链和真机工具链验证：

```sh
# 依赖方向 + 宿主行为 + 与设备端持久化格式的逐字节兼容性
python3 -m unittest runtime.tests.unit.test_domain_ports -v

# 架构布局与三个构建入口
python3 -m unittest runtime.tests.test_architecture_layout -v
```

`test_domain_ports` 会把现有 `mod_persistence.hpp` 的编码结果与新 `PersistenceCodec` 的输出逐字节
比较；只要两者不一致，测试就会失败，避免重构静默改写设备上已有的存档格式。

## 构建

在仓库根目录执行：

```sh
docker run --rm -v "$PWD":/work -w /work devkitpro/devkita64:latest \
  sh -lc '. /opt/devkitpro/devkita64.sh && make -C runtime clean && make -C runtime'
```

产物包含 ExeFS 覆盖和一个 NRO IPS：

```text
runtime/deploy/atmosphere/contents/010021C000B6A000/exefs/main.npdm
runtime/deploy/atmosphere/contents/010021C000B6A000/exefs/subsdk9
runtime/deploy/atmosphere/nro_patches/isaac-repentance-manager-update-relay/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips
```

`main.npdm` 是从用户提供的原始 NPDM 只读生成的最小权限覆盖，构建工具会校验输入 SHA-256 和结构，只增加 Runtime 所需的 `svcMapProcessMemory`/`svcUnmapProcessMemory` 权限。原始游戏文件不会被修改。原始文件不在默认路径时，可用 `make ORIGINAL_NPDM=/path/to/main.npdm` 覆盖输入路径。

## Stage102：房间切换只读诊断

该诊断不是默认 Runtime，也不会加载 Lua 或任何 Mod。它仅在原版 `Game::ChangeRoom` 已完成 `Level::ChangeRoom` 后读取一次当前 RoomType；收集四次房间切换后受控退出。构建命令：

```sh
docker run --rm -v "$PWD":/work -w /work devkitpro/devkita64:latest \
  sh -lc '. /opt/devkitpro/devkita64.sh && make -C runtime clean && make -C runtime DIAGNOSTIC_STAGE=102'
```

只复制 `runtime/deploy-diagnostic/stage102/atmosphere/`。该树必须只有：

```text
contents/010021C000B6A000/exefs/main.npdm
contents/010021C000B6A000/exefs/subsdk9
nro_patches/isaac-repentance-change-room-relay/...ips
```

进入任意一局后做四次普通房间切换，复制完整 Atmosphere 日志。成功报告为 `ISAAC_CR`，低位 bit0 表示至少到达一次原版房间切换、bit1 表示 post-call RoomType 可读、bit2 表示读取失败，bits 8--15 是最后一次 RoomType。这个结果不能证明宝箱房首次进入、保存/读档、重开或宝箱访问计数语义；它只授权后续研究事件可达性。

将整个 `runtime/deploy/atmosphere/` 树合并复制到 SD 卡的 `atmosphere/` 目录。不能只复制 `exefs`，否则 `Manager::Update` 中继 IPS 不会被 Atmosphere 应用：

```text
atmosphere/contents/010021C000B6A000/exefs/main.npdm
atmosphere/contents/010021C000B6A000/exefs/subsdk9
atmosphere/nro_patches/isaac-repentance-manager-update-relay/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips
```

该 IPS 只改写 `Repentance.nro` 的 `Manager::Update` 入口和同一 `.text` 区的中继代码洞，不与捐款机 IPS 写入相同偏移，因此可以共存。Atmosphere 启动该 Title 时会自动应用三个覆盖文件。复制前可核对 SHA-256：

```sh
shasum -a 256 runtime/deploy/atmosphere/contents/010021C000B6A000/exefs/main.npdm
shasum -a 256 runtime/deploy/atmosphere/contents/010021C000B6A000/exefs/subsdk9
shasum -a 256 runtime/deploy/atmosphere/nro_patches/isaac-repentance-manager-update-relay/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips
```

## Stage 37：Lua `Game():IsPaused()` 真机验证通过

`Game()` 返回可缓存的无指针 userdata：Lua 可以在脚本顶层保存它，但 userdata 不保存 `Game*`、owner 或 thunk。
`Game():IsPaused()` 使用统一原生访问作用域：只允许在 Runtime callback 正在同步分发时调用；在脚本顶层或分发返回后
调用，以及 owner/thunk 逐次解析、地址校验或原版调用失败，均返回 Lua error。每次调用都从已验证的 owner/thunk 地址
重新解析原版对象链，绝不跨调用缓存原生指针。当前只实现 MC_POST_UPDATE；这不是对 `MC_POST_RENDER`、输入或房间类
PC 回调已经兼容的声明。

后续 Game API 分为两类：标量只读 API 返回 boolean、integer 或 number，复用同一作用域、对象链和错误处理；对象返回 API
必须先设计无指针 userdata，不能向 Lua 泄露裸地址。当前 PC Mod 统计中 `GetLevel`、`GetNumPlayers`、`GetFrameCount`、
`GetRoom` 使用频率最高；其中 `GetFrameCount`、`GetNumPlayers` 是后续标量候选，`GetLevel`、`GetRoom` 属于对象返回
API，均尚未实现或锁定原版地址。

thunk 的安装期验证不能替代运行期边界：每次调用在读取 owner 或调用原版函数前，都要求 thunk 非零、4 字节对齐，
并且完整 16 字节仍位于 `MemType_ModuleCodeStatic`/`Perm_Rx` 映射；任一条件失败返回 Lua error。不在调用期重读
16 字节 guard，也不增加 I/O、线程、日志或 IPS。

默认/Stage14 的地址发布前置是：先验证 owner 槽、`Game::IsPaused()` thunk、relay 入口与 callback 槽，再以
release 发布并以 acquire 复核；只有全部复核成功才把地址交给 Lua Runtime。任何前置失败都不发布绑定，不能借由
旧地址继续调用。

这里的 Stage 14 是 `DIAGNOSTIC_STAGE=14` 的 Lua Game 诊断包，不是历史 Stage 14 分析。回调状态机在
`Installing` 时静默等待，避免 Lua 初始化尚未完成时 relay callback 抢先执行造成初始化竞态；进入 `Ready` 后只认领
一次回调。唯一成功判据是同次完整 Atmosphere 报告中的 `ISAAC_GP/(14,1)`（返回 false）或
`ISAAC_GP/(14,2)`（返回 true）。`ISAAC_GF` 的任一状态、普通崩溃、冻结、无报告或其他 magic 都是失败，不能写成
真机成功。

真机报告 `crash_reports/01787805095_010021c000b6a000.log` 已完成验证：Program ID 为
`010021C000B6A000`，`Repentance.nrs` Build ID 为 `91C73FDD575061318D68886316AFEAC72388B2AB`，线程为
`MainThread`，Break Address 为 `ISAAC_GP`，Info 2 为 `(14,2)`。Runtime Module ID
`2DFFF55CADFCE9C01492E7738B3F7A8D6EA5299F` 与本次 Stage14 `subsdk9` 一致，故 Stage37 真机验证通过：Lua
`Game():IsPaused()` 返回 boolean `true`。用户随后恢复 `runtime/deploy/atmosphere/` 默认三文件并确认游戏正常
进入、持续运行且无报错，故 **Stage37 默认包 smoke test 通过**。Stage37 的诊断、恢复与回归流程至此闭合。

本轮已使用 UPD 转储中的原始 NRO/NPDM 完成真实 Docker clean build：`Repentance.nro` SHA-256 为
`cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a`，默认与 Stage14 三文件树均已生成并审核。
这证明正式构建输入、交叉编译和部署集合正确；它本身仍不等于真机，真机结论以上述同次完整 Atmosphere 报告为准。

## 启动冻结分段诊断

仅在真机启动时发生整机冻结、或需要验证 Runtime 到达特定边界时使用此流程。阶段 0 至 5 的最小诊断入口会跳过 fake heap、`exl_init()` 和 C/C++ 构造数组；阶段 6 则复用完整 Runtime 的扫描和 Hook 路径。所有诊断阶段都会调用 `svcBreak(BreakReason_User, ...)` 和 `svcExitProcess()`；因此 `subsdk9` 报告退出是诊断信号，不是可继续进入游戏的 Runtime 构建。

| 阶段 | 构建参数 | 当前阶段执行前缀 | 隔离输出目录 |
| --- | --- | --- | --- |
| 0 | `DIAGNOSTIC_STAGE=0` | 仅 `svcBreak`/`svcExitProcess` | `runtime/deploy-diagnostic/stage0` |
| 1 | `DIAGNOSTIC_STAGE=1` | 阶段 0 + `exl::util::impl::InitMemLayout()` | `runtime/deploy-diagnostic/stage1` |
| 2 | `DIAGNOSTIC_STAGE=2` | 阶段 1 + `virtmemSetup()` | `runtime/deploy-diagnostic/stage2` |
| 3 | `DIAGNOSTIC_STAGE=3` | 阶段 2 + `exl::hook::Initialize()`，显式初始化 Hook/Inline Hook 两套 JIT 映射 | `runtime/deploy-diagnostic/stage3` |
| 4 | `DIAGNOSTIC_STAGE=4` | 阶段 3 + `RuntimeFsLogDiagnose`：连接 `fsp-srv`、打开 SD 根目录、打开日志文件、读取长度、写入一行 | `runtime/deploy-diagnostic/stage4` |
| 5 | `DIAGNOSTIC_STAGE=5` | 阶段 3 后立即继续游戏启动；独立线程等待 12 秒后执行 `RuntimeFsLogDiagnose` | `runtime/deploy-diagnostic/stage5` |
| 6 | `DIAGNOSTIC_STAGE=6` | 运行完整扫描与 Hook 安装；在原始 `Manager::Update` 返回后解析 `0xAAC698 -> owner -> Game*`，同步调用 `0x671100` 的 `Game::IsPaused()` PLT thunk | `runtime/deploy-diagnostic/stage6` |
| 7 | `DIAGNOSTIC_STAGE=7` | 运行完整扫描、内嵌 Lua 初始化及 `MC_POST_UPDATE` 回调；120 次回调后主动报告 | `runtime/deploy-diagnostic/stage7` |
| 8 | `DIAGNOSTIC_STAGE=8` | 运行完整扫描，在 `Manager::LoadConfigs` 入口验证 `Manager + 0x36800` 后主动报告 | `runtime/deploy-diagnostic/stage8` |
| 9 | `DIAGNOSTIC_STAGE=9` | 已完成硬件诊断：原生 `ModManager::Reset()` 在 `ListMods()` 的目录枚举中主动中止，不能作为后续启动方案 | `runtime/deploy-diagnostic/stage9`（仅保留取证，不再部署） |
| 10 | `DIAGNOSTIC_STAGE=10` | 历史内部读取诊断；已确认小文件预读后直接读取底层 EOF，禁止重新部署 | `runtime/deploy-diagnostic/stage10`（仅保留报告取证） |
| 11 | `DIAGNOSTIC_STAGE=11` | 已在真机确认：原 Update 返回后可一次调用游戏公开 `KAGE::Filesys::File::Read()` 读取固定 RomFS 哨兵 | `runtime/deploy-diagnostic/stage11`（仅保留硬件取证） |
| 12 | `DIAGNOSTIC_STAGE=12` | 已在真机确认：读取一个受限的 RomFS Lua 文件，并用现有最小 Lua Runtime 执行其 `MC_POST_UPDATE` 回调 120 次 | `runtime/deploy-diagnostic/stage12` |
| 112 | `DIAGNOSTIC_STAGE=112` | 仅在 `Game::ChangeRoom` 完成后进入/重进同一宝箱房时读取 `RoomDescriptor` 的两个候选状态槽位并受控报告 | `runtime/deploy-diagnostic/stage112` |

### Stage 88：Instant Restart 原生输入只读诊断

Stage88 不是可游玩包，也不执行重开。它在 `Manager::Update` 原函数返回后，固定查询原版 `Manager::IsActionTriggered(self, 16, 0, nullptr)`，并重新解析已认证 Game owner 链，观察 Level stage 与 `Game::IsPaused()`；最多 600 次更新后通过 `ISAAC_IR` 退出。它不派发 `MC_INPUT_ACTION`，不调用 Console/`Isaac.ExecuteCommand`，不读取未知 Game 字段，也不接入 Lua API。

构建：

```sh
docker run --rm -v "$PWD":/work -w /work devkitpro/devkita64:latest \
  sh -lc '. /opt/devkitpro/devkita64.sh && make -C runtime clean DIAGNOSTIC_STAGE=88 && make -C runtime DIAGNOSTIC_STAGE=88'
```

只部署 `runtime/deploy-diagnostic/stage88/atmosphere/` 的完整树。成功报告的 Info 1 为 `0x49534141435F4952`（`ISAAC_IR`），Info 2 高 32 位为 `88`；低位 bit0..4 和 bit8..15 的含义见 `docs/问题与解决记录.md`。`ISAAC_IF`、普通崩溃、冻结、无报告都不是成功。该 Stage88 文档是历史诊断说明；Stage90 已单独恢复`Level:IsAscent`，但`Input.IsActionTriggered`、`MC_INPUT_ACTION`与`Isaac.ExecuteCommand`仍未实现。

### Stage 89：Action 16 控制器只读诊断

Stage89 用于修正 Stage88 没有有效输入窗口的问题：它在原 `Manager::Update` 返回后，依次只读查询
`Manager::IsActionTriggered(self, 16, 0/1/2/3/-1, nullptr)`，最长运行 7200 次 Update（约两分钟）。任一命中会以
`ISAAC_IT` 受控退出；它绝不调用重开、Console、Lua 或 `IsActionPressed`。该已验证安全时序可能错过原版同帧已消费的输入 edge，
因此日志只能回答这一时序下的可达性。

Stage89 曾出现两次启动期 `PC=0`：第一版是未验证的前置查询时序，第二版是完整 Runtime 编译门禁遗漏造成入口未编入；当前包已恢复
Stage88 同类完整 Runtime 的入口与内存布局，并对门禁与查询顺序设有回归测试。

构建并仅部署 `runtime/deploy-diagnostic/stage89/atmosphere/` 的完整树：

```sh
docker run --rm -v "$PWD":/work -w /work devkitpro/devkita64:latest \
  sh -lc '. /opt/devkitpro/devkita64.sh && make -C runtime clean DIAGNOSTIC_STAGE=89 && make -C runtime DIAGNOSTIC_STAGE=89'
```

真机操作：进入首层后，在两分钟内长按一次 L+R，随后复制完整 Atmosphere 报告。成功为 `ISAAC_IT`，Info 2 高 32 位为 89；
低位编码见 `docs/问题与解决记录.md`。超时 `0xff`、`ISAAC_IF`、普通崩溃、冻结或无报告均不等同于“Lua API 已实现”。

### Stage 112：同一宝箱房的描述符重进快照诊断

Stage112 只复用已真机验证的 `Game::ChangeRoom` post-call relay。它不加载 Lua、Starterr 或 RomFS，不写原版/兼容状态，不调用重开；仅在 `RoomType=4` 时安全读取 `Game -> Room + 0x8 -> RoomDescriptor` 的 `+0xc`、`+0x50` 两个候选 `u32`。第一次进入宝箱房保存快照；离开后重进**同一** `(room_index, dimension)` 宝箱房时，以 `ISAAC_DR` 受控退出。

部署 `runtime/deploy-diagnostic/stage112/atmosphere/` 的完整树后，从相邻房间进入一个宝箱房、退出、再进入同一宝箱房，再复制完整 Atmosphere 日志。成功 Info 2 的高 32 位为 `112`；低位 bit0=见过宝箱房、bit1=房间键可读、bit2=描述符快照可读、bit3=同一逻辑房间重进、bit4/bit5 分别表示 `+0xc/+0x50` 在两次进入间未变化，bits 8--15 和 16--23 分别为第二次快照的 `+0xc/+0x50` 低 8 位。`ISAAC_DF`、普通崩溃、冻结或未进入同一宝箱房都不是成功结论；即使成功也不代表 getter 已实现。

在仓库根目录构建全部阶段：

```sh
for stage in 0 1 2 3 4 5 6 7 8 9 10 11 12; do
  docker run --rm -v "$PWD":/work -w /work devkitpro/devkita64:latest \
    sh -lc ". /opt/devkitpro/devkita64.sh && make -C runtime clean DIAGNOSTIC_STAGE=$stage && make -C runtime DIAGNOSTIC_STAGE=$stage"
done
```

各阶段除部署目录外，也分别使用 `runtime/build-diagnostic-stage0` 至 `runtime/build-diagnostic-stage12` 保存对象和链接产物。因此可以不执行 `clean` 而连续切换阶段，Make 也不会复用其他阶段带有不同宏的对象或 ELF；上面的命令仍保留 clean，以提供可重复的最终验证。

下列删除命令仅可用于部署旧历史诊断阶段（0 至 11），且仅当已确认 SD 卡不存在该 Title 的任何既有覆盖时：先完全退出游戏，再删除整个 Title 覆盖目录和旧中继 IPS。Stage 12 绝不可执行这些命令，必须按后文的备份和专用覆盖流程部署。

```sh
rm -rf atmosphere/contents/010021C000B6A000
rm -rf atmosphere/nro_patches/isaac-repentance-manager-update-relay
rm -rf atmosphere/nro_patches/isaac-repentance-game-observer-relay
rm -rf atmosphere/nro_patches/isaac-repentance-game-update-observer-relay
rm -rf atmosphere/nro_patches/isaac-repentance-game-state2-observer-relay
rm -rf atmosphere/nro_patches/isaac-repentance-game-ispaused-render-observer-relay
rm -rf atmosphere/nro_patches/isaac-repentance-manager-loadconfigs-relay
```

随后只从当前阶段的整个 `atmosphere/` 树合并复制到 SD 卡的 `atmosphere/`。不能混入其他阶段或默认 `runtime/deploy` 的文件：

```text
runtime/deploy-diagnostic/stage<N>/atmosphere/contents/010021C000B6A000/exefs/main.npdm
runtime/deploy-diagnostic/stage<N>/atmosphere/contents/010021C000B6A000/exefs/subsdk9
runtime/deploy-diagnostic/stage<N>/atmosphere/nro_patches/isaac-repentance-manager-update-relay/<Build-ID>.ips
runtime/deploy-diagnostic/stage8/atmosphere/nro_patches/isaac-repentance-manager-loadconfigs-relay/<Build-ID>.ips
runtime/deploy-diagnostic/stage9/atmosphere/nro_patches/isaac-repentance-manager-loadconfigs-relay/<Build-ID>.ips
runtime/deploy-diagnostic/stage10/atmosphere/contents/010021C000B6A000/romfs/isaac_mod_probe.lua
runtime/deploy-diagnostic/stage12/atmosphere/contents/010021C000B6A000/romfs/runtime_probe.lua
```

按阶段 0、1、2、3 依次测试。查看 Atmosphere 报告中 `svcBreak` 的两个 Info 值：Info 1 为 `0x49534141435F4449` 时表示正常到达阶段边界，Info 1 为 `0x49534141435F4558` 时表示初始化异常；Info 2 是当前阶段号。异常报告绝不能作为阶段成功。每个阶段只有在应用（subsdk9）报告成功且包含完成 magic 时才记录 Atmosphere、系统、游戏版本、当前部署树全部产物的 SHA-256 和结果，然后推进下一阶段。若出现异常报告或整机冻结，立即停止并记录所在阶段；不要继续下一阶段，也不要在该状态下测试默认 Runtime 构建。

阶段 4 与阶段 5 是已保留的历史文件系统诊断，不是默认 Runtime 的组成部分。它们的独立 FS IPC 已在本机的真机测试中稳定失败，后续不得把这两个阶段的结果作为默认包需要修复的前提。

## Stage 6：Game owner 链观察（真机验证通过）

Stage 6 不写 SD 卡日志，也不是正常游玩包。Stage 35 只使用既有 Manager Update relay IPS，在
`Manager::Update` 原函数已返回后临时解析固定版本的
`module.base + 0xAAC698`，即 `global -> owner -> Game*`。安装时先验证全局槽位于目标模块的可读写数据映射，
回调时再依次确认 owner 和 `Game*` 对齐且位于可读、不可执行映射。诊断不调用 `Game::IsPaused()` 或其他游戏
API，不保存 owner 或 `Game*`，也不向 Lua 暴露对象。

真机报告 [`01787760316_010021c000b6a000.log`](../crash_reports/01787760316_010021c000b6a000.log)
已确认 Program ID `010021C000B6A000`、`Repentance.nrs` Build ID
`91C73FDD575061318D68886316AFEAC72388B2AB`、`MainThread`、`ISAAC_HP`
（`0x49534141435F4850`）和
`0x0000000600000001`。报告中的 Runtime Module ID
`A6D03340A1D7B205CC14B4FAE7315DE7A7CB5F64` 与当前 Stage 6 `subsdk9` 的 NSO 头一致，因此 Stage 35
owner 链观察已通过真机验证。这不授权缓存对象指针、调用 `Game::IsPaused()` 或添加 Lua 绑定；诊断包已经完成
取证，默认三文件树也已恢复。当前为：**默认包 smoke test 通过（用户确认游戏正常进入并持续运行，无报错）**。

### Stage 36：Game::IsPaused 原生同步调用（真机验证通过）

当前 Stage 6 诊断包在 Stage 35 已验证的同一 MainThread 时机，每次重新解析 owner 链；首次取得映射可读的
`Game*` 后，通过 `module.base + 0x671100` 的原版 PLT thunk 同步调用一次 `Game::IsPaused() const`。安装阶段
会验证该入口位于目标模块 RX text 映射，并核对固定 16 字节 guard。调用只保留 bool，不缓存 owner 或 `Game*`，
不接入 Lua、文件、线程或日志，也不新增 IPS。

Stage 36 继续只使用一个 Manager Update relay IPS。真机报告
[`01787765092_010021c000b6a000.log`](../crash_reports/01787765092_010021c000b6a000.log) 已确认 Program ID
`010021C000B6A000`、`Repentance.nrs` Build ID `91C73FDD575061318D68886316AFEAC72388B2AB`、
`MainThread`、`ISAAC_IP` 和 Info 2 `0x0000000600000002`。报告中的 Runtime Module ID
`3264FDDF5191F7E0B1BCBA8F39F8C0BE1E4E583A` 与本次 Stage 6 `subsdk9` 的 NSO 头一致，证明固定 thunk
调用在真机正常返回 true，而不是旧包、普通崩溃或仅有本地构建结果。

Stage 6 clean build 的必需 payload 恰好是三个文件：`main.npdm`、`subsdk9` 和一个 Manager Update relay IPS。
不能混入默认包、其他诊断阶段或历史 observer IPS：

```text
runtime/deploy-diagnostic/stage6/atmosphere/contents/010021C000B6A000/exefs/main.npdm
runtime/deploy-diagnostic/stage6/atmosphere/contents/010021C000B6A000/exefs/subsdk9
runtime/deploy-diagnostic/stage6/atmosphere/nro_patches/isaac-repentance-manager-update-relay/<Build-ID>.ips
```

Stage 36 成功报告的 Info 1 为 `0x49534141435F4950`（`ISAAC_IP`）。Info 2 为 `(6,1)` 表示
`Game::IsPaused()` 正常返回 false，`(6,2)` 表示正常返回 true。只有这两种报告才算原生调用成功。

若 Info 1 是 `0x49534141435F4946`（`ISAAC_IF`），则 Stage 6 在安装失败或固定 600 次观察窗口结束后受控退出；
Info 2 高 32 位始终为 `6`，低 32 位按下表定位停止位置：

| Info 2 低 32 位 | 停止位置 |
| ---: | --- |
| 1 | Program ID 查询失败或不是 `010021C000B6A000` |
| 2 | 等待 30 秒仍未找到合法 `Repentance.nrs` |
| 3 | 找到同名模块，但 Build ID 不匹配 |
| 4 | `Manager::Update` 地址或 RX 映射不匹配 |
| 5 | 旧 Stage 6 包的泛化 Hook 安装失败；Stage 35 包不再使用此状态 |
| 7 | Runtime worker 线程创建或启动失败 |
| 8-14 | 旧 Stage 6 包的远距离 trampoline 细分状态；Stage 35 包不再使用 |
| 15 | 中继 IPS 缺失，或入口分支/固定中继指令不匹配 |
| 16 | 中继回调指针槽不是初始零值，Runtime 拒绝覆盖 |
| 17 | fallback 或 Runtime callback 指针发布后的复核失败 |
| 18 | 历史 Stage 29 包的“未进入 Game Update relay”；Stage 35 包不再使用 |
| 19-36 | 历史 observer 包的安装、发布或路径观察结果；Stage 35 包不再产生这些状态 |
| 37 | 状态 37：安装时全局 owner 槽偏移、范围、映射类型或权限校验失败 |
| 38 | 状态 38：全局 owner 槽地址发布后的 acquire 复核失败 |
| 39 | 状态 39：600 次回调内 owner 始终为空 |
| 40 | 状态 40：600 次回调内 owner 链无法安全读取（包括运行期 owner 槽地址无效或不可读，以及已观察到非空 owner 后地址未对齐或字段映射不可读） |
| 41 | 状态 41：600 次回调内 owner 可读，但 `Game*` 始终为空 |
| 42 | 状态 42：600 次回调内最深结果为 `Game*` 未对齐或映射不可读 |
| 43 | 状态 43：`0x671100` thunk 地址、text 范围、RX 映射或 16 字节 guard 校验失败 |
| 44 | 状态 44：`Game::IsPaused()` thunk 地址发布后的 acquire 复核失败 |

状态 37、状态 38 是安装时全局槽失败；状态 39、状态 40、状态 41、状态 42 是固定窗口结束时报告的最深
观察结果。状态 40 的回调期槽失效是防御性路径；安装期初始验证或发布失败仍分别报告状态 37 或状态 38。状态
19-36 仍保留为历史报告含义，但 Stage 36 包不会产生它们，也不会安装对应的历史 IPS。状态 43、状态 44 是
Stage 36 新增的固定入口验证和发布失败。

Stage 6 无论产生成功或失败报告，都复制同次完整 Atmosphere 报告。每次真机尝试后都必须完全退出游戏，清理本项目
诊断覆盖，只恢复默认 `runtime/deploy/atmosphere/` 的 `main.npdm`、`subsdk9` 和一个 Manager Update relay IPS
三文件树，然后完全重启游戏并执行正常启动 smoke test。报告 `01787760316_010021c000b6a000.log` 已满足
Stage 35 的 `ISAAC_HP/(6,1)` 真机成功条件；用户随后确认默认包能够正常进入并持续运行且无报错，恢复步骤也已
通过。Stage 36 报告 `01787765092_010021c000b6a000.log` 已满足 `ISAAC_IP/(6,2)`，确认
`Game::IsPaused()` 返回 true。用户随后恢复默认三文件树并确认游戏正常进入、持续运行且无报错。当前为：
**Stage 36 默认包 smoke test 通过（用户确认游戏正常进入并持续运行，无报错）**。Stage 36 的诊断、恢复和
回归流程已经闭合；下一阶段可独立设计 Lua `Game():IsPaused()`，但仍不得扩展到其他原生 API。

## Stage 7：内嵌 Lua 闭环

Stage 7 不是正常游玩包。它将 `runtime_test.lua` 作为编译期文本内嵌到 `subsdk9`，不从 SD 卡读取任何 Lua 或 Mod 文件。该脚本只调用 PC 风格的 `RegisterMod`、`AddCallback` 和 `ModCallbacks.MC_POST_UPDATE`，每次回调递增内存计数。

先构建 stage 7，再完全退出游戏，删除旧的 Title 覆盖目录和中继 IPS。只将 `runtime/deploy-diagnostic/stage7/atmosphere/` 的完整树合并复制到 SD 卡的 `atmosphere/`，不得混入默认包或其他诊断阶段。启动游戏后，应用会在 Lua 回调达到 120 次时预期报错退出；复制同次完整 Atmosphere 报告。

唯一成功信号是 Info 1 为 `0x49534141435F4C50`（`ISAAC_LP`），Info 2 为 `0x0000000700000078`。它证明 Lua state 创建、内嵌脚本加载、`RegisterMod`、`AddCallback`、已验证的 `Manager::Update` Hook 和 Lua 函数体都实际执行。

Info 1 为 `0x49534141435F4C46`（`ISAAC_LF`）表示可控失败，Info 2 高 32 位固定为 `7`，低 32 位含义如下：

| Info 2 低 32 位 | 停止位置 |
| ---: | --- |
| 1 | `luaL_newstate()` 创建 Lua state 失败 |
| 2 | 内嵌脚本编译失败 |
| 3 | 内嵌脚本执行失败 |
| 4 | 脚本没有注册 `MC_POST_UPDATE` |
| 5 | 已注册的 Lua 回调运行时报错，Runtime 已禁用该回调 |
| `0xSSRR` | 打开安全库或注册最小 Mod API 失败。`SS` 是步骤：`01` base、`02` coroutine、`03` table、`04` string、`05` math、`06` utf8、`08` Mod API；`RR` 是 Lua 状态码，其中 `02` 为运行时错误、`04` 为内存不足。`bit32` 兼容库暂不开放。 |

任何 `ISAAC_LF`、普通崩溃报告、无报告的应用错误或整机冻结都不是成功；停止后续 API 扩展，并保留同次完整报告。得到 `ISAAC_LP` 后，完全退出游戏，再次删除两个覆盖目录，改为将 `runtime/deploy/atmosphere/` 的完整树合并复制到 SD 卡，重新启动游戏。默认包不会主动报告；游戏能够正常进入且持续运行才是默认包 smoke test 成功。

## Stage 8：ModManager 生命周期入口诊断

Stage 8 不是正常游玩包，也不会加载 Mod、读取 SD 卡或调用 `ModManager::Reset()`。它只在 `Manager::LoadConfigs` 的第一条指令处得到 `Manager*`，计算 `Manager + 0x36800`，确认该候选地址非空、无整数溢出且按 8 字节对齐，然后预期报错退出。

完全退出游戏后，删除 Title 覆盖和两个 relay 目录，再只将 `runtime/deploy-diagnostic/stage8/atmosphere/` 的完整树合并复制到 SD 卡的 `atmosphere/`。stage 8 包含两个 IPS：既有的 `isaac-repentance-manager-update-relay` 与专用的 `isaac-repentance-manager-loadconfigs-relay`；本阶段只发布后者的 callback。不要与默认包或其他阶段混用。

唯一成功信号是 Atmosphere 报告的 Break Address 为 `0x49534141434D4F44`（`ISAACMOD`），Info 2 为 `0x0000000800036800`，并且调用栈包含 `Manager::LoadConfigs -> Manager::Init -> IsaacStartup`。`0x49534141434D464C`（`ISAACMFL`）是受控失败：低 32 位 `1` 表示没有发现正确目标模块或 Build ID，`2` 表示入口或 relay 指令不匹配，`3` 表示 relay 槽已占用或发布复核失败，`4` 表示 `Manager*` 或候选地址校验失败。

普通 Data Abort、Instruction Abort、无报告的应用错误或整机冻结都不是成功。无论结果，都先删除这三个覆盖目录再恢复默认 Runtime；stage 8 成功只证明生命周期时机和对象地址计算，不授权调用 `Reset()`。

## Stage 9：原生 ModManager Reset 返回验证（已停止）

Stage 9 是一次已完成的取证诊断，**不得用于正常游玩**，也不得再次部署。它不准备 Mod，不读 SD 文件，不执行 Lua，不恢复 `TryRedirectPath()`，也不会继续执行原始 `Manager::LoadConfigs()`。

真机报告 [`01787544345_010021c000b6a000.log`](../crash_reports/01787544345_010021c000b6a000.log) 显示：`Reset()` 已正确进入 `ListMods()`，随后在 `KAGE::Filesys::ContentManager::get_directory_entries()` 中以 `0x2F6202 (2002-6065)` 主动终止。它没有返回到 Runtime，因此不存在 `ISAACRST` 成功信号。这验证了 `Manager + 0x36800` 与 `Reset(void*)` 调用 ABI，但否定了在当前生命周期直接复用完整原生扫描器的方案。

历史协议仍保留以便复核已有产物：只有 `ISAACRST` 且 Info 2 为 `0x0000000900422440` 才表示 `Reset()` 返回；`ISAACRFL` 表示 Runtime 在调用前完成了受控拒绝。上述两个标识均未出现在本次报告，原因是原生 `ListMods()` 已先行终止。

请完全退出游戏，删除 Title 覆盖目录和两个中继目录，然后恢复默认 `runtime/deploy/atmosphere/`。后续外部 Mod 支持不得从 `Reset()`、`ListMods()` 或 `TryRedirectPath()` 的直接复用途径继续。

## Stage 11：游戏 File 公开读取 + RomFS 哨兵（已真机验证）

阶段 10 的 `ISAACFPO / 0x12` 已证明 `OpenRead()` 对 18 字节文件完成了底层预读，内部游标已到 EOF。阶段 11 因而不再直接调用内部 `read_stream_data()`，而是在相同的受控主线程边界调用公开 `File::Read()`，让游戏缓冲层先返回预读数据。它不执行 Lua，不扫描目录，不读取 SD 卡，不调用独立 FS IPC，也不代表 PC Mod、资源 Mod 或通用外部文件已经可用。

使用 Docker 构建 `DIAGNOSTIC_STAGE=11` 后，只将 `runtime/deploy-diagnostic/stage11/atmosphere/` 的完整树合并复制到 SD 卡。该树应只有四个常规文件：

```text
contents/010021C000B6A000/exefs/main.npdm
contents/010021C000B6A000/exefs/subsdk9
contents/010021C000B6A000/romfs/isaac_mod_probe.lua
nro_patches/isaac-repentance-manager-update-relay/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips
```

`isaac_mod_probe.lua` 的内容固定为 `ISAAC_ROMFS_PROBE\n`，长度为 18 字节。Stage 11 不含 LoadConfigs relay IPS；部署前先完全退出游戏，并删除 Title 覆盖、Update relay 和 LoadConfigs relay，不能与默认包或任意历史 stage 混用。

部署前必须核对 Stage 11 的四个文件：

```text
4a67e5ac4710f635e494dc6bab3e01de87cbfb584a38bcfcf9ff3d477e9d41f5  exefs/main.npdm
81a6c8c7d3267d53ad4f7f12bf42a01a64f333b8899560bec02b8a55304f2fdb  exefs/subsdk9
eddd22720685263ccf13839ecb77a8b737560553335119374a91cb9bfcf0832f  romfs/isaac_mod_probe.lua
d72e6951f1cd3a2221dc79c0bee8099d814d64ab0baa053f26d46edc77ac70ea  Update relay IPS
```

唯一成功证据是同一份 Atmosphere 报告同时显示正确的 Program ID `010021C000B6A000`、`Repentance.nrs` Build ID `91C73FDD575061318D68886316AFEAC72388B2AB`、Break Address `ISAACFOK` 和 Info 2 `0x0000000B00000012`。报告的崩溃线程/调用栈还必须位于 `Manager::Update` 与 Runtime callback；仅有静态测试、Docker 构建或应用报错都不是成功。真机报告 [`01787571729_010021c000b6a000.log`](../crash_reports/01787571729_010021c000b6a000.log) 已满足这些条件，证明 `rom:/isaac_mod_probe.lua` 的 Atmosphere 覆盖和游戏公开 `File::Read()` 路径均可用。

`ISAACFFL` 是受控失败，Info 2 高 32 位为 `11`，低 32 位如下：

| Info 2 低 32 位 | 停止位置 |
| ---: | --- |
| 1 | `svcGetInfo(ProgramId)` 失败 |
| 2 | 查询到的 Program ID 不是 `010021C000B6A000` |
| 3 | worker 读到的启动状态不是 `TitleOk` |
| 4 | 发现同名 `Repentance.nrs`，但 Build ID 不匹配 |
| 5 | 旧部署包的即时绑定失败；新包不再以此状态终止 |
| 6 | 等待 30 秒仍未找到合法 `Repentance.nrs` |
| 7 | Runtime worker 线程创建失败 |
| 8 | Runtime worker 线程启动失败 |
| 9 | 找到正确的 `Repentance.nrs`，但 30 秒内 Update relay 或任一 File 入口 guard/绑定始终未就绪 |
| 10 | `File::OpenRead` 失败 |
| 11 | `File::GetLength` 不是 18 |
| 12 | 公开 `File::Read()` 没有返回 18 字节 |
| 13 | 读取内容与固定哨兵不一致 |
| 14 | 一次性 callback 状态不是 `Running` |
阶段 11 不读取或改写 `File + 0x40`、`File + 0x58`。`File::Read()` 的入口为 `0x4CD184`，静态反汇编显示其转入游戏 `BufferedStreamBase::ReadFromBuffer()`，因此可消耗 `OpenRead()` 已建立的缓冲数据。

Runtime 每 100ms 扫描一次目标动态模块，最多等待约 30 秒；这是为了覆盖真机上 `Repentance.nrs` 晚于 `runtime` 完成加载的启动路径。若模块的路径与 Build ID 已匹配、但 `nn::ro::LoadModule` 尚未完成注册而导致 guard/绑定暂时失败，Stage 11 会在该窗口内继续扫描并重试；只有成功安装后才停止等待。不会在正常游戏帧中持续扫描。

出现 `ISAACFFL`、普通 abort、无报告应用错误或整机冻结时，停止后续 Lua 或 Mod 工作并保留同次完整报告。无论真机结果如何，都完全退出游戏，删除下面三个覆盖目录后恢复默认 `runtime/deploy/atmosphere/`，再启动游戏确认能正常进入；只有该默认包 smoke test 通过，本轮才完成恢复默认。

```sh
rm -rf atmosphere/contents/010021C000B6A000
rm -rf atmosphere/nro_patches/isaac-repentance-manager-update-relay
rm -rf atmosphere/nro_patches/isaac-repentance-manager-loadconfigs-relay
```

## Stage 12：受限 RomFS 外部 Lua 验证（已真机验证）

Stage 12 不是正常游玩包。它在已经由 Stage 11 验证过的游戏主线程边界，复用游戏公开 `KAGE::Filesys::File` 生命周期读取唯一固定路径 `rom:/runtime_probe.lua`。文件长度必须为 `1..4096` 字节；Runtime 不扫描目录、不访问 SD 卡、不调用裸 `nn::fs`，也不会读取 `metadata.xml`、资源文件或第二个 Mod。

构建并部署此诊断包：

```sh
docker run --rm -v "$PWD":/work -w /work devkitpro/devkita64:latest \
  sh -lc '. /opt/devkitpro/devkita64.sh && make -C runtime clean DIAGNOSTIC_STAGE=12 && make -C runtime DIAGNOSTIC_STAGE=12'
```

完全退出游戏后，先备份现有覆盖，再部署 Stage 12。这个过程会替换本项目使用的 `main.npdm`、`subsdk9`、两个已知诊断 Lua 文件和两个 relay IPS；因此，即使旧 Mod 看似无关，也不能跳过备份。下面命令适用于 macOS 的 zsh；将 `/Volumes/SD_CARD` 改为 SD 卡实际卷名。它只备份/清理本 Title 的目录和本项目的 relay，不会删除其他 Title 的内容。

```sh
sd_root="/Volumes/SD_CARD"
backup_root="$sd_root/isaac-stage12-backup-$(date +%Y%m%d-%H%M%S)"
title_root="$sd_root/atmosphere/contents/010021C000B6A000"
update_relay="$sd_root/atmosphere/nro_patches/isaac-repentance-manager-update-relay"
loadconfigs_relay="$sd_root/atmosphere/nro_patches/isaac-repentance-manager-loadconfigs-relay"
runtime_probe="$title_root/romfs/runtime_probe.lua"

mkdir -p "$backup_root"
if [[ -e "$title_root" ]]; then
  mkdir -p "$backup_root/contents"
  ditto "$title_root" "$backup_root/contents/010021C000B6A000"
fi
if [[ -e "$update_relay" ]]; then
  mkdir -p "$backup_root/nro_patches"
  ditto "$update_relay" "$backup_root/nro_patches/isaac-repentance-manager-update-relay"
fi
if [[ -e "$loadconfigs_relay" ]]; then
  mkdir -p "$backup_root/nro_patches"
  ditto "$loadconfigs_relay" "$backup_root/nro_patches/isaac-repentance-manager-loadconfigs-relay"
fi
[[ -e "$runtime_probe" ]] && ditto "$runtime_probe" "$backup_root/runtime_probe.lua"

rm -f "$title_root/exefs/main.npdm" "$title_root/exefs/subsdk9"
rm -f "$title_root/romfs/isaac_mod_probe.lua" "$runtime_probe"
rm -f "$update_relay/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips"
rm -f "$loadconfigs_relay/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips"
ditto runtime/deploy-diagnostic/stage12/atmosphere "$sd_root/atmosphere"
```

`title_root` 的备份会包含其中已有的 `romfs/runtime_probe.lua`；单独保存该文件只是让本次诊断文件可直接核对。若其中有用户原有 Mod，Stage 12 的 `exefs` 文件仍会覆盖它们，因此测试结束必须按下文“回滚”优先恢复这个时间戳备份。不得用 `rm -rf` 删除整个 Title 目录或 `nro_patches` 目录，也不要把默认 Runtime 当作有旧覆盖时的回滚替代品。部署后的树必须恰好包含下列四个本项目文件；同一 Title 下未列出的用户文件会被保留：

```text
contents/010021C000B6A000/exefs/main.npdm
contents/010021C000B6A000/exefs/subsdk9
contents/010021C000B6A000/romfs/runtime_probe.lua
nro_patches/isaac-repentance-manager-update-relay/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips
```

本次 clean build 的实测 SHA-256：

```text
4a67e5ac4710f635e494dc6bab3e01de87cbfb584a38bcfcf9ff3d477e9d41f5  exefs/main.npdm
11e34a1389c9b9ca258f4b1fc4ccb29d1e2b9e2daf27b4d6221a3c3c5492fa91  exefs/subsdk9
843a030c6e365e2a51ae807de94cb4347c8b54b4f0ae85f2790bb5d1b73c569a  romfs/runtime_probe.lua
d72e6951f1cd3a2221dc79c0bee8099d814d64ab0baa053f26d46edc77ac70ea  Update relay IPS
```

`runtime_probe.lua` 使用 PC 风格 `RegisterMod` 和 `AddCallback(ModCallbacks.MC_POST_UPDATE, ...)`。其回调额外调用现有的 `RuntimeTest.MarkPostUpdate()`，这只是诊断计数器，用来证明外部脚本函数体实际被调用，不是将来 PC Mod 的新增依赖。

唯一成功信号是同一份 Atmosphere 报告同时满足：Program ID 为 `010021C000B6A000`、`Repentance.nrs` Build ID 为 `91C73FDD575061318D68886316AFEAC72388B2AB`、线程为 `MainThread`、Break Address 为 `0x4953414143454C50`（`ISAACELP`），且 Info 2 为 `0x0000000C00000078`。这是第 120 次 `MC_POST_UPDATE` 回调后故意触发的 User Break，不是普通游戏崩溃。真机报告 [`01787581471_010021c000b6a000.log`](../crash_reports/01787581471_010021c000b6a000.log) 已满足这些条件；Docker 构建、哈希和静态测试本身仍不能替代真机成功证据。

`ISAACELF`（`0x4953414143454C46`）是受控失败，Info 2 高 32 位固定为 `12`：低 32 位 `1..4` 分别是 Program ID、Title、启动状态或 Build ID；`6..9` 分别是模块超时、worker 创建/启动失败或 File/relay 绑定在等待窗口内未就绪；`10` 是 `OpenRead` 失败，`11` 是文件为空或超过 4096，`12` 是短读；`13..16` 分别是 Lua state/准备、脚本编译、脚本执行或没有注册 `MC_POST_UPDATE`；`17` 是后续 Lua 回调报错，`18` 是诊断状态机异常。任何 `ISAACELF`、普通 abort、无报告应用错误或整机冻结都不是成功：保留完整报告，停止后续兼容性开发。

无论结果，都完全退出游戏，并按下文“回滚”优先恢复刚才创建的时间戳备份；只有测试前不存在任何本 Title 覆盖和本项目 relay 时，才部署默认 `runtime/deploy/atmosphere/` 作为恢复路径。恢复后重新启动游戏确认能正常进入。Stage 12 即使通过，也只证明一个受限 RomFS Lua 文件能经游戏 File API 载入现有 Lua Runtime 并运行此回调；它不证明 SD 卡访问、多 Mod 发现/启停、`metadata.xml`、资源 Mod、`ListMods()`、`TryRedirectPath()` 或完整 PC Mod 兼容。

## Stage 13：清单、受限 require 与 PC 回调链诊断（待真机验证）

Stage 13 不是正常游玩包。它只使用 Task 1 的确定性 `00 Runtime Require Probe`：首次
`Manager::Update()` 原函数返回后，在游戏主线程读取
`rom:/isaac_mods/manifest.json`，选择首个启用项，读取其 `main.lua`，再通过受限
`require` 加载 `src/mod.lua`。当前 fixture 还让 `src/mod.lua` 使用嵌套
`require("src.metadata")` 取得 Lua 表中的 `name` 后创建 Mod。回调累计到 120 次后会主动 User Break；
这是诊断协议的一部分。不要把该包与默认 Runtime 或 Stage 7/11/12 混用，也不要替换 fixture
为真实第三方 Mod。

在仓库根目录构建并检查独立产物：

```sh
docker run --rm -v "$PWD":/work -w /work devkitpro/devkita64:latest \
  sh -lc '. /opt/devkitpro/devkita64.sh && make -C runtime clean DIAGNOSTIC_STAGE=13 && make -C runtime DIAGNOSTIC_STAGE=13'

find runtime/deploy-diagnostic/stage13/atmosphere -type f -print | sort
```

`runtime/deploy-diagnostic/stage13/atmosphere/` 必须包含 `exefs/main.npdm`、`exefs/subsdk9`、
Update relay IPS，以及生成的 `romfs/isaac_mods/manifest.json`、`main.lua`、`src/mod.lua` 和
`src/metadata.lua`。Stage 13 不应包含 LoadConfigs relay、Stage 11 哨兵或 Stage 12 probe。

真机操作必须由用户在游戏完全退出后完成。下面命令适用于 macOS zsh；在仓库根目录执行，先将
`/Volumes/SD_CARD` 改为实际 SD 卡卷名。它只备份并清理本 Title 和本项目两个已知 relay 的
具体文件，然后将 Stage 13 的 `atmosphere/` **合并**到 SD 卡，不会替换整个
`atmosphere` 或删除其他 Title：

```sh
sd_root="/Volumes/SD_CARD"
backup_root="$sd_root/isaac-stage13-backup-$(date +%Y%m%d-%H%M%S)"
title_root="$sd_root/atmosphere/contents/010021C000B6A000"
update_relay="$sd_root/atmosphere/nro_patches/isaac-repentance-manager-update-relay"
loadconfigs_relay="$sd_root/atmosphere/nro_patches/isaac-repentance-manager-loadconfigs-relay"
relay_name="91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips"
stage13_romfs="$title_root/romfs/isaac_mods"

mkdir -p "$backup_root"
if [[ -d "$title_root" ]]; then
  mkdir -p "$backup_root/contents"
  ditto "$title_root" "$backup_root/contents/010021C000B6A000"
fi
if [[ -d "$update_relay" ]]; then
  mkdir -p "$backup_root/nro_patches"
  ditto "$update_relay" "$backup_root/nro_patches/isaac-repentance-manager-update-relay"
fi
if [[ -d "$loadconfigs_relay" ]]; then
  mkdir -p "$backup_root/nro_patches"
  ditto "$loadconfigs_relay" "$backup_root/nro_patches/isaac-repentance-manager-loadconfigs-relay"
fi

rm -f "$title_root/exefs/main.npdm" "$title_root/exefs/subsdk9"
rm -f "$title_root/romfs/isaac_mod_probe.lua" "$title_root/romfs/runtime_probe.lua"
rm -rf "$stage13_romfs"
rm -f "$update_relay/$relay_name" "$loadconfigs_relay/$relay_name"
ditto runtime/deploy-diagnostic/stage13/atmosphere "$sd_root/atmosphere"
```

首次两层 `require` Probe 的真机报告为 `ISAACRQF / 0x0000000D00000016`，表示脚本已进入
`require` 的执行阶段。将模块返回值降为 `true`、把 `RegisterMod` 放在入口后，真机已得到
`ISAACRQP / 0x0000000D00000078`，证明第一层 `require` 本身可用。随后让 `src/mod.lua` 在模块内部创建
Mod 并保存到测试专用全局 `RuntimeRequireProbeMod`，仍只返回 `true`，真机报告
`01787668086_010021c000b6a000.log` 仍为 `13:22`。更新后的真机报告
`01787669259_010021c000b6a000.log` 已确认新 Runtime 模块 ID `29D0F711...` 被加载，但仍不是 `13:26`，
故可排除“旧二进制仍在运行”和已知重复注册错误。当前包把未知模块执行错误改为 `ISAACRQT`
（`0x4953414143525154`），其 Info 2 直接存放原始 Lua 错误末尾最多 8 个字节；这是一次性取证格式，
不再有固定的 `13` 高位。`13:26` 仍表示 Lua 明确返回了 `RegisterMod accepts exactly one Mod`。无论结果如何，
嵌套 `require` 都必须在该边界明确后恢复并单独复测。报告
`01787673514_010021c000b6a000.log` 已证明 `src/mod.lua` 可直接 `return RegisterMod(...)`，入口以
`local mod = require("src.mod")` 接收该 userdata 后调用 `mod:AddCallback(...)`。当前 Probe 在此基础上
恢复 `src/mod.lua -> require("src.metadata") -> Lua 表 -> RegisterMod`，用于单独验证同一 Mod 根目录内的
嵌套依赖；它不开放跨 Mod 导入或额外 API。

报告 `01787671551_010021c000b6a000.log` 已返回 `ISAACRQT`，Info 2 为 `0x68206d656d6f7279`，即
`not enough memory` 的末尾。根因是 Runtime 原有 `0x5000`（20 KiB）私有 fake heap 耗尽，而不是主机
系统内存不足；当前 Stage 13 构建将其提升到 `0x20000`（128 KiB）。该数值只增加 Runtime 自己的 BSS
预算，不修改 NPDM、不申请系统额外内存，也不代表复杂第三方 Mod 已经受支持。

启动游戏后预期应用主动退出。将 SD 卡重新连接电脑，在仓库外或专门的取证目录复制报告；下面
命令只读取 SD 卡报告，不会删除原件。记录启动时间，并在复制结果中选择同一时刻且 Program ID
为 `010021C000B6A000` 的完整 Atmosphere 报告：

```sh
report_dir="../stage13-atmosphere-reports"
mkdir -p "$report_dir"
ditto "$sd_root/atmosphere/crash_reports" "$report_dir"
```

报告必须同时显示：

- Program ID `010021C000B6A000`；
- `Repentance.nrs` Build ID `91C73FDD575061318D68886316AFEAC72388B2AB`；
- 线程为 `MainThread`，调用栈位于 `Manager::Update` 返回后的 Runtime callback；
- Break Address 为 `0x4953414143525150`（`ISAACRQP`），Info 2 为
  `0x0000000D00000078`。

只有满足以上同次真机报告，才能确认 Stage 13 成功。`ISAACRQF`
（`0x4953414143525146`）表示受控失败；Info 2 高 32 位固定为 `13`，低 32 位如下：

| 低 32 位 | 停止位置 |
| ---: | --- |
| 1 / 2 / 3 / 4 | Program ID 查询 / Title 不匹配 / worker 启动状态 / Build ID 不匹配 |
| 5 | 未使用 |
| 6 / 7 / 8 / 9 | 模块等待超时 / worker 创建 / worker 启动 / File 或 relay 绑定未就绪 |
| 10 / 11 / 12 / 13 | 清单打开 / 长度 / 读取 / 解析或选择 |
| 14 / 15 | 入口打开 / 入口长度或读取 |
| 16 / 17 / 18 | Lua 准备 / 入口编译 / 入口执行 |
| 19 / 20 / 21 / 22 | require 路径 / 打开 / 长度或读取 / 模块编译 |
| 23 / 24 / 25 | 未注册回调 / 回调执行错误 / 状态机异常 |
| 26 | 模块执行中明确收到 `RegisterMod accepts exactly one Mod` |

`ISAACRQT` 不是常规 `ISAACRQF`。它只表示未分类的模块执行错误；将 Info 2 的 16 位十六进制数按每两位
按每两位转换为字节，即可得到原始 Lua 错误的末尾；ASCII 错误可直接阅读。例如 `0x65206661696C6564` 表示
`e failed`。

普通崩溃、无报告应用错误、冻结或仅有本地构建都不是成功。

采集报告后完全退出游戏，按下列命令只移除本次 Stage 13 写入的具体文件，再恢复刚才的时间戳
备份。若备份中没有 Title 和两个 relay，说明测试前没有这些覆盖，这时才合并默认 Runtime：

```sh
rm -f "$title_root/exefs/main.npdm" "$title_root/exefs/subsdk9"
rm -rf "$stage13_romfs"
rm -f "$update_relay/$relay_name"

[[ -d "$backup_root/contents/010021C000B6A000" ]] && \
  ditto "$backup_root/contents/010021C000B6A000" "$title_root"
[[ -d "$backup_root/nro_patches/isaac-repentance-manager-update-relay" ]] && \
  ditto "$backup_root/nro_patches/isaac-repentance-manager-update-relay" "$update_relay"
[[ -d "$backup_root/nro_patches/isaac-repentance-manager-loadconfigs-relay" ]] && \
  ditto "$backup_root/nro_patches/isaac-repentance-manager-loadconfigs-relay" "$loadconfigs_relay"

if [[ ! -d "$backup_root/contents/010021C000B6A000" && \
      ! -d "$backup_root/nro_patches/isaac-repentance-manager-update-relay" && \
      ! -d "$backup_root/nro_patches/isaac-repentance-manager-loadconfigs-relay" ]]; then
  ditto runtime/deploy/atmosphere "$sd_root/atmosphere"
fi
```

上述最后一个分支使用的默认恢复树是 `runtime/deploy/atmosphere/`。恢复后重新启动游戏做默认包
smoke test。
Stage 13 即使通过，也只证明固定 fixture 的清单选择、三个 Lua 文件的受限 require 和一个
`MC_POST_UPDATE` 回调链；真实第三方 Mod、多个 Mod、资源重定向及其他游戏 API 仍不支持。

## 默认运行与诊断

默认 Runtime 不产生 `isaac-runtime-probe.log`；没有该文件是预期行为。默认包只校验 Title ID、扫描指定 Build ID 的 `Repentance.nrs`、安装 `Manager::Update` Hook、创建内嵌 Lua state 并分发最小 `MC_POST_UPDATE` 回调。它不调用 SD 卡、游戏 `nn::fs`、`smInitialize()` 或 `fsInitialize()`，也不加载外部 PC Mod。

默认包能正常进入游戏时，表示当前启动路径没有触发未处理异常；若出现应用错误，复制同次 Atmosphere 报告用于分析。需要确认 Hook callback 是否真正到达时，只使用阶段 6，不要通过延长等待时间或恢复文件日志猜测状态。

## 回滚

使用 Stage 12 部署命令生成的 `$backup_root` 恢复，先只删除本项目刚写入的具体文件，再将备份合并回 SD 卡。以下命令假定仍在同一终端；若重新打开终端，先把 `backup_root` 设为实际创建的 `isaac-stage12-backup-时间戳` 目录。它不会删除其他 Title 或当前 Title 中不属于本项目的文件：

```sh
rm -f "$title_root/exefs/main.npdm" "$title_root/exefs/subsdk9"
rm -f "$title_root/romfs/isaac_mod_probe.lua" "$title_root/romfs/runtime_probe.lua"
rm -f "$update_relay/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips"
rm -f "$loadconfigs_relay/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips"

[[ -d "$backup_root/contents/010021C000B6A000" ]] && ditto "$backup_root/contents/010021C000B6A000" "$title_root"
[[ -d "$backup_root/nro_patches/isaac-repentance-manager-update-relay" ]] && ditto "$backup_root/nro_patches/isaac-repentance-manager-update-relay" "$update_relay"
[[ -d "$backup_root/nro_patches/isaac-repentance-manager-loadconfigs-relay" ]] && ditto "$backup_root/nro_patches/isaac-repentance-manager-loadconfigs-relay" "$loadconfigs_relay"
```

若上述备份目录中没有 Title 覆盖和两个 relay，说明部署前没有可恢复的本项目/用户覆盖；这时才可以执行 `ditto runtime/deploy/atmosphere "$sd_root/atmosphere"` 恢复默认 Runtime，并完全重启游戏确认能正常进入。该流程从不触碰游戏安装文件。

## 真机记录

Manager Update relay 与内嵌 Lua Stage 7 均已完成真机验证；Stage 7 的成功条件与报告证据见上文“Stage 7：内嵌 Lua 闭环”。每次测试记录以下内容：

| Atmosphere | 系统版本 | 游戏版本 | Runtime SHA-256 | 构建类型 | 启动/报告结果 |
| --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |
