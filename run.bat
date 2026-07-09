@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo   BOSS直聘自动化求职工具
echo ========================================
echo.
echo 确保已用"启动浏览器.bat"打开Edge并登录BOSS直聘
echo.
echo 确认无误后按任意键开始...
pause >nul
echo.
"python-embed\python.exe" boss_auto.py
pause
