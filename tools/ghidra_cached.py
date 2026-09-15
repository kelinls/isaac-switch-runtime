#!/usr/bin/env python3
"""`analyzeHeadless` 的**缓存包装器**：同样的脚本 + 工程 + 参数，第二次直接复用上次的导出结果。

## 为什么需要它（门禁提速，2026-09-15）

门禁里有 **35 个模块**各自跑一次 Ghidra 无头分析（每个约 5–13 秒，合计 164 秒），而且它们
**共用同一个 Ghidra 工程**，所以只能串行 —— 这一路就是整轮门禁的墙钟下限（实测 204 秒里
它占 164 秒）。它们做的事却是同一件：打开工程 → 跑一个导出脚本 → 写一个 JSON。

## 用法

**与 `analyzeHeadless` 的参数完全一致**，直接把它当成 `analyzeHeadless` 用：

    tools/ghidra_cached.py <工程目录> <工程名> -process <程序> -noanalysis \\
        -scriptPath <脚本目录> -postScript <脚本名> [脚本参数…] <输出路径>

测试模块里就是把 `GHIDRA` 那个常量指到本脚本（一行改动）。

## 缓存键必须覆盖**全部输入**（否则会拿旧结果骗门禁）

键 = sha256(`真身路径` + `工程指纹` + `-process 程序名` + `脚本文件内容` + `脚本参数`去掉输出路径)

* **工程指纹**：`<工程目录>/<工程名>.rep` 下所有文件的（相对路径, 大小, mtime）——
  工程只在"重新导入/重建"时才变，用它足够，而且很便宜；
* **输出路径不进键**：每个模块都用 `TemporaryDirectory`，路径每次都不同；
* 命中时把缓存里的输出文件还原到输出目录，并回放上次的 stdout/stderr 与退出码 ——
  所以调用方对输出内容的断言（例如 `assertNotIn("REPORT SCRIPT ERROR:", ...)`）照旧成立。

## 找不到真身时

退出码 **77** 并打印明确说明（而不是假装成功）。缓存命中时**不需要**装 Ghidra。

可用 `ISAAC_GHIDRA_HEADLESS` 指定真身路径；默认找下面那几个常见位置。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

#: `analyzeHeadless` 的候选位置（本机实测路径在前）。
DEFAULT_CANDIDATES = (
    "/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless",
    "/opt/homebrew/bin/analyzeHeadless",
    "/usr/local/bin/analyzeHeadless",
)
EXIT_HEADLESS_MISSING = 77


def real_headless() -> Path | None:
    override = os.environ.get("ISAAC_GHIDRA_HEADLESS")
    candidates = (override,) if override else DEFAULT_CANDIDATES
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return Path(candidate)
    found = shutil.which("analyzeHeadless")
    return Path(found) if found else None


def cache_root() -> Path:
    override = os.environ.get("ISAAC_GHIDRA_CACHE")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "isaac-ghidra-export-cache"


#: 每次会话都会被重建的**会话缓冲**：名字每次都换（`db.4377.gbf` → `db.4378.gbf`）、
#: 内容也每次不同（147 MB！），所以既不能进键、也不能当"工程变了"的信号。
VOLATILE_SUFFIXES = (".gbf",)


def project_fingerprint(project_dir: Path, project_name: str) -> str:
    """工程**持久状态**的内容指纹（排除每次会话重建的会话缓冲）。

    实测（2026-09-15）：`.rep` 里只有 8 个文件，其中 147 MB 的 `db.NNNN.gbf` 每次运行都
    新建、内容不同；剩下的 `project.prp`/`idata/00/00000000.prp` 与几个索引文件加起来
    **919 字节**，跨会话逐字节稳定。所以：
      * 按**内容**哈希剩下的（便宜到可以忽略，而且是内容寻址的）；
      * `.gbf` 一律排除。

    ⚠️ 残留风险（写在代码里免得忘）：如果有人在**不改脚本、不改被分析的程序**的前提下
    就地重跑分析并得到不同结论，这个键不会变、缓存会给旧结果。真要重跑，用
    `ISAAC_GHIDRA_NO_CACHE=1`（或删掉缓存目录）。
    """
    rep = project_dir / f"{project_name}.rep"
    digest = hashlib.sha256()
    if not rep.is_dir():
        digest.update(f"missing:{rep}".encode())
        return digest.hexdigest()
    entries: list[tuple[str, str]] = []
    for path in rep.rglob("*"):
        if not path.is_file() or path.name.endswith(VOLATILE_SUFFIXES):
            continue
        entries.append((str(path.relative_to(rep)),
                        hashlib.sha256(path.read_bytes()).hexdigest()))
    for name, content_hash in sorted(entries):
        digest.update(f"{name}\0{content_hash}\n".encode())
    return digest.hexdigest()


#: 被分析的程序二进制在哪找（工程里的程序名就是文件名，例如 `Repentance.nro`）。
PROGRAM_SEARCH_ROOTS = ("The Binding of Isaac Rebirth pc", "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]")
PROGRAM_HASH_LIMIT = 64 * 1024 * 1024


def program_fingerprint(project_dir: Path, program: str) -> str:
    """被分析的程序本体的内容哈希（找不到就记 `not-found`，不影响可用性）。

    分析结果是从这个二进制推出来的，所以它是键里最硬的一项；11 MB 的 NRO 哈希约 30 ms。
    """
    if not program:
        return "no-program"
    repo = project_dir.resolve().parents[1]
    for root_name in PROGRAM_SEARCH_ROOTS:
        root = repo / root_name
        if not root.is_dir():
            continue
        for path in root.rglob(program):
            if not path.is_file():
                continue
            size = path.stat().st_size
            if size > PROGRAM_HASH_LIMIT:
                return f"too-large:{program}:{size}"
            return hashlib.sha256(path.read_bytes()).hexdigest()
    return f"not-found:{program}"


def parse_invocation(argv: list[str]) -> dict:
    """从 `analyzeHeadless` 的参数里取出算键与判输出所需的东西。"""
    if len(argv) < 2:
        raise SystemExit("用法：ghidra_cached.py <工程目录> <工程名> [analyzeHeadless 的参数…]")
    info = {
        "project_dir": Path(argv[0]),
        "project_name": argv[1],
        "program": "",
        "script_path": None,
        "script": None,
        "script_args": (),
        "output": None,
    }
    index = 2
    while index < len(argv):
        token = argv[index]
        if token == "-process" and index + 1 < len(argv):
            info["program"] = argv[index + 1]
            index += 2
            continue
        if token == "-scriptPath" and index + 1 < len(argv):
            info["script_path"] = Path(argv[index + 1])
            index += 2
            continue
        if token == "-postScript" and index + 1 < len(argv):
            rest = argv[index + 1:]
            info["script"] = rest[0]
            info["script_args"] = tuple(rest[1:])
            # 约定（35 个模块都是这个形态）：`-postScript <脚本> [参数…] <输出路径>`，
            # 输出路径是最后一个实参。
            info["output"] = Path(rest[-1]) if len(rest) >= 2 else None
            break
        index += 1
    return info


def cache_key(info: dict, headless: Path | None) -> str:
    digest = hashlib.sha256()
    digest.update(str(headless).encode())
    digest.update(project_fingerprint(info["project_dir"], info["project_name"]).encode())
    digest.update(info["program"].encode())
    digest.update(program_fingerprint(info["project_dir"], info["program"]).encode())
    digest.update(str(info["script"]).encode())
    script_file = None
    if info["script"] and info["script_path"]:
        script_file = info["script_path"] / info["script"]
    if script_file is not None and script_file.is_file():
        digest.update(script_file.read_bytes())
    else:
        digest.update(f"script-missing:{script_file}".encode())
    # 脚本参数里**去掉输出路径**：它在每个模块里都是临时目录，进键会让缓存永不命中。
    digest.update("\0".join(info["script_args"][:-1]).encode())
    return digest.hexdigest()


def snapshot(directory: Path) -> dict[str, bytes]:
    """把输出目录里的文件按相对路径收成 {名字: 内容}（只认文件，不递归目录）。"""
    files: dict[str, bytes] = {}
    if not directory.is_dir():
        return files
    for path in sorted(directory.iterdir()):
        if path.is_file():
            files[path.name] = path.read_bytes()
    return files


def restore(files: dict[str, bytes], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (directory / name).write_bytes(content)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    info = parse_invocation(argv)
    if os.environ.get("ISAAC_GHIDRA_NO_CACHE") == "1":
        headless = real_headless()
        if headless is None:
            sys.stderr.write("找不到 analyzeHeadless 真身（已禁用缓存）。\n")
            return EXIT_HEADLESS_MISSING
        result = subprocess.run([str(headless), *argv], text=True, capture_output=True)
        sys.stdout.write(result.stdout or "")
        sys.stderr.write(result.stderr or "")
        return result.returncode
    headless = real_headless()
    key = cache_key(info, headless)
    entry = cache_root() / key

    stdout_file = entry / "stdout.txt"
    stderr_file = entry / "stderr.txt"
    code_file = entry / "exit-code.txt"
    primary_file = entry / "primary-output-name.txt"
    outputs_dir = entry / "outputs"
    output_parent = info["output"].parent if info["output"] is not None else None

    if code_file.is_file() and outputs_dir.is_dir():
        # 命中：还原输出、回放输出流与退出码。**不需要装 Ghidra**。
        code = int(code_file.read_text(encoding="utf-8").strip() or "0")
        if output_parent is not None:
            files = snapshot(outputs_dir)
            restore(files, output_parent)
            # ★ 键里**故意不含输出路径**（每个模块都用临时目录，含进去就永不命中），
            # 于是"上次跑的时候输出文件叫什么名字"可能与这次不同（实测踩过：缓存里叫
            # `out.json`、调用方要 `remaining-getters.json` ⇒ 命中后调用方找不到文件，
            # 报 FileNotFoundError，看起来像 Ghidra 坏了）。
            # 所以：把上次那个"主输出"再写一份到这次要求的路径上。
            requested = info["output"]
            if requested is not None and requested.name not in files:
                primary_name = (primary_file.read_text(encoding="utf-8").strip()
                                if primary_file.is_file() else "")
                primary_bytes = files.get(primary_name)
                if primary_bytes is None and files:
                    primary_bytes = files[sorted(files)[0]]
                if primary_bytes is not None:
                    requested.write_bytes(primary_bytes)
        sys.stdout.write(stdout_file.read_text(encoding="utf-8", errors="replace")
                         if stdout_file.is_file() else "")
        sys.stderr.write(stderr_file.read_text(encoding="utf-8", errors="replace")
                         if stderr_file.is_file() else "")
        return code

    if headless is None:
        sys.stderr.write(
            "找不到 analyzeHeadless 真身（缓存也没有这一条）。\n"
            "  装 Ghidra，或用 ISAAC_GHIDRA_HEADLESS 指到它的 support/analyzeHeadless。\n")
        return EXIT_HEADLESS_MISSING

    result = subprocess.run([str(headless), *argv], text=True, capture_output=True)
    sys.stdout.write(result.stdout or "")
    sys.stderr.write(result.stderr or "")

    # 只缓存**成功**的结果：失败（脚本报错、工程打不开）每次都要重新跑，避免把偶发失败固化。
    if result.returncode == 0 and output_parent is not None:
        files = snapshot(output_parent)
        if files:
            entry.mkdir(parents=True, exist_ok=True)
            restore(files, outputs_dir)
            requested = info["output"]
            primary = requested.name if (requested is not None
                                         and requested.name in files) else sorted(files)[0]
            primary_file.write_text(primary, encoding="utf-8")
            code_file.write_text(str(result.returncode), encoding="utf-8")
            stdout_file.write_text(result.stdout or "", encoding="utf-8")
            stderr_file.write_text(result.stderr or "", encoding="utf-8")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
