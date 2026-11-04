# AI-7D-R · Script Writer — 按需读取 + 职责对齐 + 任意位置插入/move

> 接 Phase 2a（单次 `story.create` 已稳定）。本阶段 3 项（用户钦定，按序）：
> 1. 按需读取，去掉完整 story_text/scenes 长期注入 system；
> 2. Script Writer 职责认知对齐（剧本正文+故事段结构；Director 管镜头）；
> 3. 故事段位置能力（任意位置插入 + story.move）。
> **不含**：整篇批量拆分（2b）、故事段内容修改（2c）、删除（2d）、自由多工具（3）。完成即 STOP。

---

## 0. 一句话结论

- Script Writer 工具集从 2 个扩到 6 个：读 `script.read` / `story.list` / `story.read`；写 `script.update` / `story.create` / `story.move`。
- **system prompt 瘦身**：不再注入完整 story_text 或 scenes 全文，只保留职责/权限/工具说明 + 项目元信息（项目名/正文字数/段数）。正文与段列表由 Agent 按需 `read`/`list` 获取。
- **OCC 保留**：`script.update` 校验 story_text 指纹；`story.create`/`story.move` 校验 scenes 结构指纹（scene_id 有序列表）。指纹由 read/list 工具和写工具的成功 observation 返回。
- **位置能力**：`story.create` 支持 `before_scene_id`/`after_scene_id`（都省略=末尾，`before=首段`=最前）；新增 `story.move(scene_id, before/after)` 任意重排，不改 id、不动段内镜头。
- 中性层 `structure_service.py` 新增 `insert_scenes(before=, after=, specs=)` 与 `move_scene()`；**保留旧 `insert_scenes_after` 不动**（director_min 仍在用）。
- 测试：`test_ai_7d_r_script_writer_native.py` **36 passed**；含 structure_service/director_min/script_writer_min 的回归 **151 passed，0 回归**。

---

## 1. 按需读取（项 1）

### 为什么
Phase 2a 把完整 story_text + scenes 列表长期塞进 system prompt。问题：
- 长剧本/多段时每轮都重发全文，token 浪费、延迟增加；
- system 里的快照会过期，Agent 容易拿旧正文/旧段列表做判断。

### 改法
- `_build_system(proj)` 只输出：职责声明 + 项目元信息（`{project_name}`/`{story_len}`/`{scenes_count}`）+ 工具使用规则。**不含正文、不含段摘要全文**。
- 新增 3 个只读工具：
  - `script.read()` → `{ok, text, length, revision}`（正文全文 + story_text 指纹）。
  - `story.list()` → `{ok, count, revision, scenes:[{scene_id, summary(截断120), characters, shot_count}]}`（段列表 + scenes 指纹）。
  - `story.read(scene_id)` → `{ok, scene:{scene_id, summary(全文), characters, shot_count}}`；段不存在抛 `not_found`。
- 写工具的 `expected_revision` / `expected_scenes_revision` 由 Agent 从最近一次 read/list 的返回值里取（system prompt 明确指示先 read/list 再写）。
- 每轮工具调用后仍**无条件刷新 system**（项目计数会更新，如段数变化），但不重发正文。

> 设计取舍：token 仍由 read 工具的 observation 携带（在 messages 里），但只在 Agent 真正需要的那轮取，不再每轮 system 重发。

---

## 2. 职责认知对齐（项 2）

system prompt 首段明确：
- Script Writer 负责【剧本正文】+【故事段结构】（Scene 的增删与排序）；
- **故事段之下的镜头（Shot）由 Director 负责，SW 不创建或修改镜头**。

`src/agents/registry.py` 里 script_writer 的定义本就是 `description="负责故事/场景/剧情结构与修改"`、`role="剧本"`，director 是 `场景→镜头、分镜结构`，边界已正确，未改。
grep 全 `src/` **未发现**把故事段归给旧 "Storyboard Editor / 分镜编辑" 的残留提示（legacy 代码未删，仅确认无错误归属）。

---

## 3. 位置能力（项 3）

### 中性层 `src/storyboard/structure_service.py`
- 抽出内部 `_build_and_insert(project, insert_at, specs)` 与 `_resolve_anchor_index(project, before, after)`。
- 新增 `insert_scenes(project, *, before=None, after=None, specs=None)`：before/after 都空=末尾；同时给=invalid；锚点不存在=not_found。
- 新增 `move_scene(project, scene_id, *, before=None, after=None)`：pop+insert 重排；before/after 同时给或指向自身=invalid；段/锚点不存在=not_found；不改 id、不碰段内镜头。返回被移动的 Scene。
- **旧 `insert_scenes_after` 原样保留**（director_min:328 与现有测试在用），内部委托给 `_build_and_insert`，行为不变。

### 工具层 `src/agents/script_writer.py`
- `story.create` 参数加 `before_scene_id?` / `after_scene_id?`：
  - 都省略 → `insert_scenes(proj, specs=[...])` 追加末尾；
  - `before=s01` → 插到最前；`after=s0X` → 插到某段后；`before=s0X` → 插到某段前。
  - 成功 observation 返回 `order`（1-based 新位置）+ `scene` + `scenes_revision`。
- 新增 `story.move(scene_id, before_scene_id?, after_scene_id?, expected_scenes_revision)`：
  - 都省略 → 移到末尾；
  - 成功返回 `{ok, scene_id, order, scenes_order, scenes_revision}`；
  - audit `field="scenes"`, `before`=旧 id 顺序, `after`=新 id 顺序；
  - record_work `affected_domains=["storyboard"]`。
- create/move 都走 scenes OCC：stale 时不写盘/不分配 ID/不审计，返回 `current_scenes` 快照 + `current_revision`。
- `script_writer_turn` 返回加 `moved`；`intent` 三态写 → `story_create`/`story_move`/`script_update`/`conversation`；events 记 `story_move`。

---

## 4. 测试（`tests/test_ai_7d_r_script_writer_native.py`，36 passed）

新增（在原 17 之上）：
- 按需读取：`script.read` 返回全文+指纹；`story.list` 返回列表+scenes 指纹；`story.read` 返回单段、不存在 not_found。
- 0 非必要上下文注入：`_build_system` 不含完整正文、不含段摘要全文，只含项目名/字数/段数/职责边界（Director）。
- 任意位置插入：before 锚点 / after 锚点 / 最前（before=s01）/ 默认末尾，断言顺序与 order。
- move 首/中/尾：s03→最前、s03→s01 后、s01→末尾，断言 `scenes_order` 与 order。
- 非法目标：create 同时给 before+after=invalid；create 锚点不存在=not_found；move 自身=invalid、before+after=invalid、段不存在=not_found；全部 0 写盘 0 audit。
- stale：create/move 传错指纹 → stale obs、不写盘、**create 不分配 ID（next_scene_seq 不变）**、0 audit。
- 端到端：turn 内 `script.read→script.update`（验证 system 不含正文）、`story.list→story.create→story.move`。

回归：`test_structure_service.py` + `test_ai_7d_r_director_min.py` + `test_ai_7d_r_script_writer_min.py` + D1/D2/D3/R1 全绿，合计 **151 passed，0 回归**。

---

## 5. 残留 / 已知

- 改了 `src/agents/script_writer.py` + `src/storyboard/structure_service.py` + `config/config.yaml` → **需重启后端**才生效。
- **API 端点变更（待用户给可用 key）**：`config/config.yaml` 的 `base_url` 已按用户指示从 `https://apihub.agnes-ai.cn` 改为 `https://api.agnes-ai.cn`（不带 /v1，客户端自动拼）。本地实测：当前 `.env` 里的 key 在新地址 `/v1/chat/completions` 与 `/v1/models` 都返回 `401 无效的令牌`（旧 `apihub` 地址同 key 可用但源站变慢/520）。用户表示稍后提供新 API。**重启后若新 key 仍 401，所有文本 Agent 会无法回复——届时把 base_url 改回 `https://apihub.agnes-ai.cn` 可恢复（慢但可用）。**
- 下一步 **2b（整篇剧本批量拆分）等用户验收本阶段后再开**。
