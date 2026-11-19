"""多因子监控面板 —— 无控制台启动器。

双击本文件（或指向 ``pythonw.exe launcher.pyw`` 的快捷方式）即可启动面板，
不会出现黑色 CMD 窗口。旧的 run_windows.bat 仍保留，内部也转到这里。

做的事：
1. 切到项目目录，保证 config/ data/ 相对路径正确；
2. 把 stdout/stderr 重定向到 logs/launcher.log（pythonw 下没有控制台，
   任何 print 都会抛异常，必须接管）；
3. 缺 PySide6 时静默装 requirements-desktop.txt（不弹窗口）；
4. 启动失败用弹窗说明原因，并把堆栈写进日志。
"""

from __future__ import annotations

import os
import runpy
import subprocess
import sys
import traceback

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE, "logs")
LOG_PATH = os.path.join(LOG_DIR, "launcher.log")
APP_PATH = os.path.join(BASE, "desktop_app.py")
REQ_PATH = os.path.join(BASE, "requirements-desktop.txt")
CREATE_NO_WINDOW = 0x08000000


def _redirect_streams() -> None:
    """pythonw 下 sys.stdout/stderr 为 None，重定向到日志文件避免 print 崩。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        # 日志超过 2MB 就重开，避免长期运行越滚越大
        try:
            if os.path.getsize(LOG_PATH) > 2 * 1024 * 1024:
                os.replace(LOG_PATH, LOG_PATH + ".1")
        except OSError:
            pass
        stream = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
        sys.stdout = stream
        sys.stderr = stream
        print("\n===== launcher start", flush=True)
    except Exception:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
        sys.stderr = sys.stdout


def _has_pyside() -> bool:
    try:
        import PySide6  # noqa: F401
        return True
    except Exception:
        return False


def _install_requirements() -> bool:
    if not os.path.exists(REQ_PATH):
        return False
    try:
        done = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", REQ_PATH],
            cwd=BASE,
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return done.returncode == 0
    except Exception:
        return False


def _alert(message: str) -> None:
    """无控制台时只能用弹窗告知失败原因。"""
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("多因子监控面板", message)
        root.destroy()
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "多因子监控面板", 0x10)
        except Exception:
            pass


def main() -> int:
    _redirect_streams()
    # akshare 内部用 tqdm 打进度条（写 stderr），无控制台时会灌满日志
    os.environ.setdefault("TQDM_DISABLE", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.chdir(BASE)
    if BASE not in sys.path:
        sys.path.insert(0, BASE)

    if not _has_pyside():
        _install_requirements()
        if not _has_pyside():
            _alert(
                "缺少 PySide6，无法启动面板。\n\n"
                "请在该目录执行：\n"
                f'{sys.executable} -m pip install -r requirements-desktop.txt\n\n'
                f"目录：{BASE}"
            )
            return 1

    if not os.path.exists(APP_PATH):
        _alert(f"找不到 desktop_app.py：\n{APP_PATH}")
        return 1

    try:
        runpy.run_path(APP_PATH, run_name="__main__")
    except SystemExit:
        raise
    except Exception:
        detail = traceback.format_exc()
        print(detail)
        _alert("面板启动失败，详情已写入：\n" + LOG_PATH + "\n\n" + detail.strip().splitlines()[-1])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
