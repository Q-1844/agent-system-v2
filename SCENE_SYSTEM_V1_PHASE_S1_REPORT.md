# Scene System V1 — Phase S1（Foundation）报告

分支 `feat/scene-system-v1-s1`（自冻结基线 `6b4b8ad` = tag `resources-v1-frozen` 分出，6 commits A-F）。
本轮只建立**空间与场景事实系统基础**（Location / SpatialAnchor / SetLayout / LocationView /
SceneInstance / SceneState）+ companion canonical + HTTP API + 测试。
**不接 Agent、不生成图片、不改 Shot/Director/Sub-Agent/Producer、不改 project.json / resources.json。**

---

## 1. Current Scene audit（现有 Scene 审计结论）

| 调查点 | 结论 |
|---|---|
| `src/core/models.py::Scene` | 故事结构段：`scene_id`（"s01" 身份证号，`next_scene_seq` 分配、删除不回收、永不改名）、`title`、**`location: str`（创作层自由文本）**、**`time_of_day: str`（自由文本）**、`summary`、`characters: List[str]`、`shots`。存 `output/project.json`（schema_version=2） |
| Story/Scene CRUD | `routers/scenes.py` PUT /scenes 按 scene_id 对账（新增分配新号绝不顶旧号；删除清理产物文件——悬空引用可能性旧系统已存在）；`routers/story.py` organize/continue/apply |
| Agent 域写路径 | `src/agents/script_writer.py` `SCENE_UPDATABLE_FIELDS=("title","location","time_of_day","summary","characters")`；OCC 用 `_scenes_content_token`（sha256[:8] 全局内容指纹） |
| Director | `src/ai/director.py` plan_scene 只读 `scene.summary + scene.characters`（stale 快照同） |
| Scene Sub-Agent | `src/agents/subagent/instance.py` 段状态头**展示** `scene.location/time_of_day`（自由文本，行 68-69）；工具 = Director 读工具 ∪ proposal.submit（物理无写工具） |
| continuity/review | `continuity_reviewer` 是 capability/routing 概念；Director `visual.review_image` focus=continuity 附上一镜画面；revision impact `CONTINUITY_CHAIN_CHANGED`。**无任何空间模型** |
| prompt 环境表达 | `src/core/prompt_builder.py`：首帧/尾帧/视频 prompt 的环境信息**全部来自 Shot 自由文本字段**（description/lighting/environment_motion）。`Scene.location/time_of_day` **不进 prompt** |
| 前端 | 仅 `src/api/index.ts:249` TS 类型声明含 location/time_of_day；无编辑/显示 UI（storyboard 页不显示） |
| project_signature/OCC | `project_signature`（完整 JSON sha256，change_analysis stale 检测）覆盖 location/time_of_day；`creative_source_signature`（创作子集）只含 scenes(scene_id/summary/characters)；`project_write_lock` per-project asyncio.Lock 为唯一 project 锁原语 |
| 冲突检查 | **无 BLOCKER**。`Scene.location: str`（故事层）与新 `Location`（生产空间事实实体）名字重叠但语义不同层；处理方式同 Resources V1 对 Character：不改旧 schema，SceneInstance 单向引用 |

## 2. Existing Story Scene ontology（保持不动）

```
Project.json (schema_version=2)
  scenes: [Scene]
    Scene.scene_id      "s01"（身份证号，永不改名）
    Scene.title / summary / characters
    Scene.location      ← 创作层自由文本（"太平湾码头"），保持原样
    Scene.time_of_day   ← 创作层自由文本（"日/夜"），保持原样
    Scene.shots: [Shot]（Shot.scene_id → Scene.scene_id）
```

## 3. New Scene System ontology

```
scene_system.json (schema_version=1)            project.json（只读引用）
  locations: {loc_id: Location}     ◄── LocationView/SetLayout/SpatialAnchor 挂在 location_id
  anchors:   {anc_id: SpatialAnchor}      ┌──────────────────────┐
  layouts:   {layout_id: SetLayout}       │  Story Scene (s01)   │  不修改
  views:     {view_id: LocationView}      └──────────┬───────────┘
  scene_instances: {scin_id: SceneInstance}◄───────────┘ story_scene_id 单向引用
                         └── state: SceneState（嵌入，唯一 state 真相源）
                               └── prop_placements ──引用──► resources.json props（只引用）
```

- **Location** = 这个地点是谁（长期稳定空间身份）
- **SpatialAnchor** = 地点内稳定空间对象/区域（门/窗/楼梯/家具/地标/区域）
- **SetLayout** = 给系统理解空间的结构化平面信息（orientation + anchor placements）
- **LocationView** = 地点的一个观察视角 metadata（camera_zone → look_direction）
- **SceneInstance** = 某个 Story Scene 在某个 Location 中发生时的生产空间状态实例
- **SceneState** = 该 instance 的剧情时刻状态（time/weather/lighting + anchor 结果态 + prop 位置）

## 4. Storage decision

`projects/{project}/output/scene_system.json`（companion canonical），沿用 Resources V1 已验证模式：

- 🔴 不修改 project.json / Project dataclass / `schema_version=2` / migrate_v1_to_v2。
- 🔴 不触碰 resources.json（冻结区；T24/T25 以字节哈希证明零扰动）。
- 读零副作用（无文件返回空文档，绝不创建）；首写才建文件；`atomic_write_json` 原子写。
- 复用 `project_write_lock`（同 project_name key，与 project.json/resources.json 写串行化；**不创建第二套 project lock**）。
- 坏 JSON / 顶层非 object / 未知 schema_version → `SceneSystemSchemaError` fail closed，绝不静默重置。
- `schema_version=1` 顶层五 collection：`locations / anchors / layouts / views / scene_instances`。

## 5. Schema

```json
{
  "schema_version": 1,
  "locations":  { "loc_<uuid12>": {name, description, identity_description, style_notes,
                                    created_at, updated_at} },
  "anchors":    { "anc_<uuid12>": {location_id, name, kind, description, semantic_zone,
                                    relations: [{relation, object_anchor_id, note}],
                                    created_at, updated_at} },
  "layouts":    { "layout_<uuid12>": {location_id, orientation_convention, notes,
                                       placements: [{anchor_id, zone, x?, y?, note}],
                                       reference_asset_id, created_at, updated_at} },
  "views":      { "view_<uuid12>": {location_id, label, camera_zone, look_direction,
                                     visible_anchor_ids, reference_asset_id,
                                     approval_status, created_at, updated_at} },
  "scene_instances": { "scin_<uuid12>": {story_scene_id, location_id,
                                          location_revision_at_bind, status,
                                          state: {
                                            time_of_day, weather, lighting,
                                            environment_conditions, notes,
                                            anchor_states: {anchor_id: {condition, note}},
                                            prop_placements: {prop_id: {anchor_id, state, note}}
                                          },
                                          created_at, updated_at} }
}
```

- `anchor.kind` enum：door/window/stairs/furniture/landmark/zone/entry/exit/other。
- `anchor.relation` 词表：north_of/south_of/east_of/west_of/left_of/right_of/adjacent_to/opposite/inside/connected_to/near。
- `SceneInstance.status`：active/archived/retired（字段就位，V1 只产生 active）。
- Location **不持久化** anchor_ids/view_ids/layout_id（单真相源派生，见 §6）。

## 6. Location

只放长期稳定空间身份（"圆形控制室、北侧入口、西墙三扇窗、中央控制台、南侧楼梯"）。
不放剧情时刻状态（今晚暴雨/灯坏了/窗户破了）——那是 SceneState。

设计决定：`anchor_ids/view_ids/layout_id` 不持久化在 Location 内，而是从 anchors/views/layouts
的 `location_id` FK **read 时派生**（read API envelope 附带）。理由：杜绝双向同步失真（anchor
create/delete 不需要回改 Location），且 location_revision 只反映 Location 自身内容（rename
anchor 不会 bump location revision，OCC 冲突面最小）。这是对任务书 §4 字段列表的有意偏离，
语义完全覆盖（见 §18 Deviations D1）。

## 7. SpatialAnchor

初版限定 kind enum（9 值）+ 自由 semantic_zone + **内嵌 relations[]**（subject 隐含=所在 anchor，
三元组语义完整：subject/relation/object）。不做 3D 坐标 / 物理引擎 / scene graph solver。
校验：relation ∈ 词表；object anchor 存在、同 location、不自指；跨 location 关系 V1 拒绝
（cross_location_relation，422——跨地点语义留给 Location 级叙事描述）。
删除 fail closed：被其他 anchor relations / view visible_anchor_ids / layout placements /
scene_instance state 引用 → `anchor_in_use`（无级联 sweep）。

## 8. SetLayout

一个 Location 至多一个 layout（1:0..1；重复 create → `layout_already_exists`）。
字段：`orientation_convention`（如 north_up）、`placements[]`（anchor_id + zone +
**normalized x/y ∈ [0,1] layout hint**）、`notes`、`reference_asset_id`（optional）。
normalized XY 已实现（实现成本低），🔴 明确语义：只帮 Agent/continuity 理解相对方位的 hint，
绝不解释为物理厘米/3D transform（校验强制 [0,1] 且 receipt 错误信息内注明）。

## 9. LocationView

不用 Character 式 front/side/back。字段：`label`（"South-west to North-east"）、
`camera_zone`（"south_west"）、`look_direction`（"north_east"）、`visible_anchor_ids`
（校验：存在 + 同 location）、`reference_asset_id`（optional，本轮无生成）、
`approval_status`（字段就位：unreviewed/approved/rejected）。
**View Bank 原则写入设计与测试**（T9）：Minimum Coverage + On-demand Expansion——
1 个 view 是合法状态，系统不要求、不预生成固定 8 方向。

## 10. SceneInstance

- `story_scene_id` → 现有 Story Scene（create 时同锁经 ProjectStore 校验存在；
  旧 Scene 被删后 read 返回 `story_scene_missing: true` 明确标注，不静默忽略）。
- `location_id` → Location（T12）。
- `location_revision_at_bind` = 绑定时刻 location revision 快照——**历史事实，写一次不再更新**
  （不是当前 revision 的缓存派生值；当前值可随时重算比对，V1 不做比对 UI）。
- V1 规则（T13）：同一 story_scene_id 至多一个 `status=active` instance（`instance_already_active`）。
  schema 不封死未来：status 字段含 archived/retired，未来"同 Scene 多 location / sequence"通过
  非 active instance 表达。
- 换绑 location：fail closed——state 引用旧 location anchor → `state_references_old_location`
  （先清 state 再换绑；换绑成功后刷新 bind 快照）。

## 11. SceneState

嵌入 SceneInstance（该 instance 的当前状态；**不建第二个竞争 state 真相源**）。

- 时间/环境结构化字段：`time_of_day / weather / lighting / environment_conditions / notes`。
- `anchor_states`：`{anchor_id: {condition, note}}`——跨 Shot 需要持续成立的**结果状态**
  （window_west_02: broken；north_door: open）。校验：key 必须是该 instance location 的合法 anchor。
- `prop_placements`：`{prop_id: {anchor_id, state, note}}`（prop_sword01 在 floor_near_console，
  intact）。校验：prop_id 必须存在于 Resources（T16）；anchor 必须合法。
- 🔴 不保存 Shot 内动作过程（"Alice 把剑扔到地上"的完整动作属于 Shot/Video）。
- 🔴 不加 `characters: Dict[str, Any]` 万能 JSON；未来 typed CharacterPlacement 作为新字段加入
  （from_dict known-field 过滤前向兼容，无需迁移）。
- patch 语义：字符串字段逐个 patch；`anchor_states/prop_placements` 整 dict 替换
  （read-patch-write + OCC 保障并发安全，语义简单可预测；未 patch 的 dict 字段保持不变）。

## 12. Resources relationship（T16 无 deviation）

Prop identity 属冻结的 Production Resources V1，Scene System **只引用不复制**：

```
Resources（resources.json）      Scene System（scene_system.json）
  Prop（"木剑是什么"）      ◄──引用──  SceneState.prop_placements（"木剑现在在哪里、什么状态"）
  ReferenceAsset            ◄──引用──  LocationView/SetLayout.reference_asset_id（未来视觉参考）
```

跨 store 校验**在写锁内安全复用**（纯文件读、无网络，临界区仍短）：
- story_scene_id → `ProjectStore.load()`（FileNotFoundError → story_scene_not_found）。
- prop_id → `ResourceStore.load().props`（resources.json 损坏 → `resources_lookup_failed`
  fail closed；不触及 prop 校验的 patch 不受影响——T16b 分别证明）。

依赖方向：`src/scene_system` → `src/resources`（只读）→ 单向，无环；resources 不 import scene_system。

## 13. Shot boundary（§12 冻结）

- Shot/Video 负责"**发生了什么**"（动作过程、镜头内状态变化）。
- SceneState 负责"**发生以后世界现在是什么状态**"（跨 Shot 持续成立的结果）。
- 本轮只建数据能力，**不改 Shot schema、不写任何 Shot→SceneState 绑定**。未来 Shot 通过
  `scene_instance_id`（新字段）选择场景 Reference 时，即自动获得所在 SceneState 的约束。

## 14. OCC / revision

完全沿用 Resources 成熟经验：

- 指纹族：`document_revision / location_revision / anchor_revision / layout_revision /
  view_revision / scene_instance_revision` = sha256(canonical json, sort_keys)[:8]。
- **deterministic**（T17）：同一磁盘内容 → 同一 revision；**不持久化任何 derived revision**。
- 所有 mutation：create 需 `expected_document_revision`；update/delete 需 `expected_revision`。
- stale → 结构化 receipt `{ok:false, error:"stale_revision", expected_revision,
  current_revision, current}`；**不写入、不自动替换、不 silent retry**（T19 以磁盘字节证明）。
- 并发：同 revision 双 mutation 恰好一个成功（T20，per-project 锁串行化 + OCC）。
- save 后重读磁盘验证 → `receipt.verified`（truth gate 语义对齐）。

## 15. APIs

命名空间 `/api/project/{p}/scene-system/*`（不与 /scenes /story /resources 冲突）。
Router 只做 HTTP mapping（`_ERROR_STATUS`: stale/in_use/already_active→409、not_found 族→404、
validation/invalid_relation→400、cross_location_*→422、resources_lookup_failed→500）；
业务全在 `src/scene_system/service.py`。

| 方法 | 路径 | OCC 期望值 |
|---|---|---|
| GET | /overview（document_revision 来源）/locations /locations/{id} /anchors /anchors/{id} /layouts/{id} /locations/{id}/layout /views /views/{id} /scene-instances /scene-instances/{id} | —（读零副作用） |
| POST | /locations, /anchors, /views | expected_document_revision |
| POST | /locations/{loc_id}/layout | expected_document_revision |
| POST | /scene-instances | expected_document_revision |
| PATCH | /locations/{id}（rename）、/anchors/{id}、/layouts/{id}、/views/{id}、/scene-instances/{id}/location、/scene-instances/{id}/state | expected_revision |
| DELETE | /locations/{id}（in_use→409）、/anchors/{id}（in_use→409）、/layouts/{id}、/views/{id}、/scene-instances/{id} | expected_revision |

`api_server.py` 仅 +2 行（import + include_router）。**本轮无 UI**（Backend Foundation 优先）；
未改 Storyboard / Director / Agent Center 前端。

## 16. Tests（T1-T26 全覆盖，25 tests passed）

`tests/test_scene_system_s1.py`（tmp_path 隔离，不触碰真实 projects/）：

- T1 老项目空读零副作用 / T2 首写建文件 ✓
- T3 Location 稳定 id 跨"重启" / T4 rename id 不变（旧 expected 复用→stale）✓
- T5 anchor 归属校验 / T6 relation 三类拒绝（未知词/自指/跨 location）+ 合法通过 ✓
- T7 view 合法 visible anchors / T8 缺失拒绝 / T9 无固定 8-view 假设（1 个 view 合法）✓
- T10 layout 持久化 orientation/placements + x/y∈[0,1] 强制 + 1:0..1 ✓
- T11 story_scene 必须真实 / T12 location 必须真实 / T13 一段一 active + 换绑 fail closed ✓
- T14 time/weather/lighting round-trip / T15 anchor_state 非法引用拒绝 /
  T16 prop 必须真实（+T16b resources.json 损坏 fail closed 且不阻塞其他 patch）✓
- T17 fingerprint deterministic / T18 revision 随 mutation 变 / T19 stale 拒绝且磁盘零污染 ✓
- T20 并发同 revision 恰好一个成功 / T21 坏 JSON / T22 未知 schema fail closed ✓
- T23 重启持久化全图（location/anchor/relation/view/layout/instance/state）✓
- 悬空 story_scene read 标注 `story_scene_missing:true` + project.json 缺失时创建 fail closed ✓
- T24/T25 project.json / resources.json 字节哈希在"全谱系 mutation + 失败路径"前后不变 ✓
- T26 `src/agents`、`src/ai` 全源码无 `scene_system` 引用（Agent 零接入的机械证明）✓
- HTTP 冒烟：200/409/404/400/422 mapping + 读零副作用 ✓

## 17. Regression

| 套件 | 结果 | 基线对比 |
|---|---|---|
| Scene System 套件 | **25 passed** | 新增 |
| 资源八套件（T27） | **144 passed** | 与冻结基线完全一致 |
| Agent 关键回归（T28）：test_agents + ai_7d_r story/batch/recovery/director_min/script_writer_min/multi_tool | 134 passed + 2 failed | 2 failed 均为已知 `not_configured` 族套件内干扰（memory 已知 flake），**单跑各自复绿**，零新增 |
| Sub-Agent + 7B 协议（subagent×7 + 7b_1/2/4） | **118 passed** | 零失败 |

端口检查：运行前确认 :8000/:7777 未占用（避免已知端口干扰噪声）。

## 18. Deviations（有意偏离任务书的点，均已实现且测试覆盖）

- **D1** Location 不持久化 `anchor_ids/view_ids/layout_id` 字段（§4 列表含它们）：
  改为 read 层从 FK 派生（单真相源）。read envelope 完整提供三者，消费语义无差。
  收益：无双向同步 bug；location_revision 语义更干净。
- **D2** AnchorRelation 内嵌于 `anchor.relations[]`，`subject_anchor_id` 由所在 anchor 隐含
  （§6 三元组中 subject 显式列出）：结构上不可能 subject≠所在 anchor，校验更少一个自由度。
- **D3** `location_revision_at_bind` 持久化（§10 要求字段；§17 说"不持久化 derived revision"）：
  定性为**绑定时刻历史快照**（写一次不再更新，不是会失效的当前值缓存），遵守 §17 精神。
- **D4** normalized x/y 已实现（§7 允许延后）：成本极低，校验强制 [0,1] + 语义标注"hint 非物理"。
- **D5** `anchor_states/prop_placements` patch 为整 dict 替换而非 per-key merge：
  OCC 已保障并发安全，整替换语义更简单可预测；read-patch-write 是标准用法。
- **D6** T16 跨 store 校验**安全可行，无 deviation**：prop 校验直接复用 ResourceStore（只读），
  未复制 Prop canonical；resources.json 损坏时 fail closed（`resources_lookup_failed`）。

## 19. Blockers

无。Phase S1 完成，全部验收点覆盖。

## 20. Next phase recommendation（下一阶段建议）

1. **Scene System → Agent Integration（独立阶段）**：
   - 只读工具：`scene.location.list/read`（含 anchors/relations/layout/view metadata 摘要）、
     `scene.instance.read`（SceneState）。Director 规划 shot 时获得"在哪里 + 此刻状态"。
   - 写路径建议走 proposal protocol（Sub-Agent proposal.submit 模式），不直接给写工具。
2. **Scene Reference 生成（LocationView → 图）**：复用 Resources 三阶段生成协议 +
   per-entity single-flight + Visual Review 六维度（见 J）。从 hero establishing 起步。
3. **CharacterPlacement（§13 下一阶段）**：`SceneState.character_placements:
   List[CharacterPlacement]`（character_name + anchor_id + pose/screen_direction + state_note，
   typed dataclass），不再推迟 characters 万能 JSON。
4. **删除生命周期 sweep**：Location 删除级联清理、Story Scene 删除时对 SceneInstance 的
   告警/归档策略（当前 fail closed 已安全，UX 可以更顺）。
5. **前端最小 debug 页**（低优先）：Location/Anchor/Instance 树 + SceneState 表单。

---

## §26 特别问题逐条回答

**A. Existing Story Scene 与 SceneInstance 最终关系是什么？**
组合关系：Story Scene 是故事结构单位（"发生什么剧情"），SceneInstance 是该剧情段在某个
Location 中发生时的**生产空间状态单位**（"在哪里、此刻世界什么状态"）。`story_scene_id`
单向引用、生命周期解耦（旧 Scene 被删 → instance 悬空，read 明确标注 missing，创建拒绝）。
V1 约束一段至多一个 active instance；schema 留 archived/retired 支持未来多 location/sequence。
现有 Scene 的 `location/time_of_day` 自由文本字段保持原样，不迁移不覆盖——两层真相
（创作描述 vs 生产事实）有意并存，最终由 Agent integration 阶段决定消费优先级。

**B. Location 为什么不能等同 Background image？**
背景图是**一个视角的一次像素化渲染**：它冻结了天气、时间、光线、构图和视角；同一灯塔控制室
在暴雨夜/晴天/灯坏了时需要不同的图。Location 是**长期稳定的空间身份**（谁、有什么、什么
空间关系），是"可复现地生成任何视角/任何状态背景图"的语义基底。把两者等同 = 每换一次
天气/时间就要重新描述整个地点，一致性无锚点、跨 Shot 状态（窗破了）无处安放。

**C. SceneState 哪些状态属于 Scene，哪些属于 Shot？**
属于 SceneState（跨 Shot 持续成立的结果/条件）：time_of_day/weather/lighting/
environment_conditions；anchor 的结果 condition（窗已破、门已开、灯已坏）；prop 的位置与
状态（剑在地上、完好）。属于 Shot（一次发生的动作过程）：opening_state/end_state、
motion/action_sequence、frame_change、镜头内临时状态（"举剑"的过程帧）。判据：**该状态
是否必须在下一镜继续成立且无需重述**——是→SceneState；只在本镜画面中短暂出现→Shot。

**D. SpatialAnchor 第一版需要多复杂？**
就做当前实现这么多：稳定 id + name + 9 值 kind enum + 描述 + semantic_zone + 11 词有向
关系表。再多（3D 坐标、mesh、物理引擎、自动 scene graph solver）是负资产：Agent 消费不了、
维护成本高、错误精度反而破坏 continuity 判断。第一版的价值在**词汇表统一**（"北门""主控台"
有稳定 id，Agent/continuity/review 说的是同一个东西）。

**E. 是否需要 normalized 2D layout？**
需要，但只作为 hint——已实现（AnchorPlacement.x/y ∈ [0,1] 强制校验）。它便宜地让
"视角 A 看不到北门（在南墙）"这类推理成为可能（可见性/相对方位），且未来 top-down 示意
渲染可直接消费。明确不解释为物理厘米——真实度量对本项目的 Agent/生成链路无消费者。

**F. LocationView 如何表达角度，为什么不使用 front/side/back？**
表达：`camera_zone`（相机在哪）+ `look_direction`（看向哪）+ `visible_anchor_ids`
（从该视角应看到哪些 anchor）+ 自由 label。不用 front/side/back 因为：①角色三视图是
**对象中心**的封闭集合（一个人确实就前后左右），而空间视角是**场景中心**的开放连续集
（控制室可以有 17 个有用角度）；②front/side/back 携带的"正交相对物体"语义在空间里
不成立（建筑没有"背面视图"惯例）；③camera_zone/look_direction 直接对接未来 Shot 的
camera_angle 字段与可见性推理。

**G. 初始 Location 应生成多少 Views？**
2~4 张（写进设计）：hero establishing（定空间身份）+ reverse coverage（反打）+ 1~2 个
关键故事方向。原则 **Minimum Coverage + On-demand Expansion**：Shot 真需要新角度时再按需
生成新 LocationView（metadata 先行，图片后补）。T9 测试固定了"系统不假设 8 方向"，
防止未来 UI/Agent 默认"全方位生成"。

**H. top-down SetLayout 应该是 canonical 结构、visual asset，还是二者兼有？**
二者兼有、分层：canonical 结构 = 本轮已实现的 SetLayout（orientation + placements +
relations，OCC 保护、Agent 可读）；visual asset = 未来由它**渲染**出的一张 top-down 示意图
（存为 Resources ReferenceAsset，`SetLayout.reference_asset_id` 指回，本轮字段就位未生成）。
结构是真相、图是投影——图过时可以重渲染，结构永不过时。反过来（图是真相）则空间语义
永远困在像素里。

**I. Location references 如何复用 Resources ReferenceAsset 而不把 Location 变成 generic Resource？**
当前已实现 + 未来扩展：LocationView/SetLayout 持 `reference_asset_id` 指向 Resources canonical
的 ReferenceAsset（媒体、checksum、URL 均归 Resources 管）；Location 实体本身留在
scene_system.json。不塞回 resources.json 的理由：Resources 的 schema（look/clothing/prop/
composition）是"**可生成视觉资产**"域，而 Location/SceneInstance 是**空间与状态语义**域，
二者生命周期、审批语义、review 维度都不同；混域会让 Resources 的 write_service 审批硬门槛
与 Scene 的状态 patch 互相污染。单一依赖方向：scene_system → resources（只读）。

**J. 以后 Scene Visual Review 应该检查什么？**
复用 Resources 已验证的 vision review + deterministic verdict + explicit approval 模式，
冻结六维度（本轮不改 Director Visual Review tool）：
① `location_identity`——图是否呈现该 Location 的 identity_description（圆形控制室≠矩形机房）；
② `anchor_consistency`——visible_anchor_ids 中的对象是否出现且相对关系正确（北门在北侧）；
③ `layout_consistency`——与 SetLayout 方位/placement hint 不矛盾；
④ `scene_state_consistency`——与绑定 SceneInstance 的 SceneState 一致（暴雨图不能出晴天）；
⑤ `lighting_weather_consistency`——time_of_day/weather/lighting 与画面光影一致；
⑥ `visual_defects`——通用缺陷（Resources 同维度）。
审批产物 = approved LocationView → 才能被未来 Shot binding 消费（fail closed 同 Resources）。

**K. 以后 Director / Scene Agent 分别应该读什么，但为什么现在不接？**
- Director（镜头规划）：`scene.instance.read`（SceneState：此刻天气/时间/光/窗破门开）+
  `scene.location.read`（identity_description + layout orientation + view metadata）——
  用于把"环境正确性"写进 shot 提案与 prompt 结构字段。
- Scene Sub-Agent（段级设计）：同上 + `scene.view.list`——为每个 shot 选择/申请 LocationView
  reference；端态提案中携带 anchor 级 continuity 约束。
- **现在不接的原因**：①工具语义未经过真实生产检验，先让数据层在无 Agent 压力下稳定
  （Resources 的教训：先冻结基础层，Agent 层才不会把 schema 演进和工具语义演进搅在一起）；
  ②写路径必须走 proposal protocol，需要单独设计 review/验证规则；③避免本轮冻结基线
  （Resources V1）之外的回归面。T26 以源码级断言证明零接入。

---

## Commits（A-F）

| Commit | 内容 |
|---|---|
| `b9c1f6c` A | 现有 Scene 审计 + typed models（六实体 + fingerprint 族 + schema v1 fail closed） |
| `ac25c9a` B | SceneSystemStore（零副作用读 / atomic 写 / project_write_lock 复用 / fail closed） |
| `dfd7434` C | Location/Anchor/Layout/View service（CRUD + 校验 + fail closed 删除 + OCC receipt） |
| `54a39fe` D | SceneInstance/SceneState service（双外键校验 / 一段一 active / bind 快照 / state patch / prop 跨 store 校验） |
| `54d2189` E | routers/scene_system.py HTTP API + api_server 挂载（+2 行） |
| `54e70b4` F | tests/test_scene_system_s1.py（25 tests，T1-T26） |

## 停止点确认

本轮未做（按 §29 停止条件）：场景图片生成 / Scene Vision Review / Shot 修改 / Director 修改 /
Sub-Agent / Producer 接入 / 自动给 Story Scene 创建 Location / 自动生成 8 视角 /
完整 continuity validator。到此停止，等用户验收后决定是否冻结与进入下一阶段。
