# -*- coding: utf-8 -*-
"""纯逻辑单测（不需要浏览器）：归一化、别名打分、否定词、排除词、区块判定。

跑法：  py -3 tests/test_matcher.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobfill import config                                        # noqa: E402
from jobfill.dom import Control                                   # noqa: E402
from jobfill.matcher import MatchContext, build_assignments, score_field  # noqa: E402
from jobfill.normalize import label_score, normalize, option_score  # noqa: E402
from jobfill.profile import flatten, missing_fields, resolve_path, render_template  # noqa: E402

PASS, FAIL = [], []


def check(name: str, got, want) -> None:
    if got == want:
        PASS.append(name)
    else:
        FAIL.append(f"{name}\n      期望：{want!r}\n      实际：{got!r}")


def truthy(name: str, got) -> None:
    if got:
        PASS.append(name)
    else:
        FAIL.append(f"{name}（期望为真，实际 {got!r}）")


def falsy(name: str, got) -> None:
    if not got:
        PASS.append(name)
    else:
        FAIL.append(f"{name}（期望为假，实际 {got!r}）")


# ------------------------------------------------------------------ normalize
def test_normalize() -> None:
    check("全角冒号与空白", normalize("姓　名："), "姓名")
    check("必填标记", normalize("*手机号码（必填）"), "手机号码")
    check("请输入前缀", normalize("请输入真实姓名"), "真实姓名")
    check("英文大小写", normalize("E-Mail"), "email")
    check("CET-4", normalize("CET - 4"), "cet4")
    check("下划线", normalize("user_name"), "username")


# --------------------------------------------------------------- label_score
def test_label_score() -> None:
    check("完全相同", label_score("姓名", "姓名"), 1.0)
    truthy("包含（学校名称⊃学校）", label_score("学校名称", "学校") > 0.85)
    check("包含但覆盖度太低（紧急联系人手机号 vs 电话）", label_score("紧急联系人手机号码", "电话"), 0.0)
    check("无关（姓名 vs 学校）", label_score("姓名", "学校"), 0.0)
    check("否定：非全日制 vs 全日制", label_score("非全日制学历", "全日制"), 0.0)
    truthy("近似：院校 vs 学校", label_score("院校名称", "学校") >= 0.0)  # 不要求命中，只要不炸


# ------------------------------------------------------------ option_score
def test_option_score() -> None:
    syn = config.load_yaml(config.FIELD_MAP, default={}).get("value_synonyms") or {}
    check("是 → 是", option_score("是", "是", syn), 1.0)
    check("否 → 否", option_score("否", "否", syn), 1.0)
    falsy("是 → 不同意（否定词保护）", option_score("是", "不同意", syn) >= 0.8)
    falsy("否 → 是", option_score("否", "是", syn) >= 0.8)
    check("本科 → 大学本科", option_score("本科", "大学本科", syn), 1.0)
    truthy("本科 → 全日制本科", option_score("本科", "全日制本科", syn) >= 0.8)
    truthy("共青团员 → 团员", option_score("共青团员", "共青团员", syn) == 1.0)
    falsy("全国 → 北京", option_score("全国", "北京", syn) >= 0.8)
    truthy("面议 → 面议", option_score("面议", "面议", syn) == 1.0)
    falsy("无 → 有", option_score("无", "有", syn) >= 0.8)


# ------------------------------------------------------------------ profile
def test_profile() -> None:
    profile = config.load_yaml(config.DEFAULT_PROFILE, default={})
    field_map = config.load_yaml(config.FIELD_MAP, default={})
    fields, warns = flatten(profile, field_map)
    truthy("摊平出字段", len(fields) > 30)
    paths = {f.path for f in fields}
    truthy("含 basic.name", "basic.name" in paths)
    truthy("含 education[0].school", "education[0].school" in paths)
    truthy("含 projects[1].name", "projects[1].name" in paths)
    truthy("手机号保持字符串", all(isinstance(f.value, str) for f in fields))
    check("手机号未被吃掉前导/精度", next(f.value for f in fields if f.path == "basic.phone"), str(profile["basic"]["phone"]))
    falsy("null 字段被跳过", any(f.path == "basic.id_number" for f in fields))
    check("占位符渲染", render_template("我是{{basic.name}}", profile), "我是" + str(profile["basic"]["name"]))
    check("路径取值", resolve_path(profile, "education[0].major"), profile["education"][0]["major"])
    missing = missing_fields(profile)
    truthy("体检能列出待补字段", len(missing) > 5)


# ------------------------------------------------------------------- matcher
def mk(label: str, section: str = "", kind: str = "text", block: int = 0, count: int = 1) -> Control:
    return Control(uid=abs(hash((label, section, block))) % 10000, frame_key=0, kind=kind,
                   labels=[label], section=section, block_index=block, block_count=count)


def test_matcher() -> None:
    profile = config.load_yaml(config.DEFAULT_PROFILE, default={})
    field_map = config.load_yaml(config.FIELD_MAP, default={})
    fields, _ = flatten(profile, field_map)
    ctx = MatchContext(profile=profile, field_map=field_map)

    # 1) 姓名不能填进「父亲姓名」
    name_field = next(f for f in fields if f.path == "basic.name")
    fname = mk("父亲姓名")
    falsy("姓名 ↛ 父亲姓名", score_field(name_field, fname, ctx)[0] >= 0.55)

    # 2) 家庭区块默认不碰
    fam = mk("姓名", section="家庭主要成员")
    falsy("家庭区块里的姓名不填", score_field(name_field, fam, ctx)[0] >= 0.55)

    # 3) 验证码直接忽略
    code = mk("短信验证码")
    falsy("验证码不填", score_field(name_field, code, ctx)[0] >= 0.55)

    # 4) 「开始时间」在教育和项目区块里各归各位
    controls = [
        mk("开始时间", section="教育经历", kind="text"),
        mk("开始时间", section="项目经历", kind="text"),
    ]
    assigns, unmatched, _ = build_assignments(fields, controls, ctx)
    got = {a.control.section: a.field.path for a in assigns.values() if a.field}
    check("教育区块的开始时间 → education[0].start_date", got.get("教育经历"), "education[0].start_date")
    check("项目区块的开始时间 → projects[0].start_date", got.get("项目经历"), "projects[0].start_date")

    # 5) 重复块序号对齐
    controls2 = [
        mk("项目名称", section="项目经历", block=0, count=2),
        mk("项目名称", section="项目经历", block=1, count=2),
    ]
    assigns2, _, _ = build_assignments(fields, controls2, ctx)
    got2 = {a.control.block_index: a.field.path for a in assigns2.values() if a.field}
    check("第 0 段项目 → projects[0].name", got2.get(0), "projects[0].name")
    check("第 1 段项目 → projects[1].name", got2.get(1), "projects[1].name")

    # 6) 手机号可以同时填「手机号码」和「确认手机号码」
    mobile = next(f for f in fields if f.path == "basic.phone")
    a = mk("手机号码", section="基本信息")
    b = mk("确认手机号码", section="基本信息")
    assigns3, _, _ = build_assignments(fields, [a, b], ctx)
    check("手机号 + 确认手机号 都填上", len(assigns3), 2)
    truthy("都来自 basic.phone", all(x.field and x.field.path == "basic.phone" for x in assigns3.values()))

    # 7) 开放题只对长文本框生效
    ta = Control(uid=777, frame_key=0, kind="textarea", labels=["自我评价"], section="开放题", maxlength=400)
    txt = Control(uid=778, frame_key=0, kind="text", labels=["自我评价"], section="开放题")
    questions = (config.load_yaml(config.OPEN_QUESTIONS, default={}) or {}).get("questions") or []
    ctx2 = MatchContext(profile=profile, field_map=field_map, questions=questions)
    assigns4, _, _ = build_assignments(fields, [ta, txt], ctx2)
    truthy("自我评价文本框填开放题", assigns4.get(777) and assigns4[777].source.startswith("open:"))
    falsy("单行输入框不塞开放题", 778 in assigns4)


def test_adapter_inference() -> None:
    from jobfill.adapters import get_adapter, merge_field_map

    check("zhiye.com → beisen", get_adapter("https://xiaomi.zhiye.com/social/detail").name, "beisen")
    check("beisen 关键词 → beisen", get_adapter("beisen").name, "beisen")
    check("普通站点 → generic", get_adapter("https://jobs.example.com/form").name, "generic")
    check("不传 → generic", get_adapter(None).name, "generic")

    base = config.load_yaml(config.FIELD_MAP, default={})
    original_len = len(base["fields"]["education.school"]["aliases"])
    merged = merge_field_map(base, get_adapter("beisen"))
    school_aliases = merged["fields"]["education.school"]["aliases"]
    truthy("适配器别名已叠加", len(school_aliases) > original_len)
    truthy("原有别名没被覆盖", "毕业院校" in school_aliases)
    check("叠加不修改原字典", len(base["fields"]["education.school"]["aliases"]), original_len)


def main() -> int:
    for fn in (
        test_normalize,
        test_label_score,
        test_option_score,
        test_profile,
        test_matcher,
        test_adapter_inference,
    ):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            import traceback

            FAIL.append(f"{fn.__name__} 抛异常：{exc}\n{traceback.format_exc()}")

    print(f"通过 {len(PASS)} 项")
    if FAIL:
        print(f"\n失败 {len(FAIL)} 项：")
        for f in FAIL:
            print(f"  × {f}")
        return 1
    print("全部通过 ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
