@echo off
rem ===================================================================
rem MultiFactorPanel launcher (ASCII only: non-ASCII breaks cmd parsing)
rem Starts launcher.pyw with pythonw.exe, so no console window stays open.
rem Project path contains "(1)", so this script avoids if(...) blocks.
rem ===================================================================
setlocal
set "BASE=%~dp0"
cd /d "%BASE%"

if not exist "%BASE%launcher.pyw" goto :fallback

set "PYW="
if exist "D:\Python312\pythonw.exe" set "PYW=D:\Python312\pythonw.exe"
if not defined PYW for /f "delims=" %%I in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%I"
if not defined PYW for /f "delims=" %%I in ('where python 2^>nul') do if not defined PYW set "PYW=%%~dpIpythonw.exe"

if not defined PYW goto :fallback

start "" "%PYW%" "%BASE%launcher.pyw"
exit /b 0

:fallback
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py -3"
if not defined PY goto :nopython

%PY% -c "import PySide6" >nul 2>nul
if %errorlevel% neq 0 (
    echo First run: installing dependencies, please wait ...
    %PY% -m pip install -r requirements-desktop.txt
)

%PY% desktop_app.py
pause
exit /b 0

:nopython
echo Python 3.10+ not found. Install it from https://www.python.org/downloads/
echo Remember to tick "Add Python to PATH" during installation.
pause
exit /b 1
