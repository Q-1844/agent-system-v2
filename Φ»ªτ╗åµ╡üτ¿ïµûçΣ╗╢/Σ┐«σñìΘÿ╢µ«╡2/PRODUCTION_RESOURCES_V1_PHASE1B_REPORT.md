# Production Resources V1 — Phase 1B 验收报告（Resource Foundation）

> 阶段：**Phase 1B — Resource Foundation**（typed models + ResourceStore + legacy adapter + read service + read-only API + tests）
> 分支：`feat/resources-v1-phase1b`（自 `feat/producer-v1-bridge` @ `21f24bc` Visual Review 冻结点切出）
> 前置：`PRODUCTION_RESOURCES_V1_PHASE1A_AUDIT.md`（Phase 1A 审计，本报告 §15 承接其 Known Issues）
> 纪律：本轮零 Agent 接入、零图片生成、零前端改动、零 saved_characters 改动、零 Shared Library 实施。

---

## 1. 实际新增/修改文件

### 新增（5 个代码文件 + 1 测试文件）
| 文件 | 行数 | 内容 |
|---|---|---|
| `src/resources/__init__.py` | 4 | 包入口（声明冻结区零改动纪律） |
| `src/resources/models.py` | 327 | typed domain models + enums + ResourceDocument + ID 生成 + revision 指纹 |
| `src/resources/store.py` | 262 | ResourceStore（sync）+ legacy Character.views 只读 adapter |
| `src/resources/service.py` | 267 | 轻量 list + 精确 read + 内部 typed create/update（测试/Phase 1C 预留） |
| `routers/resources.py` | 166 | 只读 HTTP API（9 个 GET 端点） |
| `tests/test_resources_phase1b.py` | ~430 | T1-T17 + HTTP API 契约测试（22 个） |

### 修改（1 个文件，+2 行）
| 文件 | 改动 |
|---|---|
| `api_server.py` | +1 行 `from routers import resources`、+1 行 `app.include_router(resources.router)`（diff 逐行核对，无其他改动） |

### 文档（不在本轮代码范围，随分支提交）
`PRODUCTION_RESOURCES_V1_PHASE1A_AUDIT.md`、`详细流程文件/修复阶段/1.md`、`详细流程文件/修复阶段/资源模块.md`、本报告。

---

## 2. 最终 resources.json schema（真实序列化样本）

位置：`projects/{project}/output/resources.json`。稳定 ID keyed map，ID 与显示名解耦。

```json
{
  "schema_version": 1,
  "looks": {
    "look_921b06092227": {
      "look_id": "look_921b06092227",
      "character_name": "Alice",
      "scope": "project",
      "label": "Alice · Black Coat",
      "clothing_id": "clothing_a9ba1fd0ebda",
      "appearance_description": "",
      "appearance_delta": "",
      "reference_asset_ids": ["ref_c9d6d7339807"],
      "approved_reference_asset_ids": ["ref_c9d6d7339807"],
      "cover_asset_id": "ref_c9d6d7339807",
      "generation_status": "ready",
      "approval_status": "unreviewed",
      "origin": null,
      "created_at": 1789058466.0685778,
      "updated_at": 1789058466.0685785
    }
  },
  "clothings": {
    "clothing_a9ba1fd0ebda": {
      "clothing_id": "clothing_a9ba1fd0ebda",
      "scope": "project",
      "name": "Black Coat",
      "description": "long black wool coat",
      "design_traits": "high collar, silver buttons",
      "materials": ["wool"],
      "colors": ["#111111"],
      "reference_asset_ids": ["ref_c9d6d7339807"],
      "approved_reference_asset_ids": [],
      "cover_asset_id": "ref_c9d6d7339807",
      "tags": ["outerwear"],
      "approval_status": "unreviewed",
      "origin": null,
      "created_at": 1789058466.0530932,
      "updated_at": 1789058466.053094
    }
  },
  "props": {},
  "reference_assets": {
    "ref_c9d6d7339807": {
      "asset_id": "ref_c9d6d7339807",
      "scope": "project",
      "media_type": "image",
      "local": "output/characters/Alice_front_seg.png",
      "url": "https://platform-outputs.agnes-ai.space/x.png",
      "checksum": "3bf9a2c1",
      "view": "front",
      "purpose": "identity",
      "background_type": "",
      "source": "manual",
      "origin": null,
      "created_at": 1789058466.0498974,
      "updated_at": 1789058466.0498984
    }
  }
}
```

要点：
- `origin: null` = 本地资源（Phase 1D Shared Library import 时填充 ResourceOrigin）。
- Look/Clothing/Prop **只存 asset_id 引用**；ReferenceAsset 全文档唯一一份（T8 验证）。
- 未知 `schema_version` fail closed（T12）；坏 JSON 明确报错不静默重置（T13）。

---

## 3. 每个 typed model 字段

全部 stdlib dataclass（风格对齐 `src/core/models.py`），`to_dict/from_dict` + known-field 过滤（前向兼容：新字段旧文档可读）。

### 枚举（`src/resources/models.py`）
| 枚举 | 值 | 说明 |
|---|---|---|
| `ResourceScope` | project / shared | 资源属性（非资源类型）；Phase 1B 全 project |
| `ResourceSource` | local / shared_library / imported | origin.source 取值；Phase 1B 全 local |
| `ApprovalState` | unreviewed / approved / rejected | 审批状态（独立维度） |
| `GenerationStatus` | none / pending / generating / ready / failed | 生成状态（独立维度） |

### ReferenceAsset
`asset_id`(身份, `ref_<uuid12>`) / `scope` / `media_type`(="image") / `local` / `url` / `checksum`(Phase 1D 用，可空) / `view`(free string) / `purpose`(free string) / `background_type` / `source`(provenance) / `origin`(ResourceOrigin) / `created_at` / `updated_at`
- 不存 base64；不靠路径命名推断身份；不存 generation receipt（最小化）。

### CharacterLook
`look_id`(`look_<uuid12>`, 独立稳定身份) / `character_name`(FK→Character.name，**不引入 character_id**) / `scope` / `label`(显示名，rename 不变 id) / `clothing_id`(可空 FK) / `appearance_description` / `appearance_delta` / `reference_asset_ids` / `approved_reference_asset_ids` / `cover_asset_id` / `generation_status` / `approval_status` / `origin` / `created_at` / `updated_at`

### Clothing
`clothing_id`(`clothing_<uuid12>`) / `scope` / `name`(显示名) / `description` / `design_traits` / `materials[]` / `colors[]` / `reference_asset_ids` / `approved_reference_asset_ids` / `cover_asset_id` / `tags[]` / `approval_status` / `origin` / `created_at` / `updated_at`

### Prop（Phase 1B 最小）
`prop_id`(`prop_<uuid12>`) / `scope` / `name` / `description` / `reference_asset_ids` / `approved_reference_asset_ids` / `cover_asset_id` / `tags[]` / `approval_status` / `origin` / `created_at` / `updated_at`
- 未实现 holder / damage timeline / variant graph / continuity engine（结构不封死，dataclass 可加字段）。

### ResourceOrigin（lineage）
`source` / `shared_resource_id` / `shared_version` / `imported_at`——字段就位，Phase 1D 填充。
- 注意：`shared_version`（Shared 母版版本，Phase 1D）与 §7 的 derived revision（内容指纹）**是不同概念**，模型层已分离。

### ResourceDocument
`schema_version`(=1) / `looks` / `clothings` / `props` / `reference_assets`（均为 ID keyed map）。

---

## 4. ReferenceAsset 为什么没有修改 legacy Asset（修正 A 执行）

按本轮修正 A，**未**给 `src/core/models.py::Asset` 增加 asset_id/checksum/source/origin，而是新建独立 `ReferenceAsset` typed object。理由：

1. **legacy Asset 被广泛使用**：`Character.views`、`ImageAsset`(首帧/尾帧)、`AudioAsset`、`VideoAsset`、`video_end_frame` 全部内嵌它。加字段即改所有旧对象的序列化输出。
2. **project.json 与 project signature 不容无意改动**：`revision/models.py:project_signature`（完整 sha256）与 Agent OCC token 依赖序列化形态；`Asset` 加字段会改变 `asdict()` 输出 → 改变 signature → 触发 `base_is_stale`/`PROJECT_CHANGED_AFTER_APPROVAL` 误报（先例：AI-7C-H F-1 曾因 views 进签名误报而专门剔除）。
3. **T17 结构断言**把"Asset 字段恒等 `{local,url,status,error,updated_at}`"固化为测试，未来任何对 legacy Asset 的扩展都会被测试捕获。
4. `ReferenceAsset` 是 Resources 域 typed object，与 legacy Asset 无继承关系；只读 adapter（§6）提供统一读视图。

---

## 5. 为什么本阶段使用独立 resources.json（修正 B 执行）

按本轮修正 B，**未**把 looks/clothings/props/reference_assets additive 写入 `project.json`，也未改 Project dataclass / `to_dict`/`from_dict` / `schema_version=2` / `migrate_v1_to_v2`。理由：

1. Production Resources 属**生产资产域**，没有理由因资源模块上线而改变所有旧 Project 的序列化形状 / project_signature / workspace revision。
2. `ResourceStore` 直接复用 `src/core/atomic.atomic_write_json`（文件级原子）+ `src/core/write_lock.project_write_lock`（锁原语），**不依赖 ProjectStore 也能可靠写盘**。
3. T17 以"资源写入前后 project.json 字节不变"+"顶层 shape 恒等"双断言固化。
4. 代价（已接受，Phase 1C+ 评估）：跨 canonical 一致性（如删 Character 时 Look 的 character_name 悬空）由 service 读容错（missing 标记）+ 未来 reference sweep 处理，不在本轮。

---

## 6. legacy Character.views adapter 实现（§8）

`src/resources/store.py::legacy_character_references(project_dir, character_name)`：

- 读 `ProjectStore(pdir).load().get_character(name).views[front/side/back]`，映射为 `ReferenceAsset`-compatible 读对象：
  - `asset_id = "legacy:{character_name}:{view}"` ——**合成派生 id，不分配、不持久化、不入库**；
  - `local/url` 原样取自 legacy `Asset`；`checksum=None`（不臆造）；`background_type=""`（local 可能是 raw（router bind）或 seg（handler），无法可靠区分，不猜）；`source="legacy"`；`purpose="identity"`；`origin=None`；
  - 文件不存在/角色缺失 → 返回 `[]`；ProjectStore 只 load 不 save。
- **四不承诺**（T7 逐项验证）：不持久化 ref_id、不自动建 CharacterLook/Base Look、不写 resources.json、不修改 Character（project.json 字节不变）。
- 目标达成：未来 Resources UI / Look Generator 可用统一 `read` 方式消费旧三视图；用户显式创建 Base Look 时才生成真正稳定 look_id/ref_id。

---

## 7. revision / fingerprint 语义（§7）

- **deterministic content fingerprint**：`sha256(canonical_json(sort_keys, ensure_ascii=False))[:8]`——与现有 Agent OCC token（`_shot_token`/`_scenes_token` sha256[:8]）同口径。函数：`document_revision` / `look_revision` / `clothing_revision` / `prop_revision` / `reference_asset_revision`（`src/resources/models.py` 尾部）。
- **不持久化**：canonical 内无 revision 字段（`"revision" not in look.to_dict()` 有测试断言）；service read 响应携带 `revision` 供未来 Agent `expected_revision` 比对。
- **不与 shared_version 混淆**：Shared Library 的 `shared_version` 是母版版本号（Phase 1D、持久化于 origin），derived revision 是内容指纹（每次读取重算）。
- T11 验证：同内容跨实例 revision 相同；内容变 revision 变；canonical json sort_keys 保证 deterministic。

---

## 8. API contract（只读，9 端点）

Base：`/api/project/{project}`（404=项目不存在；资源数据损坏 → 500 带 `资源数据错误` 前缀）。

| 方法 | 路径 | 返回 |
|---|---|---|
| GET | `/resources?type=look\|clothing\|prop\|reference_asset\|all` | 轻量列表（T9：仅 id/type/name/status/cover_asset_id/revision/relationship_summary 等轻字段） |
| GET | `/resources/{resource_id}` | 按 id 前缀 dispatch（look_/clothing_/prop_/ref_）→ 完整 typed read；404=不存在 |
| GET | `/resources/legacy-character-references/{character_name}` | legacy 三视图只读 adapter（数组，可空） |
| GET | `/looks?character_name=` | Look 轻量列表（可按角色过滤） |
| GET | `/looks/{look_id}` | Look 完整 read：+ reference_assets（逐个 resolve，缺失标 `missing:true`）+ approved_reference_assets + cover_asset + clothing(resolve) |
| GET | `/clothings` | Clothing 轻量列表 |
| GET | `/clothings/{clothing_id}` | Clothing 完整 read：+ reference_assets + cover_asset + `used_by_looks`（反向） |
| GET | `/props` | Prop 轻量列表 |
| GET | `/props/{prop_id}` | Prop 完整 read：+ reference_assets + cover_asset |

- **零写端点**：router 不注册任何 POST/PUT/PATCH/DELETE（有测试断言 `test_api_read_only_no_mutation_endpoints`）。
- 命名空间 `/resources|/looks|/clothings|/props` 为新增，不与现有 `routers/characters.py`（`/character` 单数、`/characters/full`）冲突。
- mutation 能力只存在于 `ResourceService` 内部方法（sync，供测试 / Phase 1C 预留），HTTP 层不暴露。

---

## 9. 所有测试及真实结果

### 9.1 本轮新增：`tests/test_resources_phase1b.py` — **22 passed**
```
tests/test_resources_phase1b.py  22 passed in 2-4s
```
T1 读零副作用（list 空 + 不创建文件 + project.json 字节不变）✅
T2 首写建文件 ✅
T3 ReferenceAsset round-trip（local/url/checksum/view/purpose/background_type/source/scope）✅
T4 Look round-trip（character_name + clothing_id + asset ids + approved + cover）✅
T5 Clothing rename：clothing_id 不变 + Look 关系不失效 + revision 变化 ✅
T6 Look label rename：look_id 不变 ✅
T7 legacy adapter：3 视图映射 + 不改 Character + 不写 resources.json + 缺失角色 [] ✅
T8 single truth：Look/Clothing/Prop 只存 id、不嵌对象 ✅
T9 轻量 list：无 base64/bytes/receipt/prompt/heavy 字段（forbidden 集合断言）✅
T10 read 完整 relationship：look→assets/clothing/cover、clothing→used_by_looks、asset→used_by、dispatch、missing 容错 ✅
T11 revision deterministic：同内容同指纹（len=8）、内容变指纹变、canonical 不持久化 revision ✅
T12 未知 schema_version fail closed（store 与 service 两层）✅
T13 坏 JSON / 非 object：明确报错且文件不被静默重置 ✅
T14 restart persistence：新实例从磁盘重读全关系 ✅
T15 Character Designer 冻结面：7 工具名恒等 + write ops 含 create/update/delete + 无 resource./look. 工具混入 + `_base_receipt` 字段完好 ✅
T16 冻结模块可导入 + resources 包不反向依赖 Agent 模块 ✅
T17 project.json shape 零变化：Asset 字段恒等 / Project.to_dict 顶层 shape 恒等 / Character shape 恒等 / schema_version=2 / 资源写后 project.json 字节不变 ✅
API×5：list 轻量 / read_look 完整关系 / dispatch+404 / looks 过滤+legacy / **router 无任何写方法** / 项目 404 ✅

### 9.2 既有角色/Agent 关键套件（T15/T16 直接证据）— 248 passed + 1 skipped
```
test_character_design.py + test_character_designer_agent.py      → 53 passed
+ director/producer/script_writer/subagent_governance 等 12 个关键文件
→ 合计 248 passed, 1 skipped, 3 failed(not_configured 族) 
→ 3 个 not_configured 单跑复绿（3 passed，已知套件内自扰，与本轮无关）
```

### 9.3 全量回归 — **34 failed / 1187 passed / 1 skipped（44:33）**
```
D:/Delta Force/python.exe -m pytest tests/ -q
→ 34 failed, 1187 passed, 1 skipped, 2 warnings in 2673.56s
```
**与冻结基线（`_phase3_pointer_regression.log`，Producer V1 Phase 3 指针提交时的全量）逐条 diff**：
- 基线真实失败 35（37 条 grep 记录中 2 条为日志解析噪声"FAILED 故事段"）= 34 同构 + 1 套件内自干扰（`test_full_project_autopilot.py::test_case6_external_source_change_blocked`，基线已知单跑复绿）；
- 本轮 34 条 = 基线 34 同构的**严格子集**（基线那条自干扰本次未复现；not_configured×3 已单跑复绿验证）；
- **只在本轮出现的失败：0 条。零新增回归。**
- 失败家族（全部既有）：`test_ai_7d_d3_runtime_control`（端口族）×11 / not_configured×3 / `test_full_project_autopilot`×7 / `test_project_autopilot`×6 / `test_interface`×2 / `test_shot_request_rules`×5。
- 附注：本轮全量耗时 44:33 明显长于以往（`--durations` 显示 full_project_autopilot 多个 case 各 ~180s=超时上限打满，符合记忆中"免费 Agnes 档 429/400 瞬态退避"既有坑，与本轮改动无关）。

---

## 10. git commits（feat/resources-v1-phase1b，均未 push）

| # | commit | 内容 |
|---|---|---|
| 0 | `bb9b61b` | docs: Phase 1A 审计报告 + 两份执行文档（Phase 1B 的前置上下文） |
| A | `adba4bd` | feat(resources): typed domain models + serialization |
| B | `bd3f592` | feat(resources): ResourceStore + legacy Character.views adapter |
| C | `3d2630c` | feat(resources): read service + read-only HTTP API（+api_server 2 行） |
| D | （本次提交） | test: T1-T17 + API 契约测试 + adapter background_type 不臆造修正 + 本报告 |

---

## 11. 冻结文件是否真正零改动 — **是（git 铁证）**

```
git diff --stat 21f24bc..HEAD -- src/core/ src/revision/ src/agents/ \
    routers/characters.py routers/shots.py src/views/ src/store/ src/api/ \
    src/components/ saved_characters/
→ 输出为空（零文件、零行）
```
分支全部改动 = `src/resources/`（4 新文件）+ `routers/resources.py`（新）+ `api_server.py`（+2 行）+ 文档。`saved_characters/` 未动一个字节（§11 要求的"只预留语义"达成：scope/origin/lineage 字段已在模型中就位）。

## 12. project.json 是否真正零 schema 变化 — **是**

- Project dataclass / `to_dict`/`from_dict` / `schema_version=2` / `migrate_v1_to_v2` / `ProjectStore` 语义零改动（冻结 diff 为空）。
- T17 四重断言：Asset dataclass 字段恒等 `{local,url,status,error,updated_at}`；`Project.to_dict()` 顶层 keys 恒等 9 键；Character 序列化恒等 3 键；**资源写入后 project.json 逐字节相等**。

## 13. deviations

1. **§6.D 锁接线的时机**：`ResourceStore` 为纯 sync（对齐 ProjectStore），`project_write_lock` 经 `store.write_lock()` 访问器暴露（同 project_name key），但 Phase 1B **未在任何路径实际持锁**——因为本轮 HTTP 只读 + 测试为 sync 单线程，不存在与 project.json 并发写的竞态方。Phase 1C 异步写路径必须以 `async with store.write_lock(): store.load()+mutate+save()` 持锁（已在 store.py docstring 标注）。判定：条件式条款（"如果当前项目已有 project_write_lock"）在本轮未触发，非违规，但如实记录。
2. **报告位置**：执行文档 §17 隐含报告放仓库根（同 Phase 1A 惯例）；按你的明确指示放 `详细流程文件/修复阶段2/`。用户指示优先。
3. **Commit 结构**：在 A 之前多了一个 docs commit（Phase 1A 报告 + 执行文档入库），因为 Phase 1B 文档要求"首先完整阅读 Phase 1A 报告"，该报告应当随分支可追溯。其余严格按 A→B→C→D 分 commit、每阶段测试通过后再进。
4. **adapter `background_type` 留空**：初版实现臆造 `"transparent"`，self-review 后改为 `""`（router bind 的 local 是白底 raw、handler 的是透明 seg，无法可靠区分）——宁缺毋错。

## 14. blockers

**无。** 未出现"真实代码迫使修改冻结文件"的情形。

## 15. known existing issues（从 Phase 1A 承接，本轮未顺手修）

1. **REST rename 遗漏 `Scene.characters`**：`routers/characters.py:839-841` 只级联 `Shot.characters`，rename 后 Scene 层角色名 stale（REST 路径可触发；Agent 协议层禁改名故生产未爆）。
2. **Character delete 悬空引用**：`routers/characters.py:894-926` 删除角色不清理 Scene/Shot 中的旧名引用。
3. **REST 改名 vs Agent 禁止改名**矛盾（`change_protocol/character_proposal.py:8,27`）。
4. **"character_id" 语义双轨**（库 `{name}_{uuid8}` vs 项目 name 即身份）。
5. **三视图两套落盘命名**（handler `{safe_name}/{view}_{sig8}_seg.png` vs router `{name}_{view}.png`）+ `views_meta.json` 与 `Character.views` 双真相源。
- 处置：全部**不在本轮修**（与 Resource Foundation 无关，顺手修会扩大回归面）；待单独修复、单独验收。

## 16. 对 Phase 1C 的推荐（只推荐，不实现）

1. **CharacterLook 生成流**（第一个价值切片）：`look.generate_candidates` service——复用 `AgnesClient.generate_image(prompt, image_refs=[char_ref, clothing_ref])` + rembg + `appearance_sig` 语义版本 + `_base_receipt(verified)` 写后重读；落盘统一 `output/looks/{look_id}/{view}_{sig8}_seg.png`（新模板，不续接 C1 的两套旧命名）。
2. **审批流**：`look.approve_reference(look_id, asset_id)` / reject——approved ids 只能从 reference_asset_ids 中选；approval_status 与 generation_status 独立演进（本轮数据模型已分开，不卡死重生成）。
3. **异步写路径 + 锁**：service 写方法 async 化并持 `project_write_lock`（本轮已留好 `write_lock()` 访问器），ResourceStore 本体保持 sync。
4. **Shared Library additive 升级**（可独立小切片）：saved_characters meta.json 加 origin/version/scope 字段（lazy default 双读）；Phase 1D 第一版**真 copy-on-import**（本轮已确认不把 content-addressed media 作为前置）。
5. **只读前端视图**（可选）：角色页渐进加 scope/type 过滤；不重写 Agent Center。
6. **Vision Review 复用决策**推迟到 1C 开工时定：并行路径（Resource Review 自有 prompt 调 `chat_completion_vision`）vs 抽 `vision_review_service` 薄 service（Director 工具委托）。倾向先并行、后收敛，不强耦合。
7. **reference sweep**：删 Character/Clothing/Prop 时对 Look 引用做 stale 标记或级联清理（本轮 read 已 missing 容错，写侧未做）。

---

## 停止条件核对

- [x] Resource models（Commit A）
- [x] ResourceStore（Commit B）
- [x] legacy adapter（Commit B）
- [x] read service（Commit C）
- [x] read-only API（Commit C）
- [x] tests T1-T17 + API（Commit D，22 passed）
- [x] 关键 Agent 套件零回归（248 passed + 3 已知噪声单跑复绿）
- [x] 全量回归零新增失败（34 failed 全为基线严格子集 / 1187 passed）
- [x] Phase 1B 报告（本文件）

**Phase 1B 完成。未进入 Phase 1C。等待确认。**
