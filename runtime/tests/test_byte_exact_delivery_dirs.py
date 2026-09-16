"""交付目录的**逐字节副本**门禁 —— "`.gitattributes` 写了但没生效"这类静默失效。

**为什么需要它**：项目有一条硬纪律（AGENTS.md / CONTRIBUTING）：交给设备的逐字节副本
**不许让 git 做行尾规范化**，否则"回退件"就不再等于我们验过的那个文件
（2026-09-15 实测：`main.lua` 87328 字节被 CRLF→LF 变成 85297，哈希全变）。
做法是在放副本的目录里写 `.gitattributes`。

**2026-09-16 又栽了一次，而且是新的形态**：写了一个只有 `-text`（**没有匹配模式**）的
`.gitattributes` —— 这种行是**非法行、git 直接忽略**，于是保护等于没写：
`git check-attr` 什么都不报，提交时三个 `main.lua` 副本被规范化，
仓库里的对象与磁盘不一致（磁盘 `e7bb9466…` / 仓库 `cb10b902…`）。
危险的地方在于**没有任何报错**：`git add` 只给一句 "CRLF will be replaced by LF" 的警告，
很容易被当成噪音划过去。

所以这里把三件事都钉住（每条都对应上面那次失败的一个环节）：

1. 目录里的 `.gitattributes` 必须**有生效的行**（模式 + `-text`），不能只有裸 `-text`；
2. `git check-attr text <文件>` 必须回 `unset`（证明这条规则真的落到文件上了）；
3. 已跟踪文件的**仓库对象必须与工作区文件逐字节相同**（证明提交时没被规范化）。
"""

import fnmatch
import hashlib
import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True)


def delivery_dirs() -> list[pathlib.Path]:
    """要按"逐字节副本"对待的交付目录。

    两类：① 自己带 `.gitattributes` 的目录（约定：这就是标记）；
    ② 被**顶层** `.gitattributes` 的规则罩住的目录（例如 `dist/isaac-eid-release-*`）。
    """
    if not DIST.is_dir():
        return []
    marked = {path for path in DIST.iterdir()
              if path.is_dir() and (path / ".gitattributes").is_file()}
    for pattern in top_level_patterns():
        # 只取模式**直接命名**的那一层（模式里的 `/**` 是递归通配，不能拿它的结果当目录）
        head = pattern.split("/", 1)[-1].split("/")[0]
        marked.update(path for path in DIST.glob(head) if path.is_dir())
    return sorted(marked)


def top_level_patterns() -> list[str]:
    """顶层 `.gitattributes` 里关掉行尾转换的模式（相对仓库根）。"""
    path = ROOT / ".gitattributes"
    if not path.is_file():
        return []
    patterns = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2 and any(part in ("-text", "!text", "binary") for part in parts[1:]):
            patterns.append(parts[0])
    return patterns


def declared_patterns(folder: pathlib.Path) -> list[str]:
    """该目录 `.gitattributes` 里声明的模式（相对该目录）；没有这个文件就返回空。"""
    attributes = folder / ".gitattributes"
    if not attributes.is_file():
        return []
    lines = attributes.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines:
        parts = line.split()
        if parts and not parts[0].startswith("#") and len(parts) >= 2:
            if any(part in ("-text", "!text", "binary") for part in parts[1:]):
                out.append(parts[0])
    return out


class ByteExactDeliveryDirTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        inside = git("rev-parse", "--is-inside-work-tree")
        cls.has_git = inside.returncode == 0 and inside.stdout.strip() == b"true"

    def test_gitattributes_rules_have_a_pattern(self):
        """裸 `-text` 是非法行、会被 git 忽略 —— 这正是本次栽的那一跤。

        只查"目录自己带 `.gitattributes`"的那些（被顶层规则罩住的发布包目录不算）。
        """
        for folder in delivery_dirs():
            attributes = folder / ".gitattributes"
            if not attributes.is_file():
                continue
            with self.subTest(folder=folder.name):
                lines = [line.strip() for line in
                         attributes.read_text(encoding="utf-8").splitlines()]
                effective = [line for line in lines if line and not line.startswith("#")]
                self.assertTrue(effective, f"{folder.name} 的 .gitattributes 没有生效行")
                for line in effective:
                    parts = line.split()
                    self.assertGreaterEqual(
                        len(parts), 2,
                        f"{folder.name}：`{line}` 缺匹配模式 ⇒ git 会整行忽略（保护等于没写）")
                    self.assertTrue(any(part in ("-text", "!text", "binary") for part in parts[1:]),
                                    f"{folder.name}：`{line}` 没有关掉行尾转换")

    def test_declared_patterns_match_real_files_and_take_effect(self):
        """声明的模式必须**真的盖住某个已跟踪文件**，并且那些文件的转换要真被关掉。

        只查"目录里有没有带 `-text` 的行"是不够的：老目录里写的是逐文件模式
        （`main.lua -text`），文件一改名（`main.lua.original`）保护就落空了 ——
        这条同时管住"模式打错/文件改名"和"规则没生效"。
        """
        if not self.has_git:
            self.skipTest("不在 git 工作区里（隔离副本）")
        for folder in delivery_dirs():
            patterns = declared_patterns(folder)
            relative_dir = folder.relative_to(ROOT)
            tracked = git("ls-files", str(relative_dir)).stdout.decode().split()
            names = [pathlib.Path(name).name for name in tracked
                     if pathlib.Path(name).name != ".gitattributes"]
            if not names:
                continue
            for pattern in patterns:
                matched = [name for name in names if fnmatch.fnmatch(name, pattern)]
                with self.subTest(folder=folder.name, pattern=pattern):
                    self.assertTrue(matched, f"{folder.name}：模式 `{pattern}` 没盖住任何文件")
                    for name in matched:
                        relative = str(relative_dir / name)
                        out = git("check-attr", "text", relative).stdout.decode().strip()
                        self.assertTrue(out.endswith("text: unset"),
                                        f"{relative} 的行尾转换没被关掉（{out or '没有任何属性'}）")

    def test_tracked_copies_are_byte_identical_to_the_worktree(self):
        """最终判据：仓库里存的对象 == 工作区文件（哈希能拿去和设备读数核对）。"""
        if not self.has_git:
            self.skipTest("不在 git 工作区里（隔离副本）")
        for folder in delivery_dirs():
            tracked = git("ls-files", str(folder.relative_to(ROOT))).stdout.decode().split()
            for relative in tracked:
                path = ROOT / relative
                if not path.is_file() or path.name == ".gitattributes":
                    continue
                blob = git("show", f":{relative}").stdout
                with self.subTest(file=relative):
                    self.assertTrue(blob, f"{relative} 没进索引")
                    self.assertEqual(
                        hashlib.sha256(blob).hexdigest(),
                        hashlib.sha256(path.read_bytes()).hexdigest(),
                        f"{relative} 在仓库里与工作区不一致（被行尾规范化了）"
                        f" —— 修法：把该目录的 .gitattributes 写成 `* -text`，"
                        f"再 `git rm --cached` + `git add` 让对象重新入索引")


if __name__ == "__main__":
    unittest.main()
