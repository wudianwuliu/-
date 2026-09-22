# -*- coding: utf-8 -*-
"""命令行入口。

    py -3 -m jobfill check                 # 资料体检
    py -3 -m jobfill scan  --url ...       # 只扫描页面，列出所有字段
    py -3 -m jobfill fill  --url ...       # 填表（--dry-run 只预览不填）
    py -3 -m jobfill launch-info           # 怎么启动带调试端口的 Chrome
"""

from __future__ import annotations

import argparse
import json
import sys

from . import config
from .adapters import get_adapter, merge_field_map
from .browser import open_session
from .learn import load_learned, save_learned
from .profile import flatten, missing_fields
from .report import print_outcomes, print_summary, print_unmatched
from .runner import FillOptions, cleanup, run_fill


def load_context(profile_path=None, adapter_hint=None, learned_path=None):
    """加载 profile / 识别表 / 学习记录 / 开放题 / 适配器。命令行和图形界面共用这一处。"""
    profile = config.load_yaml(profile_path or config.DEFAULT_PROFILE, default={})
    if not profile:
        raise SystemExit(f"读不到 profile：{profile_path or config.DEFAULT_PROFILE}")

    adapter = get_adapter(adapter_hint)
    field_map = merge_field_map(config.load_yaml(config.FIELD_MAP, default={}), adapter)
    learned = load_learned(learned_path)
    questions = (config.load_yaml(config.OPEN_QUESTIONS, default={}) or {}).get("questions") or []

    # 学习记录里的「忽略」并进忽略词表
    if learned.get("ignore"):
        ignore = list(field_map.get("ignore_labels") or [])
        for item in learned["ignore"]:
            if item not in ignore:
                ignore.append(item)
        field_map["ignore_labels"] = ignore

    return profile, field_map, learned, questions, adapter


def _load(args):
    """命令行参数 → load_context 入参。没显式指定适配器时从 URL 猜（zhiye → 北森）。"""
    return load_context(
        getattr(args, "profile", None),
        getattr(args, "adapter", None) or getattr(args, "url", None),
        getattr(args, "learned", None),
    )


# --------------------------------------------------------------------- check
def cmd_check(args) -> int:
    profile, field_map, learned, questions, adapter = _load(args)
    ok = True

    print("═" * 62)
    print("网申工具 · 资料体检")
    print("═" * 62)

    missing = missing_fields(profile)
    total = len(flatten(profile, field_map)[0]) + len(missing)
    print(f"\n[profile] {config.DEFAULT_PROFILE}")
    print(f"  已填 {total - len(missing)} 项 / 待补充 {len(missing)} 项")
    if missing:
        print("\n  还没有值的字段（网申时会被跳过，建议补齐）：")
        for m in missing:
            print(f"    · {m}")

    fields, warns = flatten(profile, field_map)
    print(f"\n[识别表] field_map.yaml：{len(field_map.get('fields') or {})} 条字段定义")
    if warns:
        print(f"  以下 profile 字段没有对应定义（只能靠字形近似匹配）：")
        for w in warns:
            print(f"    · {w}")

    print(f"\n[开放题] {config.OPEN_QUESTIONS}：{len(questions)} 道")
    for q in questions:
        print(f"    · {q.get('id')}  (关键词 {len(q.get('match') or [])} 个)")

    answers = learned.get("answers") or {}
    print(f"\n[学习记录] {config.LEARNED}：已记住 {len(answers)} 个字段，忽略 {len(learned.get('ignore') or [])} 个")
    for k, v in list(answers.items())[:10]:
        print(f"    · {k} = {v}")

    print(f"\n[适配器] {adapter.name} —— {adapter.description}")

    port_open = config.check_chrome_debug_port()
    print(f"\n[Chrome 调试端口 9222] {'已就绪，可以直接 --attach 接管' if port_open else '未开启'}")
    if not port_open:
        print("  填表前先运行：  .\\launch_chrome.ps1")
        ok = False

    print("")
    if missing:
        print("提示：profile.yaml 里所有 null 都可以现在补上，补一次后面每家都能用。")
    return 0 if not missing else 1


# ---------------------------------------------------------------------- scan
def cmd_scan(args) -> int:
    profile, field_map, learned, questions, adapter = _load(args)
    session = open_session(
        url=args.url,
        attach=not args.launch,
        port=args.port,
        headless=args.headless,
        match_url=args.match_url,
        new_tab=args.new_tab,
        log=print,
    )
    try:
        adapter.prepare(session.page, print)
        from .dom import clear_marks, scan_page

        clear_marks(session.page)
        scan = scan_page(session.page)
        print("")
        print(f"共 {len(scan.controls)} 个控件，来自 {len(scan.frames)} 个 frame：")
        for f in scan.frames:
            print(f"  frame#{f['key']}  {f['count']:>4} 个   {f['url'][:90]}")
        for note in scan.notes:
            print(f"  ! {note}")
        print("")
        current = None
        for c in scan.controls:
            if c.section != current:
                current = c.section
                print(f"\n── {current or '（无区块）'} " + "─" * 30)
            tags = []
            if c.required:
                tags.append("必填")
            if c.options:
                tags.append(f"{len(c.options)}选项")
            if c.readonly:
                tags.append("只读")
            print(f"  #{c.uid:<4} {c.kind:<10} {c.display_label[:30]:<32} {'/'.join(tags)}")
            if c.options:
                print(f"          候选标签：{c.labels[:3]}")
        if args.out:
            payload = {
                "url": session.page.url,
                "frames": scan.frames,
                "controls": [c.to_row() for c in scan.controls],
            }
            with open(args.out, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            print(f"\n已写出：{args.out}")
        return 0
    finally:
        cleanup(session.page)
        session.close()


# ---------------------------------------------------------------------- fill
def cmd_fill(args) -> int:
    profile, field_map, learned, questions, adapter = _load(args)
    opts = FillOptions(
        learn=args.learn,
        yes=args.yes,
        dry_run=args.dry_run,
        resume_file=args.resume_file,
        photo_file=args.photo_file,
        only=args.only,
        add_blocks=not args.no_add_blocks,
        max_add_per_section=args.max_add,
        screenshot=not args.no_screenshot,
    )
    session = open_session(
        url=args.url,
        attach=not args.launch,
        port=args.port,
        headless=args.headless,
        match_url=args.match_url,
        new_tab=args.new_tab,
        log=print,
    )
    try:
        run_fill(session.page, profile, field_map, learned, questions, adapter, opts, log=print)
        if not args.dry_run and not args.no_pause:
            print("")
            print("页面保持打开状态，你可以直接在浏览器里改。")
            try:
                input("按回车结束本次（不会提交任何表单）…")
            except (EOFError, KeyboardInterrupt):
                pass
        return 0
    finally:
        cleanup(session.page)
        session.close()


# --------------------------------------------------------------- launch-info
def cmd_launch_info(args) -> int:
    print("接管 Chrome 填表的正确姿势：")
    print("")
    print("  1) 在本项目目录双击运行（或在 PowerShell 里执行）：")
    print("       .\\launch_chrome.ps1")
    print("     它会用独立配置目录启动 Chrome 并打开 9222 调试端口。")
    print("     独立配置目录的好处：不影响你日常的 Chrome，登录一次长期有效。")
    print("")
    print("  2) 在弹出的 Chrome 里正常登录网申系统，把表单页面停在当前标签页。")
    print("     （登录、短信验证码、滑块都由你自己完成，工具不碰。）")
    print("")
    print("  3) 回到终端执行：")
    print("       py -3 -m jobfill fill --url <表单页地址>")
    print("     或者不写 --url，直接对当前活动标签页操作。")
    print("")
    port_open = config.check_chrome_debug_port()
    print(f"当前 9222 端口：{'已开启 ✔' if port_open else '未开启 ✘'}")
    if port_open:
        try:
            import urllib.request

            with urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=2) as resp:
                info = json.loads(resp.read().decode("utf-8", "ignore"))
            print(f"  浏览器：{info.get('Browser')}")
            print(f"  调试地址：{info.get('webSocketDebuggerUrl', '')[:70]}")
        except Exception as exc:
            print(f"  （读取 /json/version 失败：{exc}）")
    return 0


# ------------------------------------------------------------------- learned
def cmd_learned(args) -> int:
    learned = load_learned(args.learned)
    answers = learned.get("answers") or {}
    ignore = learned.get("ignore") or []
    print(f"学习记录：{config.LEARNED}")
    print(f"  已记住 {len(answers)} 个字段：")
    for k, v in sorted(answers.items()):
        print(f"    {k} = {v}")
    if ignore:
        print(f"  忽略 {len(ignore)} 个标签：{'、'.join(ignore)}")
    if args.forget:
        for key in args.forget:
            answers.pop(key, None)
            if key in ignore:
                ignore.remove(key)
        save_learned({"answers": answers, "ignore": ignore}, args.learned)
        print(f"  已删除：{'、'.join(args.forget)}")
    return 0


# -------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobfill",
        description="网申自动填表工具：接管你已登录的 Chrome，填完就停，绝不自动提交。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "典型用法：\n"
            "  py -3 -m jobfill check\n"
            "  py -3 -m jobfill fill --learn            # 对当前标签页填表，边填边学\n"
            "  py -3 -m jobfill fill --url https://xxx.zhiye.com/... --dry-run\n"
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common(p):
        p.add_argument("--profile", help="profile.yaml 路径（默认用项目里的）")
        p.add_argument("--learned", help="learned.yaml 路径")
        p.add_argument("--adapter", default=None, help="站点适配器：beisen / generic（默认 generic）")
        p.add_argument("--port", type=int, default=9222, help="Chrome 调试端口（默认 9222）")

    def add_browser(p):
        p.add_argument("--url", help="表单页地址；不填则对当前活动标签页操作")
        p.add_argument("--launch", action="store_true", help="不用已开的 Chrome，独立启动一个（需要重新登录）")
        p.add_argument("--headless", action="store_true", help="无头模式（配合 --launch 用）")
        p.add_argument("--match-url", help="在已开的标签页里按地址片段挑一个")
        p.add_argument("--new-tab", action="store_true", help="新开标签页而不是复用当前页")

    p_check = sub.add_parser("check", help="资料体检：看看 profile 还缺什么")
    add_common(p_check)
    p_check.add_argument("--url", help="给个表单地址，用来自动推断站点适配器（不联网访问）")
    p_check.set_defaults(func=cmd_check)

    p_scan = sub.add_parser("scan", help="只扫描页面，列出所有字段（不填）")
    add_common(p_scan)
    add_browser(p_scan)
    p_scan.add_argument("--out", help="把控件清单写到 JSON 文件")
    p_scan.set_defaults(func=cmd_scan)

    p_fill = sub.add_parser("fill", help="填表（填完暂停，绝不提交）")
    add_common(p_fill)
    add_browser(p_fill)
    p_fill.add_argument("--learn", action="store_true", help="遇到没认出来的字段就问你，并记住答案")
    p_fill.add_argument("-y", "--yes", action="store_true", help="非交互：未识别字段直接跳过（配合 --learn 时忽略提问）")
    p_fill.add_argument("--dry-run", action="store_true", help="只匹配不填写，预览每个框会填什么")
    p_fill.add_argument("--resume-file", help="要上传的简历文件（PDF）")
    p_fill.add_argument("--photo-file", help="要上传的证件照")
    p_fill.add_argument("--only", help="只填区块或标签里含这个词的字段（例如 --only 教育）")
    p_fill.add_argument("--no-add-blocks", action="store_true", help="不自动点「添加」补足教育/实习/项目经历")
    p_fill.add_argument("--max-add", type=int, default=5, help="每个区块最多自动添加几段（默认 5）")
    p_fill.add_argument("--no-screenshot", action="store_true", help="不截图")
    p_fill.add_argument("--no-pause", action="store_true", help="填完直接退出，不等待回车")
    p_fill.set_defaults(func=cmd_fill)

    p_info = sub.add_parser("launch-info", help="怎么启动带调试端口的 Chrome")
    p_info.set_defaults(func=cmd_launch_info)

    p_learned = sub.add_parser("learned", help="查看/删除已记住的字段")
    p_learned.add_argument("--learned", help="learned.yaml 路径")
    p_learned.add_argument("--forget", nargs="*", help="删除这些标签的学习记录")
    p_learned.set_defaults(func=cmd_learned)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except RuntimeError as exc:
        print(f"\n！{exc}\n", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130
