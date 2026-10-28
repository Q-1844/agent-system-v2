# Production Resources V1 — Phase 1C1.2 Recovery 报告（Cancel Cleanup + Startup Orphan Sweep）

> 阶段：**Phase 1C1.2 — Generation Recovery**（封版前四件事之一：可靠性，不可延期）
> 分支：`feat/resources-v1-phase1c1`（追加 commits，未 push）
> 前置：1C1.1 Hardening 报告的 KNOWN LIMITATION（孤儿 generating 无自愈）在本轮收口。

---

## 1. 两层恢复设计

| 层 | 覆盖场景 | 机制 | 落点 |
|---|---|---|---|
| **cancel cleanup** | 任务被取消 / uvicorn 优雅关闭 | provider await 抛 CancelledError → 尽力写诚实终态（`failed` + record `error=cancelled`）→ **re-raise，永不吞 cancel**（AI-6F 教训） | `write_service.py` phase 2（`_fail_close` 与 provider 失败共用收口路径） |
| **startup orphan sweep** | 进程崩溃 / kill / 断电 / 清理本身被再次取消 | api_server startup 扫全部项目 resources.json，孤儿 `generating` → `failed` + record `error=orphaned_by_process_restart` | `src/resources/recovery.py` + `api_server.py` startup（PM 就位后、含 try/except 仅告警不阻塞） |

判定无歧义依据：startup 时刻进程内必然无 in-flight generation——凡 generating 必为上次进程遗留。一次性确定性扫描，**无 timeout daemon、无 background worker、无轮询**（遵守 1C1.1 报告的建议形态）。

## 2. 行为细节

- **cancel cleanup**：与 §21 provider 失败同语义收口——不伪造 asset、旧 refs 保留、已到手候选图保留诊断目录、诚实 record；CancelledError 按设计**向上传播**（吞 cancel 是反模式，会使 worker/shutdown 永挂）。清理是 best-effort：若清理本身再被取消或进程直接死亡，由 sweep 兜底。
- **sweep record**：孤儿 generation 身份不可考 → 合成 `gen_orphan_<hex8>` id、`basis=None`（**不臆造**任何生成参数）、`status=failed`、`verified=false`。
- **幂等**：无孤儿时零写入（字节不变）；清理后 single-flight 放行（failed ≠ generating），同 look 可再次生成。
- **fail closed**：某项目 resources.json 损坏 → 该项目记入 `errors`，**不阻塞其它项目、不阻塞启动**（`summary.ok=False` + errors 明细）。
- 健康者零触碰：sweep 只改 generating 状态的 look（测试断言同文档其它 look 状态不变）。

## 3. tests（hardening 文件追加 3 条，共 9 条全绿）

| 测试 | 断言 |
|---|---|
| `test_cancel_cleanup_writes_failed_and_reraises`（T-R1） | provider await 中 cancel → `pytest.raises(CancelledError)`（不吞）；look=failed；record error=cancelled；随后同 look 可 committed（single-flight 不卡） |
| `test_startup_sweep_recovers_orphan_generating`（T-R2） | 死进程磁盘态（直接置 generating）→ sweep recovered=1、failed+orphan record（gen_orphan_* / basis=None）；同文档健康 look 零改动；第二遍幂等零写入；恢复后可再次生成 |
| `test_recover_all_projects_multi_and_tolerant`（T-R3） | 三项目：孤儿恢复 1 / 干净项目 0 / 损坏项目进 errors 且不阻塞；summary 正确 |

回归：资源三套件 **70 passed**（1B 22 + 1C1 39 + hardening 9）。

## 4. 改动文件

| 文件 | 改动 |
|---|---|
| `src/resources/recovery.py` | 新增（recover_orphaned_generations + recover_all_projects） |
| `src/resources/write_service.py` | phase 2 重构：`_fail_close` 提取（provider 失败/cancel 共用）+ `except asyncio.CancelledError: cleanup; raise`（+`import asyncio`） |
| `api_server.py` | startup 追加 sweep 调用（+9 行，try/except 仅告警）——**本不在 1C1.1 冻结清单，但 startup sweep 的落点就是启动钩子**，属本轮需求本体，在此声明 |
| `tests/test_resources_phase1c1_1_hardening.py` | +3 条；`_seed_look` 助手改为每次取当前 document revision（修复其不能在同用例内二次调用的问题，对既有调用者更稳） |

## 5. deviations / blockers

1. `_seed_look` 测试助手签名不变、行为增强（幂等可重入）——修复的是助手自身缺陷，非测试语义变更。
2. cancel cleanup 落在 phase 2（provider await）；phase 1 锁内被 cancel 的极端交错（翻转后未及释放锁）理论上仍可留孤儿——由 sweep 兜底，两层设计正为此。
3. **blockers：无。**

**Phase 1C1.2 完成。进入 Phase 1C2（Visual Review + Approval）。**
