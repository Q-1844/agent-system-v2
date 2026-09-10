# Production Resources V1 — Phase 1A 审计与架构冻结报告

> 阶段：**Phase 1A — Current State Audit + Architecture Freeze（只调查、只设计、只写报告，零生产代码改动）**
> 基线分支：`feat/producer-v1-bridge`（Agent System V1 已冻结；Producer V1 三阶段冻结；Visual Review Tool V1 已报告 PASS WITH LIMITATIONS）
> 审计原则：所有结论从当前真实仓库代码出发，附 `file:line` 引用。历史信息一律经代码二次验证。
> 审计范围：Character / 角色图片 / 全局角色库 / 图片生成 / 资源存储 / 前端 UI / Visual Review / revision·OCC / receipt。

---

## 1. Executive Summary

本系统是一个以 **Project Canonical（`project.json`）** 为单一生产事实源、以 **Agent tool-loop + receipt 真相闸门** 为执行路径的 AI 动画制作系统。当前"资源"在代码中并未作为一个统一的域存在，而是以三种碎片形态分散存在：

1. **项目级 Character**（`Project.characters`，`src/core/models.py:393`）——身份= `name`，三视图= `views: Dict[str, Asset]`（front/side/back）。
2. **全局角色库 saved_characters/**（`routers/characters.py:464`）——独立目录、独立 CRUD、扁平 schema `{id,name,description,created_at,updated_at}`，**无 lineage / origin / version / approval**，是跨项目复用的唯一现成机制，且前后端均 LIVE。
3. **Shot 级生成资产**（`ImageAsset`/`Candidate`/`ImageBatch`/`VideoVersion`，`models.py:64-206`）——已有多候选 + 选择指针 + 批次历史，但只绑定在 Shot 上，不在 Character/Clothing 上。

**关键发现**：
- **没有** `Resource` / `ReferenceAsset` / `CharacterLook` / `Clothing` / `Prop` 类或文件——新命名空间干净，零冲突（`grep class (Resource|ReferenceAsset|CharacterLook|Clothing|Prop|Look)` 返回空）。
- **没有** 独立可复用的"视觉审查 service"——`visual.review_image`（`src/agents/director_vision_review.py`）是 Director 独占的只读工具，底层 `agnes-2.5-flash` Vision + data URI 路径可安全抽离成薄 service，但当前只挂在 Director tool-loop 内。
- **receipt 机制完全可复用**（`_base_receipt`，`src/agents/script_writer.py:767`），已跨 Specialist 共享。
- **revision 层 `creative_source_signature` 明确排除 `Character.views`**（`src/revision/models.py:427`）——即三视图是"生产写回落生态"，不属于角色创作身份。**这决定性地支持 CharacterLook 作为生产资产层叠加，而非改写 Character 身份模型。**
- **现存两处 CONFLICT**（见 §16）：三视图两套落盘命名（handler 子目录+sig 版本化 vs router 平铺无版本）；`views_meta.json` 与 `Character.views` 双真相源。

**推荐架构**：在 **不迁移、不破坏 Character canonical** 的前提下，叠加一层 **typed Production Resources**：`Character`（保持原样）→ 新增 `CharacterLook`（character_name FK + clothing_ref + appearance delta + ReferenceAsset bank）；新增 `Clothing` / `Prop` 独立 typed resource；统一 `ReferenceAsset` 作为图片基础设施；`saved_characters` 升级为 Shared Production Library 的雏形（additive schema + copy-on-import + lineage）。

**Phase 1B 最小代码切片**（见 §14/§17）：新增 `src/resources/` 子包（纯 additive，`ReferenceAsset` + `Resource`/`CharacterLook`/`Clothing`/`Prop` dataclass + store + 轻量 list/read API），双读适配现有 `Character.views`，不动任何 Agent / Director / Producer。

---

## 2. Current Character Architecture

### 2.1 Storage（存储位置）
- **项目级 Character 存在 `project.json`（canonical）的 `characters` 数组**，由 `ProjectStore` 读写。
  - `ProjectStore.path = project_dir / "output" / "project.json"`（`src/core/store.py:105`）。
  - legacy `parsed_script.json`（v1）在 load 时自动迁移为 v2 并备份（`store.py:112-118`，`migrate_v1_to_v2` @ `store.py:49`）。
  - `schema_version=2`（`models.py:433`）。
  - 原子写：`src/core/atomic.py:atomic_write_json`（`store.py:10` import）。
  - **ProjectManager** 在 `src/utils/project_manager.py`（`base_dir=./projects`，`get_project_dir(name)=base_dir/name`，`:17-71`）。项目创建 `create_project(name)` 建标准目录结构（含 `output/characters/`）+ 初始化空 `Project(name=name)`（characters=[]）（`:21-49`）。**无 `delete_project`/`copy_project` 方法**，`routers/projects.py` 只有 `list_projects`+`create_project` 两个端点——项目删除/复制功能当前不存在于代码。
- **三视图图片落盘**：`project_dir/output/characters/{name}_{view}.png` + `{name}_{view}_seg.png`（router 路径，`routers/characters.py:775-787`）。
  - **CONFLICT**：Agent handler 用另一套路径 `{project_dir}/output/characters/{safe_filename(name)}/{view}_{sig8}_seg.png`（`src/agents/character_views_handler.py:52-65`）。两套不可互换引用。详见 §16 C1。
- **Character 稳定标识 = `name`（字符串），不是 id**。`Project.get_character(name)` 按 name 查找（`models.py:443-444`）。saved_characters 用 `{name}_{uuid8}` 作 id，但项目内只用 name。

### 2.2 Schema（数据模型）
```python
# src/core/models.py:393-405
@dataclass
class Character:
    name: str
    description_en: str = ""
    views: Dict[str, Asset] = field(default_factory=dict)  # {"front": Asset, "side": Asset, "back": Asset}

    def ref_url(self) -> Optional[str]:  # 取一张能送进 API 的公网 URL，优先 front
        for v in ("front", "side", "back"):
            a = self.views.get(v)
            if a and a.url: return a.url
        return None
```
- `views` 的 key 固定 `front`/`side`/`back`，value 是 `Asset`（见 §4.6）。
- **无 character_id 字段、无 lineage、无 origin、无 approval、无 revision**。
- 序列化：`Project.to_dict()` 把每个 Character 序列化为 `{name, description_en, views: {k: asdict(v)}}`（`models.py:455-459`）。

### 2.3 IDs
- 项目内：`name` 即身份（name-based FK）。
- 角色库：`id = f"{name}_{uuid.uuid4().hex[:8]}"`（`routers/characters.py:526, 552`）。
- Scene/Shot 引用角色：`Scene.characters: List[str]`、`Shot.characters: List[str]`——**按 name 引用**（`models.py:378, 262`）。

### 2.4 References（角色被引用处）
- `Scene.characters`（name 列表，`models.py:378`）
- `Shot.characters`（name 列表，`models.py:262`）——送进生成 prompt 的角色参考
- Shot 生成时 `char_ref_snapshot: Dict[str, Any]` = `{角色名: {view, url}}`（`models.py:182`）——快照存角色名+视图+url
- `ImageBatch.char_ref_snapshot` / `VideoVersion.char_ref_snapshot`（`models.py:182, 194`）
- 角色参考图送进视频/图片 API 用 data URI（`routers/shots.py` 既有做法；Visual Review 也用 data URI，§5）

### 2.5 Rename 传播
- `routers/characters.py:805-860 update_project_character`：改名时
  1. 重命名 `{name}_{view}.png` / `{name}_{view}_seg.png` 文件（`:827-836`）
  2. 更新 `c.views[view].local` 路径（`:832`）
  3. `c.name = new_name`（`:837`）
  4. **遍历所有 scene.shots，把 `sh.characters` 列表里的旧名替换成新名**（`:839-841`）
  5. 调 `routers/character_design.migrate_design_key` 迁移 AI 设计草稿 key（`:850-851`）
- **rename 不影响 saved_characters 角色库**（库与项目解耦）。
- **无级联到 Look / Clothing 的需求**（当前不存在这些实体）。
- **★ BUG（CONFLICT C3，见 §16）**：rename 只级联 `Shot.characters`（`:839-841`），**遗漏 `Scene.characters`**（`models.py:378` 独立字段）。rename 后 Scene 层角色名引用 stale。
- **★ 矛盾（CONFLICT C4）**：REST API 支持改名（`new_name`），但 Agent 协议层**明确禁止 rename**——`src/agents/change_protocol/character_proposal.py:8` 注释"name 即身份 → 改名不支持"，`CHARACTER_ACTIONS=("update_character","insert_character","delete_character")`（`:27`）无 rename，rename 请求→`user_decisions_needed`。Agent 侧的禁止恰好规避了经 Agent 路径触发上述 Scene.characters bug。

### 2.6 三视图生成
- 两种 UI 路径（前端 3 个 tab）：
  - `single`（TabSingle，单视角文生图）→ `/api/character/generate-view`
  - `chain`（TabChain，链式：front 文生图 → side 图生图参考 front → back 图生图参考 front）→ `/api/character/generate-chain`
  - `sheet`（TabSheet，设定图一张出三视角裁剪）→ `/api/character/generate-sheet`
- Agent 路径：`character.generate_views` 工具 → `handler_character_views`（`src/agents/character_views_handler.py:93`）——链式生成，写回 `Character.views` + `views_meta.json`。
- 每步生成后 rembg（`isnet-anime`）抠图得透明底 PNG（`routers/characters.py:31-37`）。
- 角色三视图用竖图 `ratio="2:3"`（非视频画幅 16:9，`character_views_handler.py:116`）。
- `appearance_sig = sha256(description_en + art_style)` 做三视图语义版本签名（`character_views_handler.py:34-41`），用于判断是否需要重生成。

### 2.7 UI
- `src/views/characters/index.vue`：左侧角色列表（文字优先无头像）+ "新建角色" + "✨ AI 角色设计"(→CharacterDesignStudio) + 底部"角色库"入口(→openLibrary)。
- 右侧选中角色工作区："保存到角色库" + "从角色库导入" + 三视图生成 3 tab。
- 角色库面板：列出 `store.savedCharacters`，点击 `loadFromLibrary(id)` 导入项目。
- Pinia store：`src/store/index.ts`，`characters` ref + `savedCharacters` ref。
- 卡片**当前是文字列表项**，非方形视觉卡片（文档期望方形卡片——需 UI 升级，§11）。

### 2.8 Agent 依赖
- **Character Designer**（`src/agents/character_designer.py`，1630 行）：7 工具
  - `character.list` / `character.read`（只读，轻量地图 + 精确读）
  - `character.create` / `character.update` / `character.delete`（写，串行）
  - `character.generate_views`（生成三视图，1~3 个角色并发生成）
  - `web.search`
  - 写操作集 `CHARACTER_WRITE_OPERATIONS = {create, update, delete, generate_views}`（`:82`）
  - 写工具要求 `expected_revision`（8-hex 指纹，OCC，见 §6.2）
  - 回执走 `_base_receipt`（`:872-880`）
- Character Designer **只操作项目内 Character**，不直接读写 saved_characters 库。
- **Director**：`visual.review_image` 只读审查（§5）；不写 Character。
- **Script Writer**：定义 `_base_receipt`（`script_writer.py:767`），被 Character Designer 复用。
- **Producer**：无直接角色工具（领域 ownership：Producer→director.delegate→Director）。

---

## 3. Current Shared / Global Character Library

### 3.1 真实路径
- `saved_characters/`（submodule 根目录，`routers/characters.py:466 SAVED_CHARACTERS_DIR = BASE_DIR / "saved_characters"`）。
- 每个角色一个子目录：`saved_characters/{id}/`，含 `meta.json` + `{view}_raw.png` + `{view}_seg.png`（view ∈ front/side/back）。
- 真实样本：`saved_characters/火焰机甲_e1cabbc8/`（front/side/back raw+seg + meta.json）。

### 3.2 Schema
```json
// saved_characters/火焰机甲_e1cabbc8/meta.json
{
  "id": "火焰机甲_e1cabbc8",
  "name": "火焰机甲",
  "description": "炫酷机甲带着火焰",
  "created_at": "2026-08-01T19:34:56.436616",
  "updated_at": "2026-08-01T19:34:56.436628"
}
```
- **极简**：仅 id/name/description/created_at/updated_at。
- **无 lineage / origin / version / approval / revision / scope**。
- 三视图以独立 PNG 文件存在（不在 meta.json 内联）。

### 3.3 APIs（后端，全 LIVE）
`routers/characters.py` §"角色保存系统"（`:464-725`）：
| 方法 | 端点 | 作用 |
|---|---|---|
| POST | `/api/character/save` | 保存角色到库（生成 `{name}_{uuid8}` id，写 meta+三视图 PNG） |
| GET | `/api/character/list` | 列出库角色（附 `has_views` 布尔） |
| GET | `/api/character/load/{id}` | 加载库角色（含三视图 base64） |
| PUT | `/api/character/{id}` | 更新 name/description |
| DELETE | `/api/character/{id}` | 删除（`shutil.rmtree`） |
| PUT | `/api/character/{id}/views` | 更新三视图 |
- `_init_default_characters()`（`:504`）：api_server startup 时若库空则预置"小星""暗影"——**只 seed 库，不 seed 项目**。

### 3.4 UI（前端，LIVE）
- `src/views/characters/index.vue`：底部"角色库"按钮 → `openLibrary()` 弹库面板（`:43-48, 123-140`）。
- "保存到角色库" `saveCharacter`（`:68`）/ "从角色库导入" `loadFromLibrary`（`:94, 131`）。
- Pinia `savedCharacters` ref（`src/store/index.ts:69-80`），`loadSavedCharacters/saveCurrentCharacter/loadSavedCharacter/deleteSavedCharacter/updateCharacterInfo`。
- 前端 API：`src/api/index.ts:1138-1220`（save/list/load/delete/update/views 全套）。

### 3.5 当前用法
- **复制语义**：保存到库 = 把项目角色复制成库角色（新 id）；从库导入 = 把库角色复制进项目（按 name 追加/更新，`routers/characters.py:745-802 bind_character_to_project`）。
- 项目删除角色"不影响角色库"（前端文案 `:154`，后端 `delete_project_character` 只删项目，`:894-918`）。
- **库与项目完全解耦、无双向同步、无 lineage**。

### 3.6 Legacy / Dead / Live 判定
- **LIVE**：前后端全链路可用，有真实数据样本，startup 主动 seed。
- **但设计上是"扁平角色库"，不是"版本化共享生产库"**：无 version、无 origin、无 update_available、无 publish/import 显式语义。
- 升级路径：additive schema（加 origin/lineage/version/approval 字段，旧 meta.json 缺字段时 lazy default），它自然成为 Shared Production Library 的雏形（§8）。

---

## 4. Current Image / Asset / Review Infrastructure

### 4.1 图片生成 client
- `src/api/agnes_client.py:465 AgnesClient.generate_image`：
```python
async def generate_image(self, prompt: str, *,
    image_refs: Optional[List[str]] = None,   # 公网 URL 或 data:image/png;base64,...
    size_tier: str = "1K", ratio: str = "16:9",
    seed: Optional[int] = None, want_b64: bool = False) -> dict:
    # 返回 {"url": str|None, "b64": str|None}
```
- 端点 `POST /v1/images/generations`（`:503`），同步返回（无 task_id 轮询，区别于视频）。
- `image_refs` 支持图生图（i2i），双位置 fallback：`extra_body.image` → 顶层 `image`（`:480-498`）。
- 进程级单例：`deps.get_agnes_client()`。
- client 另提供：`chat_completion` / `chat_completion_stream`（文本）、`chat_completion_vision`（视觉）、`create_video` / `poll_video` / `download`。

### 4.2 Asset 落盘机制
- 每个 Asset 同时存 `local`（本地路径）+ `url`（公网 URL，Agnes GCS `platform-outputs.agnes-ai.space`，公开读、不过期、下载不带 Authorization——CLAUDE.md 确认）。
- 本地图转公网 URL：`src/core/image_host.py` `ImageHost` 抽象（`upload`/`ensure_url`），实现：`ImgbbHost`（默认，sha1 去重缓存）/ `R2Host` / `TunnelHost` / `AgnesRehostHost` / `AgnesPassthroughHost`。
- 写回 Character：`character_views_handler.py:162-173`——`project_write_lock` 短临界区 → `store.load()` → 改 `c.views` → `store.save(p)` + 写 `views_meta.json`。
- 图片下载：handler 自带 `_download`（httpx 60s，`:44-49`），非 `AgnesClient.download`。

### 4.3 三视图生成机制（详见 §2.6）
- 链式：front(t2i) → side(i2i, ref=front) → back(i2i, ref=front)，每步 rembg 抠图。
- prompt builder：`routers/characters.py:82-134`（`_build_view_prompt` / `_build_img2img_prompt` / `_build_sheet_prompt`）。

### 4.4 generation receipt
- `_base_receipt(operation, *, ok, target, before, after, revision, verified, error, **extra)` → dict（`src/agents/script_writer.py:767-777`）。
- 固定字段：`ok` / `operation` / `target` / `before` / `after` / `revision`(8-hex 指纹) / `verified`(bool) / `error`。
- `verified` 语义 = 写后重读 canonical（磁盘）确认实际落盘（`character_designer.py:593-597` CRUD；`:709-725 _verify_generated_views` 三视图重读 views + 文件存在）。
- 真相闸门链：`session_governance.count_structural_writes` 只计 `ok+verified+非unchanged`（`session_governance.py:183-191`）；`_honest_fallback`/`_append_fact_summary` 只信同口径（`character_designer.py:1003,1027`）。
- **完全可复用于资源生成**：`_base_receipt` 已跨 Specialist 共享，新资源工具执行→重读验证→返回 receipt 即可。需把新写操作加入 `STRUCTURAL_WRITE_OPS`（`session_governance.py:39`）。

### 4.5 Visual Review 底层
- 工具：`visual.review_image(shot_id, focus?, frame?)`（`src/agents/director_vision_review.py`）。
- 底层：`agnes-2.5-flash` Vision（`AgnesClient.chat_completion_vision`），`POST /v1/chat/completions`，`messages[].content` 数组（text + image_url）。
- **图片输入用 data URI（`data:image/png;base64,…`）**——零公网托管依赖；公网 Agnes URL 也可；**imgbb URL 恒失败**（vision 后端下载不了 i.ibb.co，`VISUAL_REVIEW_TOOL_V1_REPORT.md §1`）。
- 结果 schema：`verdict(pass/needs_fix/uncertain)` / `severity(none/minor/major/critical)` / `summary` / `issues[{category,severity,observation,expected,suggested_fix}]`。
- truth gate：Director 无直接看图能力，下判断唯一正路=调本工具；失败路径诚实（no_image/provider_error/review_parse_failed/not_configured，无伪造结论）。
- 只读：审查回执**不进写域 `receipts` 账本**，经 `trace` 记录（与 `shot.read` 同语义）；`project.json` 逐字节不变（T9 证明）；只追加 `output/agents/director/visual_reviews.jsonl`（append-only 观测工件）。
- **当前只绑定在 Director tool-loop 内**——没有独立 vision service 层。可安全抽离边界见 §10/§L。

### 4.6 现有 Asset 数据模型
```python
# src/core/models.py:35-61
@dataclass
class Asset:
    local: Optional[str] = None
    url: Optional[str] = None
    status: str = ShotStatus.PENDING.value   # pending/queued/generating/done/failed/skipped
    error: Optional[str] = None
    updated_at: float = 0.0
    # 方法: mark_done / mark_failed / ready / url_ready
```
- 子类层级：
  | 类 | 文件:行 | 扩展字段 | 用途 |
  |---|---|---|---|
  | `Asset` | `models.py:36` | local/url/status/error/updated_at | 基类，角色三视图、video_end_frame |
  | `ImageAsset` | `models.py:64` | candidates:List[Asset], selected_index | 首帧/尾帧多候选 |
  | `AudioAsset(Asset)` | `models.py:139` | duration_ms,words,voice,rate | 配音 |
  | `VideoAsset(Asset)` | `models.py:148` | video_id,task_id,num_frames,model,attempts | 视频 |
  | `Candidate` | `models.py:166` | candidate_id,local,url,status | 批次内候选 |
  | `ImageBatch` | `models.py:176` | batch_id,kind,prompt_snapshot,char_ref_snapshot,candidates | 首帧/尾帧批次 |
  | `VideoVersion` | `models.py:189` | version_id,video_id,first_frame_candidate_id,model | 视频版本 |
- **角色三视图 = `Dict[str, Asset]`（3 个普通 Asset），不是独立 Asset 子类**。
- **无 `ReferenceAsset` / `MediaAsset`**——`grep class.*Asset` 仅命中上表 7 类。

### 4.7 approval / candidate / generated asset 概念
- **candidate + 选择**：`ImageAsset.candidates` + `selected_index`（多候选，用户点选，`models.py:64-90`）；`ImageBatch.candidates` + `selected_first_frame`(candidate_id 指针)。
- **批次历史**：`ImageBatch` / `VideoVersion`，prompt_snapshot + char_ref_snapshot 快照，旧字段→legacy 批次迁移（`models.py:222-245`）。
- **状态机**：`ShotStatus`（pending/queued/generating/done/failed/skipped）——但这是**生成产物状态**，不是 **approval 状态机**。
- **没有** Character/Look 级别的 approval/candidate 机制。文档期望的 `draft→generating→candidate→reviewed→approved/rejected` 生命周期目前**只部分存在于 Shot 级**（candidate 选择近似，但无 reviewed/approved/rejected 正式状态）。
- 复用方向：Look 生成可复用 `ImageAsset` candidates+selected 机制 + 新增 approval_status 枚举。

---

## 5. Compatibility Map（哪些代码依赖现有 Character 结构）

| 依赖点 | 文件:行 | 依赖内容 | 改 Character 的风险 |
|---|---|---|---|
| Scene/Shot 引用 | `models.py:378,262` | `characters: List[str]`（name） | 改 name→id 会破坏全部分镜引用 |
| rename 传播 | `routers/characters.py:839-841` | 遍历 shots 替换 name | name-based 假设 |
| Shot 生成 char_ref_snapshot | `models.py:182,194` | `{角色名:{view,url}}` | 依赖 name+views 结构 |
| Character Designer 工具 | `character_designer.py:390-894` | 7 工具直接读写 `Character{name,description_en,views}` | schema 改动破坏 Agent（冻结区） |
| expected_revision OCC | `character_designer.py` | `_characters_token`(8-hex 指纹) | 依赖 Character 序列化形态 |
| creative_source_signature | `revision/models.py:427-456` | name+description_en 进签名，views 明确剔除 | Character 身份=name+desc_en |
| 前端 store/api | `src/store/index.ts`, `src/api/index.ts:1138-1260` | Character{name,description_en,views} 形状 | schema 改动破坏前端 |
| bind/update 项目角色 | `routers/characters.py:745-918` | name+views+三视图文件路径 | 路径模板依赖 |
| 视频生成 char ref | `routers/shots.py` | data URI 从 views.local 读 | views 结构依赖 |
| Visual Review character_consistency | `director_vision_review.py` | name+description_en+参考图 | 依赖 Character 身份字段 |
| migrate_v1_to_v2 | `store.py:49-99` | characters:[{name,description_en,views}] | v2 schema 锁定 |

**结论**：Character `{name, description_en, views}` 三字段结构被全链路硬依赖，**Phase 1 绝不能改 Character schema**。所有新能力必须 additive 叠加（新字段有默认值、新实体并行存在、双读兼容）。

---

## 6. Revision / OCC / Concurrency 机制

### 6.1 三层并发控制
系统存在**三层**并发/版本控制，Phase 1 资源设计需明确资源对象走哪层：

1. **Agent 工具级 OCC（`expected_revision` 8-hex 指纹）**
   - Character Designer 写工具要求传 `expected_revision`（`character_designer.py` schema），执行前比对当前 canonical 的 `_characters_token`/`_scenes_content_token`/`_scene_token`，不一致则拒绝（STALE）。
   - 这是"轻量 OCC"——无持久化 version 字段，靠内容指纹。
   - 精确 token 函数（sha256[:8]）：`_scenes_token`/`_scene_token`/`_scenes_content_token`（`src/agents/script_writer.py:195-235`）、`_shots_token`/`_shot_token`（`src/agents/director.py:147-192`）、`_characters_token`（Character Designer）。Shot.update 模式：`before_token=_shot_token(shot)`；`if expected_rev != before_token` → `stale_revision`，不写，回传当前指纹+视图让 Agent 重取（`director.py:981-992`）。
2. **revision workspace 层（revision counter + signatures）**
   - `ProjectWorkspace.revision: int`（`revision/models.py:234`），`base_project_signature`/`working_project_signature`（sha256）。
   - `WorkspaceChangeSet` 生命周期：draft→previewed→ready_to_apply→applied|rejected|conflict（`:24-31`），Apply 校验 `base_workspace_revision` + `before_signature`（`:347,354,357`）。
   - `project_signature`（完整 sha256）+ `creative_source_signature`（只创作结构，**排除 Character.views 生成资产**，`:420-456`）。
3. **change_protocol plan 执行层（plan_version）**
   - `(plan_id, plan_version)` 唯一标识批准（`execution_service.py:43-46`），`PLAN_VERSION_MISMATCH` 守卫（`:118-146`）。

### 6.2 ProjectStore 自身无 OCC
- `ProjectStore.save(project)` 直接原子写，无 version/etag 字段（`store.py:134-136`）。
- 并发写保护靠 `project_write_lock` 短临界区（`character_views_handler.py:162`）。
- **资源对象若存入 project.json，继承同样的"内容指纹 OCC + write_lock"模式最自然**，不新造持久化 version。

### 6.3 资源对象应走哪层？
- **项目内 Resource CRUD**（Character Look/Clothing/Prop 的 create/update/delete）：复用 Agent 工具级 `expected_revision` 模式——新增 `_resources_token`/`_looks_token` 8-hex 指纹，写前比对。
- **资源生成**（generate candidates）：复用 receipt `verified` 重读验证 + `project_write_lock`。
- **共享库 import/publish**：用 lineage + shared_version（持久化在 origin 字段），非 OCC。
- **不要**把资源塞进 revision workspace ChangeSet 的 OpType（虽然 `INSERT_CHARACTER`/`UPDATE_CHARACTER`/`DELETE_CHARACTER` 已存在为 OpType，`revision/models.py:52-54`）——资源是生产资产，不是创作语义变更，走 receipts 不走 ChangeSet（与 views 同口径）。

---

## 7. Proposed Production Resources V1 Ontology（推荐数据关系）

### 7.1 关系图
```
Shared Production Library (scope=shared, 版本化母版)
        │
        │ explicit import (copy-on-import + lineage)
        ▼
Project Production Resources (scope=project, 项目生产事实，存 project.json additive)
        │
        ├── Character (保持原 canonical，不动)
        │      │  identity: name + description_en (进 creative_source_signature)
        │      │  views: Dict[str, Asset] (保留=Base Look identity refs，双读)
        │      │
        │      └── CharacterLook (新，project-scoped)
        │              ├── look_id (稳定)
        │              ├── character_name (FK → Character.name，name-based 一致)
        │              ├── clothing_resource_id? (optional FK → Clothing)
        │              ├── appearance_description / appearance_delta
        │              ├── reference_bank: List[ReferenceAsset] (approved refs)
        │              ├── cover_asset_id?
        │              ├── status: draft|generating|candidate|reviewed|approved|rejected
        │              ├── origin/lineage (若来自 shared)
        │              └── generation_receipt (复用 _base_receipt)
        │
        ├── Clothing (新，独立 typed resource)
        │      ├── clothing_id, name, description, design_traits
        │      ├── material, colors, tags
        │      ├── reference_assets: List[ReferenceAsset]
        │      ├── cover_asset_id, revision, origin/lineage, approval_state
        │
        └── Other
               ├── Prop (新，独立 typed resource，Phase 1 最小)
               │      ├── prop_id, name, description
               │      ├── reference_assets, cover_asset_id, tags, origin, approval_state
               │      └── (预留 state/holder/damage/variants/continuity，不实现)
               ├── ReferenceAsset (共同图片基础设施，见 §E)
               └── (Location 接口预留，不实现为 misc asset，见 §7.4)
```

### 7.2 核心不变量
- **Shared Library ≠ Project Canonical 的共同可变对象**。Shared = 复用来源（版本化母版）；Project Resource = 当前项目生产事实。
- **Character 保持原 canonical**，name-based FK 不变。
- **CharacterLook 是生产资产**（同 views 口径），不进 `creative_source_signature`，不影响角色身份签名。
- **scope 是 resource 字段，不是 resource type**——未来 Shared Library 可含 Characters/Clothing/Other 全类型，不被锁死成"只有 character 能共享"。

### 7.3 typed domain model（不做成"万能 misc JSON"）
- UI 三分类（Characters/Clothing/Other）≠ 数据库三种万能对象。后台每个类型有独立 typed dataclass + type discriminator。
- `Other` 下有 Prop / ReferenceAsset / (Location 接口)，不创建无类型约束的 misc JSON。

### 7.4 Location 边界
- **Location 不塞进 Other Resources**。Location 后续与 Scene Instance / Scene State / Spatial Anchor 形成独立 Scene/Location Domain。
- Phase 1 只在 `Resource` 基类留 `resource_type` 枚举含 `location` 占位 + 一个 `location_id` 可选 FK 字段接口，**不实现 Location 对象**。

---

## 8. Shared Library Architecture

### 8.1 现状升级路径
- `saved_characters/` 升级为 Shared Production Library 雏形：
  - additive schema：meta.json 加 `scope`(default=shared) / `origin` / `shared_version`(default=1) / `lineage` / `approval_state` 字段，旧 meta.json 缺字段时 lazy default（双读兼容）。
  - 目录结构调整：`shared_library/{type}/{id}/`（type=character/clothing/prop/other），向后兼容旧 `saved_characters/{id}/`（load 时若在旧路径则按 character 类型读）。
  - 保留三视图 PNG 文件结构。

### 8.2 方案比较与选择（文档 §九要求研究）

| 方案 | 描述 | 优点 | 缺点 |
|---|---|---|---|
| A. Copy-on-import | 导入=复制 media+meta 到项目 | 强隔离、简单、符合现有"复制"语义 | 磁盘占用 |
| B. immutable version reference | 项目只存 shared_id+version 引用，不复制 media | 省磁盘 | 共享 media 误删/失效风险；不符合"可靠性>省磁盘" |
| C. content-addressed media + local metadata snapshot | media 按 checksum 共享存储，项目存本地 meta 快照 + 指针 | 省磁盘+强隔离 | 实现复杂 |
| **推荐 A+C 混合** | copy-on-import 语义 + content-addressed media 去重 | 强隔离 + 磁盘可控 | 中等复杂度 |

**推荐 A+C 混合**（文档倾向"版本化母版 + 项目 snapshot + lineage + 项目内修改不自动写回"）：
- Shared = 版本化母版（`shared_version` 递增，immutable per version）。
- Import = 项目本地 snapshot：metadata 复制到项目 resources；media 按 `checksum` 存入 content-addressed store（`shared_media/{sha256}.{ext}`），相同内容只存一份（省磁盘但不牺牲隔离）。
- 记录 `origin = {source:"shared_library", shared_resource_id, shared_version, imported_at, checksum}`。
- 项目内修改 = 改项目本地 snapshot，**不写回 Shared**（隔离保证）。
- Publish = 显式"Save to Shared Library" → 在 Shared 创建**新 version**（不覆盖旧 version）。
- Update = 显式"Update from Shared" → 拉取新版到项目 snapshot，bump lineage，**不自动**。

### 8.3 Project A 修改不污染 Project B（§I 回答）
- **由构造保证**：Project A 只持有本地 snapshot + content-addressed media 指针；Shared 母版 immutable per version；项目写只改本地 snapshot。
- Project B 持有的是它自己 import 时的 snapshot（可能不同 version），与 A 完全独立。
- Shared Alice 发布 v4 后，A/B 不会自动变 v4——只显示 `update_available=true`（比对 `origin.shared_version` 与 Shared 当前 latest version）。

---

## 9. Import / Publish / Update / Version Semantics

| 操作 | 触发方 | 语义 | 数据变化 |
|---|---|---|---|
| **Import** | 用户/Agent 显式 | Shared→Project snapshot | 复制 meta + content-addressed media 指针；写 `origin`；不改 Shared |
| **Publish** | 用户显式 | Project→Shared 新 version | Shared 创建新 `shared_version`（递增），旧 version 保留；Project `origin.shared_version` 指向新版本 |
| **Update from Shared** | 用户显式 | Shared 新版→Project snapshot | 拉取 latest version 到项目；bump lineage；保留项目本地未提交改动？→ 保守：提示 conflict 让用户决定 |
| **Project 内编辑** | Agent/User | 改本地 snapshot | 只改项目 resources；Shared 不变；`origin` 保留；`update_available` 不变 |

- **无任何隐式双向同步**。所有跨 scope 流转都是显式动作。
- Version 是 Shared 母版属性（`shared_version: int`），项目 snapshot 只记录"import 自哪个 version"。

---

## 10. CharacterLook Generation + Review Lifecycle

### 10.1 生命周期
```
draft → generating → candidate → reviewed → approved | rejected
```
- `draft`：Look 对象已建（character_name + clothing_ref + appearance_delta），无图。
- `generating`：调 `generate_image` 产出候选 ReferenceAsset。
- `candidate`：候选图就绪（复用 `ImageAsset.candidates` 多候选机制），待审查/点选。
- `reviewed`：经 Visual Review（§10.3）产出 verdict。
- `approved`：选定 approved reference(s)，可被 Shot 复用。
- `rejected`：拒绝，可重生成。

### 10.2 生成流程（复用现有图片生成能力）
- 入口：`look.generate_candidates(character_name, clothing_id?, appearance_delta, view_focus)`（新 service，**不挂 Agent 工具，Phase 1 只 service+API**）。
- 复用 `AgnesClient.generate_image(prompt, image_refs=[char_ref_url, clothing_ref_url], size_tier, ratio)`（同 chain-gen 机制）。
- 复用 `character_views_handler` 的链式 + rembg 抠图 + `appearance_sig` 版本签名模式。
- 写回：`project_write_lock` → `store.load()` → 写入 `looks` 列表 → `store.save()` + receipt（`_base_receipt(ok, verified, ...)`）。
- 落盘路径统一：`output/looks/{look_id}/{view}_{sig8}_seg.png`（解决 §16 C1 命名 CONFLICT，新实体用新统一模板）。

### 10.3 Review 接入（不破坏 Director，§L 回答）
- **抽离薄 service** `src/resources/vision_review_service.py`（纯函数）：
  ```python
  async def review_reference_image(image_bytes_or_data_uri, checks: list[str]) -> ReviewResult:
      # 调 AgnesClient.chat_completion_vision（agnes-2.5-flash）
      # data URI 路径（零托管依赖，与 Director 同源）
      # 返回 verdict/severity/summary/issues（同 visual.review_image 结构）
  ```
- **Director `visual.review_image` 改为委托该 service**（行为不变，只是把 vision 调用从 tool 内抽到 service）——tool-loop/OCC/权限/账本逐字节不动。
- **新 Resource Review 路径**直接调 `vision_review_service`，不经过 Director tool-loop、不携带 Director 历史/系统提示/context（单次请求=需求文本+图片，与 VRT 报告 §13 一致）。
- **安全复用边界明确**：service 是无状态纯函数（image + checks → verdict），无 Director state 耦合。
- 若无抽离余量，**Phase 1B 可先不抽 service**，Resource Review 暂走"调用同一 `chat_completion_vision` + 自有 prompt"的并行路径（VRT 报告 §1 已证实契约稳定），Phase 1C 再收敛成共享 service。**不强耦合**。

### 10.4 Approval
- approved reference(s) = Look 的 `reference_bank` 中 `approval_status=approved` 的 ReferenceAsset 子集。
- Shot 后续引用 Look 时优先取 approved references（Phase 1 不改 Shot 引用，仅留 look_id 接口）。

---

## 11. UI Information Architecture（§M 回答）

### 11.1 渐进升级（不重写 Agent Center）
- **复用 `src/views/characters/index.vue` 为 Resources 页种子**：
  - 顶部加 scope 切换 `[Project] [Shared Library]` + type 过滤 `[Characters] [Clothing] [Other]`。
  - 左侧列表 → 统一方形视觉卡片 Grid（cover_asset 缩图 + name + type + status + revision）。
  - Character 卡片保持现入口；新增 Clothing/Other 卡片。
- **不一次重写整个 Agent Center 或无关 UI**。最小侵入。

### 11.2 人物详情页（文档 §十二）
```
Alice
Identity: [基础身份说明]
Identity References: [front][side][back]   ← 现有 views（Base Look identity refs）
Looks:
  [ Base ]  [ School Uniform ]  [ Black Coat ]  [ Battle Suit ]   ← CharacterLook 卡片
点击 Alice · Black Coat → Look 详情：
  Character: Alice | Clothing: Black Coat | Appearance: ...
  Reference Bank: [hero][full body front][3/4][side]...
  Generation: model/prompt/source refs/receipt
  Review: identity consistency / costume consistency / visual defects / status
  Approval: approved reference(s)
```

### 11.3 服装详情页（文档 §十三）
```
Black Coat
Design: ... | References: [...]
Used By Looks: Alice·Black Coat, Bob·Black Coat
[Create Character Look] → 选 Character → 建 Look → 生成候选 → Review → Approve
```

### 11.4 项目资源页（文档 §十四）
- scope toggle + type filter + 方形卡片 Grid，如上。
- 现有"角色库"面板升级为"Shared Library"scope 视图（同组件复用）。

---

## 12. Agent-facing Read Model（§N 回答）

### 12.1 轻量 list + 精确 read（从第一天具备）
```python
# service 层设计（Phase 1 只 service+API，不挂 Agent 工具）
resource.list(scope=project, type=None) -> [
    {resource_id, type, name, scope, status, cover_asset_id, revision, relationship_summary}
]   # 不返回图片大 metadata

resource.read(resource_id) -> {
    references, lineage, generation_receipts, review_result, look_relationship, ...
}

character.read(name) -> ...      # 现有工具已实现，保持
look.list(character_name?) -> [...]
look.read(look_id) -> {...}
clothing.list/read, prop.list/read 同构
```

### 12.2 为什么 Phase 1 暂不改 Agent（§N）
- **Agent 全部冻结**（Agent System V1 已冻结 2026-09-03；Producer V1 三阶段冻结；Character Designer tool-loop 冻结）。文档明令"Phase 1 禁止修改现有 Agent tool set"。
- 接线工具到 Agent 会触碰 Specialist ownership、OCC、receipt 账本、session_governance——风险高、收益在 Phase 1 不显著（先证明数据模型）。
- 正确顺序：Phase 1 建 service+API+数据模型 → Phase 2+ 再 additive 挂 `resource.list/read` 工具到 Producer/Director（通过 additive ownership，不重写现有工具）。

---

## 13. Migration / Compatibility Strategy（§十五）

### 13.1 硬要求清单（全部满足）
- 现有项目能继续打开 ✅（project.json additive 新字段有默认值）
- 现有 Character 不丢失 ✅（不动 Character dataclass）
- 现有三视图不丢失 ✅（views 保留为 Base Look identity refs）
- Shot/Scene 对 Character 引用不失效 ✅（name-based FK 不变）
- Character Designer 不失效 ✅（工具 schema 不动）
- Director 不失效 ✅（visual.review_image 不动，或仅委托 service）
- 全局角色库内容可继续使用 ✅（saved_characters additive schema + 双读）
- 无不可逆迁移 ✅（全部 lazy/双读，旧文件不删）

### 13.2 迁移策略选择
| 策略 | 用在哪 |
|---|---|
| **adapter** | 现有 `Character.views` → 视为 Base Look 的 identity references（读时适配，不写回） |
| **lazy migration** | saved_characters meta.json load 时补默认字段；project.json load 时若 `looks`/`clothings`/`props` 缺则 default [] |
| **dual-read** | ReferenceAsset bank 与 `Character.views` 双读：Look 生成前若项目无 Look，synthesize Base Look from views |
| **versioned schema** | project.json `schema_version` 保持 2（不 bump，因 Look/Clothing/Prop 是 additive，不破坏 v2 反序列化——`from_dict` 用 known-field 过滤，`models.py:367-368`） |
| **explicit migration** | saved_characters→shared_library 目录迁移只在用户显式"Publish/Import"时发生 |

- **绝不** destructive rewrite、不一次重写盘、不自动迁移全部旧资源。

---

## 14. Exact File-level Implementation Plan（Phase 1B，下一阶段，本轮不实施）

### 14.1 新增文件（纯 additive 子包）
| 文件 | 作用 |
|---|---|
| `src/resources/__init__.py` | 新子包入口 |
| `src/resources/models.py` | `ReferenceAsset` / `Resource`(基) / `CharacterLook` / `Clothing` / `Prop` dataclass + `ApprovalState`/`LookStatus` 枚举 + `to_dict/from_dict`（风格对齐 `src/core/models.py`） |
| `src/resources/store.py` | `ResourceStore`：读写 project.json 的 `looks`/`clothings`/`props`/`reference_assets` additive 段；双读 `Character.views` 适配 Base Look；`project_write_lock` 复用 |
| `src/resources/service.py` | `list_resources`/`read_resource`/`list_looks`/`read_look` 轻量+精确读模型；`generate_look_candidates`（复用 AgnesClient+rembg+receipt） |
| `src/resources/vision_review_service.py` | 薄 vision service（§10.3），Director 工具委托之 |
| `src/resources/lineage.py` | `origin`/`lineage`/`shared_version`/`update_available` 计算 + content-addressed media 存取 |
| `routers/resources.py` | **新 router**：`/api/project/{name}/resources` list/read + `/api/project/{name}/looks` generate/read + shared library import/publish/update 端点 |
| `tests/test_resources_*.py` | 见 §15 |

### 14.2 修改文件（最小侵入，additive）
| 文件 | 改动 | 为什么 |
|---|---|---|
| `src/core/models.py` | `Project` 加 `looks: List[CharacterLook]=[]` / `clothings` / `props` / `reference_assets` additive 字段（default 空）；`to_dict/from_dict` 加序列化 | 资源存入 canonical（additive，from_dict known-filter 兼容） |
| `src/core/store.py` | `load` 时 lazy default 新字段为空列表 | 双读兼容旧 project.json |
| `src/core/models.py` `Asset` | 加 `asset_id`/`checksum`/`source`/`origin` 可选字段（default None，不破坏现有） | ReferenceAsset 复用 Asset 扩展（不新建类，最小） |
| `api_server.py` | `include_router(resources.router)` | 挂新 router |
| `routers/characters.py` | **不改行为**，仅在注释标注 views 将被 ReferenceAsset bank 双读 | 兼容标注 |
| `src/views/characters/index.vue` | 顶部加 scope/type 过滤 + 卡片 Grid（复用现有列表） | UI 渐进升级 |
| `src/store/index.ts` + `src/api/index.ts` | 加 resources store + api methods | 前端接线 |
| `src/agents/director_vision_review.py` | `visual.review_image` 内 vision 调用委托 `vision_review_service`（行为不变） | Review 复用（可选，Phase 1B 或 1C） |

### 14.3 不动的文件（冻结区）
`src/agents/character_designer.py` / `producer.py` / `director.py` / `script_writer.py` tool schema / `src/revision/*` 语义 / `src/agents/change_protocol/*` / `src/agents/subagent/*` / `src/agents/director_visual.py` 行为。

### 14.4 saved_characters 升级（独立小切片，可后置）
- `routers/characters.py` meta 读写加 additive 字段（origin/version/scope/approval_state，缺省 default）。
- 目录结构调整 `shared_library/{type}/{id}/` + 旧路径双读。
- 可作为 Phase 1C 独立切片。

---

## 15. Test Plan

| 测试类 | 目标 | 既有可复用 |
|---|---|---|
| old project compatibility | 旧 project.json（无 looks/clothings/props 段）能正常 load/save，Character/Scene/Shot 不变 | `migrate_v1_to_v2` 模式 |
| current Character unchanged | Character Designer 7 工具行为零回归 | `tests/test_character_designer_agent.py` |
| shared import isolation | Project A 改 imported Alice 不影响 Project B snapshot + Shared 母版 | 新 |
| no cross-project mutation | 同上，构造保证 | 新 |
| lineage | origin/lineage/shared_version 字段正确记录与读取 | 新 |
| reference persistence | ReferenceAsset local+url+checksum 重启后可读 | `tests/test_character_design.py` 模式 |
| Look relationship | CharacterLook.character_name FK + clothing_ref 正确关联 | 新 |
| restart persistence | 资源重启后从 project.json 恢复 | `_full_regression` 模式 |
| UI refresh persistence | 切页/刷新后资源状态不丢（参考 agentTurn processItemsByMid 模式） | 新 |
| existing Agent regression | Character Designer/Director/Producer 全量回归零新增 | `tests/` 108 文件基线 |
| Visual Review canonical read-only | Resource Review 不改 project.json 逐字节 | `test_director_vision_review.py` T9 模式 |

- 基线：当前 `tests/` 108 文件（`test_character_design.py`/`test_character_designer_agent.py`/`test_clean_review.py`/`test_director_vision_review.py` 等）。Phase 1B 目标=零新增回归 + 新增资源测试全绿。

---

## 16. Risks / Open Questions / CONFLICT

### CONFLICT C1（三视图两套落盘命名）
- **Observed**：Agent handler `character_views_handler.py:52-65` 用 `{safe_name}/{view}_{sig8}_seg.png`（子目录+版本化）；router bind `routers/characters.py:775-787` 用 `{name}_{view}_seg.png`（平铺+无版本）。两套不可互换引用。
- **Reason**：两条独立生成路径（Agent 走 handler，旧角色页 UI 走 router bind），各自演化。
- **Recommended resolution**：Phase 1B 新实体（CharacterLook）用新统一模板 `output/looks/{look_id}/{view}_{sig8}_seg.png`（handler 风格，版本化）。旧两套保留不动（兼容），不强制收敛（避免破坏现网）。
- **Impact**：现有两套共存可用，不阻塞 Phase 1；但 ReferenceAsset bank 的 `local` 字段需能指向任一模板——用 `checksum` 寻址而非路径硬编码。

### CONFLICT C2（views_meta.json 与 Character.views 双真相源）
- **Observed**：`character_views_handler.py` 同临界区写 `views_meta.json`（appearance_sig 用于重生成判定）+ `Character.views`（Asset 状态，业务消费）。两份独立。
- **Reason**：sig 元数据与业务状态分离设计。
- **Recommended resolution**：ReferenceAsset bank 成为新单一真相源（含 appearance_sig 字段）；`views_meta.json` 逐版本废弃（双读兼容期保留）。
- **Impact**：低，双读即可。

### CONFLICT C3（★ rename 遗漏 Scene.characters）
- **Observed**：`routers/characters.py:839-841` rename 只遍历 `p.scenes → sc.shots` 更新 `sh.characters`，**不更新 `sc.characters`**（`Scene.characters`，`models.py:378` 独立于 Shot 的字段）。rename 后 Scene 层角色名引用 stale。
- **Reason**：rename 级联实现遗漏 Scene 自身的 characters 字段（只处理到其下 shots）。
- **Recommended resolution**：Phase 1B 顺手补一行 `sc.characters = [new_name if cn==character_name else cn for cn in sc.characters]`（最小 fix）。此为既有 bug，非 Resource 引入；但 Resource 设计应吸取教训——**CharacterLook 应有独立 look_id 而非靠 name 串联**，避免重命名级联漏洞。
- **Impact**：中。当前被 Agent 协议层禁止 rename（见 C4）掩盖，故生产未爆，但 REST 路径直接可触发。

### CONFLICT C4（★ REST 改名 vs Agent 禁止改名 / delete 悬空引用）
- **Observed A**：REST `PUT /api/project/{p}/character/{c}` 支持 `new_name` 改名（有 C3 bug）；Agent 协议层 `src/agents/change_protocol/character_proposal.py:8,27` 明确**禁止 rename**（name 即身份无 character_id，`CHARACTER_ACTIONS` 无 rename，rename→`user_decisions_needed`）。两套态度矛盾。
- **Observed B**：`DELETE /api/project/{p}/character/{c}`（`characters.py:894-926`）删除角色**不清理 `Shot.characters`/`Scene.characters` 中对该角色的引用**——悬空旧名引用（`get_character(name)` 返回 None，`routers/shots.py:560,618,1231` 多处 None 兜底但不报错不提示）。
- **Reason**：项目角色以 name 为身份、无 id，删除/改名后引用方无可靠级联。
- **Recommended resolution**：Phase 1 不动既有行为（冻结区）。但 Resource 设计**必须给 CharacterLook 独立 `look_id`**（不靠 character_name 串联 Look），新增 typed resource（Clothing/Prop）也用独立 id——从源头避免 name-级联漏洞。删除资源时在 service 层做 reference sweep（标 stale 或级联清理），不沿用 Character 的悬空做法。
- **Impact**：高（设计教训）。这正是文档 §四.C 强调"进入 Shot Reference 系统的应是 look_id 而非 character_id+clothing_id+prompt"的根因。

### CONFLICT C5（"character_id" 语义双轨）
- **Observed**：角色库 `saved_characters/{char_id}/` 用 `char_id={name}_{uuid8hex}`（`characters.py:526,552`）；项目角色 `Character.name` 即身份无 id；Agent 协议层 `execution_models.py:156 affected_character_ids: List[str]` 注释"character name（本仓库无 character_id）"。三处共用术语易混。
- **Recommended resolution**：Production Resources V1 统一术语：项目内 typed resource（Look/Clothing/Prop）用独立 `{type}_id`；Character 仍以 name 为身份（不改）；shared library 用 `shared_resource_id`。文档与代码注释明确区分。
- **Impact**：低（术语清晰化）。

### RISK R1：Character Look 不做 look_id 会迫使 ShotReferencePack 重构
- 若 Look 不作为一等资源带稳定 `look_id`，未来 ShotReferencePack / 换装 Shot 无法稳定引用组合外观，被迫重构。→ **Phase 1 必须建 look_id**。

### RISK R2：scope 若做成 resource type 而非字段，Shared Library 无法扩展到非 character
- → **scope 必须是 resource 字段**（project/shared），不是 type。

### RISK R3：lineage/origin 不在 Phase 1 落地，import 隔离/update_available 无法 retrofit
- → **每个 resource 现在就带 origin/lineage 字段**（default None 即可）。

### RISK R4：ReferenceAsset 不统一，每类资源自造图片字段
- → **ReferenceAsset 作为共同图片基础设施**，所有 typed resource 的图片都引用它。

### RISK R5：Character name→id 切换
- 若误把 Character FK 从 name 改成 character_id，全部分镜引用 + rename 传播 + 前端 + Visual Review 全断。→ **保持 name-based FK，look_id 作为 additive 指针**。

### RISK R6：Location 误塞 Other Resources
- 若把 Location 做成 misc asset，未来 Scene/Location/SpatialAnchor 域被迫重构。→ **Location 只留接口占位，不实现**。

### OPEN Q1：ReferenceAsset 是新建类还是扩展 Asset？
- 推荐**扩展 Asset**（加 asset_id/checksum/source/origin 可选字段，default None）而非新建类——最小侵入，现有 ImageAsset/Candidate 等自动兼容。ReferenceAsset 是 Asset 的"带身份+溯源"用法，不必独立 dataclass。若需强类型约束，可用 `Asset` + 一个 `ReferenceAssetView` 适配层。

### OPEN Q2：resources 存 project.json 还是独立 resources.json？
- 推荐先存 project.json additive 段（与 Character/Scene/Shot 同 canonical，复用 ProjectStore 原子写+write_lock）。若后期资源量巨大影响 load 性能，再拆 `resources.json`（Phase 2+）。Phase 1 不拆。

### OPEN Q3：Visual Review service 抽离时机
- Phase 1B 可先并行路径（Resource Review 自有 prompt + 调 `chat_completion_vision`），Phase 1C 再收敛成共享 service。不强求 Phase 1B 抽离（避免动 Director 冻结面）。

---

## 17. Recommended Phase 1B / 1C / 1D Slices

### Phase 1B（最小代码切片，推荐先做）
- 新增 `src/resources/` 子包（models + store + service），纯 additive。
- `ReferenceAsset`（扩展 Asset）+ `Resource`/`CharacterLook`/`Clothing`/`Prop` dataclass。
- `ResourceStore` 双读 project.json additive 段 + `Character.views` 适配 Base Look。
- 轻量 `list`/`read` API（`routers/resources.py`，前端不接，先 service+API 可用）。
- 测试：old project compatibility / Character unchanged / restart persistence / Look relationship。
- **不动**：任何 Agent / Director / Producer / 前端 UI。

### Phase 1C
- CharacterLook 生成流程（复用 AgnesClient + rembg + receipt + 统一落盘模板）。
- Vision Review 复用（并行路径→收敛 service）。
- saved_characters additive schema 升级 + 双读。
- 前端 UI 渐进升级（scope/type 过滤 + 方形卡片 Grid + Look 详情页）。

### Phase 1D
- Shared Library import/publish/update + lineage + content-addressed media + update_available。
- 隔离性测试（Project A 不污染 B）。
- Prop 最小实现 + Location 接口占位。

---

## 18. 架构问题明确回答（A–O）

**A. Production Resources V1 数据结构？**
叠加 typed Production Resources 层（`Character`保持 + 新 `CharacterLook`/`Clothing`/`Prop` + 统一 `ReferenceAsset`），additive 存入 project.json，scope=字段。

**B. Character 保持原 canonical 还是迁移？**
**保持原 canonical，不迁移**。被全链路硬依赖（§5），且 `creative_source_signature` 已把 Character 身份定义成 name+description_en（排除 views）。迁移收益低、风险极高。

**C. CharacterLook 存哪、如何关联 Character？**
存 project.json 的 `looks` additive 段（`ResourceStore`）。关联用 `character_name`（name-based FK，与 Scene/Shot 引用一致），不引入 character_id。不嵌入 Character 对象（避免膨胀 Character + 保持身份签名稳定）。

**D. Clothing 如何建模？**
独立 typed resource：clothing_id/name/description/design_traits/material/colors/reference_assets(ReferenceAsset 列表)/cover_asset_id/tags/revision/origin/approval_state。与 Character 解耦，可被多个 Look 引用。

**E. ReferenceAsset 如何统一图片基础设施？**
扩展现有 `Asset`（加 asset_id/checksum/source/origin 可选字段，default None）作为共同基础。不新建独立类（最小侵入）。现有三视图 `views` 保留为 Base Look 的 identity references（双读），不删。

**F. Prop 第一阶段做到什么程度？**
最小：prop_id/name/description/reference_assets/cover_asset_id/tags/origin/approval_state。预留 state/holder/damage/variants/continuity 字段接口但不实现。Location 只留接口占位，不塞进 Other。

**G. saved/global library 如何保留和升级？**
LIVE 保留。additive schema 加 origin/lineage/shared_version/scope/approval_state（lazy default），升级为 Shared Library 雏形。目录结构 `shared_library/{type}/{id}/` + 旧路径双读。

**H. Shared Library import/publish/update 语义？**
Import=copy-on-import（meta 复制 + media content-addressed 去重）+ 记录 origin；Publish=显式创建 Shared 新 version（不覆盖旧）；Update=显式拉新版到项目 snapshot + bump lineage。无隐式双向同步。

**I. Project A 修改不污染 B？**
构造保证：项目只持本地 snapshot + content-addressed media 指针；Shared 母版 immutable per version；项目写只改本地。B 持自己 snapshot，与 A 独立。

**J. 旧三视图如何进入新体系？**
双读 adapter：ReferenceAsset bank 为新真相源；`Character.views` 视为 Base Look 的 identity references（不删、不重写）。无 Look 时 synthesize Base Look from views。lazy migration，不破坏现有。

**K. Character+Clothing→Look 生成如何复用图片生成能力？**
复用 `AgnesClient.generate_image(prompt, image_refs=[char_ref,clothing_ref])`（同 chain-gen 机制）+ rembg + `appearance_sig` + receipt（`_base_receipt` + verified 重读）。`character_views_handler` 为模板。

**L. Review 如何接入不破坏 Director/Visual Review？**
抽薄 `vision_review_service`（无状态纯函数：image+checks→verdict），Director `visual.review_image` 委托之（行为不变），Resource Review 直接调 service（不进 Director tool-loop/context）。无安全复用边界则并行路径暂不耦合（§10.3）。绝不改 Director tool-loop/OCC/账本。

**M. UI 如何渐进升级？**
复用 `src/views/characters/index.vue`：加 scope toggle + type filter + 方形卡片 Grid。Character 卡片保持，新增 Clothing/Other。不重写 Agent Center。角色库面板升级为 Shared scope 视图。

**N. Agent 如何方便 list/read，为何 Phase 1 不改 Agent？**
service+API 从第一天具备轻量 list + 精确 read。不改 Agent 因 Agent 全冻结 + 接线触碰 ownership/OCC/账本高风险 + 先证明数据模型。Phase 2+ 再 additive 挂工具。

**O. 哪些设计点现在做错会迫使未来重构？**
1. Look 必须有 look_id（否则 ShotReferencePack/换装 Shot 无法引用）；
2. scope 必须是字段非 type（否则 Shared 无法扩展到非 character）；
3. lineage/origin 现在就带（否则 import 隔离无法 retrofit）；
4. ReferenceAsset 必须统一（否则每类资源自造图片）；
5. checksum/content-addressed media（否则 copy-on-import 浪费磁盘+无法去重）；
6. 保持 Character name-based FK 不切 id（否则全链路断）；
7. Location 不塞 Other（否则 Scene/Location 域被迫重构）。

---

## 19. 结束条件核对

- [x] 完整阅读真实仓库（Character/角色库/图片生成/资源存储/前端 UI/Visual Review/revision·OCC/receipt 全部从代码验证）
- [x] 不修改任何生产代码（本轮零代码改动）
- [x] 输出 `PRODUCTION_RESOURCES_V1_PHASE1A_AUDIT.md`
- [x] 给出精确到文件级的下一阶段实施方案（§14）
- [x] 关系图（§7.1）
- [x] A–O 架构问题明确回答（§18）
- [x] CONFLICT 标注（§16 C1/C2）

**本轮停止。等待用户确认后再进入 Phase 1B 实施。**
