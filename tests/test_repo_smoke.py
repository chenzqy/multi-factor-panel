"""语法与配置冒烟测试：不导入 PySide6，只保证仓库里的代码能编译、配置能解析。"""

from __future__ import annotations

import compileall
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"build", "dist", ".venv-mcp", "__pycache__", ".git", "logs"}


def test_every_python_file_compiles():
    ok = compileall.compile_dir(
        str(ROOT),
        quiet=1,
        force=True,
        rx=None,
        maxlevels=10,
    )
    assert ok, "存在无法编译的 Python 文件"
    # compileall 默认会遍历 build/dist，这里显式确认业务代码本身可编译
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_watchlist_yaml_is_valid_and_safe_to_open_source():
    cfg = yaml.safe_load((ROOT / "config" / "watchlist.yaml").read_text(encoding="utf-8"))
    assert isinstance(cfg["stocks"], list) and cfg["stocks"], "A 股自选不能为空"
    assert isinstance(cfg["us_stocks"], list) and cfg["us_stocks"]
    weights = cfg["weights"]
    assert abs(sum(weights.values()) - 1.0) < 1e-6, "因子权重之和应为 1"
    assert cfg["deepseek"]["api_key"] == "", "仓库里不能存在 API Key"
    assert cfg["settings"]["pool_size"] >= 1


def test_universe_csv_files_are_present():
    """离线代码表是全市场降级的最后一道兜底，必须随仓库分发。"""
    for name in ("universe_a.csv", "universe_us.csv"):
        path = ROOT / "data" / name
        assert path.exists(), f"{name} 缺失"
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) > 10, f"{name} 行数异常"
        assert "," in lines[0]


def test_no_hardcoded_absolute_paths():
    """开源项目里不能出现开发机的绝对路径（本测试自身除外）。"""
    marker = "Us" + "ers" + "\\"  # 拼出来，避免这条规则命中测试文件自己
    bad = []
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.parent.name == "tests":
            continue
        text = path.read_text(encoding="utf-8")
        if marker in text or marker.replace("\\", "/") in text:
            bad.append(str(path.relative_to(ROOT)))
    assert not bad, f"以下文件含开发者本机路径: {bad}"
