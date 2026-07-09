@echo off
chcp 65001 >nul
echo =============================================
echo   编译 创新创业项目报名系统
echo =============================================

REM 检查是否存在 SQLite JDBC 驱动
if not exist "sqlite-jdbc-3.45.1.0.jar" (
    echo.
    echo [提示] 未找到 sqlite-jdbc JAR，正在自动下载...
    echo.
    powershell -Command "Invoke-WebRequest -Uri 'https://repo1.maven.org/maven2/org/xerial/sqlite-jdbc/3.45.1.0/sqlite-jdbc-3.45.1.0.jar' -OutFile 'sqlite-jdbc-3.45.1.0.jar'"
    if errorlevel 1 (
        echo [错误] 下载失败！请手动下载 sqlite-jdbc-3.45.1.0.jar 放到当前目录
        echo        下载地址: https://github.com/xerial/sqlite-jdbc/releases
        pause
        exit /b 1
    )
    echo [完成] 下载成功！
)

echo.
echo 正在编译...
javac -cp ".;sqlite-jdbc-3.45.1.0.jar" -encoding UTF-8 RegistrationSystem.java
if errorlevel 1 (
    echo [失败] 编译出错，请检查代码！
    pause
    exit /b 1
)

echo [成功] 编译完成！生成 RegistrationSystem.class
echo.
pause
