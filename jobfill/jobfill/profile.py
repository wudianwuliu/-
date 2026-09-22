# -*- coding: utf-8 -*-
"""把 profile.yaml 摊平成可匹配的字段列表，并提供缺失字段体检。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterator

# 顶层分组 → field_map.yaml 里 fields 键名的前缀
GROUP_OF_TOP = {
    "basic": "basic",
    "intention": "intention",
    "education": "education",
    "internships": "internship",
    "projects": "project",
    "scores": "scores",
    "skills": "skills",
    "languages": "language",
    "family": "family",
    "emergency": "emergency",
    "misc": "misc",
}

# 这些顶层键是「列表：每个元素是一段经历」
LIST_TOPS = {"education", "internships", "projects", "family"}

_TEMPLATE_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


@dataclass
class Field:
    """一个待填的值。"""

    key: str                    # field_map 里的键，如 basic.name / project.name
    path: str                   # profile 里的路径，如 basic.name / projects[0].name
    value: str
    group: str                  # basic / education / project ...
    index: int | None           # 重复块的序号（第 0 段教育经历 / 第 1 段）
    aliases: list[str]
    exclude: list[str]
    widget: str                 # text / textarea / choice / date / file
    expected_sections: list[str]

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志
        idx = "" if self.index is None else f"[{self.index}]"
        return f"{self.group}{idx}.{self.key.split('.')[-1]}={self.value}"


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        v = value.strip()
        return v == "" or v.lower() in {"null", "none", "todo", "待补充", "unknown"}
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def as_text(value: Any) -> str:
    """把 YAML 里的值转成要填进输入框的字符串。"""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "、".join(as_text(v) for v in value)
    return str(value).strip()


def _iter_leaves(top: str, payload: Any) -> Iterator[tuple[str, Any, int | None, str]]:
    """产出 (字段名, 值, 重复块序号, 完整路径)。"""
    if isinstance(payload, list):
        for i, item in enumerate(payload):
            if isinstance(item, dict):
                for k, v in item.items():
                    if isinstance(v, (list, dict)):
                        continue
                    yield k, v, i, f"{top}[{i}].{k}"
        return
    if isinstance(payload, dict):
        for k, v in payload.items():
            if isinstance(v, dict):
                for k2, v2 in v.items():
                    if isinstance(v2, (list, dict)):
                        continue
                    yield k2, v2, None, f"{top}.{k}.{k2}"
            elif isinstance(v, list):
                for i, item in enumerate(v):
                    if not isinstance(item, dict):
                        yield k, item, None, f"{top}.{k}[{i}]"
                        continue
                    for k2, v2 in item.items():
                        if isinstance(v2, (list, dict)):
                            continue
                        yield k2, v2, i, f"{top}.{k}[{i}].{k2}"
            else:
                yield k, v, None, f"{top}.{k}"


def flatten(profile: dict, field_map: dict) -> tuple[list[Field], list[str]]:
    """把 profile 摊平成 Field 列表；同时返回「没写进 field_map」的提醒。"""
    warnings: list[str] = []
    fields_def: dict = field_map.get("fields") or {}
    group_sections: dict = field_map.get("group_sections") or {}
    out: list[Field] = []

    for top, payload in (profile or {}).items():
        group = GROUP_OF_TOP.get(top)
        if not group or _is_blank(payload):
            continue
        expected = list(group_sections.get(group) or [])
        for key, value, index, path in _iter_leaves(top, payload):
            if _is_blank(value):
                continue
            full_key = f"{group}.{key}"
            spec = fields_def.get(full_key)
            if spec is None:
                warnings.append(f"{path}：field_map.yaml 里没有 {full_key} 条目，只能靠近似匹配")
                spec = {}
            out.append(
                Field(
                    key=full_key,
                    path=path,
                    value=as_text(value),
                    group=group,
                    index=index,
                    aliases=[a for a in (spec.get("aliases") or [key]) if a],
                    exclude=[e for e in (spec.get("exclude") or []) if e],
                    widget=spec.get("widget") or "text",
                    expected_sections=expected,
                )
            )
    return out, warnings


def resolve_path(profile: dict, path: str) -> Any:
    """按 'education[0].school' 这样的路径取值，取不到返回 None。"""
    cur: Any = profile
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if part.startswith("["):
            idx = int(part[1:-1])
            if not isinstance(cur, list) or idx >= len(cur):
                return None
            cur = cur[idx]
        else:
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
    return cur


def render_template(text: str, profile: dict) -> str:
    """把 {{basic.name}} 这类占位符换成 profile 里的值。"""

    def repl(match: re.Match) -> str:
        value = resolve_path(profile, match.group(1))
        if value is None:
            return ""
        return as_text(value)

    return _TEMPLATE_RE.sub(repl, text or "")


def missing_fields(profile: dict) -> list[str]:
    """体检：列出 profile 里还没填（null / 空列表）的字段路径。"""
    missing: list[str] = []

    def walk(node: Any, prefix: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{prefix}.{k}" if prefix else str(k))
        elif isinstance(node, list):
            if not node:
                missing.append(f"{prefix}  (空列表)")
            for i, item in enumerate(node):
                walk(item, f"{prefix}[{i}]")
        elif node is None or (isinstance(node, str) and node.strip().lower() in {"", "null", "todo"}):
            missing.append(prefix)

    walk(profile, "")
    return missing
