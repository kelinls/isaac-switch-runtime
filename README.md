# isaac-switch-runtime

**作用**：让 **PC 版《以撒的结合》的 Lua 模组**（例如 EID「External Item Descriptions」物品描述）
能在 **Nintendo Switch 版《以撒的结合：忏悔》**上运行。做法是在游戏进程里注入一个运行时模块，
装上派发挂点、实现一套 Lua API，再按清单把模组加载起来。

> ⚠️ **这是开发测试版（work-in-progress / experimental），明确不保证可用。**
> 它在开发者自己的机器上只验证过少数场景；可能**启动失败、运行中崩溃、功能静默失效**，
> 也可能影响存档与设备。**请只在你自己的、已备份、可承受风险的机器上使用**，后果自负。
>
> 本仓库**不包含任何模组本体**（例如 EID 是第三方作品，请自行获取并遵守其许可），
> 也不包含游戏数据、密钥或设备凭据。

## 适用环境

| 项目 | 要求 |
| --- | --- |
| 机器 | 已破解、可运行 **Atmosphère** 的 Switch |
| 游戏 | **The Binding of Isaac: Repentance**，标题 ID `010021C000B6A000`，版本 **1.7.9b**（构建号 `91C73FDD575061318D68886316AFEAC72388B2AB`） |
| 其他 | 运行时**会核对游戏构建号**，版本不符不会加载 |

## 它做什么 / 不做什么

**做**：
- 在游戏进程里装上派发挂点（每帧更新、渲染、取道具前、Present 前、内容挂载点重建），并自己派发"游戏开局"事件；
- 实现 PC 版 Lua API 的一个**子集**（`Isaac` / `Game` / `Level` / `Entity` / `EntityPlayer` /
  `ItemConfig` / `Sprite` / `Font` / `RNG` / `Vector` / `Color` 等），并带一张**可枚举的 API Catalog**；
- 按 `manifest.json` 加载一个模组：读清单 → 读入口脚本 → 建 Lua 状态 → 注册回调 → 挂载模组资源。

**不保证**：
- **语义与 PC 完全一致**。我们按三个维度验收，任何一维不满足都会被标记，但**"未标记"不等于"一定正确"**
  （基准见 `CONTRIBUTING.md`）：① **存在**（PC 有的接口我们有没有）② **语义**（返回值是否与 PC 一致）
  ③ **时机**（回调/事件的派发时机是否与 PC 一致）。
- 兼容所有模组：目前只在 EID 上做过较多验证，且仍有已知缺口。

## 已知问题（节选）

1. **启动期概率崩溃**：当"运行时的启动期工作"与"游戏装载自身模块"重叠时，可能出现
   `nn::ro::LoadModule → RoModule::BindVariables → Abort`（表现是启动直接报错、进不去游戏）。
   当前规避办法是把运行时的启动期工作**推迟若干秒**（见 `runtime/source/runtime_entry.cpp` 的模块 worker）。
2. **部分 Lua 接口缺失或语义未核对**：缺失接口在模组里表现为"调用 nil"；若发生在**没有 `pcall` 保护**的
   条件回调里，会让**整条物品描述消失**（例如 `Level:IsAltStage` 曾导致"潘多拉魔盒"不显示说明）。
   仓库内附静态核对工具：`tools/eid_api_gap_report.py`。

## 构建

需要 Docker 与 `devkitpro/devkita64` 镜像：

```bash
docker run --rm -v "$PWD":/work -w /work devkitpro/devkita64:latest bash -lc \
  '. /opt/devkitpro/devkita64.sh && make -C runtime -j6 LAYERED_RUNTIME=1 PROBE_BREAK=0 \
   OUT=deploy BUILD=build runtime_module'
```

产物：`deploy/atmosphere/contents/010021C000B6A000/exefs/{subsdk9,main.npdm}`。

## 部署

把 `atmosphere` 目录合并覆盖到 SD 卡根目录，然后**重启主机**（不是重启游戏）。
运行时以"游戏额外的一个模块"（`subsdk9`）形式加载。

> ⚠️ 覆盖 `main.npdm` 前请先备份你自己的：它决定进程的系统调用权限配置。

## 目录结构

```
runtime/
  source/            运行时本体（入口、挂点管理、Lua 运行时、引擎绑定、SaltyNX 兼容层）
  src/               分层架构：domain / application / infrastructure / interfaces
  exelaunch/         exlaunch 兼容层（第三方派生，见文件头许可）
  source/third_party/lua-5.3.3   Lua 5.3.3 源码（MIT）
  tests/             单元 / 集成 / 契约测试
tools/               设备侧与离线工具：FTP 读写、调试桩读数、发布包部署、API 缺口核对
tests/               跨模块集成与契约测试
```

**设备访问凭据不写在仓库里**：所有设备工具从 `--host/--user/--password`、环境变量
（`ISAAC_DEVICE_*`），或本机私有文件 `tools/device.local.json`（已 gitignore）读取，
统一入口见 `tools/device_access.py`。

## 免责声明

- 本项目与 Nintendo、Edmund McMillen / Nicalis 及任何模组作者**无关联**；
- 不提供任何游戏本体、密钥或模组内容；
- 修改主机系统与游戏进程存在风险（也可能违反主机使用条款），请自行判断并自担后果。

## 致谢与许可

项目派生/参考了以下开源项目，**整体以 GPL-2.0 发布**（见 `LICENSE`）：

- [Atmosphère-NX](https://github.com/Atmosphere-NX/Atmosphere)（GPL-2.0）：`runtime/source/nn/os/*` 等；
- [libnx](https://github.com/switchbrew/libnx)（ISC）：`runtime/source/lib/nx/*` 等；
- [exlaunch](https://github.com/shadowninja108/exlaunch)（GPL-2.0）：兼容层与入口约定；
- [Lua 5.3.3](https://www.lua.org/)（MIT）：`runtime/source/third_party/lua-5.3.3`。

第三方模组（例如 EID，作者 wofsauge）**不在本仓库内**，版权归原作者。
