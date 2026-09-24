# _37_fable/_38_detached.py — 脱离式派生器（38.md §3.7：bash 孙进程存活）。
# 用法: python _38_detached.py <script.py> [args...]  → 日志 <script>.log
import os
import subprocess
import sys

CWD = os.path.dirname(os.path.abspath(__file__))
PY = r"D:\Delta Force\python.exe"
if not os.path.exists(PY):
    PY = sys.executable

FLAGS = 0x00000200  # CREATE_NEW_PROCESS_GROUP
script = sys.argv[1]
log = os.path.join(CWD, os.path.splitext(os.path.basename(script))[0] + ".log")
fh = open(log, "w", encoding="utf-8", errors="replace")
p = subprocess.Popen([PY, script] + sys.argv[2:], cwd=CWD,
                     stdout=fh, stderr=subprocess.STDOUT)
print(f"detached pid={p.pid} log={log}")
