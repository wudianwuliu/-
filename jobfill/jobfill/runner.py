# -*- coding: utf-8 -*-
"""填表主流程：准备页面 → 扫描 → 补重复块 → 匹配 → 填写 → 学习 → 出报告。

刻意保留的一步：**填完就停**。这里没有任何提交动作，
只有你自己确认过页面之后手动点提交。
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config
from . import report as report_mod
from .adapters import Adapter
from .dom import Control, clear_marks, scan_page
from .filler import FillOutcome, add_blocks, clear_add_marks, fill_one
from .learn import interactive_learn
from .matcher import Assignment, MatchContext, build_assignments
from .normalize import contains_any
from .profile import Field, flatten, missing_fields


@dataclass
class FillOptions:
    learn: bool = False
    yes: bool = False                 # 非交互模式：未识别字段直接跳过
    dry_run: bool = False
    resume_file: str | None = None
    photo_file: str | None = None
    only: str | None = None           # 只填区块名/标签里含这个词的字段
    add_blocks: bool = True
    max_add_per_section: int = 5
    screenshot: bool = True
    ask: Any = None                   # 提问函数（图形界面会把自己的对话框传进来）


def _max_blocks(controls: list[Control], aliases: list[str]) -> int:
    """某个区块里当前有几段记录。优先用 name/id 里解析出的序号。"""
    best = 0
    for c in controls:
        if c.section and contains_any(c.section, aliases):
            if c.name_index is not None:
                best = max(best, c.name_index + 1)
            else:
                best = max(best, c.block_count)
    return best


def ensure_repeat_blocks(
    page, profile: dict, adapter: Adapter, controls: list[Control], opts: FillOptions, log=print
) -> tuple[list[Control], int]:
    """教育经历/实习/项目是「可重复块」。不够就点白名单里的「添加」按钮补足。

    注意：dry-run 下**不点**。预览不应该改动页面。
    """
    if not opts.add_blocks or opts.dry_run:
        return controls, 0
    mapping = {
        "education": profile.get("education"),
        "internships": profile.get("internships"),
        "projects": profile.get("projects"),
        "family": profile.get("family"),
    }
    total_added = 0
    for key, entries in mapping.items():
        if not isinstance(entries, list) or not entries:
            continue
        aliases = adapter.block_sections.get(key) or []
        if not aliases:
            continue
        have = _max_blocks(controls, aliases)
        if have == 0 or have >= len(entries):
            continue
        want = min(len(entries) - have, opts.max_add_per_section)
        log(f"· {aliases[0]}：页面有 {have} 段，profile 有 {len(entries)} 段，补 {want} 段")
        added = add_blocks(page, aliases, have + want, have, log=log)
        total_added += added
        if added:
            page.wait_for_timeout(500)
            clear_marks(page)
            controls = scan_page(page).controls
    return controls, total_added


def _file_fields(opts: FillOptions, profile: dict, log=print) -> list[Field]:
    """把「要上传的文件」也变成字段，这样上传目标的识别走同一套标签匹配。

    简历附件、证件照这类控件本来就没有 profile 数据可依赖，
    它们的「值」就是文件路径本身。
    """
    out: list[Field] = []
    base_dir = Path(config.DEFAULT_PROFILE).parent

    def resolve(raw: str) -> Path:
        p = Path(str(raw))
        return p if p.is_absolute() else (base_dir / p)

    if opts.resume_file:
        p = resolve(opts.resume_file)
        if p.exists():
            out.append(
                Field(
                    key="file.resume",
                    path="file.resume",
                    value=str(p),
                    group="file",
                    index=None,
                    aliases=["简历附件", "个人简历", "简历文件", "上传简历", "简历", "附件", "中文简历"],
                    exclude=["证书", "成绩单", "照片"],
                    widget="file",
                    expected_sections=[],
                )
            )
        else:
            log(f"! --resume-file 指定的文件不存在：{p}")

    photo_raw = opts.photo_file or str((profile.get("basic") or {}).get("photo") or "")
    if photo_raw:
        p = resolve(photo_raw)
        # 显式传了的文件即使不存在也要报出来；profile 里的默认路径不存在就静默跳过
        if p.exists() or opts.photo_file:
            out.append(
                Field(
                    key="file.photo",
                    path="file.photo",
                    value=str(p),
                    group="file",
                    index=None,
                    aliases=["证件照", "个人照片", "一寸照片", "照片", "相片", "头像"],
                    exclude=["简历"],
                    widget="file",
                    expected_sections=[],
                )
            )
    return out


def run_fill(
    page,
    profile: dict,
    field_map: dict,
    learned: dict,
    questions: list[dict],
    adapter: Adapter,
    opts: FillOptions,
    log=print,
) -> dict:
    started = datetime.now().strftime("%Y%m%d_%H%M%S")
    log(f"═ 适配器：{adapter.name}（{adapter.description}）")

    # 1) 页面准备（关提示弹窗、滚动触发懒加载）
    adapter.prepare(page, log)

    # 2) 扫描
    clear_marks(page)
    scan = scan_page(page)
    log(f"· 扫描到 {len(scan.controls)} 个可填控件，分布在 {len(scan.frames)} 个 frame 中")
    for note in scan.notes[:6]:
        log(f"  ! {note}")

    missing = [m for m in missing_fields(profile)]
    if missing:
        log(f"· profile 里有 {len(missing)} 处还没填（null），这些字段会被跳过：")
        for m in missing[:12]:
            log(f"    - {m}")
        if len(missing) > 12:
            log(f"    …… 还有 {len(missing) - 12} 处")

    # 3) 补重复块（补完必须重新扫描，因为控件变了）
    controls, added = ensure_repeat_blocks(page, profile, adapter, scan.controls, opts, log)
    if added:
        log(f"· 共新增 {added} 段，重新扫描")
    if not controls:
        log("！这个页面上没找到任何可填控件。常见原因：")
        log("  · 表单在 iframe 里还没加载完 → 等页面完全出来再跑一次")
        log("  · 当前是列表页/首页，还没进入「填写简历」页面")
        log("  · 需要先点「马上申请/立即投递」才会出现表单")

    # 4) 匹配
    fields, warns = flatten(profile, field_map)
    # basic.photo 交给 _file_fields 统一处理（文件路径要解析成绝对路径）
    fields = [f for f in fields if f.path != "basic.photo"]
    fields.extend(_file_fields(opts, profile, log))
    ctx = MatchContext(
        profile=profile,
        field_map=field_map,
        learned=dict(learned.get("answers") or {}),
        questions=questions,
    )
    assignments, unmatched, _notes = build_assignments(fields, controls, ctx)
    log(f"· profile 提供 {len(fields)} 个字段，匹配上 {len(assignments)} 个控件，剩余 {len(unmatched)} 个")

    if opts.only:
        keep = {}
        for uid, a in assignments.items():
            hay = f"{a.control.section} {a.control.display_label}"
            if opts.only in hay:
                keep[uid] = a
        log(f"· --only {opts.only}：只填 {len(keep)} 个字段")
        assignments = keep

    if opts.dry_run:
        log("")
        log("（dry-run：只匹配不填写，下面是匹配结果）")
        for a in sorted(assignments.values(), key=lambda x: (x.control.frame_key, x.control.uid)):
            log(f"  → {a.control.display_label[:26]:<28} ← {a.value[:40]:<42} [{a.source}]")
        return {
            "started": started,
            "dry_run": True,
            "adapter": adapter.name,
            "url": page.url,
            "controls": [c.to_row() for c in controls],
            "matched": [
                {"label": a.control.display_label, "value": a.value, "source": a.source, "score": round(a.score, 3)}
                for a in assignments.values()
            ],
            "unmatched": [c.to_row() for c in unmatched],
        }

    # 5) 填写（按文档顺序，保证省市区这类级联下拉的先后关系）
    outcomes: list[FillOutcome] = []
    ordered = sorted(assignments.values(), key=lambda a: (a.control.frame_key, a.control.uid))
    for a in ordered:
        out = fill_one(a, ctx, {"resume_file": opts.resume_file, "photo_file": opts.photo_file})
        outcomes.append(out)
        # 下拉/单选填完后可能触发联动（省→市→区），给页面一点时间
        if a.control.kind in ("select", "multiselect", "radio"):
            try:
                a.control.frame.wait_for_timeout(120)
            except Exception:
                pass

    # 6) 未识别字段的人工学习
    learned_new: dict[int, str] = {}
    if opts.learn and unmatched and not opts.yes:
        learned_new = interactive_learn(unmatched, learned, log=log, input_fn=opts.ask or input)
        for uid, value in learned_new.items():
            ctrl = next((c for c in controls if c.uid == uid), None)
            if ctrl is None:
                continue
            fake = Assignment(control=ctrl, value=value, source="learned(本次)", score=1.0)
            outcomes.append(fill_one(fake, ctx, {"resume_file": opts.resume_file, "photo_file": opts.photo_file}))
        # 学到的内容可能让原本未匹配的控件有解了，重新算一遍剩余项
        if learned_new:
            refreshed, unmatched, _ = build_assignments(fields, controls, ctx)
            done = {o.uid for o in outcomes}
            unmatched = [c for c in unmatched if c.uid not in done]

    # 7) 报告
    report_mod.print_outcomes(outcomes, log)
    counts = report_mod.print_summary(outcomes, unmatched, log)
    report_mod.print_unmatched(unmatched, log)

    shot: str | None = None
    if opts.screenshot:
        try:
            target_dir = Path(config.REPORT_DIR)
            target_dir.mkdir(parents=True, exist_ok=True)
            shot_path = target_dir / f"fill_{started}.png"
            try:
                page.screenshot(path=str(shot_path), full_page=True, timeout=15000)
            except Exception:
                page.screenshot(path=str(shot_path), timeout=10000)
            shot = str(shot_path)
            log(f"· 页面截图：{shot}")
        except Exception as exc:
            log(f"! 截图失败：{exc}")

    payload = {
        "started": started,
        "adapter": adapter.name,
        "url": page.url,
        "counts": counts,
        "frames": scan.frames,
        "outcomes": [o.__dict__ for o in outcomes],
        "unmatched": [c.to_row() for c in unmatched],
        "profile_missing": missing,
        "screenshot": shot,
        "blocks_added": added,
        "learned_now": list(learned_new.values()),
    }
    try:
        path = report_mod.save_report(payload)
        log(f"· 报告：{path}")
    except Exception as exc:
        log(f"! 报告写入失败：{exc}")

    log("")
    log("填完了。请自己核对一遍，确认无误后再手动点提交 —— 本工具不会替你提交。")
    return payload


def cleanup(page) -> None:
    """收尾：清掉打在页面上的临时标记。"""
    try:
        clear_marks(page)
    except Exception:
        pass
    try:
        clear_add_marks(page)
    except Exception:
        pass
