"""界面状态持久化。

把「新闻自动刷新」「刷新间隔」「来源勾选」「条数」这类纯界面偏好单独存在
``config/ui_state.json``，与 watchlist.yaml 解耦：既不会污染用户的选股配置，
也不会因为 yaml 重新生成而丢失。

写盘失败（例如打包后运行在只读目录）时自动回落到用户目录，保证开关记忆始终有效。
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict

from src import paths

_FILENAME = "ui_state.json"
_APP_DIR_NAME = "MultiFactorPanel"


def _candidate_dirs() -> list:
    dirs = [os.path.join(paths.base_dir(), "config")]
    appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if not appdata:
        appdata = os.path.expanduser("~")
    dirs.append(os.path.join(appdata, _APP_DIR_NAME, "config"))
    return dirs


def state_dir() -> str:
    """返回可写的状态目录（优先项目 config\，只读时回落到用户目录）。"""
    for d in _candidate_dirs():
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".write_probe")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("1")
            os.remove(probe)
            return d
        except OSError:
            continue
    return tempfile.gettempdir()


def state_path() -> str:
    return os.path.join(state_dir(), _FILENAME)


def load_state() -> Dict[str, Any]:
    """读取全部界面状态；文件缺失或损坏时返回空字典。"""
    try:
        with open(state_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def get_section(name: str) -> Dict[str, Any]:
    section = load_state().get(name)
    return section if isinstance(section, dict) else {}


def update_section(name: str, values: Dict[str, Any]) -> Dict[str, Any]:
    """合并写入某个分组（原子替换，避免半截文件）。"""
    state = load_state()
    section = state.get(name)
    if not isinstance(section, dict):
        section = {}
    section.update(values)
    state[name] = section
    target = state_path()
    tmp = target + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
    except OSError:
        return section
    return section
