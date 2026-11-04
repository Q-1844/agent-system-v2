# Production Resources V1 — Phase 1C1 验收报告（Resource Write Safety + CharacterLook Candidate Generation）

> 阶段：**Phase 1C1 — Resource Write Safety + CharacterLook Candidate Generation**（第一次真实写入 + 真实图片生成）
> 分支：`feat/resources-v1-phase1c1`（自 `feat/resources-v1-phase1b` @ `6cf1634` 切出，4 commits，均未 push）
> 前置：Phase 1A 审计 + Phase 1B 报告（承接其 §16 推荐与遗留问题）
> 纪律：零 Agent 接入、零 Visual Review、零 Approval、零前端、零 Shared Library、零 Prop 写 API、不改 Shot/Director/Character schema。

---

## 1. 实际改动文件

### 新增（4 个代码文件）
| 文件 | 行数 | 内容 |
|---|---|---|
| `src/resources/write_service.py` | ~700 | `ResourceWriteService`：统一异步写 service（lock+OCC+validation+mutation+receipt）+ `generate_look_candidates` 三阶段协议 |
| `src/resources/look_prompt.py` | 90 | 确定性 look 候选 prompt builder（production reference 定位） |
| `src/resources/generation.py` | 230 | GenerationBasis 构造+语义指纹 / 参考图确定性选择 / data URI（仅内存）/ 候选文件落盘 |
| `src/resources/generation_records.py` | 70 | `generation_records.jsonl` append-only 读写（尾部半行容错） |

### 修改（3 个文件）
| 文件 | 改动 |
|---|---|
| `routers/resources.py` | +8 写端点（§22 白名单）+ `_ERROR_STATUS` HTTP 映射 + 4 个 list envelope 附 `document_revision`；docstring 更新 |
| `src/resources/service.py` | +`document_revision()` 方法 +1 import（创建类 mutation 的 OCC 期望值来源） |
| `api_server.py` | **零改动**（resources router 已在 Phase 1B 注册，写端点同 router 无需再注册——§32"已有则不改"达成） |

### 测试（2 个文件）
| 文件 | 改动 |
|---|---|
| `tests/test_resources_phase1c1.py` | 新增，39 条（对应执行文档 §29 T1-T22/T25 + HTTP 契约） |
| `tests/test_resources_phase1b.py` | 仅演进 1 条：`test_api_read_only_no_mutation_endpoints`（Phase 1B"router 零写方法"断言 → Phase 1C1 §22 写端点白名单契约）。**其余 21 条逐字节未动** |

## 2. mutation / OCC contract（§4）

所有写经 `ResourceWriteService`，期望失败一律返回结构化 receipt（不抛异常、不 silent retry、不自动替调用方换 revision）：

```
receipt = {ok, operation, target, before, after, revision, verified, error, **extra}
```

| 操作 | OCC 期望值 | stale 返回 |
|---|---|---|
| create（clothing/reference_asset/look） | `expected_document_revision` = 当前 ResourceDocument 指纹 | `{ok:false, error:"stale_revision", expected_revision, current_revision, current:null}` |
| update/delete/attach/detach 单资源 | `expected_revision` = 该资源当前内容指纹 | 同上，`current`=当前资源完整序列化 |
| generate（entry） | `expected_revision` = look revision | 同上（不启动生成、不写 record） |

- revision = `sha256(canonical_json(sort_keys))[:8]`（Phase 1B 同口径，deterministic，不持久化）。
- 获取途径：4 个 list 端点 envelope 的 `document_revision` + read 端点的 `revision` 字段。
- error 码目录：`stale_revision` / `not_found` / `character_not_found` / `clothing_not_found` / `asset_not_found` / `resource_in_use` / `invalid_reference_input` / `validation_error` / `invalid_candidate_count` / `missing_character_reference` / `provider_failed` / `stale_generation_basis`。
- verified 语义 = save 后从磁盘重读确认真实落盘（与 Agent 域 truth-gate 同口径；ResourceStore 无缓存，每次 load 即磁盘真相）。
- receipt 为 resources 域中性实现，**不 import 任何 Agent 模块**（§28）；字段与 `_base_receipt` 兼容。

## 3. lock contract（§5）

- 所有 load→validate→OCC→mutate→save 在 `async with project_write_lock(project_name)` 短临界区内（`store.write_lock()` 访问器，同 project.json 写串行化——Phase 1B 遗留接线在本轮全部落地）。
- **图片模型调用绝不持锁**：三阶段协议（见 §12）；T13 用 lock spy 证明 `generate_image` 调用期间 `project_write_lock(...).locked() is False`。
- 并发串行化实测：同 document revision 两个并发 create → 恰好一成一败（锁内重查 revision）。
- 并发双 create/同 revision 的 stale 判定发生在锁内，无 lost update。

## 4. Clothing CRUD（§7）

- `create_clothing`：name 必填 str；approval_status=unreviewed；不自动生成图。
- `update_clothing`：PATCH 白名单 `{name, description, design_traits, materials, colors, tags}`；未知/不可变字段→`validation_error`；rename=只改 name，clothing_id 恒不变（T3）。
- `delete_clothing`：先 OCC，后 reference check——任何 `Look.clothing_id == clothing_id` → `resource_in_use`（附 `used_by_looks`），**无级联删除、不产生悬空 Look**（T4）。
- HTTP：`POST/PATCH/DELETE /api/project/{p}/clothings[/{id}]`；DELETE 的 expected_revision 走 query param。

## 5. ReferenceAsset registration（§8）

- `register_reference_asset(local|url, view, purpose, source, background_type)`：local/url 二选一。
- local 路径验证（`_validate_local_image`）：仅项目根内**相对路径**；拒绝绝对路径、`..` 穿越、resolve 后逃出项目根、非图片扩展名（.png/.jpg/.jpeg/.webp/.bmp）、文件不存在（T6 全覆盖）。
- local 存在 → 计算真实 **sha256 全长 hex** checksum（不根据文件名猜任何元数据）；URL 注册 checksum=None（不臆造）。
- `view/purpose/background_type` 仅调用方显式提供才写入；**绝不把 base64 写进 resources.json**。
- HTTP：`POST /api/project/{p}/reference-assets`。
- attach/detach（§9）：`POST/DELETE /clothings/{id}/references[/{asset_id}]`，幂等（重复→`unchanged:true` 不改文档）；asset 必须真实存在；`approved_reference_asset_ids` 本轮不被触碰、不自动批准。

## 6. CharacterLook create / update（§6）

- `create_look(character_name, label, clothing_id?, appearance_description, appearance_delta, expected_document_revision)`：
  - Character 必须真实存在于 project.json canonical（ProjectStore 只 load 不写）→ 否则 `character_not_found`（T8）；
  - clothing_id 非空 → 必须存在于 resources.json → 否则 `clothing_not_found`（T9）；
  - 创建后 `generation_status=none`、`approval_status=unreviewed`、空 reference ids；**不自动生成、不自动建 Base Look、不修改 Character**（T7/T21/T22）。
- `update_look`：PATCH 白名单 `{label, clothing_id, appearance_description, appearance_delta}`；**character_name 是身份 FK 不可改**；clothing_id 可换绑（须存在）或 null 解绑。
- 本轮无 Look delete API（§22 未列）。

## 7. GenerationBasis schema（§11）

一次生成的事实快照（`src/resources/generation.py::compute_generation_basis`，纯函数、确定性）：

```python
{
  look_id, character_name,
  character_identity_fingerprint,      # sha256({name, description_en})[:8]——身份=name+desc_en（Phase 1A 结论），不含 views
  character_description_snapshot,      # 当时 description_en 全文
  character_reference_snapshot,        # 实际使用的 legacy views：[{view, asset_id:"legacy:{name}:{view}", url, local, sha256}]（内容 hash 固定"哪一个 Alice 状态"）
  clothing_id, clothing_revision,      # 当时 Clothing 内容指纹（含 refs 列表）
  clothing_description_snapshot,       # {clothing_id, name, description, design_traits, materials, colors}
  clothing_reference_asset_ids_used,   # 实际使用的 clothing ref ids（无图时诚实为 []）
  clothing_reference_snapshot,         # 同 character 条目结构
  appearance_description, appearance_delta,
  view_purpose, candidate_count, seed,
  model: {provider:"agnes", model_id, size_tier, ratio},
  prompt,                              # 确定性 builder 产物
  basis_fingerprint                    # sha256(以上全部)[:8]
}
```

**语义指纹设计**：`basis_fingerprint` 只覆盖生成语义子集，**故意不含裸 look_revision**（其中 updated_at/reference ids 参与指纹——label 改名等无害写会被误判 stale）。生成结束后同参数重算比对，任何关键 basis 变化（clothing 内容/换绑、角色身份/描述、views 文件内容变化、appearance 字段、prompt/model/seed/count/view_purpose）→ 指纹不同 → stale（敏感度 4 场景测试全过：clothing 改、character 描述改、view 文件内容改、look 删除）。

## 8. prompt builder（§15）

`src/resources/look_prompt.py::build_look_candidate_prompt`：

- 输入：character description / clothing(name+description+design_traits+materials+colors) / appearance_description / appearance_delta / view_purpose；输出确定性（同输入同输出，无时间/随机/LLM，T11）。
- 定位 = **clean reusable production reference**：全身正面站姿（或 view_purpose 指定角度）、身份与参考图严格一致、服装完整可读无遮挡、设计信息清楚、适合作为后续 image/video reference。
- 不引入 Shot 剧情 / cinematic camera / 复杂环境。
- 内容审核坑规避：使用实测安全措辞 `pure white backdrop`（CLAUDE.md：Agnes 拒 "pure/solid white background" 组合）。

## 9. 图片生成输入选择规则（§12/§13）

- **Character identity refs**：复用 Phase 1B `legacy_character_references`（不迁移 views）；固定顺序 **front→side→back**（adapter 顺序即确定性顺序，不依赖 dict 随机序）；可用=url 或可读 local；全部可用 refs 都传（E2E 实证 3 个 identity refs 多参考 i2i 一次通过）。
- **Clothing refs**：按 `reference_asset_ids` 存储顺序（确定性）；可用判定同上。
- **data URI 优先**：local 可读→内存 data URI（内容可验证产物优先，§13"优先本地"）；否则公网 URL。**data URI 只存内存，绝不持久化**。
- **fail closed**：无任何可用 identity ref → `missing_character_reference`，不退化纯文字生成（T10；模型零调用）。
- seed 语义：提供时候选 i 用 `seed+i`（可复现且多候选不重复）；record 诚实记录 `seeds_used`。
- 尺寸：`size_tier=1K, ratio=2:3` 竖图（与既有角色三视图口径一致）；image 位置 fallback（extra/top）由 AgnesClient 既有逻辑处理，E2E 实证生效。

## 10. generation record schema（§18）

`projects/{p}/output/resources/generation_records.jsonl`（append-only，无 base64）：

```python
{generation_id: "gen_<uuid12>", operation: "look.generate_candidates", look_id,
 basis: <§7 完整快照>, prompt, model: {provider, model_id, size_tier, ratio}, seed,
 seeds_used,                       # 每个候选实际发送的 seed（§18 之外的诚实增强）
 candidate_asset_ids,              # 仅 committed 非空
 candidate_files, candidate_urls,  # 诊断用相对路径 / provider url（stale/failed 也保留已到手部分）
 started_at, completed_at, status, # committed | stale_basis | failed
 error,                            # 错误码（真实错误全文在 detail）
 detail,                           # 真实错误信息（§21）
 verified: bool,                   # 仅 committed 且三重重读验证为 true
 resource_document_revision_after} # 仅 committed；否则 null
```

- 不回填进 ReferenceAsset（Phase 1B 最小化设计保持）；尾部半行崩溃容错读取。

## 11. output path（§17）

```
output/resources.json                                          # canonical（Phase 1B 原位不动）
output/resources/generation_records.jsonl                       # append-only provenance
output/resources/looks/{look_id}/generations/{generation_id}/candidate_NN.png   # 原始生成图
```

- 以 `look_id` 为目录身份，与 display label 解耦：rename Look 不动磁盘目录（有测试）。
- 保存**原始模型返回图**：不 rembg、不透明 PNG 替换（§16；derivative 属未来阶段）。

## 12. stale generation basis 处理（§19）

三阶段协议：
1. **阶段1 短锁**：entry OCC（expected_revision=look revision）→ compute basis → 无 identity ref fail closed → `generation_status=generating` → 释放锁；
2. **阶段2 无锁**：`AgnesClient.generate_image`（复用，未复制 HTTP client、未改其契约）；多候选顺序请求；候选图即时落盘到唯一 generation_id 目录（无需锁）；
3. **阶段3 短锁**：重算 basis 指纹：
   - 成立 → commit：每候选建 ReferenceAsset（`source=generated`、真实 checksum、local/url 双存）→ 追加 `Look.reference_asset_ids`（**不动 approved**）→ `generation_status=ready`、`approval_status` 保持 unreviewed → 单次原子 save → 重读三重验证（Look ids / assets / 文件）→ record `verified=true`（§20 八步全做）；
   - 不成立 → **拒绝 attach**：候选图保留在 generation 目录供诊断、不注册 asset、look 状态诚实还原为生成前值、record `status=stale_basis, verified=false` 诚实记录"生成完成但 canonical commit 被拒"；绝不 runtime 偷换 basis 强行提交（T19/T20：生成中途改 Look / 改 Clothing → commit 被拒、Look 不挂旧候选）。

## 13. failure semantics（§21）

- provider 失败（任意候选抛错）：**不伪造 ReferenceAsset**、整体失败（all-or-nothing，无部分提交）；已到手候选图保留 generation 目录（诊断）；`Look.generation_status=failed`、**旧 reference_asset_ids 原样保留**；record `status=failed, verified=false, error=provider_failed, detail=真实错误全文`；下一次生成可重试（T18 三段式验证：成功→失败→重试成功）。
- **E2E 期间获得一次真实 provider 失败证据**：首跑因 driver 未加载 .env（401 Invalid key）→ 失败路径逐项正确（无伪造 asset / 诚实 record / look failed / 重跑成功）。该失败记录保留在 throwaway 项目中作为证据。
- entry 失败（not_found / stale_revision / invalid_candidate_count / validation_error）：不启动、不写 record、状态不变。

## 14. mock tests

`tests/test_resources_phase1c1.py` — **39 passed**（`tests/test_resources_phase1b.py` 22 条全绿，其中 1 条按 §22 演进为写端点白名单契约）：

| 执行文档 | 测试 | 结果 |
|---|---|---|
| T1/T2 | create OCC 正确/stale 拒绝文件不变 | ✅ |
| T3 | rename id 不变+白名单 | ✅ |
| T4 | resource_in_use 无级联 | ✅ |
| T5/T6 | 注册 checksum 真实/穿越·绝对路径·缺失·非图片·空串全拒 | ✅ |
| T7/T8/T9 | Look create 验证 Character/Clothing FK | ✅ |
| T10 | 无 identity ref fail closed（模型零调用） | ✅ |
| T11 | prompt deterministic+语义+安全措辞 | ✅ |
| T12 | candidate_count 0/-1/5/100 拒、4 过 | ✅ |
| T13 | **lock spy**：generate_image 期间 `locked() is False` | ✅ |
| T14/T15 | 成功 commit 全链路+不自动 approved | ✅ |
| T16 | 多候选独立 asset/checksum/file/seed+i | ✅ |
| T17 | record 持久化+尾部容错+无 base64 | ✅ |
| T18 | provider 失败不伪造/旧 refs 保留/可重试 | ✅ |
| T19/T20 | 生成中途改 Look/Clothing → stale_basis 拒绝 attach | ✅ |
| T21/T22 | project.json 字节不变/Character.views 不迁移 | ✅（mock+E2E 双证） |
| T23 | Phase 1B 22 条全绿 | ✅ |
| T24 | 关键回归无新增失败（见 §16） | ✅ |
| T25 | 重启后 Look/assets/records 完整恢复 | ✅ |
| 附加 | basis 指纹敏感度 4 场景 / 并发串行化 / 锁释放 / attach 幂等 / Look patch 语义 / HTTP 契约 ×8 | ✅ |

## 15. real E2E（§30）

隔离项目 `projects/__res_1c1_e2e`（throwaway，保留作证据），driver `_e2e_resources_1c1.py`（scratch），service 层直驱 + **真实 Agnes**（`PipelineController` 构建客户端，与 api_server startup 同路径；HTTP 契约已由 TestClient 单独证明）：

1. 建项目 + 绑定三视图 Character（saved_characters/火焰机甲 真实 PNG 经 ProjectStore 正常创建路径拷入，非手改 JSON）；
2. 创建 Clothing "Black Coat"（wool/high collar/silver buttons）；
3. 注册真实本地图 `coat_ref.png`（checksum=真实 sha256）→ attach；
4. 创建 Look `火焰机甲 · Black Coat`；
5. 真实生成 1 candidate：`committed, verified=true`；**4 个 data URI refs（3 identity+1 clothing）多参考 i2i 真机一次通过**（image 位置 extra，AgnesClient fallback 正常）；
6. 验证全过：候选 PNG 814.8KB 真图落盘（火焰机甲身份保持+黑色长大衣完整可读+白底全身正面）、resources.json 有 ReferenceAsset（source=generated）、look.reference_asset_ids 挂上、**approved 仍空**、record verified 无 base64；
7. 重启语义（全新实例重读）完整恢复。

首跑因 driver 缺 .env 加载（401）失败，失败路径成为 §21 真机证据（见 §13）；修 driver 后重跑 PASS。**全程未手改任何 JSON 制造"成功"**。

## 16. regression（T23/T24）

| 套件 | 结果 |
|---|---|
| `tests/test_resources_phase1b.py` | 22 passed（1 条按 §22 演进，其余逐字节未动） |
| `tests/test_resources_phase1c1.py` | 39 passed |
| Character Designer / Director / Producer / Sub-Agent / Agents 关键 13 文件 | **249 passed, 1 skipped, 0 failed**（41.67s；与 Phase 1B 基线 248+1skip 同构） |
| Script Writer 三件套（min/native/false_success） | 58 passed |

**零新增失败。** 本轮未跑全量 pytest（执行文档 §29 T24 只要求关键回归；改动面 = src/resources + routers/resources，无 Agent/前端/核心接触面）。未重启 :8000 后端（遵守"全量 pytest 前停 :8000"坑——本轮未跑全量，且写端点属新路由，后端重启后生效）。

## 17. commits（feat/resources-v1-phase1c1，自 6cf1634，均未 push）

| # | commit | 内容 |
|---|---|---|
| A | `b20f3b3` | async write service + OCC + lock（+14 条测试） |
| B | `0868edd` | Clothing/ReferenceAsset/Look 写 API + document_revision 暴露（+8 条 HTTP 契约；Phase 1B 只读断言演进） |
| C | `e4bacfb` | GenerationBasis + prompt builder + generation records（+6 条测试） |
| D | `48fe668` | generate candidates 三阶段协议 + stale-basis 保护 + HTTP generate（+11 条测试） |
| E | （本次） | 真实 E2E + 本报告 |

每个 commit 均含当块测试且单独可回滚（对执行文档 §31"Commit E 集中测试"的微调：测试随 work block 分 commit，每步验证通过即存档——见 deviations #1）。

## 18. deviations

1. **测试随 commit 分发**（而非全部堆 Commit E）：每 commit 自带当块测试并验证通过后才提交，可单独回滚性更强。§31 为"建议"结构，此调整已在此声明。
2. **`test_api_read_only_no_mutation_endpoints` 演进**：Phase 1B 的"router 零写方法"断言与 §22"开放写 API"直接冲突，按本轮规定集合改为白名单契约；其余 21 条 Phase 1B 测试逐字节未动。
3. **stale-basis 校验用语义指纹而非裸 look_revision**：裸 revision 含 updated_at/reference ids，label 改名等无害写会误判 stale；§19 要求的是"关键 basis 变化"。语义子集覆盖见 §7，敏感度 4 场景有测试。
4. **stale 时 look 状态诚实还原**（而非置 failed）：§19 未规定 stale 后的 look 状态；置 failed 会与 §21"failed=provider 失败"语义混淆，还原生成前值+record 诚实记录更干净。
5. **data URI 优先于 url**（identity refs 同 clothing refs 统一规则）：§13 对 clothing refs 明确"优先本地"，identity refs 未明确；统一按"内容可验证产物优先"，E2E 实证 4 data URI 多参考可行。
6. **多候选 seed+i**：§10 未定义多候选 seed 语义；同 seed 同 prompt 大概率产出重复图，seed+i 可复现且不重复；record 以 `seeds_used` 诚实记录。
7. **checksum=全长 sha256 hex**（Phase 1B 报告样本中的 8-hex 为示意）：§8 要求"真实 sha256 checksum"，全长为标准无歧义形态。
8. **E2E 走 service 层而非 HTTP**：真实 Agnes 的 HTTP 契约已由 TestClient 测试单独证明；service 直驱与 golden path driver 惯例一致，避免后端端口依赖。
9. **报告位置**：执行文档 §33 未指明路径，按你上一轮明确指示的惯例放 `详细流程文件/修复阶段2/`。
10. **注册本地图片仅接受项目内相对路径**（绝对路径一律拒绝，即使指向项目内）：§8 禁令的取严解释，消除全部逃逸面。

## 19. blockers

**无。** 未出现"真实代码迫使修改冻结区"的情形（`git diff 21f24bc..HEAD -- src/core/ src/revision/ src/agents/ routers/characters.py routers/shots.py src/views/ src/store/ src/api/ src/components/ saved_characters/` 输出为空，铁证）。

## 20. 下一阶段建议（Phase 1C2）

1. **Vision Review 接入**（§24 预留）：候选生成后自动/手动触发 `chat_completion_vision` 审查（identity consistency / costume consistency / defects）→ 结论进 record 或独立 review 工件；Director 不动，倾向并行路径（自有 prompt 调 vision），Phase 1C2 收敛 decision。
2. **Approval 流**（§23 预留）：`approve/reject` mutation——approved ids 只能从 reference_asset_ids 中选；cover_asset_id 指定；approval_status 与 generation_status 独立演进（模型已就位）。
3. **前端最小视图**（可选）：Resources 页读-only 列表 + Look 详情 + generate 按钮（写 API 已齐）；走 agentTurn 外的普通 fetch 即可。
4. ** Look delete + reference sweep**：本轮未开放 Look delete（§22 未列）；下阶段连同"删 Character/Clothing 时对 Look 的 stale 标记"一起做（Phase 1B 遗留 #7）。
5. **multi-ref 策略细化**：本轮实证 4 refs 可行；可测 refs 数量/顺序对身份保持的影响（front-only vs front+side vs 全 3），形成选择规则。
6. **generation 清债**：generation 目录无 GC（每次生成新目录）；可在 Approval 阶段一并设计 stale 诊断目录的保留策略。

## 停止条件核对

- [x] mutation safety（Work A，Commit A）
- [x] Clothing/ReferenceAsset/Look write backend（Work B，Commit B）
- [x] Look candidate generation（Work C/D，Commits C/D）
- [x] generation records（Commit C/D）
- [x] stale-basis protection（Commit D，T19/T20）
- [x] tests（39 条新增 + 22 条 Phase 1B 全绿 + 关键回归 249+58 零新增失败）
- [x] 真实 1 次生成 E2E（committed + verified，重启恢复）
- [x] 报告（本文件）

**Phase 1C1 完成。未进入 Visual Review / Approval / Frontend / Shared Library / Agent integration。等待确认。**
