# Production Resources V1 — Phase 1C2 报告（Visual Review + Approval）

> 阶段：**Phase 1C2 — Visual Review + Approval**（封版前四件事之二：候选图真实检查 + 显式审批）
> 分支：`feat/resources-v1-phase1c1`（追加 commits，未 push）
> 核心语义（双向，严格执行）：**review pass ≠ 自动 approved；无 pass 证据不能 approve。**

---

## 1. Visual Review（真实维度检查 + 独立证据）

### 实现（`src/resources/look_review.py`，新文件）
- **三维度检查**：`identity_consistency`（身份：脸/发型/体型比例）/ `costume_consistency`（服装：设计/颜色/材质/完整度）/ `visual_defects`（明显缺陷：肢体手脸畸形、伪影、水印文字、关键设计信息遮挡、非全身构图）。
- **真实 vision 调用**：复用 `AgnesClient.chat_completion_vision`（显式 `agnes-2.5-flash`，data URI 输入——local 可读优先，与 Director 视觉审查同契约；不 import 任何 Agent 模块，Director/`visual.review_image` 零触碰——并行路径决策按 Phase 1A §10.3）。
- **期望基准**：当前 canonical 的角色描述 + 服装快照（name/description/design_traits/materials/colors）+ appearance 描述/delta + view_purpose，全部进入确定性审查 prompt；`reviewed_basis_fingerprint` 记录"本次审查对照的期望"指纹。
- **逐候选隔离**：每候选一次 vision 调用；provider 错误/JSON 解析失败/图片不可读 → 该候选 `uncertain`，不影响其它候选。
- **overall 确定性推导，不信任模型自报**：任何维度 fail→`needs_fix`；全 pass→`pass`；其余（concern/维度缺失）→`uncertain`。模型自报留档 `model_overall` 供审计。**维度缺失一律不判 pass（fail closed）**。
- **证据独立持久化**：`output/resources/review_records.jsonl`（append-only，与 generation_records.jsonl 对称且相互独立）：review_id / operation / look_id / reviewed_basis_fingerprint / model / prompt / candidate_reviews[逐候选三维度+notes+overall+model_overall+summary] / usage / started_at / completed_at / status / error / verified。无 base64、无 canonical 写入。

### JSON 解析容错
剥 code fence → 找首个平衡 `{...}` → json.loads；失败 → `parse_failed`（fail closed，不可作为 pass 证据）。

## 2. Approval（显式动作 + pass 证据门槛）

### 实现（`write_service.approve_look_reference` / `revoke_look_approval`）
- **approve(look_id, asset_id, expected_revision, set_cover?)**：短锁内 OCC → asset 必须在 `look.reference_asset_ids` 内（`not_in_candidates` 拒绝）→ **门槛：该 asset 在本 look 下最近一次审查必须 `overall=pass`**（`has_pass_evidence`；否则 `review_not_passed`，HTTP 409）→ 已在 approved 内则幂等 unchanged → 追加 `approved_reference_asset_ids` + `approval_status=approved`；cover 语义：`set_cover=None` 时首个 approved 自动成为封面（展示默认，非审批语义），`True` 强制、`False` 不动；save 后重读 verified。
- **revoke**：移除 approved；cover 若指向该 asset 置 None；approved 变空 → `approval_status` 回 unreviewed；候选仍在（不删 reference_asset_ids）。
- **双向语义落点**：pass 后 canonical 无任何自动变化（正向不自动批准，有专项测试断言）；无 pass 证据无法 approve（反向门槛，mock 3 态 + HTTP 409 测试）。

## 3. API 契约

| 端点 | 说明 |
|---|---|
| `POST /looks/{id}/review` | `{asset_ids?(缺省=全部候选), view_purpose?}` → 逐候选 verdict（写 review_records，不写 canonical） |
| `POST /looks/{id}/approve` | `{asset_id, expected_revision, set_cover?}`；无 pass 证据 → 409 `review_not_passed` |
| `POST /looks/{id}/revoke-approval` | `{asset_id, expected_revision}` |
| `GET /looks/{id}`（增强） | +`latest_reviews: {asset_id: {status, overall, 三维度, summary}}`（证据只读视图） |

Phase 1B 白名单契约测试同步扩展 3 个端点（既有 21 条 Phase 1B 测试不变）。

## 4. mock tests（`tests/test_resources_phase1c2.py`，9 条全绿）

review：pass 全流程（vision 契约面/prompt 含期望基准/证据落盘无 base64/pass 后 approved 仍空/latest_reviews）/ overall 服务端推导不信任模型（needs_fix+concern 双态）/ fence 剥离+parse_failed+provider_error 逐候选隔离 / image_unreadable+not_in_candidates+not_found / derive_overall 纯函数 + prompt 确定性。
approve：无证据/needs_fix/uncertain 三态全拒 + pass 通过（approved ids+状态+自动封面+verified）/ 幂等 / not_in_candidates / OCC / revoke 全语义（cover 清空、空则回 unreviewed、候选保留、幂等）。
HTTP：generate→未审查 approve 409→review→approve 200→read 反映全链路。

## 5. 真机 E2E（`__res_1c1_e2e`，driver `_e2e_resources_1c2.py`）

对 Phase 1C1 真实生成的候选（火焰机甲·Black Coat）执行 **3 次真实 agnes-2.5-flash vision 审查**：

| 次序 | identity | costume | defects | derived overall | model_overall | 备注 |
|---|---|---|---|---|---|---|
| 1 | pass | **concern**（高领/银扣与规格有偏差） | pass | **uncertain** | needs_fix | 给出了具体的服装改进反馈 |
| 2 | pass | pass | pass | **pass** | pass | "meets all three criteria" |
| 3 | pass | pass | pass | **pass** | pass | 复跑确认 |

- **审查反馈真实有效**：第 1 次审查明确指出"coat needs refinement to clearly match the specified high collar, silver buttons, wool"——维度化、可执行，不是走过场。
- **审查者波动性实测存在**（uncertain→pass）：这正是"review 只是证据、**人工 approve 才是终审**"的架构理由——门槛取最近一次证据，人工保留最终决定权。
- approve 真机执行成功：`approved=[ref_6f97a15706b1]`、`approval_status=approved`、`cover=ref_6f97a15706b1`；review 后 approved 仍空（pass≠approved）在真机断言。
- 重启语义：全新实例重读，latest_reviews=pass、证据 3 条完整、双 jsonl 均无 base64。
- （run1 时一次 GBK 控制台打印崩溃属 driver scratch 问题，与产品代码无关；状态变更均已在崩溃前验证。）

## 6. 改动文件

| 文件 | 改动 |
|---|---|
| `src/resources/look_review.py` | 新增（审查服务 + 证据读写 + 门槛查询） |
| `src/resources/write_service.py` | +approve_look_reference / revoke_look_approval |
| `src/resources/generation_records.py` | 路径参数化（generation/review 双流共用 IO；review_records_path 新增） |
| `src/resources/service.py` | read_look +latest_reviews |
| `routers/resources.py` | +3 端点 + 3 错误映射（review_not_passed 409 / not_in_candidates 400 / no_candidates 400） |
| `tests/test_resources_phase1c2.py` | 新增 9 条 |
| `tests/test_resources_phase1b.py` | 白名单契约 +3 端点（唯一改动） |

## 7. regression / commits / deviations

- 回归：资源四套件 **79 passed**（1B 22 + 1C1 39 + hardening 9 + 1C2 9）。
- commits：fix/feat 1C2 代码 + 测试 1 个 commit；本报告 1 个 commit。
- deviations：
  1. **approve 硬门槛**（无 pass 证据不能 approve）——你只规定了"review 后显式 approve"，我把双向都硬性化（这正是"这两个概念严格分开"的完备语义）；若未来需要"人工目视免审直批"旁路，那是显式产品决策，当前不做。
  2. overall 由三维度确定性推导，模型自报 overall 只留档不采信（可审计、可测试）。
  3. review 不校验候选生成时的 basis 与当前期望一致（只记录 reviewed_basis_fingerprint 供追溯）——候选可能在生成后被改期望再审查；最小闭环接受，UI 上审查前展示当前期望即可。
  4. 真机审查存在 run-to-run 波动（uncertain→pass）——如实记录；不通过提高 temperature=0 以外手段"稳定"它（temperature 已是 0.2）。
- **blockers：无。**

**Phase 1C2 完成。进入 Phase 1C3（Shared Production Library 最小闭环）。**
