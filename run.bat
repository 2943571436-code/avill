@echo off
chcp 65001 >nul
echo =============================================
echo   启动 创新创业项目报名系统
echo =============================================

if not exist "sqlite-jdbc-3.45.1.0.jar" (
    echo [错误] 请先运行 compile.bat 下载依赖并编译！
    pause
    exit /b 1
)

if not exist "RegistrationSystem.class" (
    echo [错误] 请先运行 compile.bat 编译源码！
    pause
    exit /b 1
)

java -cp ".;sqlite-jdbc-3.45.1.0.jar" -Dfile.encoding=UTF-8 RegistrationSystem
pause
