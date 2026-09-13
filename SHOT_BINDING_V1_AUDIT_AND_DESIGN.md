# Shot Production Binding V1 — 架构审计与设计（S3A）

> 阶段：**S3A = AUDIT + ARCHITECTURE DESIGN + IMPLEMENTATION PLAN**（执行指令 `详细流程文件/修复阶段/7.md`）。
> 本阶段**零代码修改**：不改 Agent prompt / tool schema / registry / Producer / Director / Script Writer / Scene Sub-Agent / Resources canonical / Scene System canonical / project.json。
> 审计基线：子模块 `fc7d118`（Project Visual Profile V1 Phase P1）。冻结区：Resources V1（tag `resources-v1-frozen`）、Scene System S1/S2A/S2B/S2B.1-S2B.5、Agent System V1。
> 审计方法：核心文件全部人工通读（models/store/prompt_builder/三套 canonical/instance_view_basis/visual_service/generation），生成流与 Director 消费、图片绑定清单由两路独立扫描交叉验证；所有 file:line 均为实读核对。

---

## 1. Executive Summary

**现状一句话**：系统目前存在三个互不连通的"生产世界"——① legacy 分镜管线（Shot + prompt_builder + StyleAnchor + Character.views 三视图），② Production Resources（Look/Prop/Composition 的 approved 参考），③ Scene System（LocationView/SceneInstanceView 的 approved 参考）。P1 已把 ②③ 用 ProjectVisualProfile 缝合（风格传播+stale 归因），S2B.5 验收结论是"分镜一致性参考的两半都已就绪"——**缺的就是中间那个消费者：Shot 生产绑定**。

**审计核心结论**：

1. **Shot 本身没有把图片路径当资源身份**。Shot 引用角色只用名字（`Shot.characters: List[str]`），生成的参考图证据存在 `char_ref_snapshot`（写 3 处、读 0 处，纯 provenance）。真正的"图=身份"耦合在 **Project 级 legacy `Character.views`**（raw path，无 checksum，改名要重写路径）——Resources 已用只读 adapter（`legacy_character_references`，合成 id `legacy:{name}:{view}`）桥接，未来迁移路径已预留。
2. **Shot 生成流是纯 legacy 世界**：三个 JobQueue handler（first_frame/last_frame/video，全在 `routers/shots.py` 内联）是 autopilot / Director 工具 / Producer capability 的**唯一汇合点**；参考图收集 `_collect_first_frame_refs` 只有 character + continuity 两类，**代码注释已预留 environment/prop 扩展位**；prompt_builder 对 Scene 字段零消费，对 Resources/SceneSystem/VisualProfile 零 import。
3. **Shot 是创作+生产混合体但资源引用层是空的**——这正是 Binding 要补的层，且不需要动 Shot schema 一个字节。
4. **StyleAnchor 与 ProjectVisualProfile 是两个并存的项目风格概念**（P1 报告 §22 已预告）。本设计裁决：bound shot 一律消费 ProjectVisualProfile（fail closed）；unbound shot 保持 StyleAnchor 行为字节不变；不做自动迁移。

**设计核心**：

- **ShotProductionBinding** = 独立 canonical companion `output/shot_production_bindings.json`（不动 project.json / Shot schema），只存**语义引用**（look_id / prop_id / composition_id / scene_instance_id / view id / 指纹快照），绝不存图片路径、绝不复制资源真相。
- **ShotReferencePack** = 派生编译产物（**不落盘**，每次请求重算），把 binding + 三套 canonical 编译成"人和 Agent 都能看懂"的一镜生产事实：SCENE / CHARACTERS / PROPS / ACTION / STYLE / REFERENCES / VALIDITY。
- **Binding Validity Gate** = read 模型（绝不自动 mutation）：bind 时刻存依赖指纹快照，read 时重算对比，逐依赖归因 missing / revoked / stale / mismatch。**架构性保证"无关资源变更不 stale"**：指纹只对 binding 实际引用的实体计算。
- **兼容铁律**：UNBOUND shot（含全部 legacy shot）走现有路径，行为字节不变；BOUND shot 的生成接线是 S3C 的事，且必须 gate 在"唯一汇合点"（三个 handler 的 precheck）。

**S3B 建议**（详见 §26）：S3B.1 canonical+store+CRUD/OCC → S3B.2 validity read model + production view → S3B.3 pack compiler → S3B.4 HTTP API + 最小 UI → S3C 生成接线。Agent Integration 继续延期。

**需要用户确认的架构决策 8 项**，见 §27。

---

## 2. Current Shot Architecture Audit

### 2.1 Shot schema（src/core/models.py:248-338）

Shot 是创作语义 + 生产产物的一体 dataclass，落在 project.json（schema_version=2）。字段分五组：

| 组 | 字段 | 性质 |
|---|---|---|
| 创作核心 | `description` / `shot_size` / `camera` / `lighting` / `motion` / `dialogue` / `characters: List[str]` | 纯创作；characters 是**名字**（引用 project.json `Character.name`），无任何资源 ID |
| 结构化镜头（提示词工作台） | 首帧：`opening_state` / `camera_angle` / `composition` / `important_details` / `required_elements`；尾帧：`last_frame_description` / `end_state` / `frame_change` / `preserve_elements` / `add_elements` / `remove_elements` / `end_composition`；视频：`camera_movement` / `motion_pace` / `environment_motion` / `action_sequence` / `transition_description` / `video_preserve_elements` | 创作语义，**其中已有承载 Case D 时序意图的字段雏形**（`opening_state`/`end_state`/`action_sequence`），但是自由文本 |
| 提示词 | `prompt_en` / `last_frame_prompt_en` / `video_prompt_en` + 三个 `*_prompt_override` + 三个 `*_prompt_sig` | 三段独立英文提示词；override > prompt_en > 结构字段拼接 |
| 产物 | `first_frame` / `last_frame`（ImageAsset：candidates + selected_index）、批次历史（`first_frame_batches` / `last_frame_batches`：`ImageBatch.prompt_snapshot` + `char_ref_snapshot`）、`audio`、`video` + `video_versions`（`VideoVersion` 同构 char_ref_snapshot）、`selected_*` 指针、`final_video` | 生成候选与选择；**批次是新真相源、旧单值字段兼容** |
| 镜头接力（Phase 6A） | `opening_source` / `source_shot_id` / `source_version` / `video_end_frame`（ffmpeg 提取的实际末帧）/ `video_end_source_version` | 跨镜连续性机制，与 link_mode 独立 |

**关键审计事实**：Shot 的全部字段里**没有一个资源 ID**（无 look_id / prop_id / composition_id / view_id / instance_id / profile 指纹）。Shot 对"资源/场景/风格"的全部引用 = 角色名字符串 + 自由文本描述。这是干净的创作层——Binding 作为独立 companion 补生产层，不会与任何现有字段竞争真相。

### 2.2 写路径与 OCC

- `ProjectStore.update_shot(shot_id, mutator)`（src/core/store.py:138-146）：全文件读-改-写，硬规则"所有生成任务写回必须走此方法"。
- **Director 旁路（已知的既有事实，非本轮引入）**：Director agent 的 CRUD 工具经中性层 `src/storyboard/structure_service.py` 改内存 Project 后**整项目 `pstore.save(proj)`**（src/agents/director.py:898/1032/1160/1312），不走 update_shot；安全由三层兜底——advisory OCC 指纹（`_shots_token` 结构 / `_shot_token` 单镜内容 / `_shots_content_token` 全部内容，director.py:146-202，写前检查写后重读验证）+ `update_shot_fields` 白名单（`SHOT_UPDATABLE_FIELDS`，director.py:162-174：**Director 从不写 prompt_en / override / link_mode / seed / negative_prompt**）+ `_invalidate_stale_prompts`（结构字段变化自动清空非 override 提示词，structure_service.py:358-406）。
- **对 Binding 的含义**：V1 binding 写入方只有 HTTP API + UI（7.md §13 禁改 Agent）；binding 数据不在 project.json 里，因此天然不进 Director 的整项目写路径，无写路径冲突。Director 若删镜/改镜，binding 的 shot FK 由 read 层校验（§20）。

### 2.3 `_invalidate_stale_prompts` 与 Binding 的关系

结构化创作字段变化 → prompt_en 失效 → 下次生成重新拼接。Binding 引入后新增一类失效源（look/scene/profile 变化 → binding stale）。**设计裁决**：binding staleness 是 read 派生事实（对齐 SceneInstanceView `approved_basis_fingerprint` 模式），**不去动 prompt_sig 机制**——两者语义不同：prompt_sig 管"创作字段 vs 提示词文本"，binding 管"生产依赖 vs 语义引用"。不混成一个机制。

---

## 3. Current Story Scene / Shot Relationship

- `Scene`（models.py:371-389）：`scene_id`（"s01" 身份证号，next_scene_seq 分配删除不回收）、`title` / `location` / `time_of_day` / `summary` / `characters` / `shots[]`。
- **Scene 字段的消费方**（审计实证）：
  - `location` / `time_of_day`：Script Writer 拥有（`SCENE_UPDATABLE_FIELDS`，script_writer.py:205）；Sub-Agent 场景状态头渲染（subagent/instance.py:63-71）；**机械 prompt_builder 零消费**。
  - `summary`：Director shot.list 摘要、**prompt 生成 LLM 的上下文**（`_collect_prompt_context`，routers/shots.py:844 → "故事段(背景)" 892 行——这是 shot 生成链里唯一的 scene 上下文，经 LLM 而非机械拼接）、production_planner/autopilot 的 plan stale 对比。
- **Scene System 侧已有桥**：`SceneInstance.story_scene_id` 单向 FK → Story Scene（创建时校验存在；旧 Scene 被删 → read 标注 `missing_story_scene`，不静默）。V1 规则：同一 story_scene_id 至多一个 active instance。
- **两层不互写**（S1 审计结论，models.py:13-22 头注）：`Scene.location/time_of_day` 是创作自由文本；`Location`/`SceneState` 是生产空间事实。Binding 必须尊重这个边界：scene_binding 引用 SceneInstance（生产事实），**不碰** Scene 创作字段。

---

## 4. Current Prompt / Generation Flow

### 4.1 三个 prompt builder（src/core/prompt_builder.py，全 142 行）

- 优先级统一：`*_prompt_override` > `*_prompt_en` > 结构字段拼接。
- 首帧/尾帧末尾拼 `project.style.suffix()`（**StyleAnchor 是 legacy 管线的风格注入点**）；视频不拼（继承输入帧）。
- 角色语义只有 `_char_identity`（description_en 拼接，:25-29）。**无场景语义、无道具语义、无 look 语义**。

### 4.2 first_frame（routers/shots.py:509-577）

```
POST /shot/{id}/first-frame → JobQueue(kind=first_frame)
→ _handler_first_frame:
   refs = _collect_first_frame_refs(project, shot)      # :382-410
        # 两类：character（c.ref_url() front→side→back，无 url 则 local 读字节 base64 data URI）
        #      continuity（opening_source=reference_previous 时来源镜 video_end_frame）
        # 注释原文："未来加 environment/prop 在此追加，不改首帧生成入口"
   prompt = build_first_frame_prompt(project, shot)
   use_i2i = bool(refs)                                  # 有参考=i2i，无=纯文生图
   generate_image(prompt, image_refs=refs if use_i2i else None, size_tier, ratio)
   → 批次 + char_ref_snapshot 写回 update_shot
```

### 4.3 last_frame（:580-646）与 video（:1092-1262）

- last_frame：i2i，`image_refs = [已选首帧 url] + _shot_char_refs(...)`（:604-607；"首帧始终第一张，不让角色图取代主构图参考"）。
- video：首帧仅 local 时 imgbb rehost（:1106-1121）→ `create_video(i2v: image_url=首帧 | keyframes: image_urls=[首帧, 尾帧], num_frames/seed/negative_prompt/model...)` → 轮询下载 → VideoVersion → `_extract_video_end_frame`（ffmpeg 末帧提取 + imgbb 托管，:460-488）。

### 4.4 汇合点结论（对 S3C 接线至关重要）

autopilot（`_enqueue_autopilot_job`，src/ai/autopilot.py:722-731）、Director 工具（`shot.generate_images/videos` → `q.submit`，director_visual.py:244-253）、Producer capability（`execute_shot_production` → 同一 submit，shot_producer_capability.py:353-366）、HTTP 直发——**四条入口全部收敛到 JobQueue 的三个 handler**。未来 bound shot 的 gate + pack 消费只需改这三处 handler 内部（外加 prompt 编译入口），不可能被某条入口绕过。

另一条相关链：prompt-writing LLM（`/prompt/generate` + autopilot `generate_prompt_for_stage`，:962-1053/1801-1862）会把 LLM 生成结果写进 prompt_en——**bound shot 的 S3C 设计必须决定该端点是否感知 pack**（§27 决策 5）。

---

## 5. Current Image Binding Inventory（图片到底扮演什么角色）

全库扫描（生产代码，排除测试/脚本），按角色分类。**目标不是删图，而是回答：哪张图在哪一层说话。**

### 5.1 分类清单

| 类 | 位置 | 内容 | 裁定 |
|---|---|---|---|
| **Canonical Truth（资源身份图）** | legacy `Character.views`（models.py:392-405；写入方 bind_character_to_project / character_views_handler / migrate_v1_to_v2） | front/side/back 三视图 raw path+url，**无 checksum**；`ref_url()` 取第一可用 URL；**改名角色会物理重命名 PNG 并重写 views.local**（routers/characters.py:827-836）——路径即绑定的实证 | **Legacy Artifact + 事实上的身份图**。Resources 已用只读 adapter 桥接（`legacy_character_references`，合成 id `legacy:{name}:{view}`，checksum=None 不臆造）。迁移=用户显式建 Base Look，禁自动转换（7.md §12 铁律） |
| **Canonical Truth（产物选择）** | `Shot.first_frame/last_frame` batches + selected 指针、`VideoVersion`、`video_end_frame` | 生成分镜候选/视频本身——Shot 的**产出物**身份（candidate_id/version_id 指针，不是语义资源身份） | 合法：这是 Shot 自己的产物，不是资源真相。`video_end_frame` 是 ffmpeg 派生证据（Derived） |
| **Reference Evidence（参考证据快照）** | `ImageBatch.char_ref_snapshot` / `VideoVersion.char_ref_snapshot`（models.py:182/194；写入 routers/shots.py:560-565/618-624/1231-1242） | `{角色名: {url}}` 生成时刻的参考 URL 快照 | **后端读 0 处**（仅前端类型与审计工具读）——纯 provenance。它就是"Shot→character_image"的 legacy 形态：名字→图片 URL 直接映射。Binding 落地后它的角色=bound shot 的 evidence 快照继承者（S3C 建议 bound shot 生成时把 pack 指纹快照写进 generation_records.jsonl，见 §23） |
| **Approved Reference（批准参考，正确形态）** | Resources `ReferenceAsset`（models.py:111-142："identity = asset_id；local/url 只是数据，不靠路径推断身份" + sha256 checksum）；Look/Clothing/Prop/Composition 只存 asset_id 列表（:167-168/201-202/233-234/325-326）；`LocationView.reference_asset_id` / `SceneInstanceView.reference_asset_id`（scene_system/models.py:238/407） | 语义 ID keyed，含 checksum，approve/revoke 显式协议 | ✅ **新世界正确形态**，Binding 只引用这些 |
| **Derived Pack（派生参考包，瞬态）** | `select_character_identity_refs` / `select_clothing_refs` / `select_composition_inputs`（resources/generation.py:103-373，预算 MAX_COMPOSITION_INPUT_REFS=6 截断）；`select_reference_views`（scene, ≤2）/ `select_state_view_visual_refs`（base 强制+peer+props≤2）；`to_agnes_image_ref`（local 可读→data URI 优先，否则 url；**data URI 只在内存，绝不落盘**） | 生成前的确定性参考选择 | ✅ **ShotReferencePack 的同族先例**——pack 就是把这套模式延伸到 Shot 域 |
| **Generated Candidate（候选）** | shot 批次候选、Resources/Scene 候选（`save_candidate_files` 等） | 生成产物待审查 | 与 Binding 无关 |
| **Transient（瞬态）** | base64 data URI（shots.py:370-374/396-400/407-409、vision review）、imgbb rehost（url 回填候选）、vision 模型输入 | 内存/传输态 | 正确：绝不持久化 base64 |
| **Legacy Artifact（v1 残留）** | `migrate_v1_to_v2`（store.py:49-99：旧 image_path/reference_images 映射进 v2 形状）；parsed_script.json 保留 scene.image_path / character.reference_images（deps.py:141-156、routers/characters.py:745-802 双写）；video_composer v1 Ken Burns 读 scene["image_path"]；filename-convention writers（utils/file_saver.py） | v1 兼容层 | 记录在案，不动（7.md §14"不是为了让 raw image path 成为 creative truth"——v1 层不产生新的 truth 声明） |

### 5.2 三个裁定问题

**(a) 是否存在"图片路径 = 资源身份"？**
- Resources / Scene System 域：**不存在**（ID-bound + checksum + 显式 approve）。
- legacy Character 域：**存在且活着**——`Character.views` 的 raw path 就是角色视觉身份的载体（无 checksum、rename 重写路径、v1 reference_images 双写）。但它不属于 Shot，属于 Project 级 legacy；已有只读 adapter 与"显式建 Look 才迁移"的既定路线。**Shot Binding 不碰它、不依赖它做身份**（bound shot 的角色身份引用 = character_name + look_id）。

**(b) char_ref_snapshot 写读情况**
写 3 处（first/last/video handler），后端读 0 处。角色：**Reference Evidence（审计证据）**。已核对：无任何生成逻辑回读它。

**(c) `Character.ref_url()` / `views` 在生成路径的调用方**
1. `_shot_char_refs`（last-frame i2i 参考，shots.py:367）
2. `_collect_first_frame_refs`（first-frame 参考，:393 + local fallback :396-400）
3. 三处 char_ref_snapshot 写入（:560/:618/:1231）
4. `legacy_character_references`（Resources Look 生成身份参考，resources/store.py:243-252）
5. Director vision review 的角色参考（director_vision_review.py:167-173）
6. character_views_handler 读写（三视图再生成 canonical）
7. 其余为展示/状态布尔/删除清理/迁移，非生成参考。

**结论**：Shot 域的图片绑定 = 角色参考（经 Character.views）+ 连接力（video_end_frame）+ 产物选择指针。三者都不是"资源语义身份"。ShotReferencePack 要做的是把"名字→图片"升级为"语义 ID→approved 参考→确定性选择"，同时把 Character.views 降级为 look 未建立时的 fallback（S3C 行为，非本阶段）。

---

## 6. Legacy StyleAnchor Analysis（§11 双风格 Canonical 问题）

**现状**（两套并存，P1 报告 §22.2 已定性）：

| | StyleAnchor（legacy） | ProjectVisualProfile（P1） |
|---|---|---|
| 位置 | project.json `style`（models.py:408-420） | `output/project_profile.json`（独立 companion） |
| 内容 | art_style/palette/aspect_ratio/size_tier/era/extra | render_family 五值/art_direction/style_tags/negative_style_constraints |
| 消费方 | legacy prompt_builder 后缀（build_first/last_frame_prompt）；视频尺寸/比例参数；autopilot；Agent 路径 | Resources 五 builder / Scene 两 builder / Vision Review 风格硬维度（经 `src/visual_profile/compiler.py` 唯一编译点） |
| 指纹 | 无 | `visual_profile_fingerprint`（sha256[:8] 语义子集，OCC revision 同族） |
| 项目级必选 | 否（默认 anime） | P1 后新项目创建必选；旧项目 unspecified |

**风险**：若 bound shot 同时消费两者，项目将有两个风格真相源（7.md 明令禁止）。

**本设计裁决（S3B/S3C 执行口径）**：

1. **ProjectVisualProfile 是 bound shot 的唯一风格真相源**。binding 存 `visual_profile_fingerprint`；pack 的 STYLE 段 = `compile_project_style_instruction(profile)`（单一编译点，零复制）；review 期望段用 `compile_project_style_review_expectation`。
2. **unspecified profile → bound shot NOT READY**（`project_visual_profile_required`，与 P1 生成入口 fail-closed 同口径）。不猜 anime/realistic（P1 §7 铁律延伸）。
3. **StyleAnchor 保留原职责不动**：它继续服务 unbound shot（legacy 管线字节不变）+ 视频尺寸参数（size_tier/aspect_ratio 实际是画幅参数而非风格——它们留在 StyleAnchor 的现状 S3B 不动；是否把画幅并进 profile 属 Agent Integration 期决策，本阶段不裁）。
4. **迁移方向（不本阶段执行）**：StyleAnchor 最终降级为"legacy 视图或由 profile 编译结果填充"（P1 §22.2 原文）。触发条件=Agent Integration（Character Designer / Director prompt 消费 profile 时），因为那才需要动 Agent prompt。**本阶段不做任何 StyleAnchor 改动。**

---

## 7. Proposed ShotProductionBinding

### 7.1 存储形态裁决：独立 canonical companion

**结论：采用 `output/shot_production_bindings.json`，不改 Shot schema / project.json。** 证据：

1. **禁令直接命中**：7.md §13.10 禁改 project.json；Shot schema 在 project.json 内，任何 Shot 新增字段都必须 bump schema_version（v2 runtime 对未知字段的 known-key 白名单过滤会静默丢弃——本仓库三次 schema bump 的 bump 理由全是这条，resources v1→2 / scene v1→2→3），而旧 runtime fail-closed 会让所有未升级入口炸掉。违反 §13.15"自动修改已有 Shot 的语义"的邻接风险也由此而来。
2. **仓库先例完整**：resources.json / scene_system.json / project_profile.json 三个 companion 已证明该模式（fail-closed schema + 惰性迁移 + atomic_write_json + project_write_lock 同锁族 + 纯读零副作用）。Binding 是第四个同构 companion，工程风险最低。
3. **领域边界**：Shot 保留创作语义（7.md §三目标原话）；binding 是生产域数据，写路径（HTTP+UI）与 Agent 写路径（structure_service 整项目 save）物理隔离，不互相污染 OCC。
4. **不预设答案的义务已履行**：反方案（inline 进 Shot）的唯一优势是单文件原子性，但代价是 schema bump + 触碰冻结区 + Agent 白名单扩散——证据一边倒。

### 7.2 Schema v1

```jsonc
// output/shot_production_bindings.json  — schema_version=1
{
  "schema_version": 1,
  "bindings": {
    "s01_c02": {
      "shot_id": "s01_c02",
      "scene_id": "s01",                          // 冗余存：bind 时与 project.json 比对，read 层防漂移
      "scene_binding": {                          // optional（纯角色戏/空镜可无场景绑定）
        "scene_instance_id": "scin_ab12cd34ef56",
        "location_view_id": "view_ab12cd34ef56",  // 本镜机位方向；scene_binding 存在时必填
        "pinned_scene_instance_view_id": null      // optional；null=解析该(instance,view) 的 approved SIV
      },
      "character_bindings": [
        {
          "character_name": "Alice",               // FK → project.json Character.name（对齐 Resources name-FK 惯例）
          "look_id": "look_ab12cd34ef56",          // 必填（Case A/C：穿什么由 Look 承载）
          "composition_id": "comp_ab12cd34ef56"    // optional（Case B/D：有 approved 组合版则引用之）
        }
      ],
      "prop_bindings": [                           // 仅承载未被 composition 覆盖的道具
        {
          "prop_id": "prop_ab12cd34ef56",
          "relation": "held",                      // 词表复用 CompositionRelation：held/carried/worn/attached/near
          "anchor": "right_hand",                  // 词表复用 CompositionAnchor
          "visual_state_note": ""                  // 如 "cocked, held at chest height"（语义进 prompt，不回写资源）
        }
      ],
      "action": {                                  // optional（Case D 动作镜；见 §13）
        "kind": "pick_up",                         // none/static/pick_up/put_down/hand_off/custom
        "subject_character": "Alice",
        "prop_id": "prop_ab12cd34ef56",
        "start_state": "right hand empty",
        "end_state": "right hand holding the pistol",
        "note": ""
      },
      "production_notes": "",                      // optional 生产意图自由文本（人读）
      "dependency_fingerprints": {                 // bind/refresh 时刻快照；read 层重算对比（§19）
        "visual_profile": "a1b2c3d4",
        "characters":  { "Alice": "e5f6a7b8" },
        "looks":       { "look_ab12cd34ef56": "9aabccdd" },
        "props":       { "prop_ab12cd34ef56": "11223344" },
        "compositions":{ "comp_ab12cd34ef56": "55667788" },
        "scene_instance":       "eeff0011",
        "scene_instance_view":  "7788aabb"          // 解析到的 SIV 的 approved_basis_fingerprint
      },
      "created_at": 0.0,
      "updated_at": 0.0
    }
  }
}
```

设计要点：

- **一个 Shot 至多一个 binding 对象**（`bindings[shot_id]` keyed map，对齐三套 companion 的稳定 ID keyed map 惯例）。改绑定 = 整对象全量替换（最小 API 面，OCC 语义最简单）。
- **binding 里没有一张图片路径**。全部引用是语义 ID；图只在 pack 编译时从 Resources/Scene 解析（§14）。
- **`dependency_fingerprints` 是"bind 时刻快照"不是缓存**（对齐 `location_revision_at_bind` 的写一次原则：它是历史事实，证明"bind 时依赖长这样"；当前性由 read 层重算对比得出，绝不自动更新——更新它的唯一方式是显式 `refresh` 操作，§27 决策 8）。
- **composition 内的 prop 不重复追踪**：composition plate 是 approved 的组合真相（S2B.5 边界裁定），其内部依赖（look/prop 内容）是 Composition 自身的 provenance；binding 只追 composition_revision。这是"避免过度绑定"（7.md §10）的直接应用——prop 内容变更不 stale 一张已批准的 plate。
- **OCC**：doc revision = `sha256(canonical_json(doc))[:8]`；mutation 带 `expected_binding_revision`（per-binding 用其对象的 sha256[:8]），check-then-write 全程 `project_write_lock`；`atomic_write_json` 落盘；纯读零副作用；未知 schema fail closed（对齐 scene_system v3 契约原文）。
- **孤儿不偷删**：Shot 被删 → binding 留在盘上，read 层标注 `orphan_binding (missing_shot)`；清理走未来显式 GC（对齐 approval saga orphan 原则）。

### 7.3 三态：UNBOUND / LEGACY / BOUND（derived，不落盘第三态）

- **BOUND**：bindings.json 有该 shot 条目。
- **UNBOUND**：无条目。（全部旧 shot 天然 UNBOUND——不被静默改写、不自动猜图、不自动转换，7.md §12 逐条满足。）
- **LEGACY**：UNBOUND 且已有生成产物（batches/video_versions 非空）——纯 read 展示分类，供 UI 标"旧镜"，无行为差异。
- 不引入存储态第三字段：三态全部可从"有无条目 + 有无产物"派生，零漂移风险。

---

## 8. Proposed CharacterBinding（§四 Case A-D 映射）

结构：`{character_name, look_id, composition_id?}`。

- **Case A（Alice 穿黑色大衣）** → `character_name="Alice", look_id=<Black Coat Look>`。表达的是"Alice × 该 approved Look"，**不是** alice_black_coat.png。图片由 pack 从 `look.approved_reference_asset_ids` 解析（§14）。
- **Case B（黑大衣 + 右手木剑）** → 优先 `look_id + composition_id`（若存在匹配 approved Composition——S2B.5 已证明该链路）；无 Composition 时 `look_id + prop_bindings[{wooden_sword, held, right_hand}]`。两种形态下 **Shot 都不复制 Composition 的视觉真相**：前者引用 plate，后者引用语义关系，参考图都由系统解析。
- **Case C（换装）** → 下一镜 binding 的 `look_id` 换成 Casual Look。Character 不动、不新建、服装不写进 Character。同角色不同镜可以各自 pin 不同 look_id——**binding 按 shot 独立**，这正是"换装=Look binding changed"的表达（例 1，§24）。
- **Case D（拿枪）** → 静态终态镜：同 Case B（pistol, held, right_hand）；动作镜：见 §13。

**校验（bind 时 fail closed）**：character_name 存在于 project.json；look 存在且 `character_name` 匹配 Look.character_name；look 至少 1 个 approved 参考；composition 存在、character 匹配、composition.look_id == binding.look_id、approved。
**Ready 检查（read 时）**：`set(binding.character_bindings[].character_name) == set(shot.characters)` → 否则 `cast_mismatch`（§27 决策 2）。

---

## 9. Proposed PropBinding

结构：`{prop_id, relation, anchor, visual_state_note}`，词表直接复用 Resources `CompositionRelation` / `CompositionAnchor` enum（1C5 冻结词表，不自造第二套）。

- 职责：**shot 级**"这个镜里人物与道具的视觉关系"。与 `CompositionPropBinding` 的区别只在层级：后者是 approved plate 的内容（Resource 级 reusable），前者是某一镜对该道具的使用声明（Shot 级 temporal）。
- 与 SceneState.prop_placements 的分工：SceneState 答"木剑现在靠在墙边"（场景位置事实，跨 shot 持续）；PropBinding 答"这一镜 Alice 右手拿着它"（镜头视觉关系）。两者都只引用 prop_id，"木剑是什么"永远只有 Resources 一个真相源。
- 校验：prop 存在且 approved_reference_asset_ids 非空（bind 时）；read 时指纹对比（§19）。

---

## 10. Proposed SceneBinding

结构：`{scene_instance_id, location_view_id, pinned_scene_instance_view_id?}`。

- `scene_instance_id`：生产空间状态实例（时间/天气/灯光/损坏/道具位置）。**一致性硬校验：`scene_instance.story_scene_id == shot.scene_id`**——把 shot 绑到别的剧情段的 instance 直接拒绝（fail closed）。instance status 必须 active。
- `location_view_id`：本镜机位方向（"从西南看向东北"）。为什么必填：同一 instance 的不同 shot 可以朝不同方向看（例 5），没有方向就无法解析 SIV，也无法做镜头间连续性推理。
- `pinned_scene_instance_view_id`：显式 pin 某个 approved SIV。null 时的解析规则（确定性）：取 (instance, view) 对的 SIV 中 **approved 且非 stale** 者；恰一个 → 用之；零个 → `missing_scene_view`（NOT READY，提示去生成/批准该方向的 SIV）；多个 → 理论不可能（S2B §4 身份规则：一对至多一个实体），出现即视为数据异常 fail closed。
- **stale 视图的消费态度**（P1 报告 §22.1 建议）：`is_stale=true` → NOT READY，`stale_reasons` 逐条透传；其中 `generated_before_visual_profile` 在 UI 上呈现为"生成早于项目风格定义"的显式语义（advisory 口径），但**仍然 NOT READY**——bound shot 的生成必须建立在当前风格语义之上，否则风格门形同虚设。是否对这一理由单开"允许生成但标记"的口子 → §27 决策 7 一并裁决（建议：不开口子，重新生成 SIV 即可消除）。
- SceneBinding 不引用 LocationView 的 base 参考做生成输入（那是 SIV 生成的输入，不是 shot 的）；shot 的环境输入 = **approved SIV 一张**（含状态语义），LocationView base 图仅当 SIV 缺失时作为 S3C 的降级候选（记录在 supporting，且必须伴随"状态未表达"警告）。

---

## 11. Clothing / CharacterLook Binding（§11 of 输出要求）

- Clothing 在 binding 中**不直接出现**：换装的表达单位是 Look（Character+Clothing 的完整审核组合），binding 只说 look_id。理由：Look 是"经过审核的完整视觉形态"（1B 定义），直接绑 clothing_id 意味着 pack 要自己组装 Character+Clothing 语义并承担未审核组合的风险——那正是 Look 实体存在的目的。
- Look 的指纹：`look_revision(look)`（sha256[:8]，含 approved_reference_asset_ids——**approve/revoke 都会翻动指纹**，revoke 归因可由 approved 集合差集细化为 `approved_reference_revoked`）。
- Character 身份指纹：复用 `character_identity_fingerprint`（name + description_en，generation.py:142-148，注释明言"views 不属于创作身份"）——description_en 改了 → prompt 语义变了 → stale `character_identity_changed`（合理：bound shot 的角色文本语义进 pack）。views 图片更新**不**触发 stale（已 approved 的 Look plate/refs 不因三视图重画而失效；Look 若要吸收新三视图会走 Look 重新生成，届时 look_revision 翻动）。
- **关系图**：`Character (identity) ── look_id ──> CharacterLook ── clothing_id ──> Clothing`。binding 挂在 Character+Look 两点；Clothing 的独立变化只有通过 Look 的内容变化（若 Look 描述引用它）才影响 binding——不直接追踪 clothing_revision（避免过度绑定；Look 是对外承诺的组合单位）。

---

## 12. CharacterReferenceComposition Relationship（§12）

- **复用而非复制**：binding.composition_id 指向 Resources 的 Composition（Character + approved Look + 1~3 Props + 视觉关系）。pack 解析时取其 approved plate（cover 优先，`_pick_hero` 同规则）作为该角色的组合参考图，并取其 prop_bindings 作为"关系文本"的事实来源。
- **Shot 永不写回 Composition**：某镜"拿起/放下"不进 composition.prop_bindings（模型注释原文：CompositionPropBinding "绝不表示道具的持有时间线"）。动作态属于 binding.action（§13）。
- **同一角色多套 Composition**：binding 显式选一个（无自动猜测）。例：Alice+黑大衣+木剑 与 Alice+黑大衣+燧发枪 是两个 comp_id，两个镜各 pin 各的。
- **Composition 与 look_id 的一致性**：binding.composition 的 look_id 必须等于 binding.look_id（§8 校验）——防止"Look A 的身份参考 + Look B 的持物 plate"这种组合漂移。

---

## 13. Dynamic Action / Temporal State V1（Case D 动作镜，§四重点）

**7.md 的要求**：检查现有系统是否有承载时序信息的结构；没有就先设计最小 V1 模型，不建复杂 Timeline。

**审计回答**：现有系统有**文字雏形**没有**结构**——`Shot.opening_state`（"主体起始状态"）/ `end_state`（"主体结束状态"）/ `action_sequence`（"动作发生顺序"）/ `frame_change` 四个自由文本字段 + 视频 prompt builder 已消费它们（prompt_builder.py:87-142）。缺的是：**结构化的"谁对哪个道具做了什么、起止状态各是什么"**，以及它与语义资源的连接（现在文本里写"拿起手枪"，系统不知道"手枪"是哪个 prop_id、长什么样）。

**V1 最小模型**（binding.action，§7.2）：

```jsonc
{
  "kind": "pick_up",              // 有限词表：none/static/pick_up/put_down/hand_off/custom
  "subject_character": "Alice",
  "prop_id": "prop_...",          // FK → Resources
  "start_state": "right hand empty",
  "end_state": "right hand holding the pistol",
  "note": ""
}
```

**映射到生成行为（S3C 口径，此处只定语义）**：
- 首帧 = start_state（"右手是空的"）——i2i 输入的组合参考里**不能**包含持枪 plate 作为 primary（否则首帧就持枪了）；持枪 Composition plate 降级为 **end-state 视觉证据**（给尾帧/视频参考，或 prompt 里文字描述枪的样子 + prop hero 图做枪的身份证据）。
- 尾帧/视频 = end_state（"右手持枪"）——此时持枪 Composition plate / prop hero 是 primary。
- video prompt：`action_sequence`（Shot 创作字段）与 binding.action 融合（action 提供结构化起止 + 资源身份；action_sequence 提供 творчество节奏描述）。

**红线**：
- action.start_state/end_state 是 **Prompt Semantic Instruction**，绝不回写 Look/Prop/Composition/SceneState（7.md §五；"Alice 当前是否拿着 Pistol"是 Shot/temporal 事实）。
- "拿起"不是 Resource；"Pistol"是 Resource——kind 词表 + prop_id 把这两件事分开表达。
- **不建 timeline**：V1 只有单动作起止。多动作链/道具转移链（A 给 B）/跨镜道具状态追踪，全部推迟到未来 Continuity 阶段（SceneState.prop_placements 已是跨镜位置事实的正确落点；镜头内过程留在 shot）。

**SceneState 的边界（再次确认）**：SceneState.prop_placements 记"跨 shot 持续成立的场景位置事实"（木剑在桌上）；binding.action 记"本镜内的动作过程"（Alice 把剑从桌上拿起）。前者驱动 SIV 的场景视觉，后者驱动 shot 帧的时序视觉。同一信息绝不出现在两处。

---

## 14. ShotReferencePack（§六）

**定义**：派生 / 编译产物。**不落盘、无 ID、无修订**——每次 `compile_shot_reference_pack(project_dir, shot_id)` 现算（成本=读四个 JSON + 纯函数哈希，毫秒级；无需缓存，杜绝第二真相源）。地位对齐 Resources `compute_generation_basis` 与 Scene `compute_scene_instance_visual_basis`：**它是"要让生成/审查看懂这一镜"的事实快照**。

**结构（V1）**：

```jsonc
{
  "shot_id": "s01_c02", "scene_id": "s01",
  "compiled_at": 0.0,
  "binding_state": "bound",                    // unbound 时本函数直接返回极简结构（§21）
  "validity": { "ready": true, "checks": [ /* §18 输出 */ ] },

  "creative_intent": {                         // Shot 创作字段快照（编译时刻 project.json 现值）
    "description": "...", "shot_size": "medium shot", "camera_angle": "...",
    "composition": "...", "opening_state": "...", "important_details": "...",
    "required_elements": [], "end_state": "...", "action_sequence": "...", "motion": "..."
  },                                           // 快照仅当次消费用，绝不回写

  "style": {
    "visual_profile": { /* profile_snapshot */ },
    "visual_profile_fingerprint": "a1b2c3d4",
    "style_instruction": "Visual production style: ...",   // compiler 唯一编译点输出
    "review_expectation": "..."                            // review 口径（review-only）
  },

  "scene": {
    "scene_instance_id": "scin_...", "location": { /* Location snapshot */ },
    "location_view": { /* LocationView snapshot（camera_zone/look_direction）*/ },
    "scene_instance_view": {
      "scene_instance_view_id": "sciv_...",
      "reference_asset": { "asset_id": "ref_...", "url": "...", "local": "...", "checksum": "..." }
    },
    "view_relevant_scene_state": { /* ViewRelevantSceneState 投影——metadata only */ },
    "is_stale": false, "stale_reasons": []
  },

  "characters": [
    {
      "character_name": "Alice",
      "identity_fingerprint": "e5f6a7b8",
      "description_en": "...",
      "look": { "look_id": "look_...", "label": "Black Coat",
                 "approved_refs": [ {asset_id,url,local,checksum,view,purpose} ] },
      "composition": {                          // null 若未绑
        "composition_id": "comp_...",
        "approved_plate": { asset_id,url,local,checksum },
        "prop_bindings": [ {prop_id, relation, anchor, visual_state_note} ]   // plate 内关系（事实）
      },
      "direct_prop_bindings": [                 // 未被 composition 覆盖的道具（关系=binding 声明）
        { "prop_id": "prop_...", "name": "Wooden Sword", "relation": "held",
          "anchor": "right_hand", "prop_approved_refs": [...] }
      ]
    }
  ],

  "action": { /* binding.action 原样 + "prop" 段补道具语义快照 */ } ,   // null 若无

  "references": {                              // §15 优先级分拣结果
    "primary":    [ { "role": "scene_state_view", "entry": {...} },
                     { "role": "character_composition_plate|character_look_hero", "character": "Alice", "entry": {...} },
                     { "role": "prop_hero", "prop_id": "prop_...", "entry": {...} } ],
    "supporting": [ ...超预算/备选（look 第二角度等）... ],
    "metadata_only": [ ...state projection / anchor states / layout... ],
    "prompt_instruction_only": [ ...action 起止 / relation 文本（无 plate 时） / style instruction... ],
    "review_only":     [ ...profile review expectation / validity 归因... ]
  },
  "reference_budget": { "max": 5, "primary_used": 3 },

  "fingerprints": { /* 当前重算值（与 binding.dependency_fingerprints 对比即 staleness）*/ }
}
```

**确定性**：同输入（四 canonical 当前态）→ 同输出；排序全部按稳定 id；选择规则复用/对齐既有先例（look hero=cover 优先、prop hero 同规则、SIV 解析唯一性由 S2B 身份规则保证）。

**消费方（V1 全是 read）**：① UI production view（§16 由它渲染）；② Agent 结构化视图（§17）；③ S3C 生成 handler（refs → image_inputs，prompt 段 → 编译）；④ review（风格期望 + 逐项 evidence 检查）。

---

## 15. Primary vs Supporting References（§七）

**原则（7.md 原话的工程化）**：一张图只承担一种主语义；文字能表达的不用图；图证据不足的语义必须显式降级为 warning。

| 引用 | 层级 | 承担的语义 | 备注 |
|---|---|---|---|
| approved SceneInstanceView 图 | **Primary #1** | "这个地方长什么样、当前什么状态"（环境+状态证据） | 恒 1 张；与 base LocationView 图的关系见 §10 |
| 角色 approved Composition plate | **Primary**（该角色） | "人物与道具的视觉关系"（含穿着） | 绑了 composition 的角色，plate 取代 look hero 做 primary（关系证据优先级高于单人物证据） |
| 角色 approved Look hero | **Primary**（该角色，未绑 composition 时） | "这个人穿什么长什么样" | cover 优先（`_pick_hero` 同规则）；第二角度进 supporting |
| 直接绑定 Prop hero | **Primary**（每 prop 1 张） | "这个物品长什么样" | 仅未被 composition 覆盖的 prop |
| Look 第二角度 / 其它 approved refs | Supporting | 角度补充 | 预算允许才进 primary |
| 同 instance 其它方向 approved SIV | Supporting | 连续性辅助（S2B peer 语义的 shot 级复用） | V1 默认不送生成，仅供人/Agent 参考 |
| Look 其它 approved / LocationView base 图 | Supporting / 降级证据 | — | base 图只作 SIV 缺失时的显式降级（附状态未表达警告） |
| state projection / anchor_states / prop_placements / layout | **Metadata** | 空间与状态事实 | 只进 prompt 语义段与 UI，不产生图输入 |
| binding.action（起止态）、relation/anchor 文本（无 plate 时） | **Prompt Semantic Instruction** | "这一镜发生什么、关系是什么" | 有 plate 时 relation 文本降级为"match the plate"防双重表达 |
| style_instruction | Prompt instruction（生成）+ review expectation（审查） | 项目介质/艺术方向 | 单一编译点 |
| validity / 指纹归因 | **Review/诊断 only** | 是否可生成、为什么不可 | 绝不进生成 prompt |

**预算（self-imposed；Agnes image_refs 无硬上限，对齐既有 self-imposed 惯例）**：V1 建议 `max = 5` = scene 1 + 每角色 hero 1（plate 或 look）+ 直接 prop ≤2 + 预留 1（第二角度/第二角色）。超预算按优先级截断进 supporting（截断规则确定性：先 prop_extra、再 look 第二角度——对齐 `select_composition_inputs` 的截断顺序风格）。**数值待用户确认（§27 决策 4）**。

---

## 16. Human-readable Production View（§八）

`GET /api/project/{p}/shots/{shot_id}/production-view` 返回结构化 JSON（§17 同源）+ `human_text` 渲染块（UI 直接展示 `<pre>` 或逐节卡片）。示例（bound shot）：

```text
═══ SHOT PRODUCTION VIEW — s01_c02 ═══════════════════════════
SCENE      圆形控制室 (scin_ab12…) · view: South-west → North-east
           Time: night   Weather: rainstorm   Lighting: emergency red
           Anchor states: W02 BROKEN；Prop placements: 木剑 near door
CHARACTERS
  Alice    Look: Black Coat (look_ab12…, approved)
           Composition: "Alice · Black Coat · Wooden Sword" (comp_ab12…, approved)
           Relations (from plate): wooden_sword = held @ right_hand
PROPS      （全部由 Composition 覆盖）
ACTION     —
STYLE      ProjectVisualProfile: 2d_animation
           tags: cinematic japanese anime, cel shading
REFERENCES  Primary (3): scene_state_view(sciv_…) · Alice·plate(comp_ab12…) · —
           Supporting (1): look second angle
VALIDITY   ✅ READY
           deps: visual_profile ok · look ok · composition ok ·
                 scene_instance ok · scene_instance_view ok (not stale)
```

NOT READY 示例（例 8）：

```text
VALIDITY   ❌ NOT READY
           ✗ look look_ab12… REVOKED (approved_reference_revoked: 2 → 0)
           ✗ scene_instance_view stale: base_reference_changed
           → 修复：重新生成/批准 Look 参考；刷新 SIV 后 refresh binding
```

要求逐条对齐 7.md §八：一眼看到 SCENE/CHARACTERS/PROPS/ACTION/STYLE/REFERENCES/VALIDITY 七节；人看的是渲染块，**Agent 消费的是同端点的 JSON（§17），绝不解析 UI 文本**。

---

## 17. Agent-readable Structured View（§八后半）

- 同一 read 模型的机器形态 = pack JSON 本身（§14）+ validity.checks[]（机器可判定的逐依赖结论：`{dependency, status, reason, expected, current}`）。
- **Agent Integration 期**的接入点（本阶段不改 Agent，仅预留设计）：Director `shot.read` / `shot.list` 增加 `production_binding` 摘要（bound/unbound + ready 布尔 + 不 ready 的 reason 列表）——工具 schema 属 Agent 层，S3B 不动。UI 文本永不作为 Agent 输入。
- 为什么现在就定义：pack 的 JSON 形状是 S3C handler 接线与未来 Agent 消费的共同契约，先定形状后接消费者，避免两次返工。

---

## 18. Binding Validity Gate（§九）

**纯 read 模型**（绝不自动 mutation——对齐 SceneInstanceView staleness 与 P1 §9 的既定原则）。输出：

```jsonc
{ "state": "bound",                      // unbound / legacy / bound
  "ready": false,
  "checks": [
    { "dependency": "character.Alice.look", "status": "ok",          // ok | missing | revoked | stale | mismatch
      "reason": null, "detail": {...} },
    { "dependency": "scene.instance_view", "status": "stale",
      "reason": "base_reference_changed", "detail": {...} }
  ] }
```

**检查序列（确定性顺序，逐项归因后不短路——一次看全所有问题）**：

1. binding 存在？无 → `unbound/legacy`（**ready 字段= not_applicable，不阻断 legacy 生成**，§21）。
2. project.json 侧：shot 存在？scene_id 一致？（否则 `orphan_binding` / `scene_mismatch`）
3. cast 一致性：binding 角色集合 == shot.characters（否则 `cast_mismatch`，§27 决策 2）。
4. 逐角色：look 存在（missing）→ 有 approved refs（revoked_or_unapproved）→ look_revision 对比（stale: `look_changed`）→ identity 指纹对比（stale: `character_identity_changed`）→ composition（若绑）：missing / 非 approved（revoked）/ revision（stale: `composition_changed`）/ look_id 与 binding 一致（mismatch）。
5. 逐直接 prop：存在（missing）→ approved refs（revoked_or_unapproved）→ revision（stale: `prop_changed`）。
6. scene：instance 存在且 active（missing）→ story_scene_id == shot.scene_id（mismatch）→ revision 对比（stale: `scene_state_changed`，逐字段归因 time_of_day/weather/lighting/…复用 ViewRelevantSceneState 投影对比）→ SIV 解析（missing_scene_view）→ `is_stale` 透传其细粒度 reasons。
7. profile：specified？（missing: `project_visual_profile_required`）→ 指纹对比（stale: `project_visual_profile_changed`）。

**无关变更零影响（架构性保证，7.md §九核心）**：指纹只在 binding 实际引用的实体上计算——项目里任何其它 Look/Prop/View/Character 的变化根本不进入本 shot 的检查集。测试必证（§25"unrelated resource change does not stale"）。

**Gate 的生效点（S3C，此处只定契约）**：三个 handler 的 precheck 段（对齐 Director `_precheck_targets` / autopilot `dependency_ready` / `_video_preflight` 的既有分层）：bound + ready=false → 拒绝提交，错误体携带 checks[]；unbound → 行为不变。

---

## 19. Dependency Fingerprint（§十）

**原则**：只覆盖 actual dependency；指纹函数全部复用既有实现（零新算法）；快照存 bind 时刻，当前性由 read 重算。

| 依赖 | 指纹 | 复用实现 | 变化语义 |
|---|---|---|---|
| ProjectVisualProfile | `visual_profile_fingerprint` | visual_profile/models.py:91（语义子集，不含时间戳） | 风格定义变化 |
| Character（身份语义） | `character_identity_fingerprint`（name+description_en） | resources/generation.py:142 | 角色文本设定变化；**三视图图片变化不算**（views 非创作身份，该函数注释原文） |
| CharacterLook | `look_revision` | resources/models.py:425（含 approved refs 集合） | Look 内容/批准集变化；差集归因 revoke |
| Prop | `prop_revision` | resources/models.py:433 | 道具内容/批准集变化 |
| Composition | `composition_revision` | resources/models.py:437 | 组合内容变化（内部 look/prop 不重复追踪，§7.2） |
| SceneInstance（含 SceneState） | `scene_instance_revision` | scene_system/models.py:525 | 剧情状态变化（投影对比逐字段归因） |
| SceneInstanceView | `approved_basis_fingerprint`（SIV 自带）+ read 层 `is_stale` | instance_view_basis.py:391 | 空间/状态/风格任一失配（细粒度 reasons 已有，直接透传） |
| （不做）Clothing 独立指纹 | — | — | Look 是组合承诺单位（§11） |
| （不做）Location/Layout/Anchor 独立指纹 | — | — | 已被 SIV 的 stale 检测覆盖（location_identity_changed / base_location_view_changed 等归因链），shot 层重复追踪=过度绑定 |

**避免过度绑定的两个显式裁决**（7.md §十例子逐字对齐）：Alice 的另一个无关 Look 更新 → 本 shot 指纹集根本不含那个 look_id → 不 stale（测试证）；未被本 shot 使用的 LocationView 改变 → 只可能通过 SIV stale 间接影响（且仅当该 SIV 是本 shot 依赖）→ 否则不 stale。

---

## 20. Stale / Revoked / Missing behavior（§二十 of 输出要求）

**归因词表（read 模型输出，机器可判定；命名对齐 S2B.1/P1 已有归因族）**：

| status | reasons |
|---|---|
| missing | `orphan_binding` / `shot_missing` / `scene_mismatch` / `character_missing` / `look_missing` / `prop_missing` / `composition_missing` / `scene_instance_missing` / `scene_instance_not_active` / `missing_scene_view` / `project_visual_profile_required` |
| revoked | `look_not_approved` / `approved_reference_revoked` / `composition_not_approved` / `prop_not_approved` / `instance_view_revoked` |
| stale | `look_changed` / `character_identity_changed` / `composition_changed` / `prop_changed` / `scene_state_changed`（+逐字段）/ `instance_view_stale`（透传 SIV reasons：location_identity_changed / base_location_view_changed / base_reference_changed / scene_state_field_changed:* / project_visual_profile_changed / generated_before_visual_profile）/ `project_visual_profile_changed` |
| mismatch | `cast_mismatch` / `composition_look_mismatch` / `scene_mismatch` |

**行为矩阵**：

| 情形 | binding 数据 | 生成行为（S3C 契约） |
|---|---|---|
| ok（全部 ok） | 不动 | 允许；pack 即生成 basis 的语义来源 |
| stale | **绝不自动改**（不重算快照、不重绑、不重生成任何依赖） | 拒绝；UI 给"refresh binding fingerprints"显式操作 + 修复引导 |
| revoked | 不动 | 拒绝；引导去 Resources 恢复 approve 或改绑 |
| missing | 不动 | 拒绝；引导补齐依赖 |
| unbound/legacy | — | **完全不变**（legacy 路径照旧） |

**"refresh"是唯一让快照重新变 current 的 mutation**（显式、OCC 保护、UI 确认后调用；语义="我确认当前依赖状态就是要绑定的状态"）。这保证 stale 永远是真实的事件（依赖真的变过）而不是缓存失效噪音。

---

## 21. Legacy Shot Compatibility（§十二）

- **旧 shot 无 binding = UNBOUND**，不静默改写：不自动猜"这张图像哪个 Look"、不自动把 image path 转 Resource truth、不自动生成 binding（7.md §十二三连禁令逐条落在这）。LEGACY 分类仅作 UI 展示（§7.3）。
- **unbound 生成路径零改动**：prompt_builder / `_collect_first_frame_refs` / 三个 handler 在 S3B 完全不碰；S3C 接线的唯一行为差异 = "有 binding 的 shot 才走 gate+pack"。
- **混合项目**：同一项目里部分 shot bound、部分 unbound —— 天然支持（binding 是 per-shot 的）；autopilot 批量跑混合场景时 bound 镜走 gate、unbound 镜走 legacy（S3C 细节）。
- **旧项目的双风格概念**：旧项目 profile unspecified → 本来就不可能有 READY 的 bound shot（§6 裁决 2），legacy 管线继续用 StyleAnchor——没有"半个迁移"状态。
- **project.json / resources.json / scene_system.json 字节级不变**是 S3B 的验收项（§25 末三条测试）。

---

## 22. ProjectVisualProfile Integration（§二十二）

- binding 依赖之一（指纹快照）；pack STYLE 段唯一来源；READY 的必要条件（specified + 指纹 current）。
- 与 P1 语义的衔接：profile 变更 → Resources/Scene 的 approved 资产不自动失效（P1 §9），但 SceneInstanceView 的 approved_basis_fingerprint 会让 SIV `is_stale`（P1 §13）→ 本 binding 通过两条路径同时感知：自身指纹对比 + SIV 透传——双重归因里 `project_visual_profile_changed` 会同时出现在两处，**去重规则**：SIV 透传 reasons 原样展示（它是"view 为什么 stale"），binding 顶层 reason 独立展示（它是"binding 为什么 stale"），UI 分层呈现不合并。
- `generated_before_visual_profile`（legacy SIV basis 在已设 profile 项目）：透传展示 + NOT READY（§10 已裁，§27 决策 7 复核）。
- 视频尺寸/画幅（size_tier/aspect_ratio）继续来自 StyleAnchor 现状（S3B 不动，§6 裁决 4）。

---

## 23. Data flow（§二十三）

```
                       ┌── 创作层（project.json，frozen）──────────────────────┐
                       │  Scene(创作字段) ── Shot(创作字段+提示词+产物+接力)      │
                       │  Character(name, description_en, legacy views)        │
                       └──────────────┬───────────────────────────────────────┘
                                      │ name FK（只读）
┌─ Resources（frozen）─────────┐      │        ┌─ Scene System（frozen）─────────────┐
│ Look ── Clothing             │      │        │ Location ── LocationView            │
│ Prop                         │      │        │ SceneInstance(story_scene_id FK)    │
│ Composition(look+props)      │      │        │   └ SceneState                      │
│ ReferenceAsset(approved)     │      │        │ SceneInstanceView(approved+fingerprint)│
└──────────────┬───────────────┘      │        └──────────────┬──────────────────────┘
               │ asset_id 解析（只读）  │                       │ sciv 解析（只读）
               └──────────────┬───────┴───────────────────────┘
                              ▼
        shot_production_bindings.json（S3B 新 canonical companion）
              Bind：语义 ID + 依赖指纹快照（OCC + project_write_lock + atomic write）
                              │ compile（纯函数，不落盘）
                              ▼
                     ShotReferencePack（derived）
              references / scene / characters / action / style / validity
                     │                        │
        ┌────────────┘                        └────────────┐
        ▼ (S3C)                                              ▼
 3×JobQueue handler（唯一汇合点：autopilot/Director/Producer/HTTP 都到这里）      Production View API
   bound: gate → pack.refs + pack.prompt 段 → Agnes                     （人：human_text）
   unbound: 现行为字节不变                                               （Agent：pack JSON）
        │
        └─ 生成证据：bound 生成时把 pack 指纹快照写 generation_records.jsonl
           （新 operation，如 shot.first_frame.generate；不动 project.json 的 batch 字段）
```

---

## 24. State transition examples（§二十四，八例齐全）

> 统一图例：【Canonical】= 真相所在；【Binding】= bindings.json 内容变化；【Pack】= 编译产物要点；【Validity】= gate 结论；【Generation】= 预期生成行为（S3C 契约）。

### Example 1 — Alice 换装（Casual → Black Coat）
- 【Canonical】Character Alice 不动；Look(Casual)、Look(Black Coat) 均为 approved 资源；**没有任何实体被修改**。
- 【Binding】镜 11 的 binding.look_id: look_casual → look_blackcoat（显式改绑，OCC）。镜 10 binding 不动。
- 【Pack】镜 11 的 characters[0].look 解析为 Black Coat approved refs；镜 10 仍解析 Casual。
- 【Validity】两镜各自 READY（指纹按镜独立）。
- 【Generation】镜 11 重生成 → i2i refs = Black Coat hero；镜 10 重生成仍用 Casual。换装完全由 binding 承载，Character/Look 零写回。✔ 7.md Case C 全部要求。

### Example 2 — Alice 黑大衣 + 右手木剑（静态）
- 【Canonical】Look(Black Coat) approved；Prop(Wooden Sword) approved；Composition"Alice·BlackCoat·WoodenSword" approved（relation=held@right_hand）。
- 【Binding】`{character_name: Alice, look_id: look_blackcoat, composition_id: comp_sword}`；prop_bindings 留空（已被 composition 覆盖）。
- 【Pack】Alice 的 composition.plate = approved plate（primary）；plate 内关系进 prompt_instruction（"match the plate"口径）。
- 【Validity】READY（指纹：look+composition）。
- 【Generation】refs = scene SIV + composition plate；prop 单独图不再送（plate 已含关系）——预算省 1 张。
- 若项目**没有**该 Composition：binding 退化为 `{look_id} + prop_bindings[{sword, held, right_hand}]`，pack 送 look hero + prop hero，relation 文本进 prompt。两种形态并存，Shot 都不复制视觉真相。

### Example 3 — Alice 静态持枪（终态画面）
- 同 Example 2 的枪版：`composition_id: comp_pistol`（S2B.5 真机已验证的实体形态：Ethan·Flintlock plate）。首帧=已持枪；无 action。
- 【Generation】首帧 refs 含持枪 plate（primary）——"已经拿着"就是这张 plate 的语义。

### Example 4 — Alice 动作镜头中拿起枪（视频镜）
- 【Canonical】Prop(Pistol) approved；可选 Composition"Alice·BlackCoat·Pistol" approved（持枪照）。
- 【Binding】character 同上；`action: {kind: pick_up, subject: Alice, prop_id: pistol, start_state: "right hand empty", end_state: "right hand holding the pistol"}`。
- 【Pack】references 优先级翻转：**首帧编译**时持枪 plate 不得为 primary（会污染起始态），prop hero（枪的身份证）+ look hero 为 primary，plate 进 supporting 作为 end-state 证据；**尾帧/视频编译**时 plate 回归 primary。action 起止文本进各自 prompt 段。
- 【Validity】READY（无新依赖类型）。
- 【Generation】首帧=空手（start_state）；视频 prompt = action_sequence（创作）+ pick_up 起止（结构化）；尾帧（若 keyframes）=持枪。**没有 timeline 结构被创建**——起止两态 + 词表动作就够 V1。"Alice 当前是否持枪"是本镜的 temporal 事实，不存在于任何 Resource。✔ 7.md Case D 全部要求。

### Example 5 — Alice 从 View A 移动到 View B（同场景两镜）
- 【Canonical】同一 SceneInstance（同 state）；该 Location 已有两个 LocationView（S2A 能力）→ 两个 approved SIV。
- 【Binding】镜 12 scene_binding.view=view_A；镜 13 view=view_B；pinned 均空 → 各自解析到唯一 approved SIV。
- 【Pack】两镜的 scene.reference_asset 各自解析；state projection 相同（同 instance）。
- 【Validity】两镜 READY。
- 【Generation】环境证据各用各的方向图；角色 refs 相同（同 look）→ 人物一致 + 空间换向。UI 可见"同 instance 相邻镜方向变化"的连续性 advisory（derived，不做自动处理）。

### Example 6 — 项目别处一个无关 CharacterLook 更新
- 【Canonical】用户给 Bob 的新 Look 加了 approved 参考。Alice 的 Look/Prop/Composition/SIV/profile 全部未动。
- 【Binding】零变化（Alice 镜的指纹集不含 Bob 的 look_id——**结构上不可能含**）。
- 【Pack】不变。
- 【Validity】READY 不变（测试必证项）。
- 【Generation】无影响。✔ 7.md §九/§十反例的正面证明。

### Example 7 — 项目 ProjectVisualProfile 变更（2d_animation → photorealistic）
- 【Canonical】profile 更新（OCC 翻新指纹）；Resources/Scene 已批资产**不自动失效**（P1 §9），但已批 SIV 的 approved_basis_fingerprint ≠ 新 profile → SIV is_stale=true（reason 含 project_visual_profile_changed）。
- 【Binding】数据不动；快照指纹 ≠ 当前 → `stale: project_visual_profile_changed`；SIV 透传同因。
- 【Pack】style_instruction 立即编译为新介质口径（pack 是现算的——它永远反映"如果现在生成会是什么风格"）；validity 仍 NOT READY。
- 【Validity】NOT READY。
- 【Generation】拒绝。修复路径=人重新走 SIV/Look 的重生成与批准（该链路 P1 已建成），然后 refresh binding fingerprints。✔ 与 P1 "不自动 mutation" 全链一致。

### Example 8 — 已绑 CharacterLook 被 revoke
- 【Canonical】`revoke_look_approval(look_blackcoat, asset)`（write_service.py:786）→ approved 集合变化 → look_revision 翻动。Look 实体与其余 approved ref 仍在。
- 【Binding】不动。
- 【Pack】仍能解析实体，但 validity 先行否决。
- 【Validity】NOT READY：`revoked: approved_reference_revoked`（approved 集合差集归因；全清空则 `look_not_approved`）。
- 【Generation】拒绝。若该 look 还被 Composition 引用：composition 本身不自动失效（plate 是已批图），但 binding 若绑了 composition 则只报 composition 侧 ok——**是否要求"composition 引用的 look 被 revoke 时 composition 连带降级"属 Resources 域政策，本阶段不扩 Resources（7.md 禁令 8），如实记录为已知边界**。

---

## 25. 测试设计（S3B 实现用；本阶段不执行）

**S3B.1 canonical/store 组**：
1. binding CRUD round-trip（to_dict/from_dict、unknown-key 过滤前向兼容）
2. schema fail closed：未知 schema_version → 明确异常不静默重置
3. OCC：expected_revision stale → 拒绝；写后 revision 变化
4. 并发写：project_write_lock 临界区（双写者测试）
5. 原子写 + 纯读零副作用（load 不创建文件——companion 契约）
6. FK 校验：bind 时 look character 不匹配 / composition look 不一致 / instance story_scene_id 不符 → fail closed
7. orphan：删 shot 后 read 标注 orphan_binding，文件不偷删

**S3B.2 validity 组**：
8. valid binding → ready（全 ok）
9. missing：各 FK 缺失逐一触发对应 reason
10. revoked：look/prop/composition revoke → revoked + 差集归因
11. stale：look_revision / identity / composition / prop / scene_instance / profile 各自翻动 → 对应 stale reason
12. **unrelated change 不 stale**：Bob 的 look / 未引用的 LocationView / 未引用的 Prop 变更 → ready 不变（核心反例组）
13. scene state 字段级归因（time_of_day 变 → scene_state_field_changed:time_of_day）
14. profile change → stale + SIV 双路径归因不合并
15. SIV is_stale 透传（含 generated_before_visual_profile 的 advisory 语义标注）
16. cast_mismatch（shot.characters 改后 binding 未跟）
17. scene_view 解析：无 approved SIV → missing_scene_view；多 SIV 异常态 fail closed
18. unbound/legacy 派生分类正确

**S3B.3 pack 组**：
19. 确定性：同输入两次编译逐字节同输出
20. Character+Look：refs 解析 hero 规则（cover 优先）
21. Character+Look+Prop（无 composition）：prop hero + relation 进 prompt_instruction
22. Composition 路径：plate primary、prop 不重复送图、relation 降级为 match-plate 口径
23. action 起止：首帧/尾帧参考优先级翻转（Example 4 契约）
24. reference priority + 预算截断确定性（超预算截断顺序固定，截断项进 supporting）
25. style instruction = compiler 输出（零复制的单一编译点验证）
26. prop relation 词表 = CompositionRelation/CompositionAnchor 枚举域
27. unbound shot pack 极简结构

**S3B.4 API/UI 组**：
28. HTTP bind/unbind/refresh/list/production-view 契约与错误码
29. human_text 渲染七节齐备（含 NOT READY 修复引导）
30. Agent 结构化视图 = pack JSON + checks（无 UI 文本依赖）

**冻结区回归组（每步必跑）**：
31. project.json 字节级不变（既有项目跑全套 binding 操作后 hash 对比）
32. resources.json / scene_system.json 字节级不变
33. 全量 pytest 与 P1 基线（34 failed / 1467 passed）同构零新增
34. legacy 生成路径行为不变（unbound shot 首帧/尾帧/视频 handler 的既有测试全绿）

---

## 26. S3B 建议（§二十一十问 + 实施顺序）

**十问直答**：

1. **S3B 实现哪些**：canonical（bindings.json schema v1 + store + OCC）→ validity read model（指纹重算+归因）→ pack compiler → HTTP API + 最小 UI（production view 面板 + 绑定表单 + VALIDITY 徽标）。全部是 read/写 binding 自己的域。
2. **推迟什么**：生成 handler 接线（gate+pack 消费）→ S3C；Agent 工具扩 binding（Director/Producer）→ Agent Integration；StyleAnchor 迁移、legacy Character.views 迁移、Timeline/多动作链、SIV 缺失自动补 view、binding GC daemon、composition 被 revoke 的连带政策。
3. **是否改 Shot schema**：**不改**（§7.1 证据）。
4. **是否采用 shot_production_bindings.json**：**是**（第四个同构 companion）。
5. **ShotReferencePack 放哪里**：`src/shot_binding/pack_compiler.py`（新包 `src/shot_binding/`：models/store/service/pack_compiler 四文件，镜像 resources/scene_system 结构）。不落盘；S3C 时 handler 直接调用。
6. **新增服务**：`ShotBindingService`（CRUD+OCC+lock）+ read 模型函数（validity/pack）。无 daemon。
7. **新增 API**：`GET/PUT/DELETE /project/{p}/shots/{shot_id}/production-binding`（list 用 `?all=true`）、`POST .../refresh`、`GET .../production-view`。全部零副作用读或 binding 域写。
8. **UI 第一版显示什么**：分镜工作台 shot 详情新增"Production Binding"面板：七节 production view（只读渲染）+ 绑定表单（scene instance/view 下拉、角色 look/composition 下拉、prop 关系表、action 表单）+ VALIDITY 徽标与 checks 列表 + refresh/unbind 按钮。UNBOUND/LEGACY 徽标给旧镜。
9. **Agent Integration 为什么继续延期**：①7.md §13 明令；②bound shot 的生成接线（S3C）未落地前，Agent 消费 binding 无行为可触发——先有可用生产链，再教 Agent 用它；③Agent prompt/工具 schema 属冻结 Agent 系统，动它需要独立回归周期（V1 冻结纪律）；④pack JSON 契约（§17）先行定义后，Agent 接入只是新增工具映射，无返工风险。
10. **实现顺序**：见下。

**S3B 最小可靠实施顺序（可靠性 > 速度）**：

| 阶段 | 内容 | 验收 |
|---|---|---|
| **S3B.1** | `src/shot_binding/{models,store,service}.py` + bindings.json schema v1 + CRUD/OCC/project_write_lock/原子写 + FK fail-closed 校验 + 孤儿标注 | 组 1-7 测试；冻结区 diff 空 |
| **S3B.2** | validity read model（指纹重算+逐依赖归因）+ production view read（human_text + checks） | 组 8-18；无关变更反例组必须全绿 |
| **S3B.3** | `pack_compiler.py`（references 优先级/预算/确定性 + style compiler 接入） | 组 19-27（确定性逐字节验证） |
| **S3B.4** | HTTP API（routers/shot_binding.py）+ 前端面板 | 组 28-30；vite build ✓ |
| **S3B.5（收尾）** | 真机 E2E：示例 1/2/3 全链（bind→pack→view→refresh→revoke 翻转）+ 组 31-34 冻结验证 | 真机 PASS + 全量回归同构零新增 |
| **S3C（独立阶段）** | 生成接线：3 handler gate + bound prompt 编译 + generation_records 证据 + prompt-writing 端点感知 pack | 另立执行文档 |

**三不破坏原则的落实**：不破坏领域边界（binding 只引用三套 canonical，零 schema 侵入）；不复制信息进 Shot（project.json 零改动）；不造第二套真相（pack 不落盘、指纹快照是历史事实非缓存、风格单编译点）。

---

## 27. 需要用户确认的架构决策

| # | 决策 | 建议 | 备选 |
|---|---|---|---|
| 1 | 存储形态 | ✅ 独立 `shot_production_bindings.json`（§7.1 四条证据） | inline 进 Shot（需 schema bump，与禁令冲突） |
| 2 | cast 全覆盖强制 | ✅ binding 角色集合 ≠ shot.characters → NOT READY（`cast_mismatch`） | 部分覆盖可用（遗漏角色走 legacy 参考）——语义含糊，不推荐 |
| 3 | profile 未指定的项目 | ✅ bound shot 永远 NOT READY（`project_visual_profile_required`，P1 fail-closed 同口径） | 允许绑定但生成退化到 StyleAnchor（双风格真相，禁止） |
| 4 | primary 参考预算 | ✅ max=5（scene1+角色 hero1+prop≤2+余1） | 更紧（4）/更松（6，对齐 MAX_COMPOSITION_INPUT_REFS） |
| 5 | bound shot 的 prompt 编译策略（S3C 预裁） | ✅ 新确定性 builder（`build_shot_bound_first_frame_prompt(pack, shot)`），`first_frame_prompt_override` 仍最高优先级；`prompt_en`/LLM 生成提示词端点对 bound shot 停用 | 复用旧 builder 追加 pack 段（override 语义纠缠，不推荐） |
| 6 | scene_binding 可选性 | ✅ optional（纯角色戏/空镜可只有角色绑定） | 必填（强制全项目先建 Scene System，过重） |
| 7 | `generated_before_visual_profile` 的 SIV | ✅ 透传展示 + 仍 NOT READY（重生成 SIV 消除） | 放行生成但 UI 标记（风格门开口子，不推荐） |
| 8 | 指纹快照更新方式 | ✅ 仅显式 `refresh`（OCC），绝不自动 | read 时静默续期（stale 语义失真，禁止） |

---

## 附：本阶段遵守声明

- 零代码改动（工作区 diff 仅本文档）；未修改 7.md §十三列出的 15 项禁止物中的任何一项。
- 未删除/改写任何 legacy 图片绑定行为；未把任何图片变成 Canonical Truth；未把动作状态写入任何 Resource。
- 所有"将来行为"（生成接线、Agent 消费）均标注 S3C/Agent Integration，未混入 S3B。
- 审计中发现的两个既有事实如实记录、本阶段不处理：Director 整项目 save 旁路（§2.2，OCC+重读兜底的既定设计）；Composition 对已 revoke look 的连带政策缺口（Example 8 末注）。
