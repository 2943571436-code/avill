@echo off
chcp 65001 >nul
echo ========================================
echo   启动 Edge 浏览器（调试模式）
echo ========================================
echo.
echo 这是你自己的Edge，书签密码插件都在
echo 打开后去 zhipin.com 登录
echo 登录后运行 run.bat
echo.
start msedge --remote-debugging-port=9222 --user-data-dir="%LOCALAPPDATA%\Microsoft\Edge\User Data" --profile-directory=Default
echo 浏览器已启动！
pause
