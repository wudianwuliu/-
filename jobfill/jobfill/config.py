# -*- coding: utf-8 -*-
"""配置与文件路径：profile / field_map / open_questions / learned 的读写。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

PKG_DIR = Path(__file__).resolve().parent
ROOT = PKG_DIR.parent

DEFAULT_PROFILE = ROOT / "profile.yaml"
FIELD_MAP = ROOT / "field_map.yaml"
OPEN_QUESTIONS = ROOT / "open_questions.yaml"
LEARNED = ROOT / "learned.yaml"
REPORT_DIR = ROOT / "reports"


def load_yaml(path: str | os.PathLike, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return {} if default is None else default
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if data is None:
        return {} if default is None else default
    return data


def save_yaml(path: str | os.PathLike, data: Any, header: str = "") -> None:
    """写 YAML，保留中文不转义。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        if header:
            fh.write(header if header.startswith("#") else f"# {header}\n")
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False, width=200, default_flow_style=False)


def check_chrome_debug_port(host: str = "127.0.0.1", port: int = 9222, timeout: float = 0.6) -> bool:
    """探测 Chrome 是否已经开着调试端口。"""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0
