# Define paths relative to the project root. This keeps Make's parser from
# splitting an absolute working directory that contains spaces.
MISC_PATH := misc
MK_PATH := $(MISC_PATH)/mk
SCRIPTS_PATH := $(MISC_PATH)/scripts
SPECS_PATH := $(MISC_PATH)/specs
NPDM_JSON_PATH := $(MISC_PATH)/npdm-json

# To configure exlaunch, edit config.mk.
include config.mk

# Define common variables.
NAME := $(shell basename "$(CURDIR)")
DIAGNOSTIC_STAGE ?=
STARTUP_PROBE_STAGE ?=
STARTUP_PROBE_LAYOUT ?=
SALTYNX_PLUGIN ?= 0
SALTYNX_DIAGNOSTIC_BRIDGE ?= 0
SKIP_DEFAULT_PC_MODS ?= 0
PERSISTENCE_TRACE ?= 0
PERSISTENCE_EVENT_DIAGNOSTIC ?= 0
PERSISTENCE_REQUIRED ?= 0
# 1 links the layered production Runtime (`runtime/src`) and routes the module
# entry through RuntimeBootstrap. Probe and production now share the same Lua
# API family translation units; the retired non-layered handler copies are gone.
LAYERED_RUNTIME ?= 1
ifndef TEST_BUILD_ID
TEST_BUILD_ID := $(shell date -u +%Y%m%d%H%M%S)
endif
ifneq ($(filter 0 1,$(SALTYNX_PLUGIN)),$(SALTYNX_PLUGIN))
$(error SALTYNX_PLUGIN must be 0 or 1)
endif
ifneq ($(filter 0 1,$(SALTYNX_DIAGNOSTIC_BRIDGE)),$(SALTYNX_DIAGNOSTIC_BRIDGE))
$(error SALTYNX_DIAGNOSTIC_BRIDGE must be 0 or 1)
endif
ifneq ($(filter 0 1,$(SKIP_DEFAULT_PC_MODS)),$(SKIP_DEFAULT_PC_MODS))
$(error SKIP_DEFAULT_PC_MODS must be 0 or 1)
endif
ifneq ($(filter 0 1,$(PERSISTENCE_TRACE)),$(PERSISTENCE_TRACE))
$(error PERSISTENCE_TRACE must be 0 or 1)
endif
ifneq ($(filter 0 1,$(PERSISTENCE_EVENT_DIAGNOSTIC)),$(PERSISTENCE_EVENT_DIAGNOSTIC))
$(error PERSISTENCE_EVENT_DIAGNOSTIC must be 0 or 1)
endif
ifneq ($(filter 0 1,$(PERSISTENCE_REQUIRED)),$(PERSISTENCE_REQUIRED))
$(error PERSISTENCE_REQUIRED must be 0 or 1)
endif
ifneq ($(filter 0 1,$(LAYERED_RUNTIME)),$(LAYERED_RUNTIME))
$(error LAYERED_RUNTIME must be 0 or 1)
endif
ifneq ($(strip $(STARTUP_PROBE_STAGE)),)
ifneq ($(words $(STARTUP_PROBE_STAGE)),1)
$(error STARTUP_PROBE_STAGE must be one of 1, 2, 3, 4, 5, 6, 7, 8)
endif
ifeq ($(filter 1 2 3 4 5 6 7 8,$(STARTUP_PROBE_STAGE)),)
$(error STARTUP_PROBE_STAGE must be one of 1, 2, 3, 4, 5, 6, 7, 8)
endif
ifneq ($(strip $(DIAGNOSTIC_STAGE)),)
$(error STARTUP_PROBE_STAGE cannot be combined with DIAGNOSTIC_STAGE)
endif
ifneq ($(SALTYNX_PLUGIN),0)
$(error STARTUP_PROBE_STAGE cannot be combined with SALTYNX_PLUGIN)
endif
ifeq ($(STARTUP_PROBE_STAGE),6)
ifeq ($(strip $(STARTUP_PROBE_LAYOUT)),)
STARTUP_PROBE_LAYOUT := compact
endif
ifeq ($(filter compat compact,$(STARTUP_PROBE_LAYOUT)),)
$(error STARTUP_PROBE_LAYOUT for stage 6 must be compat or compact)
endif
else ifneq ($(strip $(STARTUP_PROBE_LAYOUT)),)
$(error STARTUP_PROBE_LAYOUT is only valid with STARTUP_PROBE_STAGE=6)
endif
endif
ifeq ($(SALTYNX_PLUGIN),1)
ifeq ($(filter 133 134 135 136 137 138 139 140 141 142 143 144 145 146,$(DIAGNOSTIC_STAGE)),)
$(error SALTYNX_PLUGIN=1 requires DIAGNOSTIC_STAGE=133 through 146)
endif
OUT := deploy-saltynx/stage$(DIAGNOSTIC_STAGE)
EXL_BUILD_DIR := build-saltynx-stage$(DIAGNOSTIC_STAGE)
EXL_ARTIFACT_DIR := $(EXL_BUILD_DIR)
EXL_DIAGNOSTIC_CFLAGS := -DEXL_DIAGNOSTIC_STAGE=$(DIAGNOSTIC_STAGE)
SALTYNX_PLUGIN_OUTPUT := $(OUT)/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf
SALTYNX_STAGE133_CPPFILES := runtime_entry.cpp saltynx_external_plugin_probe.cpp
SALTYNX_STAGE133_CFILES :=
SALTYNX_STAGE133_SFILES := saltynx_external_plugin_crt0.s
SALTYNX_STAGE134_CPPFILES := runtime_entry.cpp saltynx_external_plugin_read_probe.cpp
SALTYNX_STAGE134_CFILES :=
SALTYNX_STAGE134_SFILES := saltynx_external_plugin_crt0.s
SALTYNX_STAGE135_CPPFILES := runtime_entry.cpp saltynx_external_plugin_write_read_probe.cpp
SALTYNX_STAGE135_CFILES :=
SALTYNX_STAGE135_SFILES := saltynx_external_plugin_crt0.s
SALTYNX_STAGE136_CPPFILES := runtime_entry.cpp saltynx_external_plugin_persist_probe.cpp
SALTYNX_STAGE136_CFILES :=
SALTYNX_STAGE136_SFILES := saltynx_external_plugin_crt0.s
SALTYNX_STAGE137_CPPFILES := runtime_entry.cpp saltynx_external_plugin_state_probe.cpp
SALTYNX_STAGE137_CFILES :=
SALTYNX_STAGE137_SFILES := saltynx_external_plugin_crt0.s
SALTYNX_STAGE138_CPPFILES := runtime_entry.cpp saltynx_external_plugin_state_persist_probe.cpp
SALTYNX_STAGE138_CFILES :=
SALTYNX_STAGE138_SFILES := saltynx_external_plugin_crt0.s
SALTYNX_STAGE139_CPPFILES := runtime_entry.cpp saltynx_external_plugin_bridge_probe.cpp
SALTYNX_STAGE139_CFILES :=
SALTYNX_STAGE139_SFILES := saltynx_external_plugin_crt0.s
SALTYNX_STAGE140_CPPFILES := runtime_entry.cpp saltynx_external_plugin_file_api_probe.cpp
SALTYNX_STAGE140_CFILES :=
SALTYNX_STAGE140_SFILES := saltynx_external_plugin_crt0.s
SALTYNX_STAGE141_CPPFILES := runtime_entry.cpp saltynx_external_plugin_runtime_io_probe.cpp
SALTYNX_STAGE141_CFILES :=
SALTYNX_STAGE141_SFILES := saltynx_external_plugin_crt0.s
SALTYNX_STAGE142_CPPFILES := runtime_entry.cpp saltynx_external_plugin_runtime_state_probe.cpp
SALTYNX_STAGE142_CFILES :=
SALTYNX_STAGE142_SFILES := saltynx_external_plugin_crt0.s
SALTYNX_STAGE143_CPPFILES := runtime_entry.cpp saltynx_external_plugin_runtime_state_persist_probe.cpp
SALTYNX_STAGE143_CFILES :=
SALTYNX_STAGE143_SFILES := saltynx_external_plugin_crt0.s
SALTYNX_STAGE144_CPPFILES := runtime_entry.cpp saltynx_external_plugin_persistence_bridge.cpp
SALTYNX_STAGE144_CFILES :=
SALTYNX_STAGE144_SFILES := saltynx_external_plugin_crt0.s
SALTYNX_STAGE145_CPPFILES := runtime_entry.cpp saltynx_external_plugin_persistence_bridge_trace.cpp
SALTYNX_STAGE145_CFILES :=
SALTYNX_STAGE145_SFILES := saltynx_external_plugin_crt0.s
# Task 6b: the minimal host plugin owns its own source root under `runtime/src`.
# Only its entry unit, the symbol resolver, the Host API client and the plugin
# itself are linked -- the Runtime's own layered sources must stay out of the
# plugin ELF. `HOST_PLUGIN_ROOT` puts that root on the include path for `-I`.
SALTYNX_STAGE146_CPPFILES := plugin_entry.cpp saltynx_symbol_resolver.cpp runtime_host_api_client.cpp saltynx_host_plugin.cpp
SALTYNX_STAGE146_CFILES :=
SALTYNX_STAGE146_SFILES := saltynx_external_plugin_crt0.s
SALTYNX_STAGE146_HOST_PLUGIN_ROOT_NAME := src/host_plugin
ifeq ($(DIAGNOSTIC_STAGE),134)
SALTYNX_STAGE134_READ_INPUT := diagnostic/stage134/isaac-runtime-read.bin
endif
else
ifneq ($(strip $(STARTUP_PROBE_STAGE)),)
STARTUP_PROBE_OUTPUT_SUFFIX :=
ifeq ($(STARTUP_PROBE_STAGE),6)
STARTUP_PROBE_OUTPUT_SUFFIX := -$(STARTUP_PROBE_LAYOUT)
endif
OUT := deploy-startup-probe/stage$(STARTUP_PROBE_STAGE)$(STARTUP_PROBE_OUTPUT_SUFFIX)
EXL_BUILD_DIR := build-startup-probe-stage$(STARTUP_PROBE_STAGE)$(STARTUP_PROBE_OUTPUT_SUFFIX)
EXL_ARTIFACT_DIR := $(EXL_BUILD_DIR)
EXL_DIAGNOSTIC_CFLAGS :=
EXL_STARTUP_PROBE_CFLAGS := -DEXL_STARTUP_PROBE_STAGE=$(STARTUP_PROBE_STAGE)
else ifneq ($(strip $(DIAGNOSTIC_STAGE)),)
ifneq ($(words $(DIAGNOSTIC_STAGE)),1)
$(error DIAGNOSTIC_STAGE must be one of 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 45, 48, 88, 89, 102, 104, 108, 109, 110, 112, 118, 127, 128)
endif
ifeq ($(filter 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 45 48 88 89 102 104 108 109 110 112 118 127 128,$(DIAGNOSTIC_STAGE)),)
$(error DIAGNOSTIC_STAGE must be one of 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 45, 48, 88, 89, 102, 104, 108, 109, 110, 112, 118, 127, 128)
endif
OUT := deploy-diagnostic/stage$(DIAGNOSTIC_STAGE)
EXL_BUILD_DIR := build-diagnostic-stage$(DIAGNOSTIC_STAGE)
EXL_ARTIFACT_DIR := $(EXL_BUILD_DIR)
EXL_DIAGNOSTIC_CFLAGS := -DEXL_DIAGNOSTIC_STAGE=$(DIAGNOSTIC_STAGE)
else
OUT := deploy
EXL_BUILD_DIR := build
EXL_ARTIFACT_DIR := .
EXL_DIAGNOSTIC_CFLAGS :=
endif
endif
TEST_BUILD_MANIFEST := $(OUT)/test-build-manifest.json

# Stage48 needs to keep the exlaunch JIT pages below the 0x50000 text boundary.
# Crossing it moves the JIT mapping and fails during Runtime startup on hardware.
EXL_STAGE48_CXXFLAGS :=
ifneq ($(filter 48,$(DIAGNOSTIC_STAGE)),)
EXL_STAGE48_CXXFLAGS := -Os
endif

EXL_SALTYNX_DIAGNOSTIC_BRIDGE_CFLAGS :=
ifeq ($(SALTYNX_DIAGNOSTIC_BRIDGE),1)
EXL_SALTYNX_DIAGNOSTIC_BRIDGE_CFLAGS := -DEXL_ENABLE_SALTYNX_DIAGNOSTICS
endif
EXL_PERSISTENCE_TRACE_CFLAGS :=
ifeq ($(PERSISTENCE_TRACE),1)
EXL_PERSISTENCE_TRACE_CFLAGS := -DEXL_PERSISTENCE_TRACE
endif
EXL_PERSISTENCE_EVENT_DIAGNOSTIC_CFLAGS :=
ifeq ($(PERSISTENCE_EVENT_DIAGNOSTIC),1)
EXL_PERSISTENCE_EVENT_DIAGNOSTIC_CFLAGS := -DEXL_PERSISTENCE_EVENT_DIAGNOSTIC
endif
EXL_PERSISTENCE_REQUIRED_CFLAGS :=
ifeq ($(PERSISTENCE_REQUIRED),1)
EXL_PERSISTENCE_REQUIRED_CFLAGS := -DEXL_PERSISTENCE_REQUIRED
endif
EXL_LAYERED_RUNTIME_CFLAGS :=
ifeq ($(LAYERED_RUNTIME),1)
EXL_LAYERED_RUNTIME_CFLAGS := -DEXL_LAYERED_RUNTIME=1
endif
EXL_TEST_BUILD_ID_CFLAGS := -DEXL_TEST_BUILD_ID=$(TEST_BUILD_ID)
# Probe break: compile the deliberate `svcBreak` evidence channel in. Off by default,
# because reaching the probe point then ends the session on purpose. See AGENTS.md
# 「探针证据规范」: a "did execution get here" probe must also be able to raise a
# visible error, so a crash report (module base, offsets, registers) carries the
# evidence when file writes do not.
PROBE_BREAK ?= 0
# Worker 启动延迟（秒）—— 实验开关，2026-09-16 加。
#
# 作用：让 `ModuleWorker` 一进来先等 N 秒，把"我们自己的启动期动作"（扫内存找游戏模块、装挂点、
# 自校验、读模组文件）挪到"系统装载游戏本体模块"那个窗口之后。**入口不动**（入口被游戏同步等待，
# 在那儿等等于直接给开机加秒数 —— 2026-09-15 的 E1 实验就是栽在这里：延迟 8 秒版就"约 8 秒"崩、
# 栈一模一样）。背景、实测秒数与本方案的不确定性见 `docs/问题与解决记录.md` 2026-09-16 续十六。
#
# 为什么是开关而不是写死的秒数：门禁 `test_worker_does_not_depend_on_fixed_sdk_initialization_delay`
# （`runtime/tests/test_runtime_state.py`）明令禁止 worker 依赖一个固定的初始化延迟
# —— 那条结论是 2026-08-23 用"改用独立 fs 服务"换来的。所以默认 **0**（生产形状不变、门禁不变），
# 真机实验包显式传 `WORKER_STARTUP_DELAY_SECONDS=5`。
WORKER_STARTUP_DELAY_SECONDS ?= 0
ifeq ($(shell expr "$(WORKER_STARTUP_DELAY_SECONDS)" : '^[0-9][0-9]*$$'),0)
$(error WORKER_STARTUP_DELAY_SECONDS must be a non-negative integer (seconds))
endif
EXL_WORKER_STARTUP_DELAY_CFLAGS := -DEXL_WORKER_STARTUP_DELAY_SECONDS=$(WORKER_STARTUP_DELAY_SECONDS)
# 二分开关（2026-09-16）：`ONLY_REQUIRED_HOOK=1` 时这一版**只装必需挂点**
# （登记表里 `required` 的那些，当前只有 `ManagerUpdate`），五个可选挂点一个都不装。
#
# 用来回答"模组全关时主界面停 10 秒崩 / 退出对局必崩"是不是**可选挂点**引起的：
# 上一版（卡上开关）本来想用卡上文件做这件事，但**装挂点跑在我们自己的 worker 线程上**，
# 在那条路上读卡会调游戏 nnSdk 的文件 API ⇒ 每次开机必崩（442190，两份崩溃报告）。
# 因此挂点层面的二分改为编译期开关：一条构建一个配置。默认 **0**（生产形状不变）。
ONLY_REQUIRED_HOOK ?= 0
ifneq ($(filter 0 1,$(ONLY_REQUIRED_HOOK)),$(ONLY_REQUIRED_HOOK))
$(error ONLY_REQUIRED_HOOK must be 0 or 1)
endif
EXL_ONLY_REQUIRED_HOOK_CFLAGS := -DEXL_ONLY_REQUIRED_HOOK=$(ONLY_REQUIRED_HOOK)
# 探针子开关（2026-09-13，用于二分定位探针包的启动故障）。`PROBE_BREAK=1` 时默认全开。
#
# `PROBE_BREAK_FILEIO=0` 关掉"启动路径上的文件 / IPC 写入探针"这一组：
#   * `runtime_entry.cpp` 里 `exl_main` 入口的自日志写入；
#   * `hook_manager.cpp` 渲染钩子里的自日志写入；
#   * 四个"谁能写文件"探针（ProbeFileWriteFromHook / ProbeNativeFileWrite /
#     ProbeSaltyIpc / ProbeSaltyReinit）。
# 它们是探针包里唯一在**启动阶段**就会走已知失效路由的部分 —— 代码注释自称
# "2026-09-11: none of the Runtime-side ones does [work]"。内容挂载点探针不在此列：
# 它需要 Mod 自己画够 `kContentMountProbeAfterDraws` 帧才触发，不可能在启动画面跑到。
PROBE_BREAK_FILEIO ?= 1
ifneq ($(filter 0 1,$(PROBE_BREAK_FILEIO)),$(PROBE_BREAK_FILEIO))
$(error PROBE_BREAK_FILEIO must be 0 or 1)
endif
# `PROBE_BREAK_CONTENT_MOUNT=0` 关掉 `runtime/source/probe/content_mount_point_probe.{cpp,hpp}` 整个 TU。
#
# 它**不只是**"内容挂载点探针"：`IsaacModRuntime_ProbeCallbackError()`（`:1421`）与
# `IsaacModRuntime_ProbeEnginePlayers()` 也定义在这个 TU 里，而更新钩子**每帧**都调这两个
# （见 `hook_manager.cpp` 的 `ManagerUpdateHook`），不受"600 帧绘制"条件约束 ——
# 也就是说它在**加载阶段就会执行**。
#
# `ProbeCallbackError` 会 `svcBreak` 结束会话：只要加载期出现过一次回调 Lua 错误
# （`LuaRuntime::CallbackErrorPending()`，每帧重置、不粘），它就会在加载画面把会话关掉。
# 纯修复包里同样的错误只是被静默记录、不影响运行 —— 这正是"探针包进不去、纯修复包能进"的
# 最可能解释（2026-09-13 第一轮二分排除了文件/IPC 探针组之后）。
PROBE_BREAK_CONTENT_MOUNT ?= 1
ifneq ($(filter 0 1,$(PROBE_BREAK_CONTENT_MOUNT)),$(PROBE_BREAK_CONTENT_MOUNT))
$(error PROBE_BREAK_CONTENT_MOUNT must be 0 or 1)
endif
# 子开关恒定义（取 0/1），避免 `-Wundef`；`PROBE_BREAK=0` 时主宏未定义，子开关自然不起作用。
ifeq ($(PROBE_BREAK),1)
EXL_PROBE_BREAK_CFLAGS := -DEXL_PROBE_BREAK=1 \
  -DEXL_PROBE_BREAK_FILEIO=$(PROBE_BREAK_FILEIO) \
  -DEXL_PROBE_BREAK_CONTENT_MOUNT=$(PROBE_BREAK_CONTENT_MOUNT)
else
EXL_PROBE_BREAK_CFLAGS := -DEXL_PROBE_BREAK_FILEIO=0 -DEXL_PROBE_BREAK_CONTENT_MOUNT=0
endif
EXL_STARTUP_PROBE_CFLAGS ?=

SD_OUT := atmosphere/contents/$(PROGRAM_ID)/exefs
DEPLOY_BINARY = $(OUT)/$(SD_OUT)/$(BINARY_NAME)
DEPLOY_NPDM := $(OUT)/atmosphere/contents/$(PROGRAM_ID)/exefs/main.npdm
RELAY_PATCH_DIR := $(OUT)/atmosphere/nro_patches/isaac-repentance-manager-update-relay
DEPLOY_RELAY_IPS := $(RELAY_PATCH_DIR)/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips
RENDER_RELAY_PATCH_DIR := $(OUT)/atmosphere/nro_patches/isaac-repentance-manager-render-relay
DEPLOY_RENDER_RELAY_IPS := $(RENDER_RELAY_PATCH_DIR)/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips
GAME_OBSERVER_RELAY_PATCH_DIR := $(OUT)/atmosphere/nro_patches/isaac-repentance-game-observer-relay
DEPLOY_GAME_OBSERVER_RELAY_IPS := $(GAME_OBSERVER_RELAY_PATCH_DIR)/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips
GAME_UPDATE_OBSERVER_RELAY_PATCH_DIR := $(OUT)/atmosphere/nro_patches/isaac-repentance-game-update-observer-relay
DEPLOY_GAME_UPDATE_OBSERVER_RELAY_IPS := $(GAME_UPDATE_OBSERVER_RELAY_PATCH_DIR)/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips
GAME_STATE2_OBSERVER_RELAY_PATCH_DIR := $(OUT)/atmosphere/nro_patches/isaac-repentance-game-state2-observer-relay
DEPLOY_GAME_STATE2_OBSERVER_RELAY_IPS := $(GAME_STATE2_OBSERVER_RELAY_PATCH_DIR)/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips
GAME_ISPAUSED_RENDER_OBSERVER_RELAY_PATCH_DIR := $(OUT)/atmosphere/nro_patches/isaac-repentance-game-ispaused-render-observer-relay
DEPLOY_GAME_ISPAUSED_RENDER_OBSERVER_RELAY_IPS := $(GAME_ISPAUSED_RENDER_OBSERVER_RELAY_PATCH_DIR)/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips
STAGE48_MUSIC_REPLAY_RELAY_PATCH_DIR := $(OUT)/atmosphere/nro_patches/isaac-repentance-stage48-music-replay-relay
DEPLOY_STAGE48_MUSIC_REPLAY_RELAY_IPS := $(STAGE48_MUSIC_REPLAY_RELAY_PATCH_DIR)/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips
REBUILD_MOUNT_POINTS_RELAY_PATCH_DIR := $(OUT)/atmosphere/nro_patches/isaac-repentance-rebuild-mount-points-relay
DEPLOY_REBUILD_MOUNT_POINTS_RELAY_IPS := $(REBUILD_MOUNT_POINTS_RELAY_PATCH_DIR)/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips
PRE_GET_COLLECTIBLE_RELAY_PATCH_DIR := $(OUT)/atmosphere/nro_patches/isaac-repentance-pre-get-collectible-relay
DEPLOY_PRE_GET_COLLECTIBLE_RELAY_IPS := $(PRE_GET_COLLECTIBLE_RELAY_PATCH_DIR)/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips
CHANGE_ROOM_RELAY_PATCH_DIR := $(OUT)/atmosphere/nro_patches/isaac-repentance-change-room-relay
DEPLOY_CHANGE_ROOM_RELAY_IPS := $(CHANGE_ROOM_RELAY_PATCH_DIR)/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips
LIFECYCLE_RELAY_PATCH_DIR := $(OUT)/atmosphere/nro_patches/isaac-repentance-lifecycle-relay
DEPLOY_LIFECYCLE_RELAY_IPS := $(LIFECYCLE_RELAY_PATCH_DIR)/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips
RESTART_RELAY_PATCH_DIR := $(OUT)/atmosphere/nro_patches/isaac-repentance-restart-relay
DEPLOY_RESTART_RELAY_IPS := $(RESTART_RELAY_PATCH_DIR)/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips
SAVE_LOAD_RELAY_PATCH_DIR := $(OUT)/atmosphere/nro_patches/isaac-repentance-save-load-relay
DEPLOY_SAVE_LOAD_RELAY_IPS := $(SAVE_LOAD_RELAY_PATCH_DIR)/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips
RENDER_PRESENT_RELAY_PATCH_DIR := $(OUT)/atmosphere/nro_patches/isaac-repentance-render-present-relay
DEPLOY_RENDER_PRESENT_RELAY_IPS := $(RENDER_PRESENT_RELAY_PATCH_DIR)/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips
LOAD_CONFIGS_RELAY_PATCH_DIR := $(OUT)/atmosphere/nro_patches/isaac-repentance-manager-loadconfigs-relay
DEPLOY_LOAD_CONFIGS_RELAY_IPS := $(LOAD_CONFIGS_RELAY_PATCH_DIR)/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips
ROMFS_SENTINEL_SOURCE :=
DEPLOY_ROMFS_SENTINEL := $(OUT)/atmosphere/contents/010021C000B6A000/romfs/isaac_mod_probe.lua
ROMFS_LUA_PROBE_SOURCE :=
DEPLOY_ROMFS_LUA_PROBE := $(OUT)/atmosphere/contents/010021C000B6A000/romfs/runtime_probe.lua
ifneq ($(filter 10,$(DIAGNOSTIC_STAGE)),)
ROMFS_SENTINEL_SOURCE := diagnostic/stage10/isaac_mod_probe.lua
endif
ifneq ($(filter 11,$(DIAGNOSTIC_STAGE)),)
ROMFS_SENTINEL_SOURCE := diagnostic/stage11/isaac_mod_probe.lua
endif
ifneq ($(filter 12,$(DIAGNOSTIC_STAGE)),)
ROMFS_LUA_PROBE_SOURCE := diagnostic/stage12/runtime_probe.lua
endif
PYTHON ?= python3

# ---------------------------------------------------------------------------
# Explicit build entry points.
#
# The production Runtime, the SaltyNX host plugin and the hardware probes are
# three different artifacts built from disjoint source sets. Each entry point
# below pins the variables that decide which sources are compiled, so a build
# can never silently pick up the wrong set:
#
#   make runtime_module                    -> <OUT>/atmosphere/contents/<Title>/exefs/subsdk9
#   make host_plugin HOST_PLUGIN_STAGE=145 -> <OUT>/SaltySD/plugins/<Title>/isaac-runtime.elf
#   make probe DIAGNOSTIC_STAGE=13         -> deploy-diagnostic/stage13/atmosphere/...
#   make probe STARTUP_PROBE_STAGE=5       -> deploy-startup-probe/stage5/atmosphere/...
#
# `probe` refuses to build without an explicit stage so a diagnostic package
# can never be produced by accident from a default invocation.
# ---------------------------------------------------------------------------
# Keep the historical default goal: `make -C runtime` (with or without stage
# variables) must still build `all`, not one of the entry-point dispatchers
# declared above it.
.DEFAULT_GOAL := all

RUNTIME_SOURCE_ROOT ?= source
# Additional production source roots compiled into the layered Runtime.
RUNTIME_EXTRA_SOURCE_ROOTS ?= src
ifneq ($(strip $(DIAGNOSTIC_STAGE))$(strip $(STARTUP_PROBE_STAGE)),)
# Hardware probes only need the callback registry and Lua API family units.
# Limiting the roots avoids compiling production-only adapters under diagnostic
# macros that intentionally hide some of their dependencies.
RUNTIME_EXTRA_SOURCE_ROOTS := src/application/callback src/interfaces/lua
endif
HOST_PLUGIN_STAGE ?= $(DIAGNOSTIC_STAGE)

.PHONY: runtime_module host_plugin probe

runtime_module:
	@env -u MAKELEVEL $(MAKE) --no-print-directory \
	  LAYERED_RUNTIME=1 RUNTIME_EXTRA_SOURCE_ROOTS=src \
	  DIAGNOSTIC_STAGE= STARTUP_PROBE_STAGE= STARTUP_PROBE_LAYOUT= \
	  SALTYNX_PLUGIN=0 SALTYNX_DIAGNOSTIC_BRIDGE=0 \
	  PERSISTENCE_TRACE=0 PERSISTENCE_EVENT_DIAGNOSTIC=0 PERSISTENCE_REQUIRED=0 \
	  all

host_plugin:
	@if [ -z "$(strip $(HOST_PLUGIN_STAGE))" ]; then \
	  echo "host_plugin: set HOST_PLUGIN_STAGE to the SaltyNX plugin stage to build" >&2; \
	  exit 2; \
	fi
	@env -u MAKELEVEL $(MAKE) --no-print-directory \
	  STARTUP_PROBE_STAGE= DIAGNOSTIC_STAGE=$(HOST_PLUGIN_STAGE) SALTYNX_PLUGIN=1 \
	  all

probe:
	@if [ -z "$(strip $(STARTUP_PROBE_STAGE))" ] && [ -z "$(strip $(DIAGNOSTIC_STAGE))" ]; then \
	  echo "probe: select a probe explicitly, for example:" >&2; \
	  echo "  make probe STARTUP_PROBE_STAGE=5" >&2; \
	  echo "  make probe DIAGNOSTIC_STAGE=13" >&2; \
	  exit 2; \
	fi
	@env -u MAKELEVEL $(MAKE) --no-print-directory \
	  LAYERED_RUNTIME=1 SALTYNX_PLUGIN=0 SALTYNX_DIAGNOSTIC_BRIDGE=0 all

# Read-only input from the user-provided Switch dump. Override with
# `make ORIGINAL_NPDM=/path/to/main.npdm` when the dump is elsewhere.
empty :=
space := $(empty) $(empty)
POUND := $(shell printf '\043')
ORIGINAL_NPDM ?= ../The$(space)Binding$(space)of$(space)Isaac_$(space)Afterbirth+$(space)1.7.9b$(space)[010021C000B6A800][v524288][UPD]/Program$(space)$(POUND)0/0/main.npdm
ORIGINAL_NRO ?= ../The$(space)Binding$(space)of$(space)Isaac_$(space)Afterbirth+$(space)1.7.9b$(space)[010021C000B6A800][v524288][UPD]/Program$(space)$(POUND)0/1/.nro/Repentance.nro


# Set load kind specific variables.
ifeq ($(LOAD_KIND), Module)
    LOAD_KIND_ENUM := 2
    # TODO: support subsdkX?
    BINARY_NAME := subsdk9
    SPECS_NAME := module.specs
    MK_NAME := module.mk
else ifeq ($(LOAD_KIND), AsRtld)
    LOAD_KIND_ENUM := 1
    BINARY_NAME := rtld
    SPECS_NAME := as_rtld.specs
    MK_NAME := as_rtld.mk
else
    $(error LOAD_KIND is invalid, please check config.mk)
endif
ifeq ($(SALTYNX_PLUGIN),1)
SPECS_NAME := saltynx_plugin.specs
endif
ifeq ($(STARTUP_PROBE_STAGE),6)
ifeq ($(STARTUP_PROBE_LAYOUT),compat)
SPECS_NAME := module_startup_probe_compat.specs
endif
endif

.PHONY: clean all FORCE

# Built internal C flags variable.
EXL_CFLAGS   := $(C_FLAGS) -DEXL_LOAD_KIND=$(LOAD_KIND) -DEXL_LOAD_KIND_ENUM=$(LOAD_KIND_ENUM) -DEXL_PROGRAM_ID=0x$(PROGRAM_ID) $(EXL_DIAGNOSTIC_CFLAGS) $(EXL_STARTUP_PROBE_CFLAGS) $(EXL_SALTYNX_DIAGNOSTIC_BRIDGE_CFLAGS) $(EXL_PERSISTENCE_TRACE_CFLAGS) $(EXL_PERSISTENCE_EVENT_DIAGNOSTIC_CFLAGS) $(EXL_PERSISTENCE_REQUIRED_CFLAGS) $(EXL_LAYERED_RUNTIME_CFLAGS) $(EXL_TEST_BUILD_ID_CFLAGS) $(EXL_PROBE_BREAK_CFLAGS) $(EXL_WORKER_STARTUP_DELAY_CFLAGS) $(EXL_ONLY_REQUIRED_HOOK_CFLAGS)
EXL_CXXFLAGS := $(CXX_FLAGS) $(EXL_STAGE48_CXXFLAGS)

# Export all of our variables to sub-makes and sub-processes.
export

include $(MK_PATH)/$(MK_NAME)
include $(MK_PATH)/common.mk
include $(MK_PATH)/deploy.mk
include $(MK_PATH)/npdm.mk

# The module build creates the deployment directory; patch the NPDM only after
# that step so the final tree contains exactly the runtime and its overlay.
# Module deployments use exefs/subsdk9 alongside exefs/main.npdm.
.PHONY: all
ifeq ($(SALTYNX_PLUGIN),1)
all: $(BUILD)
else
all: $(DEPLOY_NPDM)
ifneq ($(filter 102 104 108 109 110 112 127 128,$(DIAGNOSTIC_STAGE)),)
else
all: $(DEPLOY_RELAY_IPS)
endif
endif

ifneq ($(filter 13,$(DIAGNOSTIC_STAGE)),)
.PHONY: stage13-pc-mods
stage13-pc-mods: $(BUILD)
	@mkdir -p "$(OUT)"
	@cd .. && $(PYTHON) -m tools.inspect_pc_mod \
	  --mods-root runtime/diagnostic/stage13/pc-mods \
	  --romfs-output runtime/$(OUT)

$(DEPLOY_NPDM) $(DEPLOY_RELAY_IPS): stage13-pc-mods
endif

ifeq ($(strip $(DIAGNOSTIC_STAGE)),)
# 这四个中继 IPS 的产物落在各自的 `atmosphere/nro_patches/...` 目录里，不进 romfs 清单
# （`tools/inspect_pc_mod.py` 完全不引用 relay/IPS），所以它们与 `default-pc-mods` 之间
# **没有语义依赖**。
#
# 但它们**必须排在 `$(BUILD)` 之后**：`$(BUILD)` 的收尾会执行
# `misc/scripts/post-build.sh`，而该脚本第一件事就是 `rm -rf "${OUT}"`（清空整个部署树，
# 只放回 runtime.nso）。中继如果跑在它之前，产物会被直接删掉。
#
# 历史上这四个目标挂在 `default-pc-mods` 上，靠 `default-pc-mods: $(BUILD)` 间接获得了
# 这个顺序 —— 于是 `SKIP_DEFAULT_PC_MODS=1` 在砍掉 romfs 的同时，也砍掉了它们的顺序屏障，
# 四个产物全部丢失。而项目把"五个中继"写成硬要求（缺 Render 中继时 `MC_POST_RENDER`
# 根本不派发；缺 `rebuild-mount-points` 时 Mod 资源挂载链不完整），所以缺了它们产物就不是
# 可安装的包 —— 2026-09-13 的探针包就是这么坏掉的。
#
# 2026-09-13 修法：把顺序依赖直接写成 `$(BUILD)`（与 `$(DEPLOY_RELAY_IPS)` 一致），
# romfs 生成则继续单独受 `SKIP_DEFAULT_PC_MODS` 控制。
.PHONY: default-pc-mods
ifeq ($(SKIP_DEFAULT_PC_MODS),0)
default-pc-mods: $(BUILD)
	@mkdir -p "$(OUT)"
	@cd .. && $(PYTHON) -m tools.inspect_pc_mod \
	  --mods-root runtime/pc-mods \
	  --romfs-output runtime/$(OUT)

$(DEPLOY_NPDM) $(DEPLOY_RELAY_IPS): default-pc-mods
endif

all: $(DEPLOY_RENDER_RELAY_IPS) $(DEPLOY_RENDER_PRESENT_RELAY_IPS) $(DEPLOY_PRE_GET_COLLECTIBLE_RELAY_IPS) $(DEPLOY_REBUILD_MOUNT_POINTS_RELAY_IPS)
endif

ifneq ($(filter 8 9,$(DIAGNOSTIC_STAGE)),)
all: $(DEPLOY_LOAD_CONFIGS_RELAY_IPS)
endif

ifneq ($(filter 15,$(DIAGNOSTIC_STAGE)),)
all: $(DEPLOY_RENDER_RELAY_IPS)
endif

ifneq ($(filter 16,$(DIAGNOSTIC_STAGE)),)
all: $(DEPLOY_RENDER_RELAY_IPS)
endif

ifneq ($(filter 17,$(DIAGNOSTIC_STAGE)),)
all: $(DEPLOY_RENDER_RELAY_IPS)
endif

ifneq ($(filter 45,$(DIAGNOSTIC_STAGE)),)
all: $(DEPLOY_RENDER_RELAY_IPS)
endif

ifneq ($(filter 48,$(DIAGNOSTIC_STAGE)),)
all: $(DEPLOY_RENDER_RELAY_IPS)
all: $(DEPLOY_STAGE48_MUSIC_REPLAY_RELAY_IPS)
endif

ifneq ($(filter 102 104 110 112,$(DIAGNOSTIC_STAGE)),)
all: $(DEPLOY_CHANGE_ROOM_RELAY_IPS)
endif

# 游戏开局（`Game::Start` / `Game::StartFromSavedState` 的后置边界）是**常驻能力**：
# 模组的 `MC_POST_GAME_STARTED` 依赖它。它原先只在诊断阶段 108/110 下构建，
# 2026-09-14 提升为正式挂点（`HookId::GameStart`，后端 `RelaySlot`），因此无条件进产物。
all: $(DEPLOY_LIFECYCLE_RELAY_IPS)

ifneq ($(filter 109,$(DIAGNOSTIC_STAGE)),)
all: $(DEPLOY_RESTART_RELAY_IPS)
endif

ifneq ($(filter 127 128,$(DIAGNOSTIC_STAGE)),)
all: $(DEPLOY_SAVE_LOAD_RELAY_IPS)
endif

ifneq ($(strip $(ROMFS_SENTINEL_SOURCE)),)
all: $(DEPLOY_ROMFS_SENTINEL)
endif

ifneq ($(strip $(ROMFS_LUA_PROBE_SOURCE)),)
all: $(DEPLOY_ROMFS_LUA_PROBE)
endif

$(DEPLOY_NPDM): $(BUILD) tools/patch_npdm.py
	@$(PYTHON) tools/patch_npdm.py --input "$(ORIGINAL_NPDM)" --output "$@"

$(DEPLOY_RELAY_IPS): $(BUILD)
$(DEPLOY_RELAY_IPS): FORCE
	@mkdir -p "$(RELAY_PATCH_DIR)"
	@$(PYTHON) ../tools/build_patches.py --kind relay --nro "$(ORIGINAL_NRO)" --output "$@"

$(DEPLOY_RENDER_PRESENT_RELAY_IPS): $(BUILD) FORCE
	@mkdir -p "$(RENDER_PRESENT_RELAY_PATCH_DIR)"
	@$(PYTHON) ../tools/build_patches.py --kind render-present-relay --nro "$(ORIGINAL_NRO)" --output "$@"

$(DEPLOY_RENDER_RELAY_IPS): $(BUILD) FORCE
	@mkdir -p "$(RENDER_RELAY_PATCH_DIR)"
	@$(PYTHON) ../tools/build_patches.py --kind render-relay --nro "$(ORIGINAL_NRO)" --output "$@"

$(DEPLOY_GAME_OBSERVER_RELAY_IPS): FORCE
	@mkdir -p "$(GAME_OBSERVER_RELAY_PATCH_DIR)"
	@$(PYTHON) ../tools/build_patches.py --kind game-observer-relay --nro "$(ORIGINAL_NRO)" --output "$@"

$(DEPLOY_GAME_UPDATE_OBSERVER_RELAY_IPS): FORCE
	@mkdir -p "$(GAME_UPDATE_OBSERVER_RELAY_PATCH_DIR)"
	@$(PYTHON) ../tools/build_patches.py --kind game-update-observer-relay --nro "$(ORIGINAL_NRO)" --output "$@"

$(DEPLOY_GAME_STATE2_OBSERVER_RELAY_IPS): FORCE
	@mkdir -p "$(GAME_STATE2_OBSERVER_RELAY_PATCH_DIR)"
	@$(PYTHON) ../tools/build_patches.py --kind game-state2-observer-relay --nro "$(ORIGINAL_NRO)" --output "$@"

$(DEPLOY_GAME_ISPAUSED_RENDER_OBSERVER_RELAY_IPS): FORCE
	@mkdir -p "$(GAME_ISPAUSED_RENDER_OBSERVER_RELAY_PATCH_DIR)"
	@$(PYTHON) ../tools/build_patches.py --kind game-ispaused-render-observer-relay --nro "$(ORIGINAL_NRO)" --output "$@"

$(DEPLOY_STAGE48_MUSIC_REPLAY_RELAY_IPS): FORCE
	@mkdir -p "$(STAGE48_MUSIC_REPLAY_RELAY_PATCH_DIR)"
	@$(PYTHON) ../tools/build_patches.py --kind stage48-music-replay-relay --nro "$(ORIGINAL_NRO)" --output "$@"

$(DEPLOY_PRE_GET_COLLECTIBLE_RELAY_IPS): $(BUILD) FORCE
	@mkdir -p "$(PRE_GET_COLLECTIBLE_RELAY_PATCH_DIR)"
	@$(PYTHON) ../tools/build_patches.py --kind pre-get-collectible-relay --nro "$(ORIGINAL_NRO)" --output "$@"

$(DEPLOY_REBUILD_MOUNT_POINTS_RELAY_IPS): $(BUILD) FORCE
	@mkdir -p "$(REBUILD_MOUNT_POINTS_RELAY_PATCH_DIR)"
	@$(PYTHON) ../tools/build_patches.py --kind rebuild-mount-points-relay --nro "$(ORIGINAL_NRO)" --output "$@"

$(DEPLOY_CHANGE_ROOM_RELAY_IPS): $(BUILD) FORCE
	@mkdir -p "$(CHANGE_ROOM_RELAY_PATCH_DIR)"
	@$(PYTHON) ../tools/build_patches.py --kind change-room-relay --nro "$(ORIGINAL_NRO)" --output "$@"

$(DEPLOY_LIFECYCLE_RELAY_IPS): $(BUILD) FORCE
	@mkdir -p "$(LIFECYCLE_RELAY_PATCH_DIR)"
	@$(PYTHON) ../tools/build_patches.py --kind lifecycle-relay --nro "$(ORIGINAL_NRO)" --output "$@"

$(DEPLOY_RESTART_RELAY_IPS): $(BUILD) FORCE
	@mkdir -p "$(RESTART_RELAY_PATCH_DIR)"
	@$(PYTHON) ../tools/build_patches.py --kind restart-relay --nro "$(ORIGINAL_NRO)" --output "$@"

$(DEPLOY_SAVE_LOAD_RELAY_IPS): $(BUILD) FORCE
	@mkdir -p "$(SAVE_LOAD_RELAY_PATCH_DIR)"
	@if [ "$(DIAGNOSTIC_STAGE)" = "128" ]; then \
	  $(PYTHON) ../tools/stage128_save_data_manager_observation_relay.py "$(ORIGINAL_NRO)" "$@"; \
	else \
	  $(PYTHON) ../tools/stage127_save_load_observation_relay.py "$(ORIGINAL_NRO)" "$@"; \
	fi

$(DEPLOY_LOAD_CONFIGS_RELAY_IPS): FORCE
	@mkdir -p "$(LOAD_CONFIGS_RELAY_PATCH_DIR)"
	@$(PYTHON) ../tools/build_patches.py --kind loadconfigs-relay --nro "$(ORIGINAL_NRO)" --output "$@"

$(DEPLOY_ROMFS_SENTINEL): $(ROMFS_SENTINEL_SOURCE)
	@mkdir -p "$(dir $@)"
	@cmp -s "$<" "$@" 2>/dev/null || cp "$<" "$@"

$(DEPLOY_ROMFS_LUA_PROBE): $(ROMFS_LUA_PROBE_SOURCE)
	@mkdir -p "$(dir $@)"
	@cmp -s "$<" "$@" 2>/dev/null || cp "$<" "$@"

FORCE:

.PHONY: clean-runtime-output
clean: clean-runtime-output

clean-runtime-output:
	@rm -rf "$(OUT)"

# Reproduce the current Stage145 Runtime code shape, but stop at a single
# startup boundary before any thread, Hook, Lua, or SD-file work can begin.
.PHONY: startup-probe-deploy
startup-probe-deploy:
	@if [ -z "$(STARTUP_PROBE_STAGE)" ]; then \
	  echo "STARTUP_PROBE_STAGE must be one of 1, 2, 3, 4, 5, 6, 7, 8"; exit 2; \
	fi
	@env -u MAKELEVEL $(MAKE) clean STARTUP_PROBE_STAGE=$(STARTUP_PROBE_STAGE) STARTUP_PROBE_LAYOUT=$(STARTUP_PROBE_LAYOUT)
	@env -u MAKELEVEL $(MAKE) SKIP_DEFAULT_PC_MODS=1 PERSISTENCE_TRACE=1 PERSISTENCE_REQUIRED=1 STARTUP_PROBE_STAGE=$(STARTUP_PROBE_STAGE) STARTUP_PROBE_LAYOUT=$(STARTUP_PROBE_LAYOUT) TEST_BUILD_ID=$(TEST_BUILD_ID)
	@set -e; if [ "$(STARTUP_PROBE_STAGE)" = "8" ]; then \
	  env -u MAKELEVEL $(MAKE) clean STARTUP_PROBE_STAGE= STARTUP_PROBE_LAYOUT= DIAGNOSTIC_STAGE=145 SALTYNX_PLUGIN=1 TEST_BUILD_ID=$(TEST_BUILD_ID); \
	  env -u MAKELEVEL $(MAKE) STARTUP_PROBE_STAGE= STARTUP_PROBE_LAYOUT= DIAGNOSTIC_STAGE=145 SALTYNX_PLUGIN=1 TEST_BUILD_ID=$(TEST_BUILD_ID); \
	  mkdir -p "$(OUT)/SaltySD/plugins/$(PROGRAM_ID)"; \
	  cp "deploy-saltynx/stage145/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf" "$(OUT)/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf"; \
	  $(PYTHON) ../tools/write_test_package_manifest.py --root "$(OUT)" --build-id "$(TEST_BUILD_ID)" --stage "$(STARTUP_PROBE_STAGE)"; \
	fi

# Stage139 needs both independently built modules in one SD-root-shaped tree.
# The external plugin cannot verify a Runtime export without the normal subsdk9.
STAGE139_COMBINED_OUT := deploy-saltynx/stage139-combined
.PHONY: stage139-combined-deploy
stage139-combined-deploy:
	@env -u MAKELEVEL $(MAKE) clean
	@env -u MAKELEVEL $(MAKE) SALTYNX_DIAGNOSTIC_BRIDGE=1 SKIP_DEFAULT_PC_MODS=1
	@env -u MAKELEVEL $(MAKE) clean DIAGNOSTIC_STAGE=139 SALTYNX_PLUGIN=1
	@env -u MAKELEVEL $(MAKE) DIAGNOSTIC_STAGE=139 SALTYNX_PLUGIN=1
	@rm -rf "$(STAGE139_COMBINED_OUT)"
	@mkdir -p "$(STAGE139_COMBINED_OUT)"
	@cp -R deploy/atmosphere "$(STAGE139_COMBINED_OUT)/"
	@mkdir -p "$(STAGE139_COMBINED_OUT)/SaltySD/plugins/$(PROGRAM_ID)"
	@cp deploy-saltynx/stage139/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf \
	  "$(STAGE139_COMBINED_OUT)/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf"

STAGE140_COMBINED_OUT := deploy-saltynx/stage140-combined
.PHONY: stage140-combined-deploy
stage140-combined-deploy:
	@env -u MAKELEVEL $(MAKE) clean
	@env -u MAKELEVEL $(MAKE) SALTYNX_DIAGNOSTIC_BRIDGE=1 SKIP_DEFAULT_PC_MODS=1
	@env -u MAKELEVEL $(MAKE) clean DIAGNOSTIC_STAGE=140 SALTYNX_PLUGIN=1
	@env -u MAKELEVEL $(MAKE) DIAGNOSTIC_STAGE=140 SALTYNX_PLUGIN=1
	@rm -rf "$(STAGE140_COMBINED_OUT)"
	@mkdir -p "$(STAGE140_COMBINED_OUT)"
	@cp -R deploy/atmosphere "$(STAGE140_COMBINED_OUT)/"
	@mkdir -p "$(STAGE140_COMBINED_OUT)/SaltySD/plugins/$(PROGRAM_ID)"
	@cp deploy-saltynx/stage140/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf \
	  "$(STAGE140_COMBINED_OUT)/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf"

STAGE141_COMBINED_OUT := deploy-saltynx/stage141-combined
.PHONY: stage141-combined-deploy
stage141-combined-deploy:
	@env -u MAKELEVEL $(MAKE) clean
	@env -u MAKELEVEL $(MAKE) SALTYNX_DIAGNOSTIC_BRIDGE=1 SKIP_DEFAULT_PC_MODS=1
	@env -u MAKELEVEL $(MAKE) clean DIAGNOSTIC_STAGE=141 SALTYNX_PLUGIN=1
	@env -u MAKELEVEL $(MAKE) DIAGNOSTIC_STAGE=141 SALTYNX_PLUGIN=1
	@rm -rf "$(STAGE141_COMBINED_OUT)"
	@mkdir -p "$(STAGE141_COMBINED_OUT)"
	@cp -R deploy/atmosphere "$(STAGE141_COMBINED_OUT)/"
	@mkdir -p "$(STAGE141_COMBINED_OUT)/SaltySD/plugins/$(PROGRAM_ID)"
	@cp deploy-saltynx/stage141/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf \
	  "$(STAGE141_COMBINED_OUT)/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf"

STAGE142_COMBINED_OUT := deploy-saltynx/stage142-combined
.PHONY: stage142-combined-deploy
stage142-combined-deploy:
	@env -u MAKELEVEL $(MAKE) clean
	@env -u MAKELEVEL $(MAKE) SALTYNX_DIAGNOSTIC_BRIDGE=1 SKIP_DEFAULT_PC_MODS=1
	@env -u MAKELEVEL $(MAKE) clean DIAGNOSTIC_STAGE=142 SALTYNX_PLUGIN=1
	@env -u MAKELEVEL $(MAKE) DIAGNOSTIC_STAGE=142 SALTYNX_PLUGIN=1
	@rm -rf "$(STAGE142_COMBINED_OUT)"
	@mkdir -p "$(STAGE142_COMBINED_OUT)"
	@cp -R deploy/atmosphere "$(STAGE142_COMBINED_OUT)/"
	@mkdir -p "$(STAGE142_COMBINED_OUT)/SaltySD/plugins/$(PROGRAM_ID)"
	@cp deploy-saltynx/stage142/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf \
	  "$(STAGE142_COMBINED_OUT)/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf"

STAGE143_COMBINED_OUT := deploy-saltynx/stage143-combined
.PHONY: stage143-combined-deploy
stage143-combined-deploy:
	@env -u MAKELEVEL $(MAKE) clean
	@env -u MAKELEVEL $(MAKE) SALTYNX_DIAGNOSTIC_BRIDGE=1 SKIP_DEFAULT_PC_MODS=1
	@env -u MAKELEVEL $(MAKE) clean DIAGNOSTIC_STAGE=143 SALTYNX_PLUGIN=1
	@env -u MAKELEVEL $(MAKE) DIAGNOSTIC_STAGE=143 SALTYNX_PLUGIN=1
	@rm -rf "$(STAGE143_COMBINED_OUT)"
	@mkdir -p "$(STAGE143_COMBINED_OUT)"
	@cp -R deploy/atmosphere "$(STAGE143_COMBINED_OUT)/"
	@mkdir -p "$(STAGE143_COMBINED_OUT)/SaltySD/plugins/$(PROGRAM_ID)"
	@cp deploy-saltynx/stage143/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf \
	  "$(STAGE143_COMBINED_OUT)/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf"

# Production persistence needs both modules: subsdk9 hosts Lua and the external
# plugin registers SaltyNX's file table before any Mod:SaveData call.
MOD_PERSISTENCE_OUT := deploy-saltynx/mod-persistence
.PHONY: mod-persistence-deploy
mod-persistence-deploy:
	@env -u MAKELEVEL $(MAKE) clean
	@env -u MAKELEVEL $(MAKE) PERSISTENCE_REQUIRED=1
	@env -u MAKELEVEL $(MAKE) clean DIAGNOSTIC_STAGE=144 SALTYNX_PLUGIN=1
	@env -u MAKELEVEL $(MAKE) DIAGNOSTIC_STAGE=144 SALTYNX_PLUGIN=1
	@rm -rf "$(MOD_PERSISTENCE_OUT)"
	@mkdir -p "$(MOD_PERSISTENCE_OUT)"
	@cp -R deploy/atmosphere "$(MOD_PERSISTENCE_OUT)/"
	@mkdir -p "$(MOD_PERSISTENCE_OUT)/SaltySD/plugins/$(PROGRAM_ID)"
	@cp deploy-saltynx/stage144/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf \
	  "$(MOD_PERSISTENCE_OUT)/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf"

MOD_PERSISTENCE_PROBE_OUT := deploy-saltynx/mod-persistence-probe
.PHONY: mod-persistence-probe-deploy
mod-persistence-probe-deploy:
	@env -u MAKELEVEL $(MAKE) clean
	@env -u MAKELEVEL $(MAKE) SKIP_DEFAULT_PC_MODS=1 PERSISTENCE_REQUIRED=1
	@cd .. && $(PYTHON) -m tools.inspect_pc_mod \
	  --mods-root runtime/diagnostic/mod-persistence-probe/pc-mods \
	  --romfs-output runtime/deploy
	@env -u MAKELEVEL $(MAKE) clean DIAGNOSTIC_STAGE=144 SALTYNX_PLUGIN=1
	@env -u MAKELEVEL $(MAKE) DIAGNOSTIC_STAGE=144 SALTYNX_PLUGIN=1
	@rm -rf "$(MOD_PERSISTENCE_PROBE_OUT)"
	@mkdir -p "$(MOD_PERSISTENCE_PROBE_OUT)"
	@cp -R deploy/atmosphere "$(MOD_PERSISTENCE_PROBE_OUT)/"
	@mkdir -p "$(MOD_PERSISTENCE_PROBE_OUT)/SaltySD/plugins/$(PROGRAM_ID)"
	@cp deploy-saltynx/stage144/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf \
	  "$(MOD_PERSISTENCE_PROBE_OUT)/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf"

MOD_PERSISTENCE_TRACE_OUT := deploy-saltynx/mod-persistence-probe-trace
.PHONY: mod-persistence-probe-trace-deploy
mod-persistence-probe-trace-deploy:
	@env -u MAKELEVEL $(MAKE) clean
	@env -u MAKELEVEL $(MAKE) SKIP_DEFAULT_PC_MODS=1 PERSISTENCE_TRACE=1 PERSISTENCE_REQUIRED=1
	@cd .. && $(PYTHON) -m tools.inspect_pc_mod \
	  --mods-root runtime/diagnostic/mod-persistence-probe/pc-mods \
	  --romfs-output runtime/deploy
	@env -u MAKELEVEL $(MAKE) clean DIAGNOSTIC_STAGE=145 SALTYNX_PLUGIN=1
	@env -u MAKELEVEL $(MAKE) DIAGNOSTIC_STAGE=145 SALTYNX_PLUGIN=1
	@rm -rf "$(MOD_PERSISTENCE_TRACE_OUT)"
	@mkdir -p "$(MOD_PERSISTENCE_TRACE_OUT)"
	@cp -R deploy/atmosphere "$(MOD_PERSISTENCE_TRACE_OUT)/"
	@mkdir -p "$(MOD_PERSISTENCE_TRACE_OUT)/SaltySD/plugins/$(PROGRAM_ID)"
	@cp deploy-saltynx/stage145/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf \
	  "$(MOD_PERSISTENCE_TRACE_OUT)/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf"

# Event-driven persistence diagnostics use one explicitly supplied Build ID for
# the Runtime, real persistence probe and Stage145 registration plugin.
# Recursive invocations are equivalent to TEST_BUILD_ID=${ISAAC_TEST_BUILD_ID}.
MOD_PERSISTENCE_EVENT_OUT := deploy-saltynx/mod-persistence-event-diagnostic
ISAAC_TEST_BUILD_ID ?= $(TEST_BUILD_ID)
.PHONY: mod-persistence-event-diagnostic-deploy
mod-persistence-event-diagnostic-deploy:
	@env -u MAKELEVEL $(MAKE) clean
	@env -u MAKELEVEL $(MAKE) SKIP_DEFAULT_PC_MODS=1 PERSISTENCE_EVENT_DIAGNOSTIC=1 PERSISTENCE_REQUIRED=1 TEST_BUILD_ID=$(ISAAC_TEST_BUILD_ID)
	@cd .. && $(PYTHON) -m tools.inspect_pc_mod \
	  --mods-root runtime/diagnostic/mod-persistence-probe/pc-mods \
	  --romfs-output runtime/deploy
	@env -u MAKELEVEL $(MAKE) clean DIAGNOSTIC_STAGE=145 SALTYNX_PLUGIN=1 PERSISTENCE_EVENT_DIAGNOSTIC=1 TEST_BUILD_ID=$(ISAAC_TEST_BUILD_ID)
	@env -u MAKELEVEL $(MAKE) DIAGNOSTIC_STAGE=145 SALTYNX_PLUGIN=1 PERSISTENCE_EVENT_DIAGNOSTIC=1 TEST_BUILD_ID=$(ISAAC_TEST_BUILD_ID)
	@rm -rf "$(MOD_PERSISTENCE_EVENT_OUT)"
	@mkdir -p "$(MOD_PERSISTENCE_EVENT_OUT)"
	@cp -R deploy/atmosphere "$(MOD_PERSISTENCE_EVENT_OUT)/"
	@mkdir -p "$(MOD_PERSISTENCE_EVENT_OUT)/SaltySD/plugins/$(PROGRAM_ID)"
	@cp deploy-saltynx/stage145/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf \
	  "$(MOD_PERSISTENCE_EVENT_OUT)/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf"
	@$(PYTHON) ../tools/write_test_package_manifest.py \
	  --root "$(MOD_PERSISTENCE_EVENT_OUT)" --build-id "$(ISAAC_TEST_BUILD_ID)" \
	  --stage 145 --mode stage145-persistence-event
