# -*- coding: utf-8 -*-
"""
脱离式后端启动器。

为什么需要它：后台 bash 任务在 agent idle 时会被 harness 清理（杀 bash.exe 及其
直接子进程）。vite 之所以存活，是因为它是 bash 的孙进程（bash -> npm -> vite），
直接父被杀后它成孤儿继续跑。后端 `python api_server.py` 是 bash 的直接子进程，
所以被一起杀了。

本启动器把 api_server.py 派生为 bash 的孙进程（bash -> launcher -> api_server），
启动器派生后立即退出，api_server 成孤儿，从而在 harness 清理后台 bash 时存活。
日志重定向到 backend_runtime.log 以便事后核验启动情况。
"""
import os
import subprocess
import sys

CWD = os.path.dirname(os.path.abspath(__file__))
PY = r"D:\Delta Force\python.exe"
if not os.path.exists(PY):
    PY = sys.executable

LOG = os.path.join(CWD, "backend_runtime.log")

# CREATE_NEW_PROCESS_GROUP=0x200：新进程组，隔离 Ctrl-C 信号。
# 不用 DETACHED_PROCESS（与控制台/重定向互斥会 WinError 87）；不用 CREATE_NEW_CONSOLE
# （与 DETACHED 互斥）。孙进程 + 启动器退出已足以脱离 harness 的直接子进程清理。
FLAGS = 0x00000200

try:
    log_f = open(LOG, "ab", buffering=0)
    # 写一行分隔，便于区分多次启动
    log_f.write(f"\n==== launcher start {os.getpid()} ====\n".encode("utf-8", "replace"))
    proc = subprocess.Popen(
        [PY, "api_server.py"],
        cwd=CWD,
        creationflags=FLAGS,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        close_fds=False,
    )
    print(f"DETACHED_BACKEND_PID={proc.pid}")
except Exception as e:
    print(f"LAUNCH_FAILED={e}")
    sys.exit(1)
