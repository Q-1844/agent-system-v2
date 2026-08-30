# -*- coding: utf-8 -*-
"""
动画分镜工作流 - 一键启动脚本
自动检测Python路径，设置环境变量，启动后端API服务器
"""

import subprocess
import sys
import os
import shutil

# ============ 配置 ============
# API Key 禁止写入源码/Git：统一配置在项目根 .env（api_server.py 启动时自动加载）。
# 必需：AGNES_API_KEY / MIMO_API_KEY；可选：SENSENOVA_API_KEY / TAVILY_HUB_API_KEY。
API_KEYS = {}   # 留空 = 使用 .env / 现有环境变量，源码不再注入任何密钥
PORT = 8000


def find_python():
    """查找可用的Python解释器"""
    # 1. 当前脚本用的解释器一定可用
    if sys.executable and os.path.exists(sys.executable):
        return sys.executable

    # 2. 尝试常见路径
    candidates = [
        r"D:\Delta Force\python.exe",
        r"C:\Python313\python.exe",
        r"C:\Python312\python.exe",
        r"C:\Python311\python.exe",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p

    # 3. 尝试 where python
    try:
        result = subprocess.run(
            ["where", "python"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if line and os.path.exists(line) and "WindowsApps" not in line:
                return line
    except Exception:
        pass

    return None


def check_deps(python_exe):
    """检查Python依赖是否安装"""
    print("[*] 检查Python依赖...")
    try:
        result = subprocess.run(
            [python_exe, "-c", "import fastapi; import uvicorn; import httpx; print('OK')"],
            capture_output=True, text=True, timeout=10,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        if "OK" in result.stdout:
            print("[+] 依赖已安装")
            return True
        else:
            print("[!] 缺少依赖，正在安装...")
            return False
    except Exception as e:
        print(f"[!] 检查依赖失败: {e}")
        return False


def install_deps(python_exe):
    """安装Python依赖"""
    req_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")
    if not os.path.exists(req_file):
        print("[!] requirements.txt 不存在，跳过安装")
        return
    print("[*] 安装依赖...")
    subprocess.run(
        [python_exe, "-m", "pip", "install", "-r", req_file, "-q"],
        timeout=120,
    )
    print("[+] 依赖安装完成")


def start_server(python_exe):
    """启动API服务器"""
    # 设置环境变量
    for key, val in API_KEYS.items():
        if not os.environ.get(key):
            os.environ[key] = val
            print(f"[+] 设置环境变量: {key}")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    server_script = os.path.join(script_dir, "api_server.py")

    print(f"\n{'='*50}")
    print(f"  动画分镜工作流 - 后端API服务器")
    print(f"{'='*50}")
    print(f"  Python: {python_exe}")
    print(f"  端口:   {PORT}")
    print(f"  访问:   http://localhost:{PORT}")
    print(f"{'='*50}\n")

    # 启动 uvicorn
    try:
        subprocess.run(
            [
                python_exe, "-m", "uvicorn",
                "api_server:app",
                "--host", "0.0.0.0",
                "--port", str(PORT),
            ],
            cwd=script_dir,
        )
    except KeyboardInterrupt:
        print("\n[!] 服务器已停止")
    except Exception as e:
        print(f"\n[!] 服务器启动失败: {e}")


def main():
    print("=" * 50)
    print("  动画分镜工作流 - 启动中...")
    print("=" * 50)

    # 查找Python
    python_exe = find_python()
    if not python_exe:
        print("[!] 未找到可用的Python解释器")
        print("[!] 请安装Python 3.10+: https://www.python.org/downloads/")
        input("按回车键退出...")
        return

    print(f"[+] Python: {python_exe}")

    # 检查并安装依赖
    if not check_deps(python_exe):
        install_deps(python_exe)
        if not check_deps(python_exe):
            print("[!] 依赖安装失败，请手动运行:")
            print(f"    {python_exe} -m pip install -r requirements.txt")
            input("按回车键退出...")
            return

    # 启动服务器
    start_server(python_exe)


if __name__ == "__main__":
    main()
