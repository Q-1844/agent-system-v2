"""R2 B 批：候选过渡池 / 接纳协调器 / 确定性派发（协作下一阶段，实验实现）。

组成：
- ledger.py     全局协调账本（本机 SQLite，标准库 sqlite3）：任务/候选/审核回执/
                接纳/事件，唯一键 + 状态 CAS（条件 revision 更新）。
- pool.py       候选域操作：登记、审核回执挂接、失效（含后代级联）。
- promotion.py  正式接纳协议：意图→产物预备→正式写（Project 原子事务）→账本结算
                →事件补发；崩溃恢复按 promotion_id 对账，幂等。
- scheduler.py  最小确定性派发：依赖就绪即入现有 JobQueue，不调 Producer/LLM；
                稳定 dispatch key 领取，崩溃后按 key 从 JobQueue 找回。

实验性质（03 文档）：本包为拟新增能力，未注册任何新 collab 模式；旧模式路径不受影响。
"""
