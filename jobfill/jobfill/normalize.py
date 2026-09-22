# -*- coding: utf-8 -*-
"""文本归一化与相似度打分：网申字段识别的底层工具。

设计要点（踩过的坑都在这）：
  1. 中文标签里全是装饰：``*姓名：``、``姓名（必填）``、``姓 名``、全角冒号……
     不归一化就永远匹配不上。
  2. 包含匹配必须防否定：值「是」不能匹配到选项「不同意」，
     标签「非全日制」不能匹配到别名「全日制」。
  3. 覆盖度必须设下限：别名「电话」不该匹配到「紧急联系人手机号码」。
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Iterable, Mapping

# 装饰性字符：空白、下划线、破折号、各种中英文标点
_STRIP_RE = re.compile(r"[\s\u3000_\-—–~·、,，。.：:；;！!？?（）()\[\]【】{}<>《》\"'`*※#|/\\+]+")

# 括号里只写「必填/选填」这类提示，整段去掉
_PAREN_NOISE_RE = re.compile(
    r"[（(\[【]\s*(必填|必选|选填|可选|非必填|required|optional)\s*[)）\]】]", re.I
)

# 标签开头的指令词，对识别没有信息量
_PREFIX_RE = re.compile(r"^(请输入|请选择|请填写|请录入|请上传|请先选择|请如实填写|请准确填写)")

# 否定词：包含匹配遇到它会越过或直接判否
_NEG_CHARS = "不无未非没免"

_YES_GROUP = {"是", "有", "同意", "接受", "可以", "会", "愿意", "参加", "已参加", "yes", "y", "true", "1"}
_NO_GROUP = {"否", "没有", "无", "不同意", "不接受", "不可以", "不会", "未曾", "未参加", "no", "n", "false", "0", "都不"}


def to_halfwidth(text: str) -> str:
    """全角转半角（中文表单里全角字母数字很常见）。"""
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def normalize(text) -> str:
    """把任意标签/值变成可比较的形式。"""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    s = to_halfwidth(text)
    s = unicodedata.normalize("NFKC", s)
    s = _PAREN_NOISE_RE.sub("", s)
    s = _PREFIX_RE.sub("", s)
    s = _STRIP_RE.sub("", s)
    return s.strip().lower()


def _clean_contains(short: str, long_: str) -> bool:
    """long_ 里是否含有一段**未被否定**的 short。

    「同意」在「不同意」里不算命中；「全日制」在「非全日制」里不算命中。
    """
    if not short:
        return False
    start = 0
    while True:
        i = long_.find(short, start)
        if i < 0:
            return False
        if i > 0 and long_[i - 1] in _NEG_CHARS:
            start = i + 1
            continue
        return True


def label_score(label, alias) -> float:
    """标签与别名的匹配分，0~1。低于 0.55 视为不匹配。"""
    a, b = normalize(label), normalize(alias)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    if _clean_contains(b, a) or _clean_contains(a, b):
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        cover = len(shorter) / len(longer)
        if cover < 0.34:          # 覆盖太少不可信
            return 0.0
        return 0.84 + 0.14 * cover

    ratio = SequenceMatcher(None, a, b).ratio()
    if ratio >= 0.75:
        return 0.50 + 0.34 * (ratio - 0.75) / 0.25
    return 0.0


def _expand_value(value: str, synonyms: Mapping[str, Iterable[str]]) -> set[str]:
    """把 profile 里的值展开成一堆「同义写法」，用于比对表单选项。"""
    key = normalize(value)
    accepted = {key} if key else set()
    if key in _YES_GROUP:
        accepted |= {normalize(x) for x in _YES_GROUP}
    if key in _NO_GROUP:
        accepted |= {normalize(x) for x in _NO_GROUP}
    for k, values in (synonyms or {}).items():
        if normalize(k) == key:
            accepted |= {normalize(v) for v in values if v}
    accepted.discard("")
    return accepted


def option_score(value, option_text, synonyms: Mapping[str, Iterable[str]] | None = None) -> float:
    """profile 里的值 与 单选/下拉选项文本 的匹配分。"""
    option = normalize(option_text)
    if not option:
        return 0.0
    best = 0.0
    for cand in _expand_value(str(value), synonyms or {}):
        if cand == option:
            return 1.0
        if _clean_contains(cand, option) or _clean_contains(option, cand):
            shorter, longer = (cand, option) if len(cand) <= len(option) else (option, cand)
            cover = len(shorter) / len(longer)
            if cover >= 0.5:
                best = max(best, 0.80 + 0.15 * cover)
            continue
        ratio = SequenceMatcher(None, cand, option).ratio()
        if ratio >= 0.8:
            best = max(best, 0.50 + 0.30 * (ratio - 0.8) / 0.2)
    return best


def contains_any(haystack, needles: Iterable[str]) -> str | None:
    """haystack 里是否出现 needles 中任意一个（归一化后包含）。返回命中的那个。"""
    text = normalize(haystack)
    if not text:
        return None
    for needle in needles or []:
        n = normalize(needle)
        if n and _clean_contains(n, text):
            return needle
    return None
