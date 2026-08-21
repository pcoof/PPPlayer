@echo off
setlocal enabledelayedexpansion
:: 本地一键编译 EXE（Windows）
:: 用法：双击 build.bat 即可
cd /d "%~dp0"

echo [1/4] 准备 Python 虚拟环境...
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
)
set "PY=.venv\Scripts\python.exe"
set "PIP=.venv\Scripts\pip.exe"

echo [2/4] 安装依赖（pywebview 会自动带入 pythonnet）...
"%PIP%" install --upgrade pip >nul 2>&1
"%PIP%" install -r requirements.txt

echo [3/4] 使用 PyInstaller 打包（onedir）...
"%PY%" -m PyInstaller build.spec --noconfirm --clean

echo [4/4] 完成！可执行文件位于：
echo   %CD%\dist\TSPlayer\TSPlayer.exe
if exist "dist\TSPlayer\TSPlayer.exe" (
    start "" "dist\TSPlayer"
) else (
    echo 构建失败，请检查上方报错信息。
)
endlocal
pause
