# -*- coding: utf-8 -*-
"""页面控件模型，以及「从浏览器里把控件清单抽出来」这件事。

抽取在浏览器端做（extract.js），这里负责：
  * 逐帧抽取（北森的表单在 iframe 里）
  * 把 JS 返回的裸对象转成 Control 数据类
  * 提供回读值 / 清理标记 / 重复块计数等辅助
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

_EXTRACT_JS = (Path(__file__).with_name("extract.js")).read_text(encoding="utf-8")

# 清理我们打在页面上的临时标记
_CLEAR_JS = """() => {
  let n = 0;
  document.querySelectorAll('[data-jobfill-id]').forEach((el) => {
    el.removeAttribute('data-jobfill-id');
    n++;
  });
  return n;
}"""

# 批量回读控件当前值（用于填完后校验）
_READ_JS = """(uids) => {
  const res = {};
  for (const uid of uids) {
    const el = document.querySelector('[data-jobfill-id="' + uid + '"]');
    if (!el) { res[uid] = null; continue; }
    const tag = el.tagName.toLowerCase();
    if (tag === 'select') {
      const opts = Array.from(el.selectedOptions || []);
      res[uid] = opts.length ? opts.map((o) => (o.textContent || '').trim()).join('、') : '';
    } else if (el.type === 'radio' || el.type === 'checkbox') {
      res[uid] = el.checked ? 'CHECKED' : '';
    } else if (el.type === 'file') {
      res[uid] = el.files && el.files.length ? el.files[0].name : '';
    } else {
      res[uid] = el.value == null ? '' : String(el.value);
    }
  }
  return res;
}"""

# 页面上「添加一条」类按钮的文本白名单（绝不包含提交/保存/下一步）
ADD_BUTTON_TEXTS = ["添加", "新增", "+ 添加", "+添加", "添加一条", "增加", "新增一条", "添加一行"]

# 安全闸门：任何情况下都不点这些
SUBMIT_WORDS = ["提交", "保存", "投递", "确认", "确定", "下一步", "完成", "删除", "移除", "重置", "清空", "注销", "退出"]


@dataclass
class Option:
    uid: int
    text: str
    value: str = ""
    selected: bool = False
    disabled: bool = False


@dataclass
class Control:
    """页面上的一个可填控件。"""

    uid: int
    frame_key: int
    kind: str                       # text / textarea / select / multiselect / radio / checkbox / date / file / password
    tag: str = ""
    type: str = ""
    name: str = ""
    labels: list[str] = dc_field(default_factory=list)
    section: str = ""
    block_index: int = 0
    block_count: int = 1
    name_index: int | None = None   # 从 name/id 里解析出的记录序号（如 eduSchool0 → 0）
    required: bool = False
    disabled: bool = False
    readonly: bool = False
    maxlength: int | None = None
    placeholder: str = ""
    widget_guess: str = ""
    value: str = ""
    options: list[Option] = dc_field(default_factory=list)
    frame: Any = None               # 活对象，仅内部使用，不写进报告
    matched_label: str = ""         # 匹配成功后记录，方便排查

    @property
    def display_label(self) -> str:
        if self.matched_label:
            return self.matched_label
        for lbl in self.labels:
            if lbl and lbl.strip():
                return lbl.strip()
        return self.placeholder or self.name or f"({self.kind}#{self.uid})"

    @property
    def option_texts(self) -> list[str]:
        return [o.text for o in self.options if (o.text or "").strip()]

    @property
    def record_index(self) -> int:
        """这是第几「段」记录（教育经历第 0 段 / 项目第 1 段）。

        优先用 name/id 里的数字后缀（eduSchool0 → 0，最可靠），
        拿不到才用结构推断的 block_index。
        """
        return self.name_index if self.name_index is not None else self.block_index

    def to_row(self) -> dict:
        """给报告/扫描输出用的纯数据（不含 frame）。"""
        return {
            "uid": self.uid,
            "frame": self.frame_key,
            "kind": self.kind,
            "section": self.section,
            "label": self.display_label,
            "labels": list(self.labels),
            "name": self.name,
            "required": self.required,
            "disabled": self.disabled,
            "readonly": self.readonly,
            "block_index": self.block_index,
            "block_count": self.block_count,
            "name_index": self.name_index,
            "maxlength": self.maxlength,
            "value": self.value,
            "options": self.option_texts,
        }


@dataclass
class ScanResult:
    controls: list[Control] = dc_field(default_factory=list)
    frames: list[dict] = dc_field(default_factory=list)      # [{key, url, count}]
    notes: list[str] = dc_field(default_factory=list)


def _to_option(raw: dict) -> Option:
    return Option(
        uid=int(raw.get("uid", 0)),
        text=(raw.get("text") or "").strip(),
        value=str(raw.get("value") or ""),
        selected=bool(raw.get("selected")),
        disabled=bool(raw.get("disabled")),
    )


def _to_control(raw: dict, frame_key: int, frame: Any) -> Control:
    name_index_raw = raw.get("nameIndex")
    try:
        name_index = int(name_index_raw) if name_index_raw is not None else None
    except (TypeError, ValueError):
        name_index = None
    return Control(
        uid=int(raw.get("uid", 0)),
        frame_key=frame_key,
        kind=str(raw.get("kind") or "text"),
        tag=str(raw.get("tag") or ""),
        type=str(raw.get("type") or ""),
        name=str(raw.get("name") or ""),
        labels=[str(x).strip() for x in (raw.get("labels") or []) if str(x).strip()],
        section=str(raw.get("section") or "").strip(),
        block_index=int(raw.get("blockIndex") or 0),
        block_count=int(raw.get("blockCount") or 1),
        name_index=name_index,
        required=bool(raw.get("required")),
        disabled=bool(raw.get("disabled")),
        readonly=bool(raw.get("readonly")),
        maxlength=(int(raw["maxlength"]) if raw.get("maxlength") else None),
        placeholder=str(raw.get("placeholder") or ""),
        widget_guess=str(raw.get("widgetGuess") or ""),
        value=str(raw.get("value") or ""),
        options=[_to_option(o) for o in (raw.get("options") or [])],
        frame=frame,
    )


def scan_page(page) -> ScanResult:
    """遍历所有 frame，抽出全部控件。"""
    result = ScanResult()
    for key, frame in enumerate(page.frames):
        url = ""
        try:
            url = frame.url or ""
        except Exception:
            pass
        if url.startswith("devtools://") or url.startswith("chrome-extension://"):
            continue
        try:
            data = frame.evaluate(_EXTRACT_JS)
        except Exception as exc:                      # 跨域/已销毁的帧
            result.notes.append(f"frame#{key} 抽取跳过：{type(exc).__name__}: {exc}")
            continue
        if not isinstance(data, dict):
            continue
        if not data.get("ok"):
            err = str(data.get("error") or "")[:300]
            if err:
                result.notes.append(f"frame#{key} 抽取器报错：{err}")
            continue
        items = data.get("controls") or []
        if not items:
            continue
        result.frames.append({"key": key, "url": url, "count": len(items)})
        for raw in items:
            result.controls.append(_to_control(raw, key, frame))
    return result


def clear_marks(page) -> int:
    """清掉 data-jobfill-id 标记，别给别人的页面留垃圾。"""
    total = 0
    for frame in page.frames:
        try:
            total += int(frame.evaluate(_CLEAR_JS) or 0)
        except Exception:
            continue
    return total


def read_values(controls: list[Control]) -> dict[int, Any]:
    """按帧批量回读控件的当前值。"""
    by_frame: dict[int, list[Control]] = {}
    for c in controls:
        by_frame.setdefault(c.frame_key, []).append(c)
    out: dict[int, Any] = {}
    for _, group in by_frame.items():
        frame = group[0].frame
        if frame is None:
            continue
        uids = [c.uid for c in group]
        try:
            res = frame.evaluate(_READ_JS, uids)
        except Exception:
            continue
        if isinstance(res, dict):
            for k, v in res.items():
                try:
                    out[int(k)] = v
                except (TypeError, ValueError):
                    continue
    return out


def read_selected_labels(control: Control) -> list[str]:
    """回读某个单选/复选组当前被选中的选项文本。"""
    if control.frame is None or not control.options:
        return []
    uids = [o.uid for o in control.options]
    try:
        res = control.frame.evaluate(_READ_JS, uids)
    except Exception:
        return []
    picked = []
    if isinstance(res, dict):
        for opt in control.options:
            if res.get(str(opt.uid)) or res.get(opt.uid):
                picked.append(opt.text)
    return picked


def count_blocks(controls: list[Control], section: str) -> int:
    """某个区块里当前有几段重复块。"""
    best = 0
    for c in controls:
        if section and c.section and section in c.section:
            best = max(best, c.block_count)
    return best
