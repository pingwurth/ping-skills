"""分层词典加载（P1-2）。

词典目录结构（dictionary/）：
- sensitive-core.txt           高置信金融敏感词（告警 → SENSITIVE）
- sensitive-extended.txt       低置信上下文相关词（提示 → LOW）
- sensitive-words.blacklist    评审确认的敏感词黑名单（最高优先级）
- sensitive-words.whitelist    评审确认的非敏感词白名单
- en_US-large.txt              英文大词典（字段命名规范判定）
- en_US.whitelist              技术缩写白名单（视为合格命名）

词表文件支持 ``#`` 开头的注释行（版本头规范，见 SKILL.md "词典维护"章节）。
"""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

DICTIONARY_DIR = Path(__file__).resolve().parent.parent / "dictionary"

CORE_FILE = DICTIONARY_DIR / "sensitive-core.txt"
EXTENDED_FILE = DICTIONARY_DIR / "sensitive-extended.txt"
BLACKLIST_FILE = DICTIONARY_DIR / "sensitive-words.blacklist"
WHITELIST_FILE = DICTIONARY_DIR / "sensitive-words.whitelist"
EN_US_DICT_FILE = DICTIONARY_DIR / "en_US-large.txt"
EN_US_WHITELIST_FILE = DICTIONARY_DIR / "en_US.whitelist"


# 词表缓存: 键为词表文件绝对路径, 值为 (mtime, 词集合)。
# main.py 以多线程并行编排各链路, 缓存读写需加锁保证线程安全;
# mtime 变化时自动失效, 保证词表更新后无需重启进程即可生效。
_WORD_SET_CACHE: dict[str, tuple[float, set[str]]] = {}
_WORD_SET_CACHE_LOCK = threading.Lock()


def _read_word_set(file_path: Path) -> set[str]:
    """实际读取词表文件为小写集合, 跳过空行与 # 注释行(无缓存)。"""
    words: set[str] = set()
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                word = line.strip()
                if word and not word.startswith("#"):
                    words.add(word.lower())
    except OSError as exc:
        print(f"warning: cannot read dictionary {file_path}: {exc}", file=sys.stderr)
    return words


def load_word_set(file_path: Path) -> set[str]:
    """加载词表文件为小写集合, 跳过空行与 # 注释行。

    基于 文件路径+mtime 的模块级缓存: 同一进程内同一词典文件只解析一次
    (如 en_US-large.txt 约 1.6MB), 文件修改后缓存自动失效。
    注意: 返回的是缓存共享集合, 调用方不应原地修改。
    """
    if not file_path.is_file():
        return set()
    try:
        stat = file_path.stat()
        cache_key = str(file_path.resolve())
    except OSError:
        # 无法取得文件元信息时退化为直接读取, 保持原有行为
        return _read_word_set(file_path)

    with _WORD_SET_CACHE_LOCK:
        cached = _WORD_SET_CACHE.get(cache_key)
        if cached is not None and cached[0] == stat.st_mtime:
            return cached[1]

    words = _read_word_set(file_path)

    with _WORD_SET_CACHE_LOCK:
        _WORD_SET_CACHE[cache_key] = (stat.st_mtime, words)
    return words


@dataclass
class SensitiveDicts:
    """敏感词分层词典集合。

    判定优先级：whitelist > blacklist > core > extended。
    - core:     命中 → 高置信违规（SENSITIVE）
    - extended: 命中 → 低置信提示（LOW）
    """

    core: set[str] = field(default_factory=set)
    extended: set[str] = field(default_factory=set)
    blacklist: set[str] = field(default_factory=set)
    whitelist: set[str] = field(default_factory=set)

    def classify(self, words: set[str], identifier: str = "") -> tuple[str, str] | None:
        """对一组拆分后的单词做敏感级别判定。

        Args:
            words: 标识符拆分后的小写单词集合
            identifier: 原始标识符（用于整名黑/白名单匹配，小写比较）

        Returns:
            (级别, 命中词) 元组，级别为 "blacklist" | "core" | "extended"；未命中返回 None。
            白名单命中同样返回 None（视为安全）。
        """
        ident_lower = identifier.lower() if identifier else ""

        # 白名单：整名命中直接豁免（评审确认的安全词，如 cardType）
        if ident_lower and ident_lower in self.whitelist:
            return None

        # 黑名单：整名或拆分词命中，最高级别
        if (ident_lower and ident_lower in self.blacklist) or any(w in self.blacklist for w in words):
            hit = ident_lower if ident_lower in self.blacklist else next(w for w in words if w in self.blacklist)
            return "blacklist", hit

        for w in words:
            if w in self.core:
                return "core", w
        for w in words:
            if w in self.extended:
                return "extended", w
        return None


def load_sensitive_dicts() -> SensitiveDicts:
    """加载分层敏感词典（core/extended/blacklist/whitelist）。"""
    return SensitiveDicts(
        core=load_word_set(CORE_FILE),
        extended=load_word_set(EXTENDED_FILE),
        blacklist=load_word_set(BLACKLIST_FILE),
        whitelist=load_word_set(WHITELIST_FILE),
    )


def load_en_us_dict() -> set[str]:
    """加载英文大词典（字段命名规范判定用）。"""
    return load_word_set(EN_US_DICT_FILE)


def load_en_us_whitelist() -> set[str]:
    """加载技术缩写白名单（视为合格命名）。"""
    return load_word_set(EN_US_WHITELIST_FILE)
