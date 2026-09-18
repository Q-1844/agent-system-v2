# GOLDEN RUN 执行提示词（交接给 Claude Code）

> 本文档是自包含执行指令。你的任务是执行 **Golden_山雨信** 项目的全链路图片生产验收（Agnes-only）。
> 执行前通读全文；本文中的路径、函数签名、端点契约均已验证可用，不要重新发明。

---

## 一、项目与环境（事实，勿重新探索）

- 仓库根：`c:/Users/曹文淇/Desktop/ai工作流/animation-storyboard`
- Python 解释器：`"D:\Delta Force\python.exe"`（路径含空格，命令行必须加引号）
- 后端：FastAPI `api_server.py`，端口 8000，**当前已在运行**（`_launch_backend_detached.py` 后台启动，日志 `backend_runtime.log`）。修改 `routers/*.py`、`services/*.py`、`src/api/*.py`、`src/core/*.py` 后必须重启后端才生效。
- 前端：Vue/Vite 端口 7777（人工查看用）。
- 密钥：`.env` 已配置（`AGNES_API_KEY` 等，启动时自动注入环境变量）。
- 图片 provider：**本任务只用 Agnes（agnes-image-2.1-flash）**。严禁调用 SenseNova 图片（`sensenova-u1.5-lite`），`image_provider` 参数一律不传或传 `"agnes"`。
- 目标项目：`projects/Golden_山雨信/`（**已创建**：7 个场景 / 33 个镜头 / 3 个角色 / visual profile=2d_animation 单一风格，全部在 project.json 与 output/project_profile.json 中，不要改动叙事与风格定义）。

## 二、背景：已完成的前置工作（勿重做）

上一阶段已修复并回归（详见 `IMAGE_SYSTEM_FULL_ACCEPTANCE_AND_PROVIDER_COMPARISON_REPORT.md`）：
1. legacy 分镜首帧/尾帧 prompt 已接入 ProjectVisualProfile（`routers/shots.py` 的 `_legacy_vp_style_suffix`，经 `build_first_frame_prompt(style_suffix=...)` seam）——真机验证 PASS。
2. legacy 角色 4 端点（generate-view/generate-chain/generate-sheet/regenerate-view）请求体支持可选 `project` 字段——**传入项目名时自动把该项目 visual profile 风格指令追加进 prompt**（消除风格锚不一致）。Golden Run 中调用这些端点时必须带 `project: "Golden_山雨信"`。
3. Vision Review JSON 解析稳定性：`src/resources/look_review.py` 新增 `vision_json_call()`（解析失败自动重试一次），5 个 review（look/prop/composition/location view/instance view）已全部接入。
4. Look prompt 已强化全身（head-to-toe, both feet visible in frame）；view_review 方位例词已中性化。
5. 回归：针对性测试 106 passed。已知测试卫生问题：跑 pytest 前必须先停后端（`projects/_pvp_http` 等固定名目录会被后端句柄锁住导致 fixture 409）。

## 三、执行总则（硬性规则）

1. **全部走真实生产链**：ProjectVisualProfile → approved 角色参考 → approved 场景参考 → ShotProductionBinding → image generation。禁止绕过系统手写 prompt 直调 API（例外：Prop 参考图直生，见步骤 3）。
2. **禁止逐张调优**：不允许为了让单张图更好而临时改 prompt。若发现明确的 prompt compiler / 系统 bug：暂停 → 记录（问题→修复）→ 修复 → 回归 → 从明确检查点重新执行；否则**保留失败结果**。目标是判断真实系统表现，不是全绿。
3. **证据完整**：每次生成必须可追溯（compiled prompt、reference inputs、provider/model、generation basis、输出图、review/approval、失败原因）。系统的 generation/review/approval JSONL 自动落盘；你的脚本需额外把事件摘要追加到 `_golden_evidence.json`（参考 `_acc_common.py` 的 `evidence()`）。
4. **三层评估分离**：系统链路正确性 / prompt-reference 正确性 / 最终视觉质量。Vision Review PASS ≠ 艺术质量优秀——最终 Gallery 必须支持人工判断角色表现、构图、叙事可读性、风格一致性、场景一致性、参考图导致的质感损失、AI artifact。
5. **诚实标记**：某功能在真实代码中不存在就标记"系统当前不存在"（已知：Prop 无生成端点、Clothing 无生成端点、Clothing 视觉经描述进 Look prompt）。不伪造覆盖。
6. **停止条件**：Agnes 持续 401/403/429/quota 信号 → 立即停止、保存进度、汇报。非瞬态失败（同一调用重试 3 次仍失败）按第 2 条 bug 协议处理。
7. **幂等可续跑**：所有脚本用 `already_done()`（`_acc_common.py`，只认 ok=True）做步骤级幂等，中断后可从断点继续。

## 四、已验证可用的代码资产（直接复用，勿重写）

| 文件 | 可复用内容 |
|---|---|
| `_acc_common.py` | `clients()` / `get_client("agnes")`、`evidence()`、`already_done()`、`full_look_cycle(pdir, name, look_id, provider, candidate_count, seed)`（生成→review→approve 全封装）、`full_composition_cycle()`、`full_location_view_cycle()`、`full_instance_view_cycle()`、`res_drev()`、`scene_drev` 用的 `SceneSystemService.document_overview()` |
| `_acc_p5_shots.py` | 分镜 HTTP 生产+轮询模式（`gen_first_frame`/`wait_batch`：POST first-frame → 轮询 project.json 新批次 → choose） |
| `_acc_p7_extra.py` | 角色 chain/sheet/regenerate HTTP 调用、rehost、last-frame（keyframes）模式 |
| `_acc_p6_gallery.py` | Gallery 生成器（改造成 Golden 版） |
| `_e2e_scene_multi.py` | SceneSystem 服务层建模模式（create_location/create_anchor/create_view/create_scene_instance/patch_scene_state/create_scene_instance_view，均为 async，服务构造 `SceneSystemService(pdir, project_name)`） |

注意：`full_*_cycle` 内部 review 恒走 Agnes vision（系统设计，保持）。`agnes_client` 参数不传 = 默认 Agnes 单例（本任务保持不传）。

## 五、执行步骤（严格按顺序；每步完成后再进下一步）

### 步骤 0：准备
- 确认后端健康：`GET http://localhost:8000/api/health`。
- 建你的工作脚本 `_golden_run_*.py`（放在仓库根，与 `_acc_*.py` 同级）。所有服务层脚本开头必须：
  ```python
  from _acc_common import PROJ_BASE, evidence, already_done, get_client, res_drev
  ```
  （`_acc_common` 会自动加载 .env 并 `_clear_for_test()` 清进程内写锁。）
- 确认项目数据：33 镜、7 场景、3 角色在 project.json；`output/project_profile.json` render_family=2d_animation。

### 步骤 1：角色三视图（3 角色 × chain → bind）
- `POST /api/character/generate-chain`，body：`{"description": <角色 description_en>, "style": "2D anime cel-shaded", "size_tier": "1K", "ratio": "2:3", "project": "Golden_山雨信"}`（**project 字段必须传**，风格锚定）。
- 返回 `raw_views`/`seg_views`（front/side/back base64）→ `POST /api/project/Golden_山雨信/character/bind`，body：`{"name": <角色名>, "description": <description_en>, "views": {"front": {"raw":..., "seg":...}, "side": {...}, "back": {...}}}`。
- 落盘 1 张代表图到证据。3 角色描述在 project.json 的 characters[].description_en。

### 步骤 2：Look ×4（角色造型资源，走 Resources 三阶段）
4 个 Look：
| character_name | label | appearance_description |
|---|---|---|
| 凌 | uniform_main | deep-blue martial school uniform with silver trim, dark-blue headband, saber at hip（主造型，S1-S4） |
| 凌 | travel_cloak | travel cloak over the uniform, hood down, pack on back（S5-S7 变装） |
| 陈掌柜 | innkeeper_grey | brown apron over grey robe, counter cloth in hand |
| 黑衣客 | night_cloak | black traveling cloak, deep hood shadowing the face, straight sword |
- 流程（每 Look）：`ResourceWriteService(pdir, "Golden_山雨信").create_look(character_name=..., label=..., appearance_description=..., expected_document_revision=res_drev(pdir, "Golden_山雨信"))` → `full_look_cycle(pdir, "Golden_山雨信", look_id, provider="agnes", candidate_count=1, seed=<固定整数 0-999>)`。
- ⚠️ Look 生成是 i2i（identity ref = 角色绑定的 front 视图），seed 必须在 0-999（Agnes t2i seed 上限 999；i2i 大值会被放行但不要依赖）。
- 每个 Look 只 approve 一个候选（跨候选身份一致守卫尚未实现——单候选策略规避）。
- 凌的 travel_cloak 用 create_clothing 建服装实体（name="travel_cloak"…）再 create_look(clothing_id=...)，验证服装→Look 绑定链。

### 步骤 3：核心道具 密信竹筒（Prop 链）
- Prop **无生成端点**（如实标记）。流程：`create_prop(name="message_tube", description="a sealed bamboo message tube with a red wax seal on a cord", tags=["core"])` → 用 Agnes t2i 直生参考图（prompt："a sealed bamboo message tube with a red wax seal on a cord, prop design sheet, centered, plain light background", ratio 1:1，seed 0-999）→ `_acc_p7_extra.py` 的 `register_asset` 模式（服务层 `register_reference_asset`，local=项目内相对路径）→ `attach_prop_reference` → `prop_review.review_prop_reference`（vision_json_call 已内置）→ `approve_prop_reference`。
- ⚠️ 已知坑：prop 审查的 `project_style_consistency` 维度对素底设定图严格——prompt 里已带项目风格词（"2D anime cel-shaded style prop illustration"）时通过率高；若 needs_fix 且拒因是 style，按规则 2 记录（这是已知 review 口径问题，不修 prompt 超过一次）。

### 步骤 4：Composition（凌 + 竹筒）
- `create_composition(character_name="凌", look_id=<uniform_main>, prop_bindings=[{"prop_id": ..., "relation": "carried", "anchor": "left_hand"}], pose_note=..., composition_description="Ling on the mountain road at dawn holding the bamboo tube...", expected_document_revision=...)`。
- relation 合法值：`attached/carried/held/near/worn`。anchor：`right_hand/left_hand/...`（用右/左手）。
- `full_composition_cycle(...)`（agnes，1 候选）。D 项目经验：多轮 uncertain 时按规则 2 处理（最多重试 2 轮，仍不过就保留失败记录）。

### 步骤 5：场景系统（3 地点 × （hero + side_east + rear + overhead）+ 时态状态）
- 3 个 Location 的 identity_description（**非对称特征必须保留**，这是多视角可判定性的前提）：
  - `mountain_road`（山道）：winding stone path with a small wooden rest pavilion on the LEFT going up, a fork with a stone direction post on the RIGHT, scattered boulders, pine trees on the upper side only
  - `post_station`（驿站）：two-story wooden post station: single blue-lacquered door with small canopy on the EAST side, stone chimney on the WEST wall, external wooden staircase on the NORTH wall, hitching post and peach tree on the SOUTH side
  - `stone_bridge`（石桥）：old single-arch stone bridge with a swaying lantern on a post at the bridge center, stone steps down to the river on the upstream side, willow trees on the far bank only
- 每地点 4 锚点（kind 用 entry/landmark/stairs/zone/other）→ 4 个 view（hero=south_side look north；side_east=east_side look west；rear=north_side look south；overhead=overhead look straight down——overhead 命中系统 nadir 特判）。
- 每个地点的 hero 先生成+审查+批准（`full_location_view_cycle`），后续机位自动引用 approved 参考（cross-view 机制）。
- SceneInstance：7 个剧情场景各建 scene_instance（`story_scene_id` = s01..s07；山道→s01/s05/s07 三个实例不同状态，驿站→s02/s03/s04，石桥→s06）。`patch_scene_state` 设置时态：s01 morning clear / s02-s03 afternoon / s04 dusk / s05 night wind / s06 night heavy rain / s07 dawn after rain。
- 每个实例基于 hero view 建 SceneInstanceView（`create_scene_instance_view` + `full_instance_view_cycle`）——这是 BOUND 的硬门槛（无 approved sciv → binding NOT_READY）。
- ⚠️ 每地点 4 机位 × 3 地点 = 12 view + 7 sciv ≈ 19 组生成+审查。overhead 对 2D 风格要求"2D game map / floor plan"正语义锚，系统已内置。

### 步骤 6：BOUND binding ×33 镜
- 每镜 `ShotBindingService(pdir, "Golden_山雨信").create_binding_async(payload)`：
  ```python
  {"shot_id": "s01_c01", "scene_id": "s01",
   "character_bindings": [{"character_name": <镜中出场角色>, "look_id": <该镜 Look>}],
   "scene_binding": {"scene_instance_id": <该场景实例>, "location_view_id": <该地点 hero view>}}
  ```
- 镜→Look 映射：S1-S4 凌用 uniform_main；S5-S7 用 travel_cloak；黑衣客 night_cloak；掌柜 innkeeper_grey。空角色镜（establishing/insert）不带 character_bindings。
- binding_readiness 必须 READY 才能生成（NOT_READY 会 fail-closed，按 hint 补前置）。
- 大建立镜头（无角色）同样 binding（scene_binding 提供场景参考）。

### 步骤 7：33 镜首帧生产（Agnes-only）
- `POST /api/project/Golden_山雨信/shot/{shot_id}/first-frame`，body `{"variants": 1}`（BOUND 链路自动带 visual profile 指令 + approved 参考 pack；不要传 image_provider 或传 "agnes"）。
- 轮询新批次（参考 `_acc_p5_shots.wait_batch`）→ `first-frame/choose` 选第一候选。
- 失败处理：provider 错误重试 1 次；再失败按规则 2（记录保留）。审查不适用于分镜（分镜无 vision review 环节），质量人工判断进 Gallery。
- 顺序按 s01_c01 → s07_c04 线性推进；每镜完成即 evidence。

### 步骤 8：关键镜尾帧（抽查链路，非全部）
- 选 2-3 个有动作的镜（s04_c05、s06_c04）设 `link_mode="keyframes"`（ProjectStore 直改）→ `POST last-frame`（body 可省或 `{"image_provider": "agnes"}`）→ 轮询 last_frame_batches。
- ⚠️ 首帧候选必须有公网 URL（Agnes 生成自带；若需手动补走 `first-frame/rehost`）。

### 步骤 9：Gallery + 报告
- 改造 `_acc_p6_gallery.py` → `_golden_gallery.py`：按 场景→镜 顺序连续浏览（每镜卡片显示：图、compiled prompt（batch.prompt_snapshot）、basis（image_provider/visual_profile_fingerprint/resolved refs）、review 结论、人工评注空栏）；资产区（角色三视图/Look/Prop/Composition）与场景区（多视角+sciv）单独分区。
- 写 `GOLDEN_RUN_REPORT.md`（仓库根），必须回答：
  1. 当前图片系统是否已能支撑真实规模的单风格项目（以实际运行证据）；
  2. Agnes 在完整高质量 prompt + approved reference 链下的实际表现；
  3. 失败归因分层：系统 bug / prompt-reference 问题 / Agnes 模型边界（各列证据）；
  4. 场景参考进入 Shot 后是否真的导致质感下降（对比同镜有无参考的表现）；
  5. 33 镜连续生产中角色、场景、风格一致性的观察结论；
  6. 是否还有必要对 SenseNova 做公平重测（**注意：在 Agnes 基线冻结前不得得出新的 Provider 路由结论**）。
- 报告必须保留失败结果与未通过审查的资产，不粉饰。

## 六、已知的坑（上一轮实测，务必记住）
1. Agnes 图片 503"文生图队列已满"= 服务端拥堵，`AgnesClient._request` 会自动指数退避重试（最多 5 次）；脚本层无需额外重试图片调用。
2. Agnes t2i seed ∈ [-1, 999]，传大值 400；i2i 大 seed 被放行（疑似忽略）。
3. `first-frame/last-frame` 的 Candidate.url 对 Agnes 恒有值；`choose` 需要 candidate_id 跨批次查询。
4. binding relation 枚举：`attached/carried/held/near/worn`；anchor 用 `right_hand/left_hand` 等。
5. 服务层脚本与运行中后端并发写同一项目有进程内锁隔离——**Golden 脚本只操作 `Golden_山雨信`，不要动其它项目**；跑 pytest 前停后端。
6. look/composition/location 的 `expected_revision` 从当前 canonical 现读（参考 `_acc_common` 封装的现读模式），OCC stale → 重读重试。
7. 敏感内容过滤：Agnes 拒绝某些组合词（历史记录：`pure/solid white background`）；prompt 措辞避开。

## 七、进度汇报格式
每完成一个步骤，输出一行状态：`[步骤N] PASS/FAIL 摘要（张数/耗时/失败明细）`。全部完成后输出报告路径 + Gallery 路径 + 六问答案。
