# -*- coding: utf-8 -*-
"""启动 Chrome（纯 Python，不依赖 PowerShell）。

为什么不用 launch_chrome.ps1：本机执行策略是 Restricted，
双击 .ps1 会被拒绝执行。这里用 subprocess 直接调 chrome.exe，绕开这个问题。
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from .config import check_chrome_debug_port

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

# 独立配置目录：不影响你日常的 Chrome，登录一次长期有效
PROFILE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "jobfill-chrome"


def find_browser(prefer_edge: bool = False) -> str | None:
    found = [p for p in CHROME_CANDIDATES if Path(p).exists()]
    if not found:
        return None
    if prefer_edge:
        for p in found:
            if "msedge" in p.lower():
                return p
    return found[0]


def is_port_open(port: int = 9222) -> bool:
    return check_chrome_debug_port(port=port)


def launch_chrome(
    exe: str,
    port: int = 9222,
    profile_dir: Path | str | None = None,
    url: str | None = None,
) -> subprocess.Popen:
    """启动带调试端口的浏览器。profile_dir=None 表示用你日常的配置（需先完全退出 Chrome）。"""
    args = [
        exe,
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if profile_dir:
        d = Path(profile_dir)
        d.mkdir(parents=True, exist_ok=True)
        args.append(f"--user-data-dir={d}")
    if url:
        args.append(url)
    return subprocess.Popen(args, close_fds=True)


def wait_for_port(port: int = 9222, seconds: float = 15.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if is_port_open(port):
            return True
        time.sleep(0.5)
    return is_port_open(port)
