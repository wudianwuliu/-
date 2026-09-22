// ============================================================================
// 页面控件抽取器（在浏览器里跑，每个 iframe 各跑一次）
// ----------------------------------------------------------------------------
// 北森这类网申系统的 HTML 结构特点，决定了这里必须做得很脏：
//   * 表单常在 iframe 里（#iframe / #contentFrame），所以要逐帧抽取
//   * 大量 table 布局：<td>姓名</td><td><input></td>，标签在**前一个单元格**
//   * 标签写法五花八门：label[for] / 包裹式 label / placeholder / 前置 div
//   * 必填标记是裸的 * 号，或者 <span class="star">
//   * 分步表单 + 折叠区块，很多控件在视口外（不能只按可见性过滤）
//
// 所以：一个控件收集**多个**候选标签，交给 Python 侧按别名表挑最匹配的那个。
// 每个可填控件打上 data-jobfill-id,Python 侧靠它精确定位，不用猜选择器。
// ============================================================================
(() => {
  try {
    // 每次抽取前先清掉上一轮的标记：否则补过「添加」重复块之后，
    // 新旧两轮的 uid 会串号，同一个 data-jobfill-id 落到两个元素上。
    document.querySelectorAll('[data-jobfill-id]').forEach((el) => el.removeAttribute('data-jobfill-id'));
    document.querySelectorAll('[data-jobfill-opt]').forEach((el) => el.removeAttribute('data-jobfill-opt'));
    document.querySelectorAll('[data-jobfill-add]').forEach((el) => el.removeAttribute('data-jobfill-add'));

    const MAX_CONTROLS = 1500;
    let uid = 0;
    const out = [];
    const nodeIds = new WeakMap();
    let nodeSeq = 0;
    const nodeId = (n) => {
      if (!n) return 0;
      if (!nodeIds.has(n)) nodeIds.set(n, ++nodeSeq);
      return nodeIds.get(n);
    };

    const cls = (el) => {
      if (!el) return '';
      const c = el.className;
      return typeof c === 'string' ? c : (el.getAttribute && el.getAttribute('class')) || '';
    };
    const txt = (el) => (el == null ? '' : String(el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim());
    const vis = (el) => {
      if (!el) return false;
      const st = getComputedStyle(el);
      if (st.visibility === 'hidden' || st.display === 'none') return false;
      if (parseFloat(st.opacity) === 0) return false;
      const r = el.getBoundingClientRect();
      return r.width >= 2 && r.height >= 2;
    };
    // 克隆后剥掉表单控件再取文本——用于「容器里除了控件以外的那句提示」
    const textWithoutControls = (el, limit) => {
      if (!el) return '';
      let c;
      try {
        c = el.cloneNode(true);
      } catch (e) {
        return '';
      }
      c.querySelectorAll('input,select,textarea,button,script,style,svg,i,img,option,ul,ol,li,dl,dt,dd').forEach((n) => n.remove());
      const t = txt(c);
      return t && t.length <= limit ? t : '';
    };
    const pushUniq = (arr, v) => {
      const t = (v || '').replace(/\s+/g, ' ').trim();
      if (t && t.length <= 60 && !arr.includes(t)) arr.push(t);
    };

    // 一个容器里装了几个「字段」？同名单选/复选算一个字段。
    // 用来判断容器文本能不能当标签用 —— 一行里塞了两个控件的容器文本
    // （例如「身份证号 政治面貌」）绝不能拿来当标签，否则会互相串味。
    const fieldCount = (el) => {
      if (!el || !el.querySelectorAll) return 0;
      const ctrls = Array.from(el.querySelectorAll('input,select,textarea')).filter(
        (c) => (c.getAttribute('type') || '').toLowerCase() !== 'hidden'
      );
      const groups = new Set();
      let singles = 0;
      ctrls.forEach((c) => {
        const t = (c.getAttribute('type') || '').toLowerCase();
        if (t === 'radio' || t === 'checkbox') {
          groups.add('g:' + (c.getAttribute('name') || 'anon'));
        } else {
          singles += 1;
        }
      });
      return singles + groups.size;
    };

    // ---------------------------------------------------------------- 标签
    const labelsFor = (el) => {
      const cands = [];
      if (el.id) {
        try {
          const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
          if (l) pushUniq(cands, textWithoutControls(l, 60));
        } catch (e) { /* CSS.escape 不支持时忽略 */ }
      }
      const wl = el.closest && el.closest('label');
      if (wl && fieldCount(wl) <= 1) pushUniq(cands, textWithoutControls(wl, 60) || txt(wl));
      ['aria-label', 'placeholder', 'title', 'data-label', 'data-name', 'data-field'].forEach((a) => {
        pushUniq(cands, el.getAttribute && el.getAttribute(a));
      });
      // 表格布局：标签在前一个单元格（必须是纯标签格，自己不能再装控件）
      const cell = el.closest && el.closest('td,th');
      if (cell) {
        let prev = cell.previousElementSibling;
        let hop = 0;
        while (prev && hop < 3) {
          if (fieldCount(prev) === 0) {
            const t = textWithoutControls(prev, 40);
            if (t) { pushUniq(cands, t); break; }
          }
          prev = prev.previousElementSibling;
          hop++;
        }
        if (fieldCount(cell) <= 1) pushUniq(cands, textWithoutControls(cell, 40));
      }
      // div 布局：标签在前一个兄弟节点
      let p = el.previousElementSibling;
      let hop2 = 0;
      while (p && hop2 < 3) {
        if (fieldCount(p) === 0) {
          const t = textWithoutControls(p, 40);
          if (t) { pushUniq(cands, t); break; }
        }
        p = p.previousElementSibling;
        hop2++;
      }
      // 兜底：往上找容器自身的文本，同样要求容器里只有这一个字段
      let par = el.parentElement;
      let hop3 = 0;
      while (par && hop3 < 4) {
        if (fieldCount(par) <= 1) {
          const t = textWithoutControls(par, 60);
          if (t) { pushUniq(cands, t); break; }
        }
        par = par.parentElement;
        hop3++;
      }
      return cands;
    };

    // ---------------------------------------------------------------- 区块
    const HEAD_TAGS = ['h1', 'h2', 'h3', 'h4', 'h5'];
    const HEAD_CLASS_RE = /(section|panel|form|group|card|block|item|sub|header).*(title|head)|(title|head).*(section|panel|form|group|card|block|item|sub|header)/i;
    const collectHeads = () => {
      const list = [];
      document.querySelectorAll('h1,h2,h3,h4,h5,h6,legend,caption,div,span,p,strong,b').forEach((el) => {
        const tag = el.tagName.toLowerCase();
        const isTag = HEAD_TAGS.includes(tag) || tag === 'legend' || tag === 'caption';
        const isCls = HEAD_CLASS_RE.test(cls(el));
        if (!isTag && !isCls) return;
        const t = txt(el);
        if (!t || t.length > 30) return;
        if (el.querySelector('input,select,textarea')) return;
        list.push({ el, text: t });
      });
      return list;
    };
    const HEADS = collectHeads();
    const sectionFor = (el) => {
      // 1) 就近：祖先的直接子标题里，取**最后一个**排在元素之前的。
      //    （取第一个会把页面顶部的 h1 当成区块名，把「家庭主要成员」这类
      //     安全相关的区块判定彻底搞坏。）
      let par = el.parentElement;
      let hop = 0;
      while (par && hop < 9) {
        let found = '';
        for (const child of par.children) {
          if (child === el) continue;
          if (!child.contains || child.contains(el)) continue;
          const tag = child.tagName.toLowerCase();
          const cand = HEAD_TAGS.includes(tag) || tag === 'legend' || tag === 'caption' || HEAD_CLASS_RE.test(cls(child));
          if (!cand) continue;
          const t = txt(child);
          if (!t || t.length > 30) continue;
          if (child.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) found = t;
        }
        if (found) return found;
        par = par.parentElement;
        hop++;
      }
      // 2) 兜底：文档里元素之前最近的一个标题
      let best = '';
      for (const h of HEADS) {
        if (h.el.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) best = h.text;
      }
      return best;
    };

    // ------------------------------------------------------------ 重复块
    // 「这是第几段教育经历」这种序号。三层判断，从可靠到兜底：
    //  1) name/id 里的数字后缀 —— 最可靠。北森这类系统反复块通常写成
    //     eduList[0].school / eduSchool0 / project_1_name，直接就是记录序号。
    //  2) 结构分析：找「重复出现的同结构兄弟容器」。难点是表格布局里 tr 也会
    //     满足这个条件（那是同一段里的行，不是记录），所以收集所有候选后取
    //     重复次数最少的那个：记录容器只重复「记录条数」次，tr 会重复「行数」次。
    //  3) 都没有就 0。
    const indexFromName = (el) => {
      const src = ((el.getAttribute && el.getAttribute('name')) || '') + ' ' +
                  ((el.getAttribute && el.getAttribute('id')) || '');
      if (!src.trim()) return null;
      let m = src.match(/\[(\d+)\]/);
      if (m) return parseInt(m[1], 10);
      m = src.match(/(\d+)(?![\s\S]*\d)/);
      if (m) {
        const n = parseInt(m[1], 10);
        if (n <= 50) return n;
      }
      return null;
    };

    const blockInfo = (el) => {
      const nameIdx = indexFromName(el);
      const cands = [];
      let par = el.parentElement;
      let hop = 0;
      while (par && par.parentElement && hop < 7) {
        const pk = par.parentElement;
        const tag = par.tagName.toLowerCase();
        if (tag !== 'td' && tag !== 'th') {
          const sig = par.tagName + '|' + cls(par);
          const sibs = Array.from(pk.children).filter((c) => c.tagName + '|' + cls(c) === sig);
          if (sibs.length >= 2) {
            cands.push({ index: sibs.indexOf(par), count: sibs.length, hop: hop });
          }
        }
        par = pk;
        hop++;
      }
      if (!cands.length) return { index: nameIdx === null ? 0 : nameIdx, count: 1, nameIndex: nameIdx };
      cands.sort((a, b) => (a.count - b.count) || (b.hop - a.hop));
      const best = cands[0];
      return {
        index: nameIdx === null ? best.index : nameIdx,
        count: best.count,
        nameIndex: nameIdx,
      };
    };

    // ---------------------------------------------------------- 必填/只读
    const isRequired = (el) => {
      if (el.required || (el.getAttribute && el.getAttribute('aria-required') === 'true')) return true;
      const cont =
        (el.closest && el.closest('td,th,.form-item,.form-group,.ant-form-item,.el-form-item,.item,.field,.row,.control-group')) ||
        el.parentElement;
      if (!cont) return false;
      if (cont.querySelector('.required,.star,.must,.ant-form-item-required,.required-mark,.red,.xing')) return true;
      const t = txt(cont);
      return t.includes('*') || t.includes('＊') || /必填|必选|必录/.test(t);
    };

    const widgetGuess = (el) => {
      let p = el;
      let hop = 0;
      while (p && hop < 4) {
        if (/select|dropdown|picker|chosen|combobox/i.test(cls(p))) return 'fake_select';
        p = p.parentElement;
        hop++;
      }
      if (el.readOnly) return 'readonly';
      return '';
    };

    const maxLen = (el) => {
      const m = el.getAttribute && el.getAttribute('maxlength');
      const n = m ? parseInt(m, 10) : NaN;
      return isNaN(n) || n <= 0 ? null : n;
    };

    const optionTextOf = (el) => {
      if (el.id) {
        try {
          const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
          if (l) {
            const t = textWithoutControls(l, 40) || txt(l);
            if (t) return t;
          }
        } catch (e) { /* ignore */ }
      }
      const wl = el.closest && el.closest('label');
      if (wl) {
        const t = textWithoutControls(wl, 40) || txt(wl);
        if (t) return t;
      }
      const nx = el.nextElementSibling;
      if (nx) {
        const t = textWithoutControls(nx, 40) || txt(nx);
        if (t) return t;
      }
      const pv = el.previousElementSibling;
      if (pv) {
        const t = txt(pv);
        if (t && t.length <= 40) return t;
      }
      return el.value || '';
    };

    const emit = (el, kind, extra) => {
      if (out.length >= MAX_CONTROLS) return null;
      const id = uid++;
      try {
        el.setAttribute('data-jobfill-id', String(id));
      } catch (e) {
        return null;
      }
      const bi = blockInfo(el);
      const rec = Object.assign(
        {
          uid: id,
          kind: kind,
          tag: el.tagName.toLowerCase(),
          type: (el.getAttribute('type') || '').toLowerCase(),
          name: el.getAttribute('name') || '',
          labels: labelsFor(el),
          section: sectionFor(el),
          blockIndex: bi.index,
          blockCount: bi.count,
          nameIndex: bi.nameIndex,
          required: isRequired(el),
          disabled: !!el.disabled,
          readonly: !!el.readOnly,
          maxlength: maxLen(el),
          placeholder: el.getAttribute('placeholder') || '',
          widgetGuess: widgetGuess(el),
          value: '',
          options: [],
        },
        extra || {}
      );
      out.push(rec);
      return rec;
    };

    // ------------- 单选 / 复选：同名（或同容器）的一组算一个控件 -------------
    const byGroup = new Map();
    const groupKeyOf = (el) => {
      if (el.name) return 'name:' + el.name;
      let p = el.parentElement;
      let hop = 0;
      while (p && hop < 4) {
        if (p.querySelectorAll('input[type=radio],input[type=checkbox]').length >= 2) return 'node:' + nodeId(p);
        p = p.parentElement;
        hop++;
      }
      return 'node:' + nodeId(el.parentElement || el);
    };
    document.querySelectorAll('input[type=radio],input[type=checkbox]').forEach((el) => {
      if (!vis(el) && !el.checked) return;
      const k = groupKeyOf(el);
      if (!byGroup.has(k)) byGroup.set(k, []);
      byGroup.get(k).push(el);
    });
    byGroup.forEach((els) => {
      const kind = els[0].getAttribute('type').toLowerCase();
      const first = els[0];
      const id = uid;
      const options = els.map((el) => {
        const ouid = uid++;
        try {
          el.setAttribute('data-jobfill-id', String(ouid));
        } catch (e) { /* ignore */ }
        return {
          uid: ouid,
          text: optionTextOf(el),
          value: el.value || '',
          selected: !!el.checked,
          disabled: !!el.disabled,
        };
      });
      const optTexts = options.map((o) => o.text);
      const labels = labelsFor(first).filter((t) => !optTexts.includes(t));
      const bi = blockInfo(first);
      out.push({
        uid: id,
        kind: kind,
        tag: 'input',
        type: kind,
        name: first.getAttribute('name') || '',
        labels: labels,
        section: sectionFor(first),
        blockIndex: bi.index,
        blockCount: bi.count,
        nameIndex: bi.nameIndex,
        required: isRequired(first),
        disabled: els.every((e) => e.disabled),
        readonly: false,
        maxlength: null,
        placeholder: '',
        widgetGuess: '',
        value: options.filter((o) => o.selected).map((o) => o.text).join('、'),
        options: options,
      });
    });

    // ------------------------------------------------------------- 其余控件
    document.querySelectorAll('input,select,textarea').forEach((el) => {
      const tag = el.tagName.toLowerCase();
      const type = (el.getAttribute('type') || (tag === 'input' ? 'text' : '')).toLowerCase();
      if (tag === 'input') {
        if (['hidden', 'submit', 'button', 'reset', 'image', 'radio', 'checkbox'].includes(type)) return;
      }
      if (!vis(el)) return;

      if (tag === 'select') {
        const multiple = !!el.multiple;
        const opts = Array.from(el.options).map((o) => {
          const ouid = uid++;
          try {
            o.setAttribute('data-jobfill-id', String(ouid));
          } catch (e) { /* ignore */ }
          return { uid: ouid, text: txt(o), value: o.value, selected: !!o.selected, disabled: !!o.disabled };
        });
        emit(el, multiple ? 'multiselect' : 'select', {
          value: multiple ? Array.from(el.selectedOptions).map((o) => txt(o)).join('、') : (el.selectedIndex >= 0 ? txt(el.options[el.selectedIndex]) : ''),
          options: opts,
        });
        return;
      }

      if (tag === 'textarea') {
        emit(el, 'textarea', { value: el.value || '' });
        return;
      }

      if (type === 'file') {
        emit(el, 'file', { value: '' });
        return;
      }
      if (type === 'password') {
        emit(el, 'password', { value: '' });
        return;
      }
      if (type === 'date' || type === 'month' || type === 'datetime-local') {
        emit(el, type, { value: el.value || '' });
        return;
      }
      if (type === 'checkbox' || type === 'radio') return;
      emit(el, 'text', { value: el.value || '' });
    });

    return { ok: true, controls: out, url: location.href, title: document.title };
  } catch (err) {
    return { ok: false, error: String((err && err.stack) || err), url: location.href };
  }
})()
