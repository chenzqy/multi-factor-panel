"""资源路径解析：兼容源码运行与 PyInstaller 打包后的临时目录。"""

from __future__ import annotations

import os
import sys


def base_dir() -> str:
    """返回资源根目录。

    - 源码运行：项目根目录（本文件上两级）；
    - PyInstaller 打包：解压临时目录 ``sys._MEIPASS``。
    """
    if getattr(sys, "frozen", False):  # 已打包
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(rel: str) -> str:
    """拼接相对资源路径（如 config/watchlist.yaml）。"""
    return os.path.join(base_dir(), rel)
