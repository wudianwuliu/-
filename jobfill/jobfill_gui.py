# -*- coding: utf-8 -*-
"""网申填表助手 —— 双击「网申填表助手.cmd」使用。

界面：深色顶栏 + 扁平卡片 + 强调色主按钮 + 深色日志台。
只用标准库 tkinter，不装任何额外依赖。
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE / "libs", HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import tkinter as tk                                        # noqa: E402
import tkinter.font as tkfont                               # noqa: E402
from tkinter import filedialog, messagebox, ttk             # noqa: E402

PORT = 9222
ADAPTERS = {"北森校招（zhiye.com 等）": "beisen", "通用（其他网申系统）": "generic"}

# ------------------------------------------------------------------ 配色
HEADER = "#0f172a"
STRIP = "#2563eb"
BG = "#eef2f7"
CARD = "#ffffff"
BORDER = "#dbe3ec"
TEXT = "#0f172a"
MUTED = "#64748b"
ACCENT = "#2563eb"
ACCENT_HOVER = "#1d4ed8"
GHOST_HOVER = "#f1f5f9"
OK = "#16a34a"
BAD = "#dc2626"
LOG_BG = "#0b1220"
LOG_FG = "#d7e2f2"
DISABLED_BG = "#e8edf4"


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.q: queue.Queue[str] = queue.Queue()
        self.busy = False
        self.port = PORT
        self.ui_font, self.mono_font = pick_fonts(root)

        root.title("网申填表助手")
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{min(940, sw - 60)}x{min(720, sh - 90)}")
        root.minsize(min(780, sw - 60), min(580, sh - 90))
        root.configure(bg=BG)

        self.adapter = tk.StringVar(value=list(ADAPTERS)[0])
        self.only = tk.StringVar()
        self.resume = tk.StringVar()
        self.photo = tk.StringVar()
        self.add_blocks = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="正在检查环境…")

        self._style_ttk()
        self._build_header()
        self._build_actions()
        self._build_options()
        self._build_log()
        self._build_footer()

        root.after(120, self._drain)
        self.refresh_status()

    # ------------------------------------------------------------ ttk 主题
    def _style_ttk(self) -> None:
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("Jf.TCombobox", fieldbackground=CARD, background=CARD, foreground=TEXT,
                     bordercolor=BORDER, lightcolor=CARD, darkcolor=CARD, arrowcolor=MUTED,
                     padding=4, relief="flat")
        st.map("Jf.TCombobox", fieldbackground=[("readonly", CARD)],
               bordercolor=[("focus", ACCENT)])
        st.configure("Jf.Vertical.TScrollbar", background="#334155", troughcolor="#0b1220",
                     bordercolor="#0b1220", arrowcolor="#94a3b8", relief="flat")
        st.configure("Jf.Horizontal.TProgressbar", background=ACCENT, troughcolor="#dbe3ec",
                     bordercolor="#dbe3ec", lightcolor=ACCENT, darkcolor=ACCENT, thickness=4)
        self.root.option_add("*TCombobox*Listbox.background", CARD)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    # -------------------------------------------------------------- 各区块
    def _build_header(self) -> None:
        tk.Frame(self.root, bg=STRIP, height=3).pack(fill="x")
        head = tk.Frame(self.root, bg=HEADER)
        head.pack(fill="x")
        left = tk.Frame(head, bg=HEADER)
        left.pack(side="left", padx=(20, 0), pady=14)
        tk.Label(left, text="网申填表助手", bg=HEADER, fg="#ffffff",
                 font=(self.ui_font, 15, "bold")).pack(anchor="w")
        tk.Label(left, text="资料填一次，各家复用　·　填完就停，绝不自动提交",
                 bg=HEADER, fg="#8fa3bf", font=(self.ui_font, 9)).pack(anchor="w", pady=(3, 0))

        right = tk.Frame(head, bg=HEADER)
        right.pack(side="right", padx=(0, 20))
        self.dot = tk.Label(right, text="●", bg=HEADER, fg=BAD, font=(self.ui_font, 12))
        self.dot.pack(side="left")
        tk.Label(right, textvariable=self.status, bg=HEADER, fg="#c7d4e6",
                 font=(self.ui_font, 9)).pack(side="left", padx=(6, 0))

        self.progress = ttk.Progressbar(self.root, style="Jf.Horizontal.TProgressbar",
                                        mode="indeterminate")

    def _card(self, title: str, pady=(10, 0)) -> tk.Frame:
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="x", padx=16, pady=pady)
        tk.Label(outer, text=title, bg=BG, fg=MUTED,
                 font=(self.ui_font, 9, "bold")).pack(anchor="w", padx=2, pady=(0, 5))
        card = tk.Frame(outer, bg=CARD, highlightthickness=1,
                        highlightbackground=BORDER, highlightcolor=BORDER)
        card.pack(fill="x")
        return card

    def _build_actions(self) -> None:
        card = self._card("主操作")
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=14, pady=14)
        inner.columnconfigure(0, weight=1)
        inner.columnconfigure(1, weight=1)
        specs = [
            ("▶　启动 / 连接 Chrome", self.act_chrome, "primary"),
            ("◎　体检（资料 + 环境）", self.act_check, "normal"),
            ("◉　预览（只匹配不填）", lambda: self.act_fill(dry=True), "normal"),
            ("✔　开始填表", lambda: self.act_fill(dry=False), "primary"),
        ]
        self.buttons: list[tk.Button] = []
        for i, (text, cmd, kind) in enumerate(specs):
            b = self._button(inner, text, cmd, kind, size=10, pady=11)
            b.grid(row=i // 2, column=i % 2, sticky="ew", padx=5, pady=5)
            self.buttons.append(b)

    def _build_options(self) -> None:
        card = self._card("选项")
        g = tk.Frame(card, bg=CARD)
        g.pack(fill="x", padx=14, pady=12)
        g.columnconfigure(1, weight=3)
        g.columnconfigure(3, weight=2)

        self._label(g, "站点适配器").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Combobox(g, textvariable=self.adapter, values=list(ADAPTERS), state="readonly",
                     style="Jf.TCombobox", font=(self.ui_font, 9)).grid(
            row=0, column=1, sticky="ew", padx=(8, 16), pady=5)
        self._label(g, "只填含此词的区块").grid(row=0, column=2, sticky="w", pady=5)
        self._entry(g, self.only, width=14).grid(row=0, column=3, sticky="ew", padx=(8, 0), pady=5)

        tk.Checkbutton(g, text="自动补足教育 / 实习 / 项目经历", variable=self.add_blocks,
                       bg=CARD, fg=TEXT, activebackground=CARD, activeforeground=TEXT,
                       selectcolor=CARD, font=(self.ui_font, 9), bd=0,
                       highlightthickness=0).grid(row=1, column=0, columnspan=2,
                                                  sticky="w", pady=(6, 0))

        self._file_row(g, 2, "简历附件", self.resume, "选择简历文件")
        self._file_row(g, 3, "证件照", self.photo, "选择证件照")

    def _file_row(self, parent, row: int, label: str, var, title: str) -> None:
        self._label(parent, label).grid(row=row, column=0, sticky="w", pady=5)
        self._entry(parent, var).grid(row=row, column=1, columnspan=2,
                                      sticky="ew", padx=(8, 8), pady=5)
        self._button(parent, "选择", lambda: self._pick(var, title), "normal", size=9).grid(
            row=row, column=3, sticky="e", pady=5)

    def _pick(self, var: tk.StringVar, title: str) -> None:
        """挑文件。注意：Tk 的 filetypes 模式必须用空格分隔，分号会导致筛不出任何文件。"""
        current = var.get().strip()
        start = str(Path(current).parent) if current else str(HERE.parent)
        try:
            path = filedialog.askopenfilename(
                title=title,
                filetypes=[
                    ("简历 / 图片", "*.pdf *.doc *.docx *.png *.jpg *.jpeg"),
                    ("PDF", "*.pdf"),
                    ("图片", "*.png *.jpg *.jpeg"),
                    ("所有文件", "*.*"),
                ],
                initialdir=start,
            )
        except Exception as exc:                                  # noqa: BLE001
            messagebox.showerror("打开文件对话框失败", f"{type(exc).__name__}: {exc}")
            return
        if path:
            var.set(path)
            self.log(f"已选择：{path}")

    def _build_log(self) -> None:
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=16, pady=(10, 0))
        bar = tk.Frame(outer, bg=BG)
        bar.pack(fill="x", pady=(0, 5))
        tk.Label(bar, text="运行日志", bg=BG, fg=MUTED,
                 font=(self.ui_font, 9, "bold")).pack(side="left", padx=2)
        self._button(bar, "复制日志", self.copy_log, "ghost", size=9).pack(side="right", padx=2)
        self._button(bar, "打开最近截图", self.open_shot, "ghost", size=9).pack(side="right", padx=2)
        self._button(bar, "打开报告文件夹", self.open_reports, "ghost", size=9).pack(side="right", padx=2)

        shell = tk.Frame(outer, bg=LOG_BG, highlightthickness=1,
                         highlightbackground="#1e293b", highlightcolor="#1e293b")
        shell.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(shell, orient="vertical", style="Jf.Vertical.TScrollbar")
        self.txt = tk.Text(shell, wrap="word", state="disabled", bd=0, relief="flat",
                           bg=LOG_BG, fg=LOG_FG, insertbackground=LOG_FG,
                           selectbackground="#1e40af", font=(self.mono_font, 9),
                           padx=12, pady=10, height=14)
        self.txt.configure(yscrollcommand=sb.set)
        sb.configure(command=self.txt.yview)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)

    def _build_footer(self) -> None:
        foot = tk.Frame(self.root, bg=BG)
        foot.pack(fill="x", padx=16, pady=(8, 12))
        tk.Label(foot, text="※  本工具只负责「填」，登录 / 验证码 / 提交都由你自己完成",
                 bg=BG, fg=MUTED, font=(self.ui_font, 9)).pack(side="left", padx=2)

    # ------------------------------------------------------------ 控件工厂
    def _label(self, parent, text: str) -> tk.Label:
        return tk.Label(parent, text=text, bg=CARD, fg=TEXT, font=(self.ui_font, 9))

    def _entry(self, parent, var, width: int | None = None) -> tk.Entry:
        return tk.Entry(parent, textvariable=var, width=width, font=(self.ui_font, 9),
                        bg="#fbfdff", fg=TEXT, relief="flat", bd=0,
                        highlightthickness=1, highlightbackground=BORDER,
                        highlightcolor=ACCENT, insertbackground=TEXT)

    def _button(self, parent, text: str, command, kind: str = "normal",
                size: int = 9, pady: int = 6) -> tk.Button:
        if kind == "primary":
            bg, fg, hover = ACCENT, "#ffffff", ACCENT_HOVER
        elif kind == "ghost":
            bg, fg, hover = CARD, MUTED, GHOST_HOVER
        else:
            bg, fg, hover = CARD, TEXT, GHOST_HOVER
        b = tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                      activebackground=hover, activeforeground=fg,
                      font=(self.ui_font, size), relief="flat", bd=0,
                      highlightthickness=1 if kind == "normal" else 0,
                      highlightbackground=BORDER, cursor="hand2",
                      padx=14, pady=pady, anchor="w" if kind != "ghost" else "center")
        b.jf_bg, b.jf_hover = bg, hover
        b.bind("<Enter>", lambda _e, w=b: self._hover(w, True))
        b.bind("<Leave>", lambda _e, w=b: self._hover(w, False))
        return b

    @staticmethod
    def _hover(btn: tk.Button, on: bool) -> None:
        if str(btn["state"]) == "disabled":
            return
        btn.configure(bg=btn.jf_hover if on else btn.jf_bg)

    # ------------------------------------------------------------ 日志与状态
    def log(self, msg: object = "") -> None:
        self.q.put(str(msg))

    def _drain(self) -> None:
        try:
            while True:
                line = self.q.get_nowait()
                self.txt.configure(state="normal")
                self.txt.insert("end", line + "\n")
                self.txt.see("end")
                self.txt.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(120, self._drain)

    def copy_log(self) -> None:
        text = self.txt.get("1.0", "end").strip()
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        messagebox.showinfo("已复制", "日志已复制到剪贴板，可以直接粘给我。")

    def refresh_status(self) -> None:
        from jobfill.chrome import find_browser, is_port_open

        exe = find_browser()
        ok = is_port_open(self.port)
        self.dot.configure(fg=OK if ok else BAD)
        bits = [f"Chrome {self.port}：" + ("已就绪" if ok else "未开启")]
        if exe:
            bits.append(Path(exe).name)
        self.status.set("　·　".join(bits))

    def _enable(self, on: bool) -> None:
        for b in self.buttons:
            b.configure(state="normal" if on else "disabled",
                        bg=b.jf_bg if on else DISABLED_BG)
        if on:
            self.progress.pack_forget()
        else:
            self.progress.pack(fill="x", padx=16, pady=(0, 2))
            self.progress.start(12)

    # ------------------------------------------------------------------ 提问
    def ask(self, prompt: str) -> str:
        box = {"v": ""}
        done = threading.Event()

        def show() -> None:
            win = tk.Toplevel(self.root)
            win.title("未识别字段")
            win.configure(bg=CARD)
            win.transient(self.root)
            win.grab_set()
            win.resizable(False, False)
            tk.Label(win, text="这个字段没能自动识别，答一次以后就自动填",
                     bg=CARD, fg=MUTED, font=(self.ui_font, 9)).pack(anchor="w", padx=18, pady=(16, 4))
            tk.Label(win, text=prompt, bg=CARD, fg=TEXT, justify="left", wraplength=580,
                     font=(self.ui_font, 10, "bold")).pack(anchor="w", padx=18, pady=(0, 10))
            entry = tk.Entry(win, width=58, font=(self.ui_font, 10), relief="flat", bd=0,
                             bg="#fbfdff", highlightthickness=1, highlightbackground=BORDER,
                             highlightcolor=ACCENT)
            entry.pack(padx=18, pady=(0, 14), ipady=5)
            entry.focus_set()

            def ok(_evt=None) -> None:
                box["v"] = entry.get().strip()
                win.destroy()
                done.set()

            def skip(_evt=None) -> None:
                win.destroy()
                done.set()

            bar = tk.Frame(win, bg=CARD)
            bar.pack(pady=(0, 16), padx=18, anchor="e")
            self._button(bar, "跳过", skip, "normal").pack(side="right", padx=(8, 0))
            self._button(bar, "确定并记住", ok, "primary").pack(side="right")
            win.bind("<Return>", ok)
            win.bind("<Escape>", skip)
            win.protocol("WM_DELETE_WINDOW", skip)

        self.root.after(0, show)
        done.wait()
        return box["v"]

    # ------------------------------------------------------------ 任务执行器
    def _job(self, fn) -> None:
        if self.busy:
            return
        self.busy = True
        self._enable(False)

        def run() -> None:
            try:
                fn()
            except Exception as exc:                       # noqa: BLE001
                detail = traceback.format_exc()
                self.log(f"\n！出错了：{type(exc).__name__}: {exc}")
                self.log(detail)
                self.root.after(0, lambda: messagebox.showerror(
                    "出错了", f"{type(exc).__name__}: {exc}\n\n点「复制日志」把详细内容发我。"))
            finally:
                self.busy = False
                self.root.after(0, lambda: (self._enable(True), self.refresh_status()))

        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------------ 动作
    def act_chrome(self) -> None:
        def fn() -> None:
            from jobfill.chrome import (PROFILE_DIR, find_browser, is_port_open,
                                        launch_chrome, wait_for_port)

            if is_port_open(self.port):
                self.log(f"端口 {self.port} 已就绪，可以直接用。")
                return
            exe = find_browser()
            if not exe:
                raise RuntimeError("没找到 Chrome 或 Edge，请先装一个浏览器。")
            self.log(f"启动浏览器：{exe}")
            self.log(f"独立配置目录：{PROFILE_DIR}")
            launch_chrome(exe, port=self.port, profile_dir=PROFILE_DIR)
            if wait_for_port(self.port, 20):
                self.log("调试端口已就绪 ✔")
                self.log("")
                self.log("接下来：")
                self.log("  1) 在这个新开的 Chrome 里登录网申系统（登录 / 验证码 / 滑块你自己来）")
                self.log("  2) 把「填写简历」页面停在当前标签页")
                self.log("  3) 回来点「✅ 开始填表」")
                self.log("")
                self.log(f"登录状态保存在 {PROFILE_DIR}，下次不用重新登录。")
            else:
                raise RuntimeError(f"浏览器起来了，但调试端口 {self.port} 没开。\n"
                                   "通常是 Chrome 已用同一配置目录在跑——完全退出 Chrome 后重试。")

        self._job(fn)

    def act_check(self) -> None:
        def fn() -> None:
            from jobfill.chrome import find_browser, is_port_open
            from jobfill.cli import load_context
            from jobfill.profile import flatten, missing_fields

            self.log("═" * 58)
            self.log("环境")
            self.log(f"  Python      {sys.version.split()[0]}")
            try:
                import playwright                                 # noqa: F401
                self.log("  playwright  已就绪")
            except Exception as exc:                              # noqa: BLE001
                self.log(f"  playwright  缺失：{exc}")
            self.log(f"  浏览器      {find_browser() or '没找到'}")
            self.log(f"  调试端口    {'已就绪' if is_port_open(self.port) else '未开启（先点①）'}")

            profile, field_map, learned, questions, adapter = load_context(
                adapter_hint=ADAPTERS[self.adapter.get()])
            fields, _ = flatten(profile, field_map)
            missing = missing_fields(profile)
            self.log("")
            self.log("资料")
            self.log(f"  适配器      {adapter.name}")
            self.log(f"  已填 {len(fields)} 项 / 待补充 {len(missing)} 项")
            if missing:
                self.log("  还没值的字段（填表时会跳过，补一次以后都不用填）：")
                for m in missing[:15]:
                    self.log(f"    · {m}")
                if len(missing) > 15:
                    self.log(f"    …… 还有 {len(missing) - 15} 项")
            self.log("")
            self.log(f"  开放题 {len(questions)} 道 / 已记住字段 {len(learned.get('answers') or {})} 个")
            self.log("═" * 58)

        self._job(fn)

    def act_fill(self, dry: bool) -> None:
        def fn() -> None:
            from jobfill.browser import open_session
            from jobfill.cli import load_context
            from jobfill.runner import FillOptions, cleanup, run_fill

            profile, field_map, learned, questions, adapter = load_context(
                adapter_hint=ADAPTERS[self.adapter.get()])
            opts = FillOptions(
                learn=not dry,
                dry_run=dry,
                resume_file=self.resume.get().strip() or None,
                photo_file=self.photo.get().strip() or None,
                only=self.only.get().strip() or None,
                add_blocks=self.add_blocks.get(),
                ask=self.ask,
            )
            session = open_session(url=None, attach=True, port=self.port, log=self.log)
            try:
                run_fill(session.page, profile, field_map, learned, questions, adapter, opts, log=self.log)
                if not dry:
                    self.log("")
                    self.log("★ 填完了。核对无误后请自己点提交 —— 本工具不会替你提交。")
            finally:
                cleanup(session.page)
                session.close()

        self._job(fn)

    def open_reports(self) -> None:
        from jobfill import config

        config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(config.REPORT_DIR))                    # noqa: S606

    def open_shot(self) -> None:
        from jobfill import config

        shots = sorted(config.REPORT_DIR.glob("fill_*.png"), key=lambda p: p.stat().st_mtime)
        if not shots:
            messagebox.showinfo("还没有截图", "先跑一次填表，会自动截图。")
            return
        os.startfile(str(shots[-1]))                            # noqa: S606


def pick_fonts(root: tk.Tk) -> tuple[str, str]:
    fams = set(tkfont.families(root))
    ui = next((f for f in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI") if f in fams),
              "TkDefaultFont")
    mono = next((f for f in ("Cascadia Mono", "Consolas", "Courier New") if f in fams),
                "TkFixedFont")
    return ui, mono


def enable_dpi_awareness() -> None:
    """高 DPI 屏幕上文字不糊。"""
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def main() -> int:
    enable_dpi_awareness()
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                          # noqa: BLE001
        tb = traceback.format_exc()
        try:
            (HERE / "gui-error.log").write_text(tb, encoding="utf-8")
        except Exception:
            pass
        try:
            from tkinter import messagebox as _mb

            _mb.showerror("启动失败", tb[-1800:])
        except Exception:
            print(tb)
        sys.exit(1)
