# -*- coding: utf-8 -*-
"""填表报告：终端里看得清，落盘可回溯。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import config

ICON = {
    "verified": "✔",
    "filled": "✔",
    "needs_manual": "!",
    "skipped": "-",
    "failed": "×",
}


def print_outcomes(outcomes, log=print, show_values: bool = True) -> None:
    if not outcomes:
        log("（没有可填的字段）")
        return
    current = None
    for out in sorted(outcomes, key=lambda o: (o.section or "", o.uid)):
        if out.section != current:
            current = out.section
            log("")
            log(f"── {current or '（无区块）'} " + "─" * 30)
        icon = ICON.get(out.status, "?")
        value = out.value or ""
        if show_values and len(value) > 46:
            value = value[:46] + "…"
        line = f"  {icon} {out.label[:26]:<28} {value}"
        if out.note:
            line += f"    [{out.note}]"
        log(line)


def print_summary(outcomes, unmatched, log=print) -> dict:
    counts: dict[str, int] = {}
    for out in outcomes:
        counts[out.status] = counts.get(out.status, 0) + 1
    log("")
    log("═" * 62)
    log(
        "填表结果："
        f"成功 {counts.get('verified', 0) + counts.get('filled', 0)}"
        f" | 需手动 {counts.get('needs_manual', 0)}"
        f" | 失败 {counts.get('failed', 0)}"
        f" | 跳过 {counts.get('skipped', 0)}"
        f" | 未匹配控件 {len(unmatched)}"
    )
    need_manual = [o for o in outcomes if o.status in ("needs_manual", "failed")]
    if need_manual:
        log("")
        log("需要你手动处理的字段：")
        for o in need_manual:
            log(f"  · {o.label}（{o.section or '无区块'}）：{o.note or o.value}")
    return counts


def print_unmatched(unmatched, log=print, limit: int = 40) -> None:
    if not unmatched:
        return
    log("")
    log(f"以下 {len(unmatched)} 个控件没有对应到 profile 里的数据（多为验证码、文件上传、系统计算字段）：")
    for c in unmatched[:limit]:
        tags = []
        if c.required:
            tags.append("必填")
        if c.option_texts:
            tags.append("选项题")
        line = f"  · {c.display_label[:30]:<32} {c.kind:<10} {'/'.join(tags)}"
        if c.section:
            line += f"  区块：{c.section}"
        log(line)
    if len(unmatched) > limit:
        log(f"  …… 还有 {len(unmatched) - limit} 个，详见报告 JSON")


def save_report(report: dict, report_dir: Path | None = None) -> Path:
    target_dir = Path(report_dir or config.REPORT_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.get("started") or datetime.now().strftime("%Y%m%d_%H%M%S")
    path = target_dir / f"fill_{stamp}.json"
    with path.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    return path
