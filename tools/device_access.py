#!/usr/bin/env python3
"""设备访问参数（主机 / 端口 / 账号 / 密码）的统一来源。

**为什么要有这个文件**：这些值原先**写死在每个设备工具里**——一旦把仓库推到 GitHub，
等于把设备的 FTP 账号密码一起公开了。现在统一按下面顺序取，仓库里不再出现任何凭据：

1. 命令行参数（`--host/--port/--user/--password`）
2. 环境变量（`ISAAC_DEVICE_HOST/PORT/USER/PASSWORD`）
3. 本机私有配置 `tools/device.local.json`（**已在 `.gitignore` 里，绝不进仓库**），格式：
   ```json
   {"host": "192.168.124.11", "port": 5000, "user": "...", "password": "..."}
   ```
4. 前三条都没有 ⇒ 直接报错并说明怎么配（**不再有内置默认账号密码**）。

主机与端口本身不是机密，所以给了默认值；账号密码没有任何默认值。
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOCAL_FILE = pathlib.Path(__file__).resolve().parent / 'device.local.json'

DEFAULT_HOST = '192.168.124.11'   # 例：本项目的设备地址；不同网络请用 --host 或本地配置覆盖
DEFAULT_PORT = 5000


def _from_env() -> dict:
    import os
    return {
        'host': os.environ.get('ISAAC_DEVICE_HOST'),
        'port': os.environ.get('ISAAC_DEVICE_PORT'),
        'user': os.environ.get('ISAAC_DEVICE_USER'),
        'password': os.environ.get('ISAAC_DEVICE_PASSWORD'),
    }


def _from_local_file() -> dict:
    if not LOCAL_FILE.exists():
        return {}
    try:
        data = json.loads(LOCAL_FILE.read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001 - 配置坏了就当作没有
        return {}
    return data if isinstance(data, dict) else {}


def resolve_device(host: str | None = None, port: int | str | None = None,
                   user: str | None = None, password: str | None = None,
                   *, required: bool = True) -> tuple[str, int, str, str]:
    """按"参数 → 环境变量 → 本地配置 → 默认主机/端口"的顺序解析出设备访问参数。"""
    env = _from_env()
    local = _from_local_file()

    def pick(cli, key_env, key_local, fallback=None):
        for candidate in (cli, env.get(key_env), local.get(key_local), fallback):
            if candidate not in (None, ''):
                return candidate
        return None

    resolved_host = pick(host, 'ISAAC_DEVICE_HOST', 'host', DEFAULT_HOST)
    resolved_port = int(pick(port, 'ISAAC_DEVICE_PORT', 'port', DEFAULT_PORT))
    resolved_user = pick(user, 'ISAAC_DEVICE_USER', 'user')
    resolved_password = pick(password, 'ISAAC_DEVICE_PASSWORD', 'password')

    if required and (not resolved_user or not resolved_password):
        raise SystemExit(
            '缺少设备账号/密码。请任选一种方式提供（仓库里刻意不含任何默认凭据）：\n'
            '  1) 命令行：--user <名字> --password <密码>\n'
            '  2) 环境变量：ISAAC_DEVICE_USER / ISAAC_DEVICE_PASSWORD\n'
            f'  3) 本地配置：{LOCAL_FILE}（该文件已在 .gitignore 中，不会进仓库）\n'
            '      内容示例：{"user": "someone", "password": "secret"}'
        )
    return resolved_host, resolved_port, resolved_user or '', resolved_password or ''


if __name__ == '__main__':
    # 直接运行 = 自检：打印解析结果（密码只显示长度，不打印明文）
    h, p, u, w = resolve_device(required=False)
    print(f'host={h} port={p} user={u or "(未配置)"} '
          f'password={"*" * len(w) if w else "(未配置)"}')
