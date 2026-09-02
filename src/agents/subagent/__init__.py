# src/agents/subagent/__init__.py
# Director Sub-Agent V1（Part 1 本体论 + Part 2 协议 rev2 的实现包）。
#
# 模块布局（分项 commit，各自可回滚）：
#   fingerprints.py  —— per-scene 派生指纹（零 canonical schema 变更，读时现算）
#   diff_engine.py   —— DiffEngine：desired end state → 原语操作（零 LLM）
#   registry.py      —— SceneAgentRegistry / memory / delegations / proposals store
#   tools.py         —— MD 侧 4 工具 schema + 挂载（subagent_enabled 门控）
#   instance.py      —— Scene Agent 一次性工作实例执行链
#
# 依赖方向（防循环 import）：本包 → director/script_writer/session_governance 单向；
# director/session_governance 对本包只在函数体内懒 import（挂载点/治理收尾/reground 扩展）。
# 硬约束（Part 1 §13 + Part 2 §13）：
#   - canonical 写权限唯一归 Main Director；Scene Agent 白名单物理无写工具；
#   - 跨 delegation 持久物必须带可检测前提（指纹）；rejected/stale 不入库；
#   - 生命周期检测惰性 + 确定性（无守护进程、SW 零感知）；
#   - DiffEngine 零 LLM；subagent_enabled=false 时主链路 bit-for-bit。
