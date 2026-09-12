# Project Visual Profile V1 — Phase P1 报告

**Phase P1 — Project Visual Canonical + Generation Propagation**
日期：2026-09-12 ｜ 分支：`feat/scene-system-v1-s1`（延续）｜ 状态：实现完成，等待用户验收

执行依据：`详细流程文件/修复阶段/6.md`（P1 全量规格 §0-§25）。

---

## 1. 项目创建审计（实现前真实调查）

- 后端：`routers/projects.py` `POST /api/projects/create?name=` → `src/utils/project_manager.py::create_project`：建 `input/ + output/{characters,storyboards,videos,audio,subtitles} + cache/checkpoints/` + 写空 `project.json`。**原流程无任何 profile 文件**。
- 前端：`src/views/dashboard/index.vue` 新建对话框只有项目名 → `src/api/index.ts::createProject(name)`。
- 既有 HTTP create 测试 4 处（`tests/test_project_creation.py`），已随新契约更新（T7）。

## 2. 视觉生成入口审计（system service，全部非 Agent）

| 入口 | 端点 | basis 计算 | 迁移 |
|---|---|---|---|
| CharacterLook | `POST /resources…/looks/{id}/generate` | `src/resources/generation.py::compute_generation_basis` | ✅ |
| CharacterReferenceComposition | `POST /resources…/compositions/{id}/generate` | `compute_composition_basis` | ✅ |
| Prop | **无生成端点**（上传参考 + review + approve；T14 的"若真实存在"答案=不存在） | — | review 口径迁移 ✅ |
| LocationView | `POST /scene-system/views/{id}/generate` | `src/scene_system/visual_basis.py::compute_location_visual_basis` | ✅ |
| SceneInstanceView | `POST /scene-system/scene-instance-views/{id}/generate` | `instance_view_basis.py::compute_scene_instance_visual_basis` | ✅ |

另有 legacy 分镜管线（`src/core/prompt_builder.py` + `src/ai/*`，消费 legacy `StyleAnchor`）与 Agent 路径（Character Designer 三视图、Director 分镜图）——均不在 §11/§12 目标清单，未动（见 §19/§20）。

## 3. Ontology

`src/visual_profile/`（新包，四个文件）：

- **ProjectVisualProfile**（typed dataclass）：`render_family` / `art_direction` / `style_tags[]` / `negative_style_constraints[]` / `schema_version=1` / `created_at` / `updated_at`。
- **分层（§12）**：ProjectVisualProfile=全片介质/艺术方向；`Location.style_notes`=稳定地点身份（保留不动）；SceneState=时间/天气/灯光/损坏。三者不混淆，E2E 与单测覆盖"night/storm 不进 Project Style"的架构事实（各域 snapshot 独立进 basis）。
- 错误模型：`VisualProfileSchemaError`（坏 JSON/未知 schema/非法 render_family 一律 fail closed，绝不静默当 unspecified——静默会伪装掉 fail-closed 语义）。

## 4. Storage

- `projects/{project}/output/project_profile.json`，schema_version=1，独立 companion（对齐 resources.json/scene_system.json 惯例：读零副作用、写 atomic（`atomic_write_json`）、复用 `project_write_lock` 同 project_name 锁族）。
- **不修改 project.json**（T21 字节级验证）。
- 旧项目读取 → `unspecified`（None）；纯读绝不创建文件（T2）；第一次真实设置才创建。
- 沿途修复 1 个真 bug：create 流程原本"先建目录后构造 profile 对象"，非法 render_family 会留下半建目录 → 已改为**先验证后落盘**（fail fast）。

## 5. render_family

`photorealistic | 2d_animation | 3d_animation | stylized | custom`（§4 五值；2D/3D 区分落实；具体风格进 art_direction/style_tags，不扩枚举）。测试 T7 断言非法值 400 + 缺省 422（FastAPI 契约级拒绝），**不静默默认 photorealistic**。

## 6. Profile 指纹

`visual_profile_fingerprint = sha256(canonical_json(语义子集))[:8]`——只覆盖 render_family/art_direction/style_tags/negative_style_constraints，**不含时间戳**（T6）。`profile_revision` 即该指纹（OCC sha256[:8] 指纹族同口径）。style_tags 保留用户给定顺序（语义一部分，确定性）。

## 7. 旧项目行为

- 加载/查看不受影响；无自动迁移写文件（§7）。
- 使用 profile-aware 生成时 unspecified → fail closed `project_visual_profile_required`（HTTP 422；record 诚实记录，canonical 零写入——测试证明 generation_status 保持 none）。
- 不猜 realistic/anime/3D。

## 8. 新项目行为

- UI 必选 Visual Style 卡片（5 值）后才能创建；后端 create contract 必须收到明确 `render_family`（缺省 422、空串 400）。
- `art_direction` optional（例如 `2d_animation` + "cinematic Japanese anime, clean cel shading, detailed environment"）。
- 创建即写 project_profile.json。

## 9. Style Compiler（§10 单一编译点）

`src/visual_profile/compiler.py`：

- `compile_project_style_instruction(profile)` → 确定性 style instruction（"Visual production style: 2D animated image. Do not render as live-action photography or 3D rendered CGI. Art direction: …"）。
- `append_style_instruction(domain_prompt, profile)` → 生成 prompt 后缀式（与 legacy StyleAnchor.suffix 同惯例）；unspecified 不产生空尾巴。
- `compile_project_style_review_expectation(profile)` → Vision Review 期望段（硬介质判定口径）。
- **五个 prompt/review builder 均消费编译器输出，零复制**（T8/T9/T10 确定性验证）。

## 10. Resources 传播（§11）

- `compute_generation_basis` / `compute_composition_basis` 增 `visual_profile` 参数：编译后 style 指令进 prompt；basis 嵌 `visual_profile`（语义子集 snapshot）+ `visual_profile_fingerprint`（T12/T13）。visual_profile=None 时键诚实缺省（legacy 形态）。
- 生成入口 `write_service.generate_look_candidates` / `generate_composition_candidates`：阶段 1 短锁内加载 profile → unspecified fail closed（record error=`project_visual_profile_required`）；坏文件 → `visual_profile_unreadable`（500，不静默）。
- **Resources ontology / Shared Library schema 零改动**（T22：schema_version 仍 2、无 visual_profile 键进 resources.json）。

## 11. Scene 传播（§12）

- `compute_location_visual_basis` / `compute_scene_instance_visual_basis`（含 `compute_no_peer_basis` 口径透传）同样嵌入 profile snapshot+指纹+style prompt（T15/T16）。
- 三层架构事实由代码结构保证：location_snapshot / view_relevant_scene_state（投影）/ visual_profile 三段独立共存于 basis，互不写入。
- scene 生成入口同样 fail closed。

## 12. Stale 语义（§13）

- **生成中（三阶段协议阶段 3）**：`recompute_*_still_holds` 先查 profile 指纹——不一致 → 明确理由 `(False, "project_visual_profile_changed")`（先于笼统 basis_changed）；一致再全量重算。
- **approved 后（read 模型）**：`compute_state_view_staleness`（SceneInstanceView）新增归因——profile-aware basis 指纹 ≠ 当前 → `project_visual_profile_changed`；派生进 `read_scene_instance_view_status`。
- **LocationView / Resources**：无 approved-fingerprint 锚点且 §17 禁 schema bump → read 层 **computed style compatibility**（零落盘、零 mutation）：
  - scene：`GET /scene-system/views/{view_id}/generations` 每条 record 附加 `style_compatibility`；
  - resources：`read_look` / `read_composition` 附加 `latest_generation_style`。
- 绝不自动 mutation/regenerate/delete/revoke（§9/§13）。

## 13. Legacy Generation 语义（§14）

`style_compatibility_of_basis(basis, current_fp)`（models.py 纯函数）：

- basis 无 `visual_profile_fingerprint`（本阶段前的 generation）→ `style_compatibility="unknown"` + `generated_before_visual_profile=true`——**绝不伪造"符合当前 Style"**。
- 指纹一致 → `current`；不一致 → `project_visual_profile_changed`。
- staleness 归因区分两态：legacy basis 在已设 profile 的项目 → `generated_before_visual_profile`（诚实，与"profile 变更"区分）；项目从未设 profile + legacy basis → **不因此 stale**（不全局自动 stale 所有 legacy 项目，T17b 三态覆盖）。
- 坏 profile 文件在 staleness/read 路径 → `visual_profile_unreadable` 诚实标记（无法证明 currency → 不判 current）。

## 14. Visual Review（§15）

五个 review 全部接入 **`project_style_consistency`（hard）**：

| review | 基础维度 | style 维度 |
|---|---|---|
| look_review | 3 | profile specified 时追加（4th，hard） |
| composition_review | 6 | 追加（7th，hard） |
| prop_review | 3 | 追加（4th，hard）——审上传参考图（它们是 canonical prop visuals，风格漂移会经 composition i2i 传播） |
| view_review（S2A） | 6 | 追加（7th，hard） |
| instance_view_review（S2B） | 7 | 追加（8th，恒 hard，进入 hard 集） |

- Vision prompt 均获得 render_family + art_direction + negative constraints（编译器期望段）。
- **unspecified → 维度不参与**（prompt 无该维度、record 标 `project_style_consistency="not_applicable"`）——不把旧项目全部审死（§7/§14 一致性；T19b）。
- fail → overall=needs_fix → `has_pass_evidence=False` → approve 双向硬门槛关闭（T20）。
- 审查基准指纹 `_expectations_fingerprint` 增 `visual_profile_fingerprint`（可追溯审查时的项目风格）；**approve 侧同口径重算**（profile 变更后旧 pass 证据 → `stale_approval_basis`，需重新 review）——两侧口径一致性是本轮发现的隐含缺口，已闭合。
- record 逐候选拍平 `project_style_consistency` + `style_notes`（五 review 同形）。

## 15. UI（§18/§19，唯一前端改动面）

- **项目创建**（`dashboard/index.vue`）：Visual Style 卡片（Photorealistic/2D Animation/3D Animation/Stylized/Custom）必选后才能创建；选中后出现 optional 艺术方向输入框。
- **Settings 最小入口**：项目卡片新增风格徽章（未设置显示"风格未设置"虚线徽章），点击开小对话框——读 profile（unspecified 显示引导语）→ 编辑 → OCC 保存；`has_approved_visual_assets=true` 时显示"更改可能使已生成资产过期"强提示。
- `src/api/index.ts`：`createProject(name, renderFamily, artDirection)` + `getVisualProfile` + `updateVisualProfile`。
- T28 vite build ✓（31.6s）；T29 触达文件（dashboard/index.vue、api/index.ts）vue-tsc 零新错（其余报错均为既有债：store/index.ts / ShotCard / CreativeWorkspaceStudio）。
- 后端契约变化：`POST /api/projects/create` 缺 `render_family` → 422（**旧调用方必须补参**）。

## 16. Tests

新文件 `tests/test_visual_profile_p1.py`（26 测试）：T1-T6、T8-T23（可单测部分）+ fail-closed（unspecified/坏文件/HTTP 契约 round-trip）+ staleness 三态归因 + review hard gate。
`tests/test_project_creation.py` 更新至新 create 契约 + T7 两断言（缺省 422/非法 400/不留半建目录）。
既有 15 个生成/review 测试文件 fixture 迁移：每文件构造点 +1 行 profile 写入（photorealistic 默认）、review canned JSON +1 个维度 key、3 处测试内 basis 计算补 `visual_profile` 参数（对齐真实生成口径）、2 处维度集合断言扩为含新维度。

## 17. Real E2E（§22）

`_e2e_visual_profile_p1.py`（scratch driver，惯例不入库）——**14/14 PASS，48s**（真实 Agnes 生成 + 真实 vision review）：

- PROJECT A `_pvp_e2e_photo`（photorealistic）：LocationView 生成 committed，basis 含 profile 指纹，review `project_style_consistency=PASS`（overall=pass），record 口径=evaluated+指纹当前。
- PROJECT B `_pvp_e2e_2d`（2d_animation + art_direction）：同链路 PASS，vision 实证回报 "The image is rendered in a 2D animated style consistent with the project's direction"。
- 翻转 B：2d_animation → photorealistic（OCC 更新，不重新生成）→ 旧 generation `style_compatibility=project_visual_profile_changed`（old_fp=d75dc31e ≠ new_fp=8bc1b5f2）、`generated_before_visual_profile=false`；A 的 generation 仍 `current`（项目隔离）。

## 18. Regression

全量 `pytest tests/ -q`：见文末「回归数字」段（与冻结基线同构对照）。单域先行验证：resources 1C1/1C1.1/1C2/1C3/1C5 = 47+9+10+53 passed；scene S2A×4 + S2B×6 全绿；P1 新测试 26 passed。

## 19. Agent-deferred paths（§2/§23）

以下入口本轮**不迁移、不改 Agent 源码**（`git diff src/agents/` 为空，T24 铁证）：

- Character Designer 三视图（`character_views_handler.py` 直读 legacy `project.style.art_style`）—— `AGENT_INTEGRATION_DEFERRED`
- Director 分镜/视觉审查 —— `AGENT_INTEGRATION_DEFERRED`
- legacy 分镜管线（`src/core/prompt_builder.py`、`src/ai/production_planner.py` 等）——不在 §11/§12 清单，继续消费 `StyleAnchor`；待 Shot Binding 阶段统一裁决两套 style 概念的合并。

## 20. Deviations

1. **Prop 无生成端点**（T14 规格预留"若真实存在"）——审计证实只有上传参考+review+approve；按 §15 精神把 style gate 落在 prop review（approved 参考图是 canonical prop visuals，会经 composition i2i 传播风格）。
2. **review unspecified 语义**：doc 未明说；选择"维度不参与 + record 标 not_applicable"（否则旧项目所有 review 被审死，违背 §7/§14）。
3. **approve 口径闭合**：S2A/S2B approve 的期望指纹复核原本不含 profile——review 记录含而 approve 不含会把每次 approve 误判 stale；已把 approve 侧拉齐同口径（这是对既有 approve 语义的**最小必要扩展**，非 ontology 变更）。
4. **create 缺参状态码**：FastAPI 契约层缺 required query param = 422（非 400）；空串/非法值走 router 400。语义等价"必须收到明确选择"。
5. **测试 fixture 迁移**：为满足"回归零新增"，15 个既有测试文件的构造点注入 profile 写入 + canned JSON 补维度——测试意图全部保留，仅对齐新契约。

## 21. Blockers

无。

## 22. Recommendation for Shot Binding

1. Shot binding 消费 SceneInstanceView 时必须拒绝 `is_stale=true` 的 view——`stale_reasons` 现已含 `project_visual_profile_changed` / `generated_before_visual_profile`，建议 binding 层对后者给出"生成早于项目风格定义"的显式 UI 语义而非硬失败。
2. legacy `StyleAnchor`（art_style/palette/era，"anime" 默认）与本 Profile 是**两套并存的项目风格概念**：前者服务 legacy 分镜管线+Agent，后者服务 Resources/Scene 生产链。Shot Binding 统一时建议：ProjectVisualProfile 为唯一真相源，`StyleAnchor` 降级为 legacy 视图或由 profile 编译结果填充；Character Designer/Director 迁移属 Agent Integration 范畴。
3. Resources 侧若未来引入 approved-basis 锚点（现仅 review 证据门槛），可直接复用 `style_compatibility_of_basis` 与 SceneInstanceView 的归因模式。

---

## 回归数字（全量回归完成后填写）

全量 `pytest tests/ -q`（后端停止后净环境跑，43:55）：**34 failed / 1467 passed / 1 skipped**。

- **34 failed 与 S2B.1 冻结基线逐条同构**（`diff _pvp_failures_baseline.txt _pvp_failures_now.txt` 为空，两份名单存档于仓库 scratch）——T25/T26/T27 **回归零新增**达成。
- passed 1439 → 1467（+28：`test_visual_profile_p1.py` 26 条 + `test_project_creation.py` 契约更新净增 2）。
- 执行注记：首轮回归因"未停 ：8000 后端"卡死（既有已知坑，memory 有载），停止后端后净环境重跑通过；后端已在收尾时重启（新端点生效）。
