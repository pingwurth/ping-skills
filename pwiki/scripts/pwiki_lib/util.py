"""共享小工具(git/哈希/文件名/退出)。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys

from . import config


def posix(path: str) -> str:
    """路径统一 posix 形态(规划产物落库/比较的唯一口径)。

    曾在 ~22 个调用点各写一份 `.replace(os.sep, "/")` —— 规则要变时
    (如折叠 './'、重复斜杠)只会修到其中几处, 其余静默保持旧行为。"""
    return str(path).replace(os.sep, "/")


def dirname(p: str) -> str:
    """posix 路径的父目录(顶层返回 ''); themes 与 modules 共用一份
    (两套实现曾对同一目录算出不同的分组键, 把一个模块的证据拆成两堆)。"""
    p = str(p)
    d = p.rsplit("/", 1)[0] if "/" in p else ""
    return "" if d == "." else d


_REALPATH_CACHE: dict = {}
"""repo → realpath 根的进程级缓存(join_confined 是最热助手, 一次运行
曾对同一 repo 做 5 万+ 次两段 realpath 链; 仓库根在进程内视为常量)。"""


def join_confined(repo: str, rel: str):
    """把被引/来源相对路径并入 repo, 越界返回 None(c049)。

    绝对路径(POSIX `/` 或 Windows 盘符)丢弃 repo; `../` 链与符号链接
    逃逸都在 commonpath 校验下拦截。返回的是绝对规范路径或 None ——
    调用方把 None 当"非法路径"处理, 且**不得**再区分仓外文件的存在性
    (那会变成宿主文件存在性/行数 oracle)。"""
    p = str(rel or "").replace("\\", "/").strip()
    if not p or p.startswith("/") or re.match(r"^[A-Za-z]:", p):
        return None
    root = _REALPATH_CACHE.get(repo)
    if root is None:
        root = os.path.realpath(os.path.abspath(repo))
        _REALPATH_CACHE[repo] = root
    full = os.path.normpath(os.path.join(root, p))
    # normpath 只消 `..` 字面量; 已存在的符号链接段由 realpath 收口
    full = os.path.realpath(full)
    try:
        if os.path.commonpath([root, full]) != root:
            return None
    except ValueError:  # 不同盘符等
        return None
    return full


def git(repo: str, *args: str) -> str:
    """git 只读查询; 失败返回 ''(分支显示为 '-')。

    显式 UTF-8 + replace: text=True 的本地 locale 解码在 LC_ALL=C 下对
    非_ascii 分支名/路径直接 UnicodeDecodeError 裸崩(crp/jaut/slr 三处
    git 包装早都修过, 这里是第 4 份拷贝, 别再漂移)。"""
    try:
        p = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                           timeout=30, encoding="utf-8", errors="replace")
        return p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return ""


def die(msg: str, code: int = config.EXIT_ENV) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


# CJK 字符类(BM25 分词/qoder-plan 字节帽 lint/锚点生成共用 ——
# r4 #58: 三处各写一份 [一-鿿], 扩展字符块时只改这里)
CJK_RE = re.compile(r"[一-鿿]")


def read_submission(path_arg: str) -> str:
    """submit/write 共用的读稿入口(r4 #60: ~14 行块在两处逐字复制,
    c048/c086 的修复曾要打两遍)。

    stdin '-' 原样读; 文件缺失 = 前置不满足(3); 坏 UTF-8 = 门禁错(2)
    且带可诊断路径。strip+归一换行由调用方处理。"""
    if path_arg == "-":
        # c007: stdin 与文件分支同口径 —— 管道送 GBK/Latin-1 时裸
        # UnicodeDecodeError 曾穿透(非契约退出码、无孤儿保存)
        try:
            return sys.stdin.read()
        except UnicodeDecodeError as e:
            die(f"stdin 稿件不是合法 UTF-8({e}) — writer 侧统一 UTF-8 输出",
                config.EXIT_GATE)
    path = os.path.abspath(path_arg)
    if not os.path.isfile(path):
        die(f"页面文件不存在: {path}", config.EXIT_PRECOND)
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except UnicodeDecodeError as e:
        die(f"页面文件不是合法 UTF-8({path}: {e}) — writer 侧统一 UTF-8 输出",
            config.EXIT_GATE)


def json_list(raw):
    """JSON 列表列的容错解码('null'/None/坏行 → [])—— 单一实现,
    engine._json_list 与 publish/snapshot 的消费点共用(同一坏行 gate 侧
    被吞成 [], finish/update 却裸 JSONDecodeError)。"""
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    return v if isinstance(v, list) else []


def as_int(v, default: int = 0) -> int:
    """容错整数: 人工编辑面的 ordinal 等键可能是 None/yaml 空值/字符串
    —— 统一回落 default。`ordinal:` 空值曾以 None 穿过 validate(键存在,
    .get 默认值不生效), 在 begin/update/finish/qoder-plan 四处 int(None)
    裸崩; validate 报错是门, 这里是兜底。"""
    if isinstance(v, bool):
        return default
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def atomic_write(path: str, text: str) -> None:
    """c045 同款原子写(临时文件 + fsync + os.replace)。

    发布树工件(页面/知识文件/改名落盘/--fix 回写)曾用 open(path,'w')
    先截断 —— 进程中途被杀/ENOSPC 留下半份假装完成的发布文件, DB 行
    仍说 completed(content_hash 旧值)。planner.save_plan 之外的落盘
    点统一走这里。"""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        # c018: open/write/fsync 失败时清掉 tmp —— 无清扫方(prune 只删
        # .md, 空目录 rmdir 对含 tmp 的目录失败), 残件会随发布树一起发布
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def safe_filename(title: str) -> str:
    # c079: 手编面的 `title:` 空值(None)会击穿 .get 默认(键存在值为
    # None), finish 重读计划后不重跑 validate —— re.sub(None) 直接裸崩;
    # 非 str/空一律回落 untitled, 与 as_int 同族防御
    if not isinstance(title, str) or not title.strip():
        return "untitled"
    name = config.ILLEGAL_FN.sub("-", title).strip().rstrip(".-")
    return name or "untitled"


def slugify(text: str, fallback: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s or fallback
