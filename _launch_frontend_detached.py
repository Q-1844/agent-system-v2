# -*- coding: utf-8 -*-
"""脱离式前端（Vite dev）启动器——同后端启动器原理：派生为孙进程孤儿，
避免 harness 空闲回收杀掉。日志写 frontend_runtime.log。"""
import os
import subprocess
import sys

CWD = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(CWD, "frontend_runtime.log")
FLAGS = 0x00000200  # CREATE_NEW_PROCESS_GROUP

# Windows 下 npm 是 npm.cmd；经 shell 找到
try:
    log_f = open(LOG, "ab", buffering=0)
    log_f.write(f"\n==== frontend launcher start {os.getpid()} ====\n".encode("utf-8", "replace"))
    proc = subprocess.Popen(
        "npm run dev",
        cwd=CWD,
        shell=True,
        creationflags=FLAGS,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        close_fds=False,
    )
    print(f"DETACHED_FRONTEND_PID={proc.pid}")
except Exception as e:
    print(f"LAUNCH_FAILED={e}")
    sys.exit(1)
