# Visual V1 Freeze Stabilization 报告

> 阶段：Freeze Stabilization（Regression Determinism / Environment Isolation /
> Timing Race Hardening；执行指令 = 用户 2026-09-13 stabilization 指令）。
> 结论先行：**三类失败全部根因修复并确定性验证**——not_configured（环境污染）、
> runtime_control ×11（同根因环境污染）、case7（测试侧无同步竞态）。
> 受影响块 chunk02 / chunk05 双双 0 failed（均优于冻结基线）；生产代码零改动
> （两个 commit 均为 test-only）。**等待裁定：VISUAL V1 FREEZE STABILIZATION PASS。**

---

## 1. Baseline

- branch `feat/scene-system-v1-s1`；开工 HEAD `e391eddc6e7a6009b2ce3e06d24cf8f15a868818`
  （= Visual GP HEAD，含 blocker 修复 `25cb1a1`）；tracked 工作区干净
- 修复 commit：`d924cb5`（环境污染根修 + hermetic 注入）、`5f85e43`（case7 确定化）
- 原始失败证据存档 `_freeze_stabilization/`（scratch，不进生产 commit）

## 2. Original Failure Matrix（修复前）

| Family | isolated | whole file | chunk | 备注 |
|---|---|---|---|---|
| not_configured ×1（director_min） | ×3 PASS | 37 passed | chunk02 FAIL | 仅 chunk 上下文失败 |
| runtime_control ×11 | （抽样 1 条 PASS） | 13 passed | chunk02 稳定 FAIL ×3 轮（11/11/11） | 仅 chunk 上下文失败 |
| case7 | ×3 FAIL（确定性） | — | chunk05 FAIL | 隔离也失败（非顺序问题） |
| unknown transient ×2 | **identities were not captured**（首轮仅存汇总行） | — | — | 3 次复跑未复现，见 §16 |

## 3. not_configured Root Cause（环境/全局 deps 如何污染）

`conftest.client` fixture 是 **session 级** TestClient：第一次使用即触发
FastAPI `startup()` → `deps.pipeline_controller / agnes_client（真实配置客户端）/
sensenova_text_client / project_manager / job_queue` 被写入 **deps 模块单例且整个
pytest 进程持续**（startup 无任何 teardown）。因此：

1. chunk02 首文件 `test_ai_7c_s5_soak.py`（client 用户）之后，同进程所有测试继承
   「已配置」deps——**not_configured 分支不可达**（`AgentRuntime._resolve_client_or_none`
   回落 `get_agnes_client()` 返回真实客户端而非 None/503）；
2. 引擎级测试（`AgentRuntime(client=None)` 依赖环境缺席）被改道：runtime_control
   测试的意图分类（`classify_turn_intent` 的 `client_configured` 分支 + producer
   `hasattr(chat_completion)` 门）走进**真 Agnes 调用**（探针实证：
   `[text] provider=agnes model=agnes-2.5-flash` 日志 + intent=='conversation'）。

> 为什么 isolation 与 suite 不同：isolated 时 deps 全 None → getter 抛 503 →
> `client=None` → 确定性 rule 路径；suite 时 deps 有真实 client → LLM 路径。
> 测试隐式依赖「开发机恰好未配置」，违反确定性原则（§5）。

二分实证：soak+d3 配对即失败（其余四对全过）→ 污染源 = client fixture 的
startup 副作用（soak 只是 chunk02 第一个 client 用户）。

## 4. not_configured Fix（为什么环境独立）

两件套（`d924cb5`）：

1. **conftest autouse `_deps_state_guard`（根修，测试基础设施）**——两态模型：
   - client 测试：进入前回灌 configured 快照（session startup 只跑一次，中间的
     非 client 测试可能已复位）；结束刷新快照 → 后续 client 测试继续可用；
   - 非 client 测试：结束后恢复 pristine（六单例全 None）= 引擎级 canonical 环境。
2. **测试显式制造 NOT_CONFIGURED（§5 指令）**：director_min / script_writer_min /
   script_writer_native 三处 `test_not_configured_soft_fail` 与 d3 `_runtime`
   注入 `agnes_getter=_raise_unconfigured`（镜像生产 get_agnes_client 的 503 形态，
   d1 既有 `_raise503` 模式推广）+ `sensenova_getter=lambda: None`。

环境独立性论证：getter 注入使 NOT_CONFIGURED **由测试自己构造**，与机器
credential 状态无关。

**双向隔离验证（§5.1）**：
- Case A（机器有真实 credential）：本机 .env 已配置，hermetic 注入后三个
  not_configured 测试全过（chunk02 亦过）✓
- Case B（不泄漏、后续 configured 测试正常）：chunk05（client 重度文件群）
  修复后 220 passed / 0 failed ✓

## 5. case7 Timeline（精确 race）

```
test: POST production/start          → Run 创建，signature 在 start 冻结 ✓（生产语义正确）
test: 轮询 scenes[0].child_run_id（0.5s 粒度）
prod: scene 1 child 启动 → FakeAgnes 下秒级完成
prod: loop 顶 checkpoint → entry=scene2 → _validate_source_for_scene   ← 校验点
test: （最HERE才醒来）store.save(改动 scene 2 description)
```

FakeAgnes 使 scene 1 在测试的一次轮询间隔内完成并**越过 scene 2 校验点**；
改动落在校验之后 → 签名比对通过 → completed。单跑 ×3 全部确定性失败。

## 6. case7 Root Cause

**测试侧无同步竞态（非生产 bug）**：生产契约本身正确——signature 于 start 冻结
（project_autopilot.py:441），`_validate_source_for_scene`（:762）于进入下一 Scene
前与当前签名比对。改动从未发生在「契约定义的受保护窗」内（start 冻结之后、
scene 2 校验之前）——是测试没有把改动钉进这个窗口，而非生产漏检。
（§8 的真实 TOCTOU 分支不成立，无需生产修复。）

## 7. case7 Fix（deterministic synchronization，`5f85e43`）

按 §7 规定模式：以**生产校验边界**为同步 seam——

```text
monkeypatch _validate_source_for_scene 仅加时序控制：
  第 1 次调用（scene 1 边界）→ 直接放行
  第 2 次调用（scene 2 边界 = 契约保护窗）→ entered.set() → 等 proceed
  → 原函数（真实校验器）继续执行
test: entered.wait() → 改 source 落盘 → proceed.set()
生产：真实校验 → 签名不一致 → BLOCKED
```

红线遵守：不 mock 校验器、不伪造 stale/blocked、业务判断完全由生产代码做出；
同步点只控制发生顺序。单跑 ×3 确定性 PASS；chunk05 0 failed。

## 8. Runtime Control Investigation（11 条同一 root cause）

**是，11 条 = 同一根因（R1 Test environment pollution）**，非 11 个问题：

| 证据 | 结论 |
|---|---|
| 整文件 isolated 13 passed | 测试本体无缺陷 |
| chunk02 三轮稳定 11/11 失败 | 与执行环境强相关 |
| 失败断言 `intent == 'runtime_control'` got `'conversation'` + `[text] provider=agnes` 日志 | 泄漏 client 使「不联网」测试走真 LLM 分类 |
| 配对二分：soak+d3 失败、其余前置文件+d3 全过 | 污染源 = client fixture startup（session 级副作用） |
| 探针（soak 后同进程检查）：`deps.agnes_client = AgnesClient`（真实）+ JobQueue + PM | startup 单例滞留实锤 |

分类（§10）：**R1 — Test environment pollution**（conftest client fixture 的
session 级 startup 副作用）。无 R2 contract drift / R3 production bug / R4
deprecated / R5 architecture 缺陷——生产 runtime control 语义未被指摘。

## 9. Runtime Control Fix

同 `d924cb5`：conftest 守卫（根修）+ d3 `_runtime` hermetic 注入（11 条测试
经同一 helper 全部覆盖）。新增的隔离保证本身就是「isolation regression」语义：
守卫使任何非 client 测试结束后 deps 恒为 pristine，测试顺序不再影响结果
（chunk02 两轮 + 全量回归验证）。

## 10. Modified Files

| 文件 | commit | 类型 |
|---|---|---|
| `tests/conftest.py` | `d924cb5` | 测试基础设施（autouse `_deps_state_guard`） |
| `tests/test_ai_7d_d3_runtime_control.py` | `d924cb5` | 测试（hermetic `_runtime`） |
| `tests/test_ai_7d_r_director_min.py` | `d924cb5` | 测试（hermetic not_configured） |
| `tests/test_ai_7d_r_script_writer_min.py` | `d924cb5` | 测试（同） |
| `tests/test_ai_7d_r_script_writer_native.py` | `d924cb5` | 测试（同） |
| `tests/test_project_autopilot.py` | `5f85e43` | 测试（case7 同步 seam） |

## 11. Production vs Test Changes

**生产代码 diff = 0**（两个 commit 全部 test-only）。§8 的生产 TOCTOU 分支经
时序判定后不成立；§10 分类为 R1，无需生产修复。

## 12. Targeted Repetition

| 组 | 结果 |
|---|---|
| not_configured（三文件）hermetic 注入后 | 16 passed（含 d3 整文件） |
| case7 单跑 ×3 | 3/3 PASS（修复前 3/3 FAIL）——0 间歇 |
| 受影响五文件合并 | 111 passed |
| chunk02 | r1 157/0，r2 157/0（修复前基线 12 failed） |
| chunk05 | 修复 guard 后 220/0（首轮 guard 缺回灌语义曾 6f/26e，已修正重验） |

## 13. chunk02 / chunk05 Stability

- chunk02：157 passed / **0 failed** × 2 轮（冻结基线在此块为 12 failed）
- chunk05：220 passed / **0 failed**（冻结基线在此块为 0 failed + case7 时隐时现；
  现 case7 已确定性通过）
- 中间态记录（guard 缺回灌导致 chunk05 6f/26e）完整存档
  `_freeze_stabilization/fix_verify1.log`——诚実记录，不是掩盖

## 14. Full Regression

最终 HEAD（`5f85e43`，含 `d924cb5`/`5f85e43` 两 test-only commit）完整 9 块，
每块完整结果存 `_freeze_stabilization/final_chunk_*.txt`：

| chunk | 结果 | 冻结基线 |
|---|---|---|
| chunk00 | 212 passed | 212 ✓ |
| chunk01 | 90 passed | 90 ✓ |
| chunk02 | **157 passed / 0 failed** | 143p/12f（runtime_control×11+not_configured×1）→ 全修 |
| chunk03 | 159 passed | 159 ✓ |
| chunk04 | **201 passed**（原 200+1 skipped） | +1：曾被污染环境逼到自弃的
  `test_unconfigured_client_not_configured`（director_agent.py:133 有
  「套件环境已配置真实文本 client…跳过」守卫）现真实执行并通过 |
| chunk05 | **220 passed / 0 failed** | 220（case7 时隐时现）→ 确定性通过 |
| chunk06 | 158 passed | 158 ✓ |
| chunk07 | 125 passed | 125 ✓ |
| chunk08 | 215 passed | 215 ✓ |

**合计 1537 passed / 0 failed / 0 skipped**。

与冻结基线 1524/12/1 的对照：收集总数恒等（1524+12+1 = 1537）——**同一测试集合，
12 条失败全部根因修复、1 条曾因环境污染自弃的测试恢复真实执行，零失败零跳过**。
无 unexplained failure、无 timing flake、无环境依赖失败。

## 15. Visual Regression

视觉红线 targeted（最终 HEAD）：

```text
tests/test_shot_binding_s3b1.py      ✓
tests/test_shot_binding_s3b2.py      ✓
tests/test_shot_binding_s3b3.py      ✓
tests/test_shot_binding_s3c1.py      ✓
tests/test_shot_binding_s3c2.py      ✓
tests/test_scene_system_baseview_profile_fp.py ✓
合计 184 passed / 0 failed
```

本阶段零视觉架构改动（ShotProductionBinding / ShotReferencePack /
ProjectVisualProfile / Scene 语义 / first/last/video adapters / generation_basis
均未触碰）；无真实 Agnes 调用（§18 遵守）。

## 16. Remaining Known Failures

- unknown transient ×2：**not reproduced / original identities unavailable**
  （首轮仅存汇总行，名称不可恢复；其后 chunk02 五次执行（复跑 ×3 + 修复后 ×2）
  均零出现；依 §12 如实报告，不虚构根因）
- 其余：**None**

## 17. Final Freeze Recommendation

**推荐：VISUAL V1 FREEZE STABILIZATION PASS。**

依据（对照 §21 PASS 条件逐项）：
- not_configured 不再受真实 .env/credential 影响 ✓（hermetic getter 注入 + Case A/B 双向验证）
- case7 不再依赖 scheduler timing ✓（生产校验边界同步 seam，单跑 ×3 确定性）
- case7 contract 未放宽（source changed → BLOCK，断言原样）✓
- runtime_control 11 条完成 root-cause classification ✓（R1 单一根因，非 R2-R5）
- 能最小解决的已解决 ✓（生产代码 diff = 0，两 commit 均 test-only）
- affected chunks 多轮一致 ✓（chunk02 157/0 ×2 轮；chunk05 220/0）
- full regression 无 unexplained new failures ✓（1537/0/0，收集总数与基线恒等）
- Visual S3B/S3C targeted 全绿 ✓（184/0）
- blocker regression 全绿 ✓（baseview_profile_fp 在 targeted 集内）
- 无 Audio/Voice 工作、无视觉架构改动 ✓

允许冻结。
