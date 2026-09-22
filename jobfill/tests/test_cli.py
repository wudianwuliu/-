# -*- coding: utf-8 -*-
"""CLI 接线测试（不需要浏览器）。

针对的真实故障：你第一次真跑的一定是 `jobfill fill --learn`，
如果 cmd_fill 里写了 `args.xxx` 而解析器没定义这个参数，只会在那一刻抛
AttributeError —— 所以这里把每个子命令的参数和函数里引用的属性对一遍。

跑法：  py -3 tests/test_cli.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobfill.cli import build_parser                            # noqa: E402

CLI_SRC = (ROOT / "jobfill" / "cli.py").read_text(encoding="utf-8")

# 每个子命令给一组「把所有开关都打开」的参数
MAX_ARGV = {
    "check": ["check", "--url", "https://xiaomi.zhiye.com/a", "--adapter", "beisen", "--port", "9222"],
    "scan": [
        "scan", "--url", "https://xiaomi.zhiye.com/a", "--launch", "--headless",
        "--match-url", "zhiye", "--new-tab", "--out", "scan.json", "--port", "9333",
    ],
    "fill": [
        "fill", "--url", "https://xiaomi.zhiye.com/a", "--learn", "-y", "--dry-run",
        "--resume-file", "r.pdf", "--photo-file", "p.jpg", "--only", "教育",
        "--no-add-blocks", "--max-add", "3", "--no-screenshot", "--no-pause",
        "--profile", "p.yaml", "--learned", "l.yaml", "--adapter", "beisen",
    ],
    "launch-info": ["launch-info"],
    "learned": ["learned", "--learned", "l.yaml", "--forget", "身高(cm)", "体重(kg)"],
}

PASS: list[str] = []
FAIL: list[str] = []


def func_source(name: str) -> str:
    """抠出某个 cmd_* 函数的源码。"""
    m = re.search(rf"^def {name}\(.*?\n(?=^def |\Z)", CLI_SRC, re.S | re.M)
    return m.group(0) if m else ""


def main() -> int:
    parser = build_parser()

    # 1) 每个子命令都要能解析「全开」参数
    namespaces = {}
    for cmd, argv in MAX_ARGV.items():
        try:
            ns = parser.parse_args(argv)
            namespaces[cmd] = ns
            PASS.append(f"{cmd} 参数解析")
        except SystemExit as exc:
            FAIL.append(f"{cmd} 参数解析失败（exit={exc.code}）：{argv}")

    # 2) 函数里引用的 args.X 必须在解析结果里存在
    for cmd, ns in namespaces.items():
        src = func_source(f"cmd_{cmd.replace('-', '_')}")
        if not src:
            FAIL.append(f"找不到 cmd_{cmd.replace('-', '_')} 的源码")
            continue
        used = sorted(set(re.findall(r"\bargs\.(\w+)", src)))
        missing = [name for name in used if not hasattr(ns, name)]
        if missing:
            FAIL.append(f"cmd_{cmd} 引用了未定义的参数：{missing}")
        else:
            PASS.append(f"cmd_{cmd} 的 {len(used)} 个参数引用都有定义")

    # 3) 子命令分派要接上
    for cmd, ns in namespaces.items():
        if getattr(ns, "func", None) is None:
            FAIL.append(f"{cmd} 没有绑定 func")
        else:
            PASS.append(f"{cmd} 已绑定处理函数")

    # 4) 危险默认值：什么都不加时，必须是「不填不提交」的安全档
    defaults_ns = parser.parse_args(["fill"])
    for attr, want in (
        ("dry_run", False),
        ("yes", False),
        ("learn", False),
        ("no_pause", False),
        ("only", None),
        ("resume_file", None),
        ("photo_file", None),
        ("adapter", None),
    ):
        if getattr(defaults_ns, attr) == want:
            PASS.append(f"fill 默认 {attr} = {want!r}")
        else:
            FAIL.append(f"fill 默认 {attr} 应为 {want!r}，实际 {getattr(defaults_ns, attr)!r}")

    # 5) 铁律：整个 CLI 里不允许存在任何「提交/投递」类开关
    option_strings: list[str] = []
    for action in parser._actions:                       # noqa: SLF001 - 测试里翻参数表没问题
        for sub in getattr(action, "choices", {}) or {}:
            for sub_action in getattr(action.choices[sub], "_actions", []):
                option_strings.extend(sub_action.option_strings)
    danger = [o for o in option_strings if re.search(r"submit|投递|自动提交|apply-now", o, re.I)]
    if danger:
        FAIL.append(f"CLI 里出现了提交类开关，必须删掉：{danger}")
    else:
        PASS.append(f"没有提交类开关（共 {len(option_strings)} 个开关）")

    print(f"通过 {len(PASS)} 项")
    if FAIL:
        print(f"失败 {len(FAIL)} 项：")
        for f in FAIL:
            print(f"  × {f}")
        return 1
    print("CLI 接线全部通过 ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
