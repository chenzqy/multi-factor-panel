@echo off
chcp 65001 >nul
cd /d "%~dp0"

set PY=
where python >nul 2>nul && set PY=python
if not defined PY (where py >nul 2>nul && set PY=py -3)
if not defined PY (
    echo 未检测到 Python，请先安装 Python 3.10 及以上版本：
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)

echo 第一步：检查并安装运行依赖 ...
%PY% -m pip install -r requirements-desktop.txt
if %errorlevel% neq 0 (
    echo 依赖安装失败，请检查网络后重试。
    pause
    exit /b 1
)

echo 第二步：安装打包工具 PyInstaller ...
%PY% -m pip install "pyinstaller>=6.0"
if %errorlevel% neq 0 (
    echo PyInstaller 安装失败，请检查网络后重试。
    pause
    exit /b 1
)

echo 第三步：开始打包独立 exe ...
%PY% -m PyInstaller --noconfirm --windowed --onefile --name "MultiFactorPanel" ^
  --add-data "config;config" --add-data "data;data" ^
  desktop_app.py

if %errorlevel% neq 0 (
    echo 打包失败，请把上方报错信息反馈排查。
    pause
    exit /b 1
)

echo.
echo 打包完成！可执行文件在本目录的 dist 文件夹内：
echo   dist\MultiFactorPanel.exe
echo 双击即可运行，也可单独复制该 exe 到其他 Windows 电脑使用。
pause
