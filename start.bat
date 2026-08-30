@echo off
chcp 65001 >nul 2>&1
echo ========================================
echo   动画分镜工作流 - 启动脚本
echo ========================================
echo.

REM 强制UTF-8编码（解决emoji报错）
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

REM API密钥：禁止写入本文件/Git，统一配置在项目根 .env（api_server.py 启动时自动加载）
REM 必需：AGNES_API_KEY / MIMO_API_KEY；可选：SENSENOVA_API_KEY / TAVILY_HUB_API_KEY

REM 查找Python - 优先使用 D:\Delta Force\python.exe
set PYTHON_EXE=
if exist "D:\Delta Force\python.exe" (
    set PYTHON_EXE=D:\Delta Force\python.exe
    goto :found_python
)

REM 尝试系统python
python --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_EXE=python
    goto :found_python
)

echo 错误: 未找到Python，请先安装Python 3.10+
echo 下载地址: https://www.python.org/downloads/
pause
exit /b 1

:found_python
echo [+] Python: %PYTHON_EXE%
echo.

echo [1/2] 启动后端API服务器 (端口8000)...
start "后端API" cmd /c "set PYTHONIOENCODING=utf-8 && set PYTHONUTF8=1 && "%PYTHON_EXE%" api_server.py"

echo [2/2] 等待服务器启动...
timeout /t 3 /nobreak >nul

echo.
echo ========================================
echo   启动完成！
echo ========================================
echo.
echo   简易测试页: http://localhost:8000
echo   API健康检查: http://localhost:8000/api/health
echo.
echo   按任意键打开浏览器...
pause >nul
start http://localhost:8000
