# -*- coding: utf-8 -*-
"""字段与控件的匹配：决定「哪个值填到哪个框里」。

匹配优先级（高 → 低）：
  1. learned.yaml 里你亲手确认过的答案（标签精确对上，永远优先）
  2. profile 字段，标签几乎完全对上（≥0.85）
  3. 开放题库（只对长文本框生效）
  4. profile 字段，模糊对上（≥0.55）

安全阀：
  * exclude 关键词：任一候选标签命中就否决（「姓名」绝不填进「父亲姓名」）
  * danger_sections：「家庭/紧急联系人」区块默认不碰
  * ignore_labels：验证码、密码之类的标签直接跳过
  * 同一段重复块里，一个字段只填一个控件（不会把手机号填两遍）
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any

from .dom import Control
from .normalize import contains_any, label_score, normalize
from .profile import Field, render_template

THRESHOLD = 0.55
STRONG = 0.85


@dataclass
class Assignment:
    control: Control
    value: str
    source: str                       # profile:路径 / learned:标签 / open:题目id
    score: float
    field: Field | None = None
    question_id: str | None = None
    note: str = ""


@dataclass
class MatchContext:
    profile: dict = dc_field(default_factory=dict)
    field_map: dict = dc_field(default_factory=dict)
    learned: dict = dc_field(default_factory=dict)
    questions: list[dict] = dc_field(default_factory=list)

    @property
    def ignore_labels(self) -> list[str]:
        return list(self.field_map.get("ignore_labels") or [])

    @property
    def danger_sections(self) -> list[str]:
        return list(self.field_map.get("danger_sections") or [])

    @property
    def value_synonyms(self) -> dict:
        return dict(self.field_map.get("value_synonyms") or {})


def truncate(text: str, limit: int | None) -> str:
    """按 maxlength 截断，尽量断在句子或子句边界上。"""
    if not text:
        return text
    if not limit or limit <= 0 or len(text) <= limit:
        return text
    cut = text[:limit]
    for mark in ("。", "；", "！", "？", "\n", "，", "、"):
        pos = cut.rfind(mark)
        if pos >= int(limit * 0.7):
            return cut[: pos + 1]
    return cut


def _label_candidates(control: Control) -> list[str]:
    cands = [l for l in control.labels if l and l.strip()]
    if not cands and control.placeholder:
        cands = [control.placeholder]
    return cands


def _in_danger_section(control: Control, danger: list[str]) -> bool:
    if not control.section:
        return False
    return contains_any(control.section, danger) is not None


def _hit_ignore(control: Control, ignore_labels: list[str]) -> str | None:
    for lbl in _label_candidates(control):
        hit = contains_any(lbl, ignore_labels)
        if hit:
            return hit
    return None


def _widget_affinity(field: Field, control: Control) -> float:
    """控件形态与字段预期是否吻合。"""
    want, kind = field.widget, control.kind
    if want == "file":
        return 0.30 if kind == "file" else -0.40
    if kind == "file":
        return -0.40
    if want == "textarea":
        if kind == "textarea":
            return 0.05
        if kind == "text":
            return -0.15
        return -0.30
    if want == "date":
        return 0.05 if kind in ("date", "month", "datetime-local") else 0.0
    if want == "choice":
        return 0.05 if kind in ("select", "multiselect", "radio") else 0.0
    if kind == "textarea":
        return -0.05
    return 0.0


def score_field(field: Field, control: Control, ctx: MatchContext) -> tuple[float, str]:
    """算一个字段能填进这个控件的分数，返回 (分数, 命中的标签)。"""
    if control.kind in ("password", "hidden"):
        return 0.0, ""
    labels = _label_candidates(control)
    if not labels:
        return 0.0, ""
    if _hit_ignore(control, ctx.ignore_labels):
        return 0.0, ""

    # 排除词：任一候选标签命中就否决
    for excl in field.exclude:
        ne = normalize(excl)
        if not ne:
            continue
        for lbl in labels:
            if ne in normalize(lbl):
                return 0.0, ""

    # 家庭/紧急联系人区块默认不碰
    if _in_danger_section(control, ctx.danger_sections) and field.group not in ("family", "emergency"):
        return 0.0, ""

    best, best_label = 0.0, ""
    for lbl in labels:
        for alias in field.aliases:
            s = label_score(lbl, alias)
            if s > best:
                best, best_label = s, lbl
    if best <= 0.0:
        return 0.0, ""

    # 区块吻合加成。注意这里**不封顶到 1.0**：
    # 「开始时间」在教育经历和项目经历里都会 1.0 命中，靠这点加成才能选出对的那个。
    if field.expected_sections and control.section:
        if contains_any(control.section, field.expected_sections):
            best = best + 0.12

    best = max(0.0, best + _widget_affinity(field, control))

    # 重复块序号吻合加成（教育经历第 0 段 → 表单第 0 段）
    if field.index is not None and control.record_index == field.index:
        best += 0.05

    return best, best_label


def learned_answer(control: Control, learned: dict) -> tuple[str, str] | None:
    """查 learned.yaml：标签对上就直接用你确认过的答案。"""
    labels = [normalize(l) for l in _label_candidates(control)]
    if not labels:
        return None
    for key, value in (learned or {}).items():
        nk = normalize(key)
        if not nk:
            continue
        for nl in labels:
            if nl == nk:
                return str(value), key
            # 短标签只认精确匹配，避免「姓名」这种词误伤
            if len(nk) >= 3 and (nk in nl or nl in nk):
                return str(value), key
    return None


def question_for(control: Control, questions: list[dict]) -> dict | None:
    """给长文本框找一道开放题。只对 textarea / 长文本生效。"""
    is_long_text = control.kind == "textarea" or (
        control.kind == "text" and (control.maxlength or 0) >= 100
    )
    if not is_long_text:
        return None
    for q in questions or []:
        for lbl in _label_candidates(control):
            hit = contains_any(lbl, q.get("match") or [])
            if hit:
                return q
    return None


def build_assignments(
    fields: list[Field], controls: list[Control], ctx: MatchContext
) -> tuple[dict[int, Assignment], list[Control], list[str]]:
    """返回 (控件uid → 分配, 未分配控件, 提示信息)。"""
    notes: list[str] = []
    by_uid = {c.uid: c for c in controls}

    # 1) learned 优先
    learned_hits: dict[int, tuple[str, str]] = {}
    for c in controls:
        hit = learned_answer(c, ctx.learned)
        if hit:
            learned_hits[c.uid] = hit

    # 2) profile 全局贪心分配
    # 同一个字段**允许多次使用**（手机号 / 确认手机号、省市区级联下拉都靠这个），
    # 但第二次起必须是强匹配（≥0.85），且总次数不超过 MAX_USES，防止乱填扩散。
    pairs: list[tuple[float, Field, Control, str]] = []
    for f in fields:
        for c in controls:
            if c.uid in learned_hits:
                continue
            s, lbl = score_field(f, c, ctx)
            if s >= THRESHOLD:
                pairs.append((s, f, c, lbl))
    pairs.sort(key=lambda t: (-t[0], t[1].path))

    MAX_USES = 4
    used_control: set[int] = set()
    use_count: dict[str, int] = {}
    assignments: dict[int, Assignment] = {}
    for s, f, c, lbl in pairs:
        if c.uid in used_control:
            continue
        n = use_count.get(f.path, 0)
        if n >= MAX_USES:
            continue
        if n > 0 and s < STRONG:
            continue
        used_control.add(c.uid)
        use_count[f.path] = n + 1
        c.matched_label = lbl
        assignments[c.uid] = Assignment(
            control=c,
            value=truncate(f.value, c.maxlength),
            source=f"profile:{f.path}",
            score=s,
            field=f,
        )

    # 3) learned 覆盖（显式确认过的，优先于模糊匹配）
    for uid, (val, key) in learned_hits.items():
        c = by_uid.get(uid)
        if c is None:
            continue
        assignments[uid] = Assignment(
            control=c,
            value=truncate(val, c.maxlength),
            source=f"learned:{key}",
            score=1.0,
        )

    # 4) 开放题补齐
    for c in controls:
        if c.uid in assignments or c.kind in ("password", "hidden"):
            continue
        q = question_for(c, ctx.questions)
        if not q:
            continue
        answer = render_template(str(q.get("answer") or ""), ctx.profile).strip()
        if not answer:
            continue
        limit = c.maxlength or q.get("max_len")
        c.matched_label = c.matched_label or c.display_label
        assignments[c.uid] = Assignment(
            control=c,
            value=truncate(answer, limit),
            source=f"open:{q.get('id')}",
            score=0.90,
            question_id=str(q.get("id") or ""),
            note="开放题（提交前自己读一遍）",
        )

    unmatched = [c for c in controls if c.uid not in assignments and c.kind not in ("password",)]
    return assignments, unmatched, notes
