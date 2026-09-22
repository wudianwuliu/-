# -*- coding: utf-8 -*-
"""站点适配器：把「某个网申系统的脾气」集中在一处。

适配器能做的事：
  * extra_fields —— 补充/覆盖 field_map.yaml 里的别名（某个系统特有的叫法）
  * prepare —— 填表前做准备：等页面稳定、关掉「我知道了」弹窗、滚动触发懒加载
  * block_sections —— 哪几个区块是「可重复块」以及它们的标题叫什么
  * after_fill —— 填完之后的收尾（默认什么都不做，绝不点提交）

目前只写了 beisen（北森，*.zhiye.com 等）和 generic（通用兜底）。
"""

from __future__ import annotations

from typing import Any

from ..normalize import contains_any

# 「我知道了」这类安全弹窗，点了不会有副作用
SAFE_DISMISS_TEXTS = [
    "我知道了",
    "知道了",
    "我同意",
    "同意并继续",
    "同意",
    "关闭",
    "不再提示",
    "稍后再说",
    "跳过",
]

_JS_COLLECT_BUTTONS = """
(texts) => {
  const res = [];
  let i = 0;
  const norm = (s) => (s || '').replace(/\\s+/g, '');
  document.querySelectorAll('button,a,span,div,input[type=button]').forEach((el) => {
    if (el.querySelector('button,a')) return;
    const t = (el.innerText || el.value || '').replace(/\\s+/g, ' ').trim();
    if (!t || t.length > 12) return;
    if (!texts.includes(norm(t))) return;
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return;
    el.setAttribute('data-jobfill-btn', String(i));
    res.push({ i: i, text: t });
    i++;
  });
  return res;
}
"""

_JS_CLICK_BTN = """
(i) => {
  const el = document.querySelector('[data-jobfill-btn="' + i + '"]');
  if (!el) return false;
  el.click();
  return true;
}
"""

_JS_CLEAR_BTN = """
() => {
  let n = 0;
  document.querySelectorAll('[data-jobfill-btn]').forEach((el) => { el.removeAttribute('data-jobfill-btn'); n++; });
  return n;
}
"""

# 滚动整页，触发懒加载/折叠区块渲染，然后回到顶部
_JS_WARM_SCROLL = """
() => new Promise((resolve) => {
  let y = 0;
  let steps = 0;
  const timer = setInterval(() => {
    const h = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
    window.scrollTo(0, y);
    y += Math.max(400, window.innerHeight * 0.8);
    steps += 1;
    if (y > h + 400 || steps > 40) {
      clearInterval(timer);
      window.scrollTo(0, 0);
      setTimeout(resolve, 250);
    }
  }, 90);
})
"""


class Adapter:
    """通用适配器：只做最保守的事。"""

    name = "generic"
    description = "通用兜底：靠标签文本识别，不依赖任何站点特有结构"
    extra_fields: dict[str, dict] = {}
    block_sections: dict[str, list[str]] = {}

    def prepare(self, page, log=print) -> None:
        page.wait_for_timeout(500)
        self.dismiss_safe_popups(page, log)
        self.warm_scroll(page)

    def warm_scroll(self, page) -> None:
        try:
            page.evaluate(_JS_WARM_SCROLL)
        except Exception:
            pass

    def dismiss_safe_popups(self, page, log=print) -> int:
        clicked = 0
        for _ in range(6):
            hit = False
            for frame in page.frames:
                try:
                    cands = frame.evaluate(_JS_COLLECT_BUTTONS, SAFE_DISMISS_TEXTS)
                except Exception:
                    continue
                if not isinstance(cands, list):
                    continue
                for cand in cands:
                    try:
                        frame.evaluate(_JS_CLICK_BTN, int(cand["i"]))
                        clicked += 1
                        hit = True
                        log(f"  · 关掉提示弹窗「{cand.get('text')}」")
                    except Exception:
                        continue
                try:
                    frame.evaluate(_JS_CLEAR_BTN)
                except Exception:
                    pass
            if hit:
                page.wait_for_timeout(300)
            else:
                break
        return clicked

    def after_fill(self, page, log=print) -> None:
        """默认什么都不做。绝不在任何适配器里点提交。"""
        return None


class BeisenAdapter(Adapter):
    """北森（zhiye.com / 北森招聘云）网申。

    注意：我**没有**在真实账号里跑过选择器层面的验证，所以这里刻意不做
    「按 ID 硬编码定位」这种脆弱的事，只做三件确定安全的事：
      1. 关掉「我知道了」弹窗（否则它挡住后半个表单，抽取不到控件）
      2. 整页滚动一遍，让懒加载的区块渲染出来，再回到顶部
      3. 声明哪几个区块是可重复块，供「点添加按钮补足」使用
    字段识别仍然走统一的标签匹配，这样即使北森改版也不会整体失效。
    """

    name = "beisen"
    description = "北森招聘云 / *.zhiye.com 校招网申"
    # 键名必须和 profile.yaml 的顶层键一致（education / internships / projects / family），
    # 否则 runner 查不到别名，重复块就永远不会自动补。
    block_sections = {
        "education": ["教育经历", "教育背景", "学习经历", "教育信息"],
        "internships": ["实习经历", "工作经历", "实习经验", "工作经验"],
        "projects": ["项目经历", "项目经验", "科研经历"],
        "family": ["家庭", "家庭成员"],
    }
    # 北森表单上常见的叫法（统一在 field_map 之上叠加）
    extra_fields = {
        "basic.name": {"aliases": ["姓名（中文）", "本人姓名（与身份证一致）", "考生姓名"]},
        "basic.phone": {"aliases": ["手机号码（11位）", "常用手机号"]},
        "basic.email": {"aliases": ["常用邮箱（用于接收通知）", "电子邮箱地址"]},
        "basic.id_number": {"aliases": ["身份证号码（18位）"]},
        "basic.political_status": {"aliases": ["政治面貌（当前）"]},
        "education.degree_level": {"aliases": ["最高学历（在读/已获）", "学历（当前最高）"]},
        "education.school": {"aliases": ["毕业院校（全称）", "就读院校名称"]},
        "education.major": {"aliases": ["专业名称（全称）"]},
        "education.start_date": {"aliases": ["入学年月", "入学时间（年月）"]},
        "education.end_date": {"aliases": ["毕业年月", "预计毕业时间（年月）"]},
        "intention.position": {"aliases": ["应聘职位（第一志愿）", "意向岗位（第一志愿）"]},
        "intention.expected_city": {"aliases": ["期望工作城市（可多选）", "意向工作城市"]},
    }

    def prepare(self, page, log=print) -> None:
        # 北森的表单往往在 iframe 里异步渲染，多等一会儿很值
        for _ in range(3):
            page.wait_for_timeout(700)
            if any(len(f.child_frames) for f in page.frames):
                break
        self.dismiss_safe_popups(page, log)
        self.warm_scroll(page)
        self.dismiss_safe_popups(page, log)
        page.wait_for_timeout(400)


_ADAPTERS: dict[str, Adapter] = {
    "generic": Adapter(),
    "beisen": BeisenAdapter(),
}


def get_adapter(name: str | None) -> Adapter:
    key = (name or "generic").strip().lower()
    if key in _ADAPTERS:
        return _ADAPTERS[key]
    # 允许用 URL 猜
    if any(token in key for token in ("zhiye", "beisen", "italent")):
        return _ADAPTERS["beisen"]
    return _ADAPTERS["generic"]


def merge_field_map(field_map: dict, adapter: Adapter) -> dict:
    """把适配器的额外别名叠加到 field_map 上（不修改原文件）。"""
    if not adapter.extra_fields:
        return field_map
    merged: dict[str, Any] = dict(field_map)
    fields = {k: dict(v or {}) for k, v in (field_map.get("fields") or {}).items()}
    for key, spec in adapter.extra_fields.items():
        cur = fields.setdefault(key, {})
        aliases = list(cur.get("aliases") or [])
        for a in spec.get("aliases") or []:
            if a not in aliases:
                aliases.append(a)
        cur["aliases"] = aliases
        for k, v in spec.items():
            if k != "aliases":
                cur[k] = v
    merged["fields"] = fields
    return merged
