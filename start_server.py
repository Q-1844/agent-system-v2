# -*- coding: utf-8 -*-
"""
后端API服务器启动器
自动设置环境变量并启动uvicorn
"""
import os
import sys

# 强制UTF-8编码（解决Windows GBK编码问题）
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

# 设置API密钥
# API Key 禁止写入源码/Git：统一配置在项目根 .env（api_server.py 启动时自动加载）

# 确保当前目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn

if __name__ == "__main__":
    print(f"[+] Python: {sys.executable}")
    print(f"[+] 工作目录: {os.getcwd()}")
    print(f"[+] 启动服务器: http://0.0.0.0:8000")
    uvicorn.run("api_server:app", host="0.0.0.0", port=8000)
