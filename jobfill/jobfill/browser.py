# -*- coding: utf-8 -*-
"""浏览器接入。

两种模式：
  * attach（默认）——通过 CDP 接管你自己已经登录好的 Chrome。
    登录、短信验证码、滑块全由你自己搞定，工具只负责填表。
    注意：attach 模式下**绝不调用 browser.close()**，否则会把你的 Chrome 一起关掉。
  * launch ——独立启动一个 Chrome（channel="chrome"，用你已装的浏览器，不额外下载）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import check_chrome_debug_port

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


@dataclass
class BrowserSession:
    playwright: Any
    browser: Any
    context: Any
    page: Any
    attached: bool
    port: int = 9222

    def close(self) -> None:
        """收尾：attach 模式只断开连接，绝不关闭用户的浏览器。"""
        try:
            if not self.attached:
                self.browser.close()
        except Exception:
            pass
        try:
            self.playwright.stop()
        except Exception:
            pass

    def __enter__(self) -> "BrowserSession":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def attach_hint(port: int = 9222) -> str:
    return (
        f"没有检测到可接管的 Chrome（127.0.0.1:{port} 没开）。请先在这个项目目录下执行：\n"
        f"    .\\launch_chrome.ps1\n"
        f"它会用独立的配置目录启动 Chrome 并打开调试端口，在弹出的窗口里登录网申系统，\n"
        f"然后把表单页面停在那儿，再回来跑一次本命令。"
    )


def _pick_page(context: Any, match_url: str | None, url: str | None) -> Any:
    pages = [p for p in context.pages if not (p.url or "").startswith(("devtools://", "chrome://newtab"))]
    if match_url:
        for p in pages:
            if match_url in (p.url or ""):
                return p
    if url:
        base = url.split("?")[0].rstrip("/")
        for p in pages:
            if base and base in (p.url or ""):
                return p
    if pages:
        return pages[-1]
    return context.new_page()


def open_session(
    url: str | None = None,
    attach: bool = True,
    port: int = 9222,
    headless: bool = False,
    match_url: str | None = None,
    new_tab: bool = False,
    navigate: bool = True,
    log=print,
) -> BrowserSession:
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    try:
        if attach:
            if not check_chrome_debug_port(port=port):
                raise RuntimeError(attach_hint(port))
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            if not browser.contexts:
                raise RuntimeError("CDP 连上了，但没拿到浏览器上下文（Chrome 可能是空的）。")
            context = browser.contexts[0]
            if new_tab:
                page = context.new_page()
            else:
                page = _pick_page(context, match_url, url)
            attached = True
            log(f"已接管你的 Chrome（共 {len(context.pages)} 个标签页），当前标签页：{page.url[:110]}")
        else:
            browser = pw.chromium.launch(channel="chrome", headless=headless, args=["--start-maximized"])
            context = browser.new_context(viewport=None, locale="zh-CN")
            page = context.new_page()
            attached = False
            log("已启动独立 Chrome（未登录状态，需要你自己登录一次）")

        if navigate and url:
            current = (page.url or "").split("#")[0]
            if current != url.split("#")[0]:
                log(f"打开：{url[:120]}")
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.bring_to_front()
        except Exception:
            pass
        return BrowserSession(pw, browser, context, page, attached, port)
    except Exception:
        try:
            pw.stop()
        except Exception:
            pass
        raise
