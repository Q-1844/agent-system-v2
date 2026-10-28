# Production Resources V1 — Phase 1C1.1 Hardening 报告（Per-Look Generation Single-Flight）

> 阶段：**Phase 1C1.1 — Per-Look Generation Single-Flight Hardening**（极小可靠性补丁，只解决一个问题）
> 分支：`feat/resources-v1-phase1c1`（追加 1 个 fix commit + 1 个 docs commit，均未 push）
> 前置：Phase 1C1 主体已验收（`be0387f`）
> 纪律：不进 Visual Review / Approval / 前端；不扩功能；无 timeout daemon / background worker。

---

## 1. 原实现是否真的存在双 generation 窗口 —— **是（T1 红先证）**

### 代码确认
`generate_look_candidates` 阶段 1 短锁（原 `write_service.py:545-579`）只做：not_found 检查 → entry OCC（expected_revision==look revision）→ compute basis → missing_character_reference fail closed → **直接翻转 `generation_status=generating` 落盘**。整条路径**没有任何 `generation_status == GENERATING` 检查**。

### 攻击路径（执行文档设想的场景，逐行成立）
1. Generation A：阶段 1 把 `look.generation_status=generating` 落盘（该翻转本身改变 look_revision），释放 `project_write_lock`，进入 Agnes await（~10-60s）。
2. 调用方读取**最新** Look revision（已含 A 的 generating 翻转）。
3. Generation B 携该最新 revision 进入阶段 1：**OCC 通过**（revision 匹配）→ 无状态检查 → B 把 `prev_status` 捕获为 **"generating"（A 的标记！）** → 翻转 generating → 保存 → 进入 provider。

### 实证（修复前红测试）
`tests/test_resources_phase1c1_1_hardening.py::test_t1_same_look_second_generation_rejected_while_first_in_flight` 在修复前的实现上运行：**红**（`r_b["ok"] is True`——B 真实进入 provider 并成功 commit；T6 同红）。窗口真实存在，不是理论推演。

### 后果（修复前）
- 同一 look 双 generation 并行：双倍 provider 花费；两者 basis 语义指纹相同（不包含 reference_asset_ids），**双双通过 stale 校验、双双 commit**（重复候选图）。
- **次生 bug**：B 的 `prev_status` 捕获到 A 的 "generating"——若 B 走到 stale 路径，会把 look "还原"成 generating，**永久卡死**在 generating。

## 2. 修复方式

阶段 1 短锁内、**OCC 之后立即**增加 per-look single-flight 检查（`src/resources/write_service.py`）：

```python
if look.generation_status == GenerationStatus.GENERATING.value:
    return self._gen_result(generation_id, look_id, ok=False, status="failed",
                            error="generation_in_progress", detail="...")
```

拒绝语义（逐条满足执行文档"目标行为"）：不调用 Agnes、不创建 generation record、不修改 canonical、不替换已有 generation 状态。

**为什么只靠这一个检查就充分（不依赖 expected_revision，也无 TOCTOU）**：该检查与 A 的 generating 翻转在**同一把 `project_write_lock` 短临界区内串行化**——B 要么在 A 翻转前拿到锁（此时状态非 generating，B 成为"那个 active generation"），要么在 A 翻转后拿到锁（读到 generating → 拒绝）。单进程内不存在第三种交错。结构性收益：**`prev_status` 从此不可能捕获到 "generating"**（能走到捕获那一行，说明状态必非 generating），次生卡死 bug 被同一改动根除。

## 3. single-flight 粒度

- **仅 same look_id**：generating 标记存在 look 自身状态上，检查只看该 look。
- 不同 look 之间零新增串行化：T2 实证两个 look 的 generation **同时**处于 provider await 中（`asyncio.gather` 两个 on_call 事件都 set 后才释放），互不阻塞，双双 committed。
- 未引入 project 级/模块级互斥；未引入进程内 per-look asyncio 锁（canonical 状态 + 短锁串行化已充分，避免双真相源）。

## 4. tests

新增 `tests/test_resources_phase1c1_1_hardening.py` — **6 passed**：

| # | 测试 | 断言 |
|---|---|---|
| T1 | 同 look：A 挂在 provider await，以最新 revision（含 generating 翻转）发起 B | B `error=generation_in_progress`；`fake_b.calls==0`（未调 provider）；canonical 仍是 A 的 generating；只有 A 继续（committed）。**修复前此测试红（实证窗口）** |
| T2 | 不同 look：A、B 同时生成 | 两者同时进入 provider（无全局串行化）；双双 committed；各自 look ready |
| T3 | A provider failure 后同 look 再生成 | failed 状态可重入；重试 committed |
| T4 | A stale_basis 结束后同 look 再生成 | 还原 none 后可重入；重试 committed |
| T5 | A successful commit 后同 look 再生成 | ready 可重入；两次候选都挂上 |
| T6 | generation_in_progress 零副作用 | B 拒绝前后：record 数只差 A 自己那 1 条、无 B 的 record、reference_assets 只有 A 的候选、look 状态不被 B 触碰 |

## 5. KNOWN LIMITATION：orphaned generating state after process crash

### 协议自查（正常路径无永久卡死）
provider failed → `status=failed`（锁内写）；stale basis → 还原生成前状态；successful commit → `ready`。三条终态路径都在阶段 3 锁内落地——正常完成不卡 generating（T3/T4/T5 即证据）。

### 崩溃场景调查（实测探针 `_tb_orphan_probe.py`，scratch）
若 Python 进程在阶段 1 翻转 generating 落盘**之后**、阶段 3 终态写之前崩溃/kill：
1. `resources.json` 中该 look **永久留下 `generation_status="generating"`**；
2. "重启"后（全新 service 实例）再次 generate → **被 single-flight 以 `generation_in_progress` 拒绝**，无自愈路径。
3. 探针同时确认：任务被 cancel（CancelledError）时**按设计不被 `except Exception` 捕获、向上传播**（AI-6F 教训：吞 cancel 是反模式）——阶段 3 清理不执行，同样留下孤儿 generating。即该限制覆盖崩溃 / kill / 任务取消整个"异常终止"族。

### 与修复前对比（诚实记录）
修复前孤儿 generating 会被下一次生成"野蛮覆盖"（无检查直接翻转）——看似能自愈，实则正是双 generation 窗口本身。修复后单飞（single-flight）让孤儿变成**硬卡死**。这是正确取舍（数据一致性 > 表面可用），但使恢复机制从"可有可无"变为"下一阶段应有"。

### 下一阶段最小 recovery 建议（本轮未实现，按令不加 daemon）
**startup sweep（推荐，最小）**：api_server 启动时（此刻必然无 in-flight generation）扫描各项目 `output/resources.json`，凡 `look.generation_status=="generating"` → 持 write_lock 原子改写为 `failed` + 追加一条诚实 record（`error="orphaned_by_process_restart"`）。一次性、确定性、无后台任务。可选精化：look 上记 `active_generation_id` + 进程 boot 标记，仅对非本进程 boot 的孤儿自动清理（支持未来多 worker，当前单进程不需要）。

## 6. regression（T7）

| 套件 | 结果 |
|---|---|
| `tests/test_resources_phase1b.py` | 22 passed |
| `tests/test_resources_phase1c1.py` | 39 passed |
| `tests/test_resources_phase1c1_1_hardening.py` | 6 passed |
| 关键 Agent 套件 13 文件 + Script Writer 三件套 | 311 passed, 1 skipped；2 个 `test_not_configured_soft_fail` 为**已知套件内自扰**（Phase 1B 报告 §9.2 同族），单跑 2 passed 复绿，与本轮无关 |

**零新增失败。**

## 7. commit

| commit | 内容 |
|---|---|
| （fix，本轮） | `fix(resources): Phase 1C1.1 — per-look generation single-flight hardening`（write_service 检查 + router 409 映射 + 6 测试） |
| （docs，本轮） | 本报告 |

改动面：`src/resources/write_service.py`（+10 行检查与注释）、`routers/resources.py`（+1 行错误映射，属"新增错误映射确实必要"）、`tests/test_resources_phase1c1_1_hardening.py`（新文件）。**冻结区零触碰**（Agent/Director/Character/Shot/Project schema/前端/Shared Library 全部未动；`src/resources/*` 之外仅 routers/resources.py 一行映射）。

## 8. deviations / blockers

**deviations**：
1. T6 拆为独立用例但复用 T1 场景（挂起 A → 拒绝 B → 释放 A），断言聚焦零副作用维度；与 T1 共享场景骨架减少脆弱的并发样板。
2. 崩溃探针用**任务取消**模拟异常终止（同族语义：CancelledError 按设计向上传播 → 无清理），未真实 kill 进程——对"阶段 1 落盘后阶段 3 永不执行"这一状态的证明等价，且可重复、零污染。
3. 单飞（single-flight）检查放在 OCC 之后、deps/agnes 客户端构建之前——拒绝路径零副作用（连客户端对象都不构建）；执行文档给出的顺序即"1. OCC → 2. 读最新 Look → 3. 状态检查"，一致。

**blockers**：**无。**

## 停止核对

- [x] 双 generation 窗口验证（T1 红先证 + 代码行级确认）
- [x] 修复（阶段 1 短锁内状态检查，拒绝语义 7 条全满足）
- [x] 粒度 = same look_id（T2 证明不同 look 并发不受限）
- [x] T1-T6 全绿
- [x] T7 回归（61 资源 + 311 关键 Agent，零新增失败）
- [x] crash orphan 调查 + KNOWN LIMITATION 记录 + 最小 recovery 建议（未实现，未加 daemon）
- [x] 报告（本文件）

**Phase 1C1.1 完成。未进入 Phase 1C2。等待确认。**
