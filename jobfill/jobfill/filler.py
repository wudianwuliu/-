# -*- coding: utf-8 -*-
"""把值真正填进页面，并且每一种控件都回读校验。

支持的控件形态（北森这类系统里全都会遇到）：
  * 普通输入框 / 文本域
  * 原生 select、多选 select
  * 单选组（radio）/ 多选组（checkbox）
  * 原生 date/month 输入框
  * 只读输入框 + 自定义日历（点开面板点年、点月）
  * 假下拉（div 模拟的 select，必须点开再点选项）
  * 文件上传（简历 / 证件照；set_input_files 对隐藏 input 也有效）

原则：**只填不提交**。这里不存在任何点击「提交/保存/下一步/删除」的代码路径，
ADD_BUTTON_TEXTS 之外的按钮一律不点。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .dom import ADD_BUTTON_TEXTS, SUBMIT_WORDS, Control
from .matcher import Assignment, MatchContext, truncate
from .normalize import normalize, option_score

# --------------------------------------------------------------------- JS
_JS_FORCE_SET = """
([uid, val]) => {
  const el = document.querySelector('[data-jobfill-id="' + uid + '"]');
  if (!el) return null;
  try { el.removeAttribute('readonly'); } catch (e) {}
  const tag = el.tagName.toLowerCase();
  const proto = tag === 'textarea' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
  const desc = Object.getOwnPropertyDescriptor(proto, 'value');
  try {
    if (desc && desc.set) { desc.set.call(el, val); } else { el.value = val; }
  } catch (e) { el.value = val; }
  ['input', 'change', 'keyup', 'blur'].forEach(function (t) {
    el.dispatchEvent(new Event(t, { bubbles: true }));
  });
  return el.value;
}
"""

_JS_LIVE_OPTIONS = """
(uid) => {
  const el = document.querySelector('[data-jobfill-id="' + uid + '"]');
  if (!el || el.tagName.toLowerCase() !== 'select') return null;
  return Array.from(el.options).map(function (o, i) {
    return { i: i, text: (o.textContent || '').trim(), value: o.value, disabled: !!o.disabled };
  });
}
"""

_JS_SET_SELECT = """
([uid, idxs]) => {
  const el = document.querySelector('[data-jobfill-id="' + uid + '"]');
  if (!el || el.tagName.toLowerCase() !== 'select') return null;
  const set = new Set(idxs);
  Array.from(el.options).forEach(function (o, i) {
    o.selected = set.has(i);
  });
  ['input', 'change'].forEach(function (t) { el.dispatchEvent(new Event(t, { bubbles: true })); });
  if (window.jQuery) { try { window.jQuery(el).trigger('change'); } catch (e) {} }
  return Array.from(el.selectedOptions || []).map(function (o) { return (o.textContent || '').trim(); });
}
"""

# 收集「刚弹出来的面板」里的可点选项（下拉菜单 / 日历的年月日单元格）
_JS_COLLECT_POPUP = """
() => {
  const clsOf = (el) => { const c = el.className; return typeof c === 'string' ? c : ''; };
  const shown = (el) => {
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width >= 6 && r.height >= 6;
  };
  const res = [];
  let i = 0;
  document.querySelectorAll('li,dd,td,div,span,a,p,button,em').forEach((el) => {
    const c = clsOf(el);
    if (!/option|item|cell|choice|day|month|year|select|dropdown|menu|list/i.test(c)) return;
    if (el.querySelector('li,div,span,a,td')) return;
    if (!shown(el)) return;
    const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
    if (!t || t.length > 20) return;
    let anc = el.parentElement, okAnc = false, hop = 0;
    while (anc && hop < 7) {
      if (/dropdown|select|picker|calendar|date|popup|panel|menu|option-list|layui|chosen|ant-select/i.test(clsOf(anc))) {
        okAnc = true;
        break;
      }
      anc = anc.parentElement;
      hop++;
    }
    if (!okAnc) return;
    el.setAttribute('data-jobfill-opt', String(i));
    res.push({ i: i, text: t });
    i++;
  });
  return res;
}
"""

_JS_CLEAR_POPUP = """
() => {
  let n = 0;
  document.querySelectorAll('[data-jobfill-opt]').forEach((el) => { el.removeAttribute('data-jobfill-opt'); n++; });
  return n;
}
"""

# 收集「添加一条」类按钮（白名单 + 反白名单双重保护）
_JS_COLLECT_ADD = """
(allowed) => {
  const clsOf = (el) => { const c = el.className; return typeof c === 'string' ? c : ''; };
  const shown = (el) => {
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width >= 4 && r.height >= 4;
  };
  // 按钮属于哪个区块：取它之前最近的一个标题
  const nearestHeading = (el) => {
    let best = '';
    document.querySelectorAll('h1,h2,h3,h4,h5,h6,legend,caption').forEach((h) => {
      if (h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) {
        const t = (h.innerText || '').replace(/\\s+/g, ' ').trim();
        if (t && t.length <= 30) best = t;
      }
    });
    return best;
  };
  const BLOCK_WORDS = /提交|保存|投递|删除|清空|重置|注销|退出/;
  const res = [];
  let i = 0;
  document.querySelectorAll('button,a,span,div,i,em,strong,label,p,input[type=button]').forEach((el) => {
    if (el.querySelector('button,a,span,div,i,em,strong')) return;
    const t = (el.innerText || el.value || '').replace(/\\s+/g, ' ').trim();
    if (!t || t.length > 14) return;
    if (BLOCK_WORDS.test(t)) return;
    const norm = t.replace(/[\\s+＋]/g, '');
    // 白名单精确命中，或「添加/新增」开头（例如「添加教育经历」）
    const hit = allowed.some((a) => norm === a.replace(/[\\s+＋]/g, '')) || /^(添加|新增)/.test(norm);
    if (!hit) return;
    if (!shown(el)) return;
    // 反白名单只查自身和最近的按钮/链接祖先。
    // 千万不能往上走到页面容器 —— 整个页面里必然有「保存/提交」，
    // 那样所有「添加」按钮都会被误杀。
    const scope = [el];
    const anc = el.closest('button,a,label');
    if (anc && anc !== el) scope.push(anc);
    for (const node of scope) {
      if (BLOCK_WORDS.test((node.innerText || '').replace(/\\s+/g, ''))) return;
    }
    el.setAttribute('data-jobfill-add', String(i));
    res.push({ i: i, text: t, section: nearestHeading(el) });
    i++;
  });
  return res;
}
"""

_JS_CLICK_ADD = """
(i) => {
  const el = document.querySelector('[data-jobfill-add="' + i + '"]');
  if (!el) return false;
  el.click();
  return true;
}
"""

_JS_CLEAR_ADD = """
() => {
  let n = 0;
  document.querySelectorAll('[data-jobfill-add]').forEach((el) => { el.removeAttribute('data-jobfill-add'); n++; });
  return n;
}
"""

_DATE_RE = re.compile(r"(\d{2,4})\s*[-/年.]\s*(\d{1,2})(?:\s*[-/月.]\s*(\d{1,2}))?")


@dataclass
class FillOutcome:
    uid: int
    label: str
    section: str
    kind: str
    source: str
    value: str
    status: str          # filled / verified / failed / skipped / needs_manual
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("filled", "verified")


def _loc(control: Control, uid: int | None = None):
    target = control.uid if uid is None else uid
    return control.frame.locator(f'[data-jobfill-id="{target}"]')


def _adapt_date(value: str, control: Control) -> str:
    """按控件的类型/占位符把日期转成它想要的格式。"""
    m = _DATE_RE.search(value or "")
    if not m:
        return value
    year, month, day = m.group(1), m.group(2), m.group(3)
    if len(year) == 2:
        year = "20" + year
    month = month.zfill(2)

    hint = f"{control.placeholder or ''} {control.type or ''}".lower()
    if control.kind in ("date", "datetime-local", "month") or "yyyy-mm-dd" in hint.replace("/", "-"):
        day = (day or "01").zfill(2)
        return f"{year}-{month}-{day}"
    if "yyyy/mm/dd" in hint:
        day = (day or "01").zfill(2)
        return f"{year}/{month}/{day}"
    if "yyyy/mm" in hint:
        return f"{year}/{month}"
    if "年月" in hint or "yyyy年" in hint:
        return f"{year}年{month}月" if not day else f"{year}年{month}月{day.zfill(2)}日"
    if day:
        return f"{year}-{month}-{day.zfill(2)}"
    return f"{year}-{month}"


def _force_set(control: Control, value: str) -> bool:
    try:
        res = control.frame.evaluate(_JS_FORCE_SET, [control.uid, value])
        return res is not None
    except Exception:
        return False


def _read_current(control: Control) -> str:
    """回读控件当前值（空字符串表示还没填上）。"""
    try:
        from .dom import read_values

        got = read_values([control]).get(control.uid)
        return "" if got is None else str(got)
    except Exception:
        return ""


def _click_popup_exact(control: Control, texts: set[str]) -> bool:
    """在刚弹出的面板里精确点中某个文本（日历的年/月、下拉项）。"""
    try:
        opts = control.frame.evaluate(_JS_COLLECT_POPUP)
    except Exception:
        return False
    if not isinstance(opts, list):
        return False
    for opt in opts:
        t = str(opt.get("text") or "").strip()
        if normalize(t) in texts or t in texts:
            try:
                _loc(control, None)  # 仅确保 frame 活着
                control.frame.locator(f'[data-jobfill-opt="{opt["i"]}"]').first.click(timeout=1200)
                return True
            except Exception:
                return False
    return False


def _fill_date(control: Control, value: str) -> tuple[str, str]:
    adapted = _adapt_date(value, control)
    if control.kind in ("date", "month", "datetime-local"):
        try:
            _loc(control).fill(adapted, timeout=2500)
            return "filled", adapted
        except Exception:
            pass
    # 只读框 / 自定义日历：先点开面板，逐个点年、点月（有些还要点日）
    if control.readonly or control.widget_guess in ("readonly", "fake_select") or control.type == "text":
        try:
            _loc(control).click(timeout=1200)
        except Exception:
            pass
        m = _DATE_RE.search(adapted or "")
        if m:
            year, month, day = m.group(1), m.group(2), m.group(3)
            for token in (year, month, day):
                if not token:
                    continue
                variants = {token, str(int(token)) if token.isdigit() else token}
                if _click_popup_exact(control, variants | {t + "月" for t in variants} | {t + "年" for t in variants}):
                    control.frame.wait_for_timeout(120)
    # 日历已经点出值来了就别再覆盖它
    current = _read_current(control)
    if current:
        return "filled", current
    # 兜底：直接改 value 并派发事件（jQuery 系表单的校验读的就是 .val()）
    if _force_set(control, adapted):
        return "filled", adapted
    return "failed", adapted


def _fill_select(control: Control, value: str, ctx: MatchContext) -> tuple[str, str]:
    """原生 select：优先用**现场**读到的选项（级联下拉的选项是动态加载的）。"""
    live = None
    try:
        live = control.frame.evaluate(_JS_LIVE_OPTIONS, control.uid)
    except Exception:
        live = None
    options: list[dict] = []
    if isinstance(live, list) and live:
        options = [o for o in live if isinstance(o, dict)]
    else:
        options = [{"i": i, "text": o.text, "value": o.value, "disabled": o.disabled} for i, o in enumerate(control.options)]

    wants = [v for v in re.split(r"[、,，/|]", str(value)) if v.strip()]
    picked: list[dict] = []
    for want in wants:
        best, best_score = None, 0.0
        for opt in options:
            if opt.get("disabled"):
                continue
            s = option_score(want.strip(), str(opt.get("text") or ""), ctx.value_synonyms)
            if s > best_score:
                best, best_score = opt, s
        if best is not None and best_score >= 0.8:
            picked.append(best)
        if not control.kind == "multiselect" and picked:
            break

    if not picked:
        return "needs_manual", f"没有可匹配的选项（可选：{', '.join(str(o.get('text')) for o in options[:12])}）"

    idxs = [int(o["i"]) for o in picked]
    try:
        res = control.frame.evaluate(_JS_SET_SELECT, [control.uid, idxs])
        if isinstance(res, list) and res:
            return "verified", "、".join(str(x) for x in res)
    except Exception:
        pass
    # 兜底：用 Playwright 原生 select_option
    try:
        _loc(control).select_option(index=idxs[0], timeout=2500)
        return "filled", "、".join(str(o.get("text")) for o in picked)
    except Exception:
        pass
    return "failed", "、".join(str(o.get("text")) for o in picked)


def _choose_option(control: Control, value: str, ctx: MatchContext) -> tuple[str, str]:
    """单选 / 多选组：按值挑选项并点击。"""
    wants = [v for v in re.split(r"[、,，/|]", str(value)) if v.strip()]
    scored = []
    for opt in control.options:
        if opt.disabled or not (opt.text or "").strip():
            continue
        best = max(
            (option_score(want, opt.text, ctx.value_synonyms) for want in wants),
            default=0.0,
        )
        scored.append((best, opt))
    scored.sort(key=lambda t: -t[0])

    if control.kind == "checkbox":
        picked = [o for s, o in scored if s >= 0.8]
        if not picked:
            return "needs_manual", f"没有可匹配的选项（可选：{', '.join(control.option_texts[:12])}）"
    else:
        if not scored or scored[0][0] < 0.8:
            return "needs_manual", f"没有可匹配的选项（可选：{', '.join(control.option_texts[:12])}）"
        picked = [scored[0][1]]

    done = []
    for opt in picked:
        try:
            _loc(control, opt.uid).first.click(timeout=1500)
            done.append(opt.text)
        except Exception:
            try:
                _loc(control, opt.uid).first.check(timeout=1500, force=True)
                done.append(opt.text)
            except Exception:
                continue
    if not done:
        return "failed", "、".join(o.text for o in picked)
    return "filled", "、".join(done)


def _try_fake_select(control: Control, value: str, ctx: MatchContext) -> tuple[str, str] | None:
    """假下拉：点开 → 在弹出面板里选。失败返回 None，交给调用方兜底。"""
    try:
        _loc(control).click(timeout=1500)
    except Exception:
        return None
    control.frame.wait_for_timeout(150)
    try:
        opts = control.frame.evaluate(_JS_COLLECT_POPUP)
    except Exception:
        return None
    if not isinstance(opts, list) or not opts:
        return None
    wants = [v for v in re.split(r"[、,，/|]", str(value)) if v.strip()]
    best, best_score = None, 0.0
    for opt in opts:
        text = str(opt.get("text") or "")
        s = max((option_score(w, text, ctx.value_synonyms) for w in wants), default=0.0)
        if s > best_score:
            best, best_score = opt, s
    if best is None or best_score < 0.85:
        return None
    try:
        control.frame.locator(f'[data-jobfill-opt="{best["i"]}"]').first.click(timeout=1500)
        return "filled", str(best.get("text"))
    except Exception:
        return None


def _fill_text(control: Control, value: str) -> tuple[str, str]:
    try:
        _loc(control).scroll_into_view_if_needed(timeout=1200)
    except Exception:
        pass
    try:
        _loc(control).click(timeout=1200)
    except Exception:
        pass
    try:
        _loc(control).fill(value, timeout=2500)
        return "filled", value
    except Exception:
        pass
    # 只读 / 富文本 / 特殊控件 → 直接改值
    if _force_set(control, value):
        return "filled", value
    # 最后试逐字输入（少数控件只认键盘事件）
    try:
        _loc(control).click(timeout=1200)
        _loc(control).press_sequentially(value, timeout=5000)
        return "filled", value
    except Exception:
        return "failed", value


# ------------------------------------------------------------------ 对外接口
def fill_one(assignment: Assignment, ctx: MatchContext, options: dict | None = None) -> FillOutcome:
    """填一个控件并回读校验。"""
    control = assignment.control
    value = assignment.value
    opts = options or {}

    base = dict(
        uid=control.uid,
        label=control.display_label,
        section=control.section,
        kind=control.kind,
        source=assignment.source,
        value=value,
    )

    if control.disabled:
        return FillOutcome(**base, status="skipped", note="控件被禁用")
    if control.kind == "password":
        return FillOutcome(**base, status="skipped", note="密码类字段，工具不碰")
    if control.kind == "file":
        return _fill_file(assignment, ctx, opts)

    def done(status: str, actual: str, note: str = "") -> FillOutcome:
        final = status
        if status in ("filled", "verified"):
            if _verify(control, actual or value):
                final = "verified"
            elif control.kind in ("radio", "checkbox"):
                final = "filled"
            else:
                # 值没落上去：再强行写一次
                if control.kind in ("text", "textarea") and _force_set(control, value):
                    final = "verified" if _verify(control, value) else "filled"
                else:
                    final = "failed"
                    note = (note + " 回读校验不一致").strip()
        return FillOutcome(**base, status=final, note=note)

    kind = control.kind
    try:
        if kind == "select" or kind == "multiselect":
            status, actual = _fill_select(control, value, ctx)
            return done(status, actual)
        if kind in ("radio", "checkbox"):
            status, actual = _choose_option(control, value, ctx)
            return done(status, actual)
        if kind in ("date", "month", "datetime-local"):
            status, actual = _fill_date(control, value)
            return done(status, actual, note="日期控件")
        if kind == "textarea":
            status, actual = _fill_text(control, value)
            return done(status, actual)
        # 文本类输入框：如果是枚举字段（性别/学历/政治面貌…）而页面是假下拉，先试点选
        if assignment.field is not None and assignment.field.widget in ("choice", "date") and (
            control.readonly or control.widget_guess == "fake_select"
        ):
            if assignment.field.widget == "date":
                status, actual = _fill_date(control, value)
                return done(status, actual)
            fake = _try_fake_select(control, value, ctx)
            if fake is not None:
                return done(fake[0], fake[1], note="假下拉")
        status, actual = _fill_text(control, value)
        return done(status, actual)
    except Exception as exc:                       # 任何单点异常都不该中断整轮
        return FillOutcome(**base, status="failed", note=f"{type(exc).__name__}: {exc}")
    finally:
        try:
            control.frame.evaluate(_JS_CLEAR_POPUP)
        except Exception:
            pass


def _verify(control: Control, expected: str) -> bool:
    """回读校验：值是否真的落进页面了。"""
    if control.kind in ("radio", "checkbox"):
        from .dom import read_selected_labels

        picked = [normalize(x) for x in read_selected_labels(control)]
        want = [normalize(x) for x in re.split(r"[、,，/|]", expected) if x.strip()]
        if not picked or not want:
            return False
        return all(any(w in p or p in w for p in picked) for w in want)

    from .dom import read_values

    got = read_values([control]).get(control.uid)
    if got is None:
        return False
    got_n, want_n = normalize(got), normalize(expected)
    if not got_n:
        return False
    return got_n == want_n or got_n in want_n or want_n in got_n


def _fill_file(assignment: Assignment, ctx: MatchContext, opts: dict) -> FillOutcome:
    """文件上传：简历 / 证件照。

    要传哪个文件由 runner 生成的「虚拟文件字段」决定（值就是文件路径），
    这样上传目标和文件路径的匹配也走同一套标签识别。
    """
    control = assignment.control
    base = dict(
        uid=control.uid,
        label=control.display_label,
        section=control.section,
        kind=control.kind,
        source=assignment.source,
        value=assignment.value,
    )
    target = (assignment.value or "").strip()
    if not target:
        return FillOutcome(
            **{**base, "value": ""},
            status="needs_manual",
            note="需要手动上传（可用 --resume-file 传简历，或用 profile 里的 basic.photo）",
        )
    path = Path(target)
    if not path.exists():
        return FillOutcome(
            **{**base, "value": path.name},
            status="needs_manual",
            note=f"文件不存在，请手动上传：{path}",
        )
    try:
        _loc(control).set_input_files(str(path), timeout=8000)
        return FillOutcome(**{**base, "value": path.name}, status="verified", note="已上传")
    except Exception as exc:
        return FillOutcome(**{**base, "value": path.name}, status="failed", note=f"上传失败：{exc}")


# ------------------------------------------------------------ 重复块（添加）
def add_blocks(page, section_alias: list[str], need: int, have: int, log=print) -> int:
    """在某个区块里点「添加」补足重复块（教育经历 / 实习经历 / 项目经历）。

    安全约束：
      * 只点文本以「添加/新增」开头、或命中白名单的按钮
      * 按钮所属区块必须和目标区块对上，否则宁可不点
      * 标记（data-jobfill-add）在点击之后才清理，绝不能先清后点
    """
    from .normalize import contains_any

    added = 0
    for _ in range(max(0, need - have)):
        best = None
        for frame in page.frames:
            try:
                cands = frame.evaluate(_JS_COLLECT_ADD, ADD_BUTTON_TEXTS)
            except Exception as exc:
                log(f"  ! 收集「添加」按钮失败：{type(exc).__name__}: {exc}")
                continue
            if not isinstance(cands, list):
                continue
            for cand in cands:
                text = str(cand.get("text") or "")
                if any(w in text for w in SUBMIT_WORDS):
                    continue
                if section_alias and not contains_any(str(cand.get("section") or ""), section_alias):
                    continue          # 区块对不上，不点
                if best is None:
                    best = (frame, cand)

        if best is None:
            break
        frame, cand = best
        try:
            clicked = frame.evaluate(_JS_CLICK_ADD, int(cand["i"]))
        except Exception as exc:
            log(f"  ! 点「添加」失败：{type(exc).__name__}: {exc}")
            break
        clear_add_marks(page)
        if not clicked:
            log("  ! 「添加」按钮没点到（可能已被移除）")
            break
        added += 1
        page.wait_for_timeout(400)
        log(f"  · 点击「{cand.get('text')}」新增一段（第 {added} 段）")
    return added


def clear_add_marks(page) -> None:
    for frame in page.frames:
        try:
            frame.evaluate(_JS_CLEAR_ADD)
        except Exception:
            continue
