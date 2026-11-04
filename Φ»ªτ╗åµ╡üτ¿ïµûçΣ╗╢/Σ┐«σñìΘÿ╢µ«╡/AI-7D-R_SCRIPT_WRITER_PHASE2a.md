# AI-7D-R · Script Writer Phase 2a — `story.create` 单故事段工具

> 范围：Phase 1（`script.update` + 乐观并发锁，用户手测 PASS）之上的第二块积木。
> 阶段定位：Script Writer 读剧本 → 判断某段该成故事段 → 创建**一个**结构化故事段。
> 只证明 **单次 `story.create` 是可靠积木**（新 ID / 顺序 / 字段 / canonical 写 / 并发）。
> **完成后 STOP，不做批量拆分 / story.update / delete / 多工具自由循环（那是 2b~3）。**

---

## 0. 一句话结论

- `story.create` 工具已落地：复用中性层 `insert_scenes_after(proj, None, [spec])`（追加末尾、ID 后端确定性分配、不动已有段），原子写 canonical + audit + work memory。
- **两个独立乐观并发令牌**（字段级、模块边界）：`script.update` 校验 `story_text` 指纹；`story.create` 校验 `scenes` 指纹（`sha256` over scene_id 有序列表 前 8 位，结构指纹：增/删/重排会变，段内 summary/characters 改动不影响）。
- stale 拒绝 = **不分配 ID、不写盘、不审计**，返回当前 scenes 薄快照 + current_revision 供 Agent 重新判断（镜像 `script.update` 的 stale 流）。
- 测试：`test_ai_7d_r_script_writer_native.py` **17 passed**（9 原有 + 8 新增）；回归 D1/D2/D3/R1/director_min/script_writer_min **88 passed**。**0 回归。**

---

## 1. 关键发现（复用，不重造）

`src/storyboard/structure_service.py`（中性层，agent 无关，已存在）：
- `allocate_scene_id(project)` — `s{NN:02d}` 确定性分配。防御性重算 `next_scene_seq = max(seq, max(used)+1)`，取号后自增，**删除不回收**。→ 满足「LLM 不生成 ID」。
- `insert_scenes_after(project, after_scene_id, specs)` — `after_scene_id=None` 追加末尾，构造 `Scene(scene_id, title="", summary, characters, shots=[])`，切片插入 `project.scenes`，**不动已有段**。→ 满足「顺序/不覆盖」。
- 顺序模型：段无 `order` 字段，权威顺序 = `project.scenes` 数组位置。

数据模型（`src/core/models.py`）：`Scene{scene_id, title, location, time_of_day, summary, characters, shots}`；`Project.scenes: List[Scene]` + `Project.next_scene_seq: int`（默认 1，`from_dict` 缺失时按已用最大 +1 兜底）。

测试用水位：`_proj()` 有 1 段 `s01`（`next_scene_seq=1`）→ 首个新段 = `s02`（`max(1,2)=2` → 返回 `s02` → `next_scene_seq→3`）；第二段 = `s03`（`max(3,3)=3` → `s03` → `4`）。

---

## 2. 设计决策

1. **保留 `script.update`，新增 `story.create`**（SW 工具集 = 2 个）。Phase 1 能力不丢；原 9 测试只断言 `tools == SCRIPT_WRITER_TOOLS`（列表对象）+ observation/canonical/audit，0 回归。
2. **字段集 = `summary`(必填) + `characters`(可选 list[str])**，与 `insert_scenes_after` 的 spec 形状一致 → 直接复用中性层，不重造插入逻辑。`title/location/time_of_day` 默认 `""`，留给 Phase 2c。
3. **并发令牌 = `_scenes_token(scenes)`** = `sha256(json.dumps([s.scene_id for s in scenes]))[:8]`。字段级（只哈希 scene_id 有序列表）：catch 增/删/重排，不受 story_text 编辑影响（模块边界）。`story.create` 只校验 `expected_scenes_revision`；`script.update` 继续只校验 `expected_revision`。**两令牌独立。**
4. **位置 = 后端决定，追加末尾**。LLM 不传 position/after。新段恒为末尾。2a 不支持中间插入（那是 2c）。
5. **stale 拒绝 = 不分配 ID、不写盘、不审计**（在 `allocate_scene_id` 之前 return），返回 `current_scenes` 薄快照 + `current_revision`（镜像 `script.update` stale 流）。
6. **每轮无条件刷新 system**（从只刷 story_text 改为刷整个 proj → 两令牌都最新）——成功 / stale / no-op / 错误喂回都需 Agent 基于最新 canonical 继续。

---

## 3. 改了什么（仅 2 文件）

### `src/agents/script_writer.py`

- **新增 import**：`from src.storyboard.structure_service import insert_scenes_after`。
- **新增 `_scenes_token(scenes)`**：`sha256(json.dumps([s.scene_id for s in (scenes or [])]))[:8]`。
- **新增 `_scenes_listing(scenes)`**：每段 `- s01：summary[:60]…〔chars〕`，注入 system。
- **`_build_system` 改签名** `_build_system(story_text)` → `_build_system(proj)`：注入剧本正文+`{revision}`（不变）+ `{scenes_count}`+`{scenes_revision}`+`{scenes_listing}`。空正文显示「（空）」但指纹按真实空串算。
- **`SCRIPT_WRITER_SYSTEM` 扩展**：两工具说明 + stale 重判断指令 + 末尾「当前故事段（共 {scenes_count} 段，版本指纹 {scenes_revision}）：\n{scenes_listing}」。
- **新增 `STORY_CREATE_TOOL` schema**：`summary`(string, required) + `characters`(array<string>, optional) + `expected_scenes_revision`(string, required)。
- **新增 `_execute_story_create`**（镜像 `_execute_script_update`）：
  1. 解析 JSON（坏 → `AgentError("bad_arguments")`）。
  2. `summary` 必填非空 str（否 → `bad_arguments`）；`strip()`。
  3. `characters` 可选；非 None 必须是 list（否 → `bad_arguments`）；`[str(c) for c in (characters or [])]`。
  4. `expected_scenes_revision` 必填非空 str（否 → `bad_arguments`）。
  5. load canonical → `current_rev = _scenes_token(proj.scenes)`。
  6. **stale**：`if expected_rev != current_rev: return {ok:False, stale:True, expected_revision, current_revision, current_scenes:[{scene_id, summary[:80]}...], note}`（不写不审计不分配 ID）。
  7. **create**：`before_ids=[s.scene_id for s in proj.scenes]`；`new = insert_scenes_after(proj, None, [{summary, characters}])[0]`；`pstore.save(proj)`。
  8. **audit**：`field="scenes"`, `before=before_ids`, `after=new.to_dict()`。
  9. **work memory**：`affected_domains=["storyboard"]`, `references={"scene_id":new.scene_id}`。
  10. **return**：`{ok:True, scene_id, order:len(proj.scenes), scene:new.to_dict(), scenes_revision, note}`。
- **`TOOL_EXECUTORS`** 加 `"story.create": _execute_story_create`。
- **`_run_tool_loop` 改**：`story.create` 成功 → `created=True` + `events.append({"kind":"story_create","scene_id","summary"})`；保留 `script.update` 的 `edited`/`events`；无条件刷新 system 改为 `_build_system(proj)`（两令牌都最新）；返回 `{text, edited, created, summary, events}`。
- **`script_writer_turn` 改**：`system=_build_system(proj)`；`final` 解构加 `created`/`events`；`intent = "story_create" if created else ("script_update" if edited else "conversation")`；返回 dict 加 `created`（additive）。

### `tests/test_ai_7d_r_script_writer_native.py`

- import 加 `_scenes_token` + `SimpleNamespace`。
- 新增 helper：`_scenes_rev(tmp_path)` = 当前 canonical scenes 指纹；`_scenes_token_of_ids(ids)` = 由 id 列表预算指纹（用于循环中途刷新后的令牌）。
- **8 类测试**（均 ScriptedClient，断言 canonical/audit/work_memory/observation）：
  1. **创建成功**：`created is True`；canonical `scenes[-1].scene_id=="s02"` 且 summary/characters 写入；`next_scene_seq==3`；audit `field=="scenes"`、`before==["s01"]`、`after.scene_id=="s02"`；work_memory 有「故事段」；observation 含 `scene_id/order/scene/scenes_revision`；2 次 LLM 调用。
  2. **ID 确定性 + 单循环内连续 s02→s03**：`[s01,s02,s03]`；`next_scene_seq==4`；两 `story_create` 事件按序（第二步用刷新后 token `_scenes_token_of_ids(["s01","s02"])`）。
  3. **顺序**：原段仍在前、字段/镜头不变；新段末尾、无镜头；`order==2`。
  4. **字段校验**：空 summary / characters 非列表 / 缺 expected_scenes_revision / 坏 JSON → 全 `bad_arguments` 作 error obs 喂回，0 写盘，Agent 继续到成功创建。
  5. **并发冲突**：`expected_scenes_revision="deadbeef"` → `created is False`、canonical 未变（仍 1 段）、**`next_scene_seq` 未自增**（stale 不分配 ID）、0 audit、stale obs 含 `current_revision`+`current_scenes` 快照、2 次调用。
  6. **工具错误喂回**：未知工具 `story.delete` → `{ok:False,error:"未知工具"}`，0 写盘，Agent 继续到自然语言。
  7. **0 非预期 mutation**：纯对话 → canonical 文件字节不变、scenes/story_text 不变、1 次调用。
  8. **回归守卫**：`SCRIPT_WRITER_TOOLS` 列表对象含 `["script.update","story.create"]`（原 9 测试依赖此对象）。

#### 测试坑（已修）

`ScriptedClient.chat_completion` 存的是 `messages` 列表**引用**，而 `_run_tool_loop` 对同一列表**原地 `append`** → 所有 `client.calls[i]["messages"]` 指向同一份最终列表。flatten `[m for c in client.calls for m in c["messages"] ...]` 会重复计数（6 calls × 5 tool msg = 30）。修法：取 `client.calls[-1]["messages"]`（最后一份即完整累积列表）。

---

## 4. 三层薄约束（不变，对齐 Agents SDK tool guardrail）

```
工具前：权限（仅 script_writer）+ 参数（summary 非空 + characters list + expected_scenes_revision 非空）+ 目标（项目存在）
工具执行：乐观并发校验（scenes 域）→ 中性层追加 → 原子写 canonical + 修改前快照/audit
工具后：返回真实 observation（成功/stale/错误），让 Agent 自己判断是否继续
```

- story 域 OCC 是「工具自身正确性保护」，不是协议仪式。
- 参数错误抛 `AgentError` → runner 转成 tool 错误 observation 喂回，**不静默假成功**。

---

## 5. 验证

```
test_ai_7d_r_script_writer_native.py     17 passed   (9 原有 + 8 新增)
test_ai_7d_r1_conversation_memory.py       ✓ (R1 回归)
test_ai_7d_d1_unified_turn.py             ✓ (D1 回归)
test_ai_7d_d2_change_bridge.py            ✓ (D2 回归)
test_ai_7d_d3_runtime_control.py          ✓ (D3 回归)
test_ai_7d_r_director_min.py               ✓
test_ai_7d_r_script_writer_min.py          ✓ (legacy)
---------------------------------------- ---------
合计                                      17 + 88 = 105 passed，0 回归
```

---

## 6. 残留 / 已知

- 改了 `src/agents/script_writer.py` → **需重启后端**才在前端生效（CLAUDE.md 规则：改 `src/agents/*` 属后端代码）。
- 两令牌字段级独立：将来 Phase 2c（故事段修改）改 summary/characters **不会**触发 scenes 指纹变（只哈希 id 列表）——若 2c 需要段内字段级 OCC，可加 `_scene_field_token`，但 2a 不需要。
- 本阶段 **STOP**。下一阶段 2b（批量剧本拆分：SW 连续调 `story.create`）等用户验收 2a 后再开。
