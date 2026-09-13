# Shot Production Binding V1 — Phase S3B.1 实施报告

> 阶段：S3B.1 = Canonical + Store + Service + CRUD/OCC（严格小步，7.md S3B.1 执行指令）。
> 设计基线：`SHOT_BINDING_V1_AUDIT_AND_DESIGN.md`（S3A 报告，commit `fce423a`）。
> 结论先行：**S3B.1 PASS，Ready for S3B.2**。新增 4 文件 + 1 测试文件（35 测试全绿），
> 冻结区（agents/ai/core/resources/scene_system/visual_profile/pipeline/storyboard/routers/services）
> diff 全 0；全量回归 1521 passed / 15 failed / 1 skipped，15 条失败全部归因为
> 冻结 baseline 或已证时序 flake（逐条证据见 §6/§7），**零新回归**。

---

## 1. 修改文件清单

| 文件 | 状态 |
|---|---|
| `src/shot_binding/__init__.py` | 新增 |
| `src/shot_binding/models.py` | 新增 |
| `src/shot_binding/store.py` | 新增 |
| `src/shot_binding/service.py` | 新增 |
| `tests/test_shot_binding_s3b1.py` | 新增 |

无任何既有文件被修改（`git diff HEAD` 为空；新增包对既有代码零反向引用，grep 实证仅测试文件 import 它）。

## 2. 每个文件修改目的

- **models.py**：typed domain models——`ShotProductionBinding` / `SceneBinding` / `CharacterBinding` / `PropBinding` / `BindingAction`（kind 词表 `BindingActionKind` 六值） / `DependencyFingerprints` / `ShotBindingDocument`；`ShotBindingSchemaError`；known-field 过滤前向兼容；`document_revision` / `binding_revision`（sha256[:8] canonical json 指纹族，同 resources/scene_system 口径）。`ACTION_KINDS_REQUIRING_PROP` 词表常量。
- **store.py**：`ShotBindingStore`——`output/shot_production_bindings.json` 纯 sync store。无文件 → load 返回空 document（**绝不创建文件**）；坏 JSON / 未知 schema_version → `ShotBindingSchemaError` fail closed；写用 `atomic_write_json`；锁复用 `project_write_lock`（同 project_name key，不建第二套锁）。
- **service.py**：`ShotBindingService`——纯读（`read_document` / `read_binding`，零副作用）+ 三 mutation（`create` / `replace` / `delete`，sync 核心 + `*_async` 包装全程持 `project_write_lock`）。bind 时全量 fail-closed 校验 + 依赖指纹快照计算（写侧一次性动作；重算对比属 S3B.2，未实现）。
- **__init__.py**：公共导出面。
- **tests/test_shot_binding_s3b1.py**：§18 指令 31 项测试 + 4 项补充（词表复用 / action 校验组 / UNBOUND read 语义 / scene_id mismatch）。

## 3. Binding schema 最终实现（与 S3A §7.2 一致）

```jsonc
// output/shot_production_bindings.json — schema_version=1
{ "schema_version": 1,
  "bindings": { "<shot_id>": {
      "shot_id": "...", "scene_id": "...",          // bind 时与 Shot.scene_id 比对
      "scene_binding": { "scene_instance_id": "...", "location_view_id": "...",
                         "pinned_scene_instance_view_id": null },   // optional
      "character_bindings": [ { "character_name": "...", "look_id": "...",
                                "composition_id": null } ],         // composition optional
      "prop_bindings": [ { "prop_id": "...", "relation": "held",
                           "anchor": "right_hand", "visual_state_note": "" } ],
      "action": null,           // 或 {kind, subject_character, prop_id, start_state, end_state, note}
      "production_notes": "",
      "dependency_fingerprints": { "visual_profile": null|"fp8",
          "characters": {...}, "looks": {...}, "props": {...}, "compositions": {...},
          "scene_instance": null|"rev8", "scene_instance_view": null|"fp8" },
      "created_at": 0.0, "updated_at": 0.0 } } }
```

关键语义（实现即证）：
- **零图片路径**：binding 全部语义 ID；无 image_path/image_url/reference_image 字段。
- **词表复用**：`relation`/`anchor` 在 bind 时对冻结的 Resources `CompositionRelation`/`CompositionAnchor` 枚举校验（未创建第二套词表）；`action.kind` 校验 BindingActionKind 六值（none/static/pick_up/put_down/hand_off/custom）。
- **OCC**：per-binding `binding_revision`（含时间戳，任何落盘变化翻动 revision）；replace/delete 必带 `expected_binding_revision`，stale → `stale_binding_revision` 拒绝；create 以锁内 absence check 为 OCC（撞已有 → `binding_already_exists`，不覆盖不偷删）。无锁 read-modify-write 不存在（async 包装全程持锁；sync 核心仅供单写者测试上下文，docstring 注明）。
- **纯读**：load 永不创建文件（测试 28 三轮循环验证）。
- **fail closed**：schema 未知版本/坏 JSON → 异常不静默重置（测试 3）。
- **UNBOUND**：无条目 read → `None`，不是错误（测试 x3）；无任何自动创建/猜测路径。
- **orphan**：Shot 删除后 binding 原样保留（测试 27；标记与 GC 属 S3B.2）。

## 4. bind 时 fail-closed 校验清单（全部有测试）

Character 存在 / Look 存在 / Look.character_name 匹配 / Look 有可用 approved reference（approved id 必须真实指向存在的 ReferenceAsset）/ Composition 存在 + character 匹配 + look_id 与 binding 一致 + approved / Prop 存在 + 有可用 approved reference / relation·anchor 词表 / **§七新硬规则 binding_conflict（composition 覆盖的 prop 不得同时出现在 direct prop_bindings；composition 覆盖集可直接从 canonical `composition.prop_bindings` 确定，无需技术限制回退）** / cast 全覆盖（binding 角色集合 == shot.characters，不自动增删推导）/ SceneInstance 存在 + active + story_scene_id==Shot.scene_id / LocationView 存在 + 属于 instance 的 location / pinned SIV 存在 + 属于该 instance + 对应该 view + approved / scene_id 与 Shot 一致 / shot 存在 / action：kind 词表 + pick_up·put_down·hand_off 必带 prop_id·subject_character + subject 必须在 binding 角色集内 + prop FK 存在（approved 与否属 S3B.2 validity 口径——action 是 temporal intent 非视觉绑定，docstring 注明）。

## 5. 新增测试数量与结果

**35 条，全部通过**（`pytest tests/test_shot_binding_s3b1.py -q` → 35 passed, 4.24s）。
覆盖：模型/canonical JSON round-trip、schema fail closed、CRUD、OCC mismatch、并发写锁（asyncio.gather 两路 create/replace 竞争，恰一成功）、原子写无残留、Resources 侧 10 类 fail closed、Scene 侧 5 类 fail closed（含 pinned SIV 三向不匹配）、cast_mismatch 双向、binding_conflict、orphan、零副作用、三 canonical 字节级不变、词表/action 补充。

## 6. 全量回归结果（含执行方式说明）

**执行方式**：全量单进程后台跑两次均因**系统内存不足被环境终止**（第一次 19% 零失败、第二次 5% 零失败处被杀）。改为 **9 块分块顺序执行**（每块独立短命 pytest 进程，130 文件按字典序均分）——分块会改变跨测试状态污染型 flake 的表现，故失败甄别采用**逐条归因**而非总数比对：

| 块 | 结果 | 块 | 结果 |
|---|---|---|---|
| 00 | 212 passed | 05 | 1 failed / 219 passed |
| 01 | 90 passed | 06 | 158 passed |
| 02 | 14 failed / 143 passed | 07 | 125 passed |
| 03 | 159 passed | 08 | 215 passed |
| 04 | 200 passed, 1 skipped | | |

**合计 1521 passed / 15 failed / 1 skipped。**

15 条失败逐条归因：
- **13 条 ∈ 冻结 baseline 34 条清单**（`_pvp_failures_baseline.txt` grep 实证）：test_ai_7d_d3_runtime_control 全 11 条、test_ai_7d_r_director_min::test_not_configured_soft_fail（not_configured 已知族，单跑 3 passed 实证）、test_project_autopilot::test_case7_source_changed_blocked（memory 明载 AI-5A 预存 timing flake，stash baseline 亦失败）。
- **2 条非 baseline 但已证 flake**：test_ai_7ch_cancel_race 的 case1/case2——**整文件单跑 12/12 passed（7.92s）**；且新增包零反向引用（生产代码无 import），因果上不可能影响 cancel 竞态时序。判定：跨测试污染型时序 flake（与既有"单独跑通过"族同构，非本轮引入）。

**既有 vs 新回归区分**：既有 13（baseline 名单）+ 既有 flake 2（单跑实证）；**新回归 0**。

## 7. frozen boundary diff 检查结果

`git diff HEAD` 对以下全部区域 **0 diff lines**（逐目录实测）：
`src/agents`（Agent/Director/Producer/Script Writer/Sub-Agent）、`src/ai`、`src/core`（含 models.py/prompt_builder.py/store.py——project.json schema 零改动）、`src/resources`、`src/scene_system`、`src/visual_profile`、`src/pipeline`、`src/storyboard`、`routers`、`services`。
工作区 tracked 修改为空；新增内容仅 `src/shot_binding/` 四文件 + 测试文件。生成 handler / prompt_builder / HTTP router / UI / refresh / validity / pack 均未创建（§十七禁令逐项核销）。

## 8. 是否存在任何新 regression

**否。**（依据 §6 逐条归因 + §7 零 diff。）

## 9. 是否可以进入 S3B.2

**S3B.1 PASS**
**Ready for S3B.2**

---

## 附录 A：实现期记录的两个设计注记（不扩范围，供 S3B.2/S3B.3 参考）

1. **direct prop 的主体角色关联**：S3A §7.2 的 `prop_bindings` 无 character_name 字段（按 7.md S3B.1 §六"不要自行扩张"未加）。S3B.3 pack 需要把 direct prop 关联到持物角色（pack 的 `characters[].direct_prop_bindings` 结构已预设此需求）——届时需向用户提出"可选 character_name 字段"议案，本轮明确不实现。
2. **create 的 OCC 语义**：create 无 per-binding revision 可比，采用锁内 absence check（撞已有 → `binding_already_exists`）；replace/delete 用 `expected_binding_revision`。与 S3A §7.2 精神一致，已文档化。
3. **unpinned scene_binding 的指纹快照**：`scene_instance_view` 记 None（S3B.1 不做 SIV 自动解析，7.md §八）；解析与存在性检查由 S3B.2 validity / S3B.3 pack 承接。

## 附录 B：执行环境备注

- 全量回归期间后端 :8000 已停（已知坑遵循）；本报告提交后已重启后端。
- 分块运行的 9 份原始输出保留为 scratch（`_s3b1_run_0*.txt` / `_s3b1_all_failures.txt`），不入库（遵循 scratch 不提交惯例）。
