# -*- coding: utf-8 -*-
"""未识别字段的交互式学习。

这是整个工具「越用越省事」的关键：
网申表单每家都问同样的一百来个问题，只是叫法不同。
第一次遇到「身高(cm)」你答一次，之后所有公司都自动填。
"""

from __future__ import annotations

from pathlib import Path

from . import config

HEADER = """# ============================================================================
# 学习记录：工具没认出来的字段，你确认过的答案都存在这里。
# ----------------------------------------------------------------------------
# answers: 标签 → 值。下次遇到同样的（或包含同样文字的）标签，直接用这个值，
#          优先级高于 profile.yaml 和 field_map.yaml。
# ignore:  永远不要填的标签（验证码提示词之类的误伤可以加到这里）。
#
# 这个文件是工具自己写的，你手动加也完全可以（键就是表单上看到的标签）。
# ============================================================================
"""


def load_learned(path: str | Path | None = None) -> dict:
    data = config.load_yaml(path or config.LEARNED, default={})
    if not isinstance(data, dict):
        data = {}
    answers = data.get("answers")
    if not isinstance(answers, dict):
        answers = {}
    ignore = data.get("ignore")
    if not isinstance(ignore, list):
        ignore = []
    return {"answers": answers, "ignore": ignore}


def save_learned(data: dict, path: str | Path | None = None) -> Path:
    target = Path(path or config.LEARNED)
    payload = {
        "answers": dict(sorted((data.get("answers") or {}).items())),
        "ignore": sorted(set(data.get("ignore") or [])),
    }
    config.save_yaml(target, payload, header=HEADER)
    return target


def interactive_learn(unmatched, learned: dict, log=print, input_fn=input) -> dict[int, str]:
    """逐个问未识别字段。返回 {控件uid: 值}，供调用方立即填进去。"""
    todo = [c for c in unmatched if c.kind not in ("password", "hidden")]
    if not todo:
        return {}

    log("")
    log(f"有 {len(todo)} 个字段没能自动识别。逐个人工确认一次，以后就都不用了：")
    log("  直接回车 = 这次跳过    ！xxx = 记住忽略这个标签    q = 结束提问")
    log("")

    answers: dict[int, str] = {}
    for c in todo:
        flags = []
        if c.required:
            flags.append("必填")
        if c.option_texts:
            flags.append("选项题")
        if c.maxlength:
            flags.append(f"限{c.maxlength}字")
        if c.readonly:
            flags.append("只读/日历控件")
        where = f"  区块：{c.section}" if c.section else ""
        log(f"[未识别] {c.display_label}    ({'/'.join(flags) or c.kind}){where}")
        if c.option_texts:
            log("    可选：" + "、".join(c.option_texts[:15]))
        try:
            raw = input_fn("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            log("  (已结束提问)")
            break
        if not raw:
            continue
        if raw.lower() in ("q", "quit", "exit"):
            break
        if raw.startswith("!") or raw.startswith("！"):
            label = raw[1:].strip() or c.display_label
            learned.setdefault("ignore", []).append(label)
            log(f"    · 已记住忽略「{label}」")
            continue
        learned.setdefault("answers", {})[c.display_label] = raw
        answers[c.uid] = raw
        log(f"    · 已记住：{c.display_label} = {raw}")

    if answers or learned.get("ignore"):
        try:
            path = save_learned(learned)
            log(f"  → 已写入 {path}")
        except Exception as exc:
            log(f"  ! 写入学习记录失败：{exc}")
    return answers
