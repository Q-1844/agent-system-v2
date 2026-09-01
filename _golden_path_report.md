# Golden Path 全流程真实验收 · 最终报告

> 2026-09-01 · 真实用户+总操作员+验收工程师身份，人工替代 Orchestrator，通过真实 Agent tool-loop 驱动三个 Agent 围绕同一 Project canonical 组成动画生产链。

## 1. 项目信息

| 项 | 值 |
|---|---|
| Project name | `Golden_Path_AI_20260901_1132` |
| Project path | `animation-storyboard/projects/Golden_Path_AI_20260901_1132/output/project.json` |
| 创建时间 | 2026-09-01 11:32 |
| 作品名 | 《记忆盒》(Memory Box) |
| 如何打开 | 前端 Vite dev @ `http://localhost:7777`，在项目列表选 `Golden_Path_AI_20260901_1132`；后端 @ `http://localhost:8000`。canonical 见上述 project.json，资产在同级 `characters/`、`frames/`、`videos/` 目录 |

## 2. 最终规模

| 项 | 数量 |
|---|---|
| Story Scenes | **4**（s01-s04） |
| Characters | **2**（阿默、小铃） |
| Character 三视图 | **6**（2角色×3视图，front/side/back 全部落盘 30.1MB 含图片） |
| Shots | **15**（s01:3, s02:4, s03:4, s04:4） |
| Shot 首帧图片 | **30 文件**（15镜头×2候选变体，共 30.1MB，均有 selected_first_frame） |
| 真实视频 | **4**（s01_c03 3.4s/660KB, s02_c04 6.0s/982KB, s03_c02 7.0s/2.76MB, s04_c03 4.0s/796KB，共 20.4s） |
| Agnes 配额消耗 | video 20.5/300s, images 21/500, text 32/2000 |

## 3. Agent 执行（关键真实 tool trace）

### 阶段A · Script Writer（turn 45s）
`script.read → story.list → script.update(0→760字) → story.create s01 → story.create s02 → story.create s03 → story.create s04 → story.list(verify) → script.read(verify) → turn.completed`
- 自主创作《记忆盒》：近未来记忆修复师阿默、小女孩小铃带奶奶藏在旧怀表里的记忆芯片来修，阿默通宵修复，记忆片段中祖孙温馨过往，最终小铃看到奶奶投影重新"活"过来。贯穿道具=旧怀表。
- receipts: 5/5（script.update + 4×story.create）全 verified。写串行 fence 跳过并行多写属正常。

### 阶段B · Character Designer（turn 147s）
`story.list → script.read → character.list(空) → character.create(版本过期拒×2) → character.list(verify) → character.create 阿默 ✓ → character.create 小铃 ✓ → character.generate_views(2/2) → turn.completed`
- 跨域读取：自主用 story.list/script.read 读故事（未复制内容给它）✓
- 阿默（28-30岁记忆修理师，深棕短发圆放大镜眼镜灰蓝衬衫 desc_en 503字）+ 小铃（7-8岁深棕双辫浅蓝连衣裙白围裙 desc_en 497字）
- 6/6 三视图落盘，全 status=done + url(imgbb)。2 次前置 character.create 被真相闸门"版本过期"拒绝后自重读恢复。

### 阶段C · Director 文本分镜（turn ~155s）
`story.list → character.list → story.read×4(s01-s04) → character.read(阿默 ok/小铃 ok/奶奶 FAILED 角色不存在) → shot.list(空) → shot.create_many×4(s01:3/s02:4/s03:4/s04:4=15) → shot.list(verify) → turn.completed`
- 跨域读取：自主读 story+character ✓。奶奶读取失败但优雅处理未崩溃，且**未在 shot 引用不存在的奶奶**（所有 shot 角色引用 ∈ {阿默,小铃}）。
- **自然使用批量工具** ✓：shot.create_many 4 批按场景。
- 15 shots，shot_id 全唯一，scene 归属/index 顺序全正确。景别变化(medium/close-up/extreme close-up/wide)、镜头运动变化(slow push in/static/pan right)、对白 inline 在 description。

### 阶段D · Director 真实分镜图片（turn 150s）
`shot.generate_images(批1 s01 21.4s) → 批2(s02 37.7s) → 批3(s03) → 批4(s04) → turn.completed`
- 真实 Agnes 图片生成，4 批按场景，progress 事件正常流动。
- 30/30 图片文件落盘（30.1MB，每 shot 2 候选变体 + selected_first_frame）。receipts 4 批全 verified count=4。

### 阶段E · Director 真实视频（turn 401s）
`shot.generate_videos(s01_c03/s02_c04/s03_c02/s04_c03 共4，串行) → turn.completed`
- Director 自选每场景1个代表镜头（有动作/镜头运动）。4/4 视频全 status=done，本地 mp4 落盘 + 远程 URL。receipt verified count=4。

## 4. Golden Path 结果

| 域 | 结果 | 证据 |
|---|---|---|
| Story | **PASS** | 4 Scene，story_text 760字，receipts 5/5 verified |
| Character | **PASS** | 2 角色，desc 详尽，receipts verified |
| Views | **PASS** | 6/6 三视图落盘 30.1MB，全 status=done+url |
| Storyboard | **PASS** | 15 shots 全唯一 ID/归属/index 正确，景别+运动+对白 |
| Images | **PASS** | 30/30 图片落盘，receipts 4 批 verified |
| Videos | **PASS** | 4 真实视频 LOCAL_OK + URL，共 20.4s |
| Cross-Agent integrity | **PASS** | 全程 hash 追踪：story/scenes/characters 在各自创建后跨阶段零变化，无越权写 |

**整体：PASS** —— 三个独立 Agent 已能围绕同一 Project canonical 组成真正可用的动画生产链。

## 5. 自动发现的问题

### Blocker
无。Golden Path 全程未遇阻断性 Bug。

### Important
1. **QueueFull 终端事件丢失（已修复）** —— `routers/agents.py` 的 `event_gen._run` 中 turn.completed/turn.failed/SENTINEL 用 `q.put_nowait`，队列满(512)则 QueueFull 静默丢，消费者永挂 q.get()、driver 漏收 turn.completed、看门狗误触发。阶段C 真机触发（turn 已结束 locks=[] 但 driver 悬挂）。**已最小修复**（见第6节）。阶段D/E 修复后 driver 正常收 turn.completed（150s/401s），证明修复生效。

### Minor
2. **legacy_video 版本永留 status=generating** —— 每个视频 shot 的 video_versions 含一个 version_id="legacy_video" 的占位条目（created_at=0），视频提交时置 generating 但永不收敛到 done，真实结果落在新建的 vv_* 版本（status=done，final_video 正确指向）。JobQueue 无 stuck 任务，final_video 指向正确——非功能问题，是预存 settler 模式的元数据冗余。建议后续 settler 收口时把 legacy_video 槽也置 done 或不创建。

3. **dialogue 字段未结构化** —— Director 把对白 inline 写在 description 字段（如 `小铃："奶奶说她的记忆盒能修好"`），shot 的独立 `dialogue` 字段为空。内容完整（对白存在），但未进结构化字段，后续 TTS/字幕管线可能需从 description 解析。非阻断。

### Observation
4. **奶奶跨域引用摩擦** —— 故事 scene.characters 列表含"奶奶"，但 Character Designer 只建了2主角（按指示次要角色仅记忆片段不出三视图）。Director 读 character.read 奶奶→FAILED 角色不存在。Director 优雅处理并未在 shot 引用奶奶。这揭示 canonical 作为跨 Agent 共享媒介时，一个 Agent 的范围决策（CD 不建奶奶）会与另一个 Agent 的输入（story 引用奶奶）产生轻微摩擦——但真相闸门 + Director 自适应让链路未断。可接受。

5. **Driver 长视频轮询** —— 阶段E 4 视频 401s，driver 持续接收 progress 事件无看门狗误触发，表现稳定。语义不活跃看门狗 420s 阈值合理（视频单镜最长 ~3min，progress 间隔远小于420s）。

## 6. 做过的代码修改

**1 处最小修复**（`animation-storyboard/routers/agents.py`）：

- **文件**：`routers/agents.py`，`agent_turn_stream` 的 `event_gen` 内 `_run` 闭包。
- **原因**：终端事件（turn.completed/turn.failed/SENTINEL）`q.put_nowait` 遇 QueueFull 静默丢 → 消费者永挂、driver 漏收 turn.completed。阶段C 真机触发。
- **改动**：新增 `_put_terminal(ev)` 辅助（队列满则先清空 unconsumed 事件再入队，保证消费者必收终端信号；终端时刻丢弃未消费的细粒度事件 progress/assistant.delta 安全——消费者只要终端信号，与 _cb 的 overflow drop 同语义但保证不丢终端），替换 3 处终端 `put_nowait`。
- **风险**：局部、低风险（仅改终端事件入队路径，不影响锁释放/`_ACTIVE_TURN_EVENTS.pop`/正常事件流/_cb 回调）。
- **重新验证**：已重启后端（PID 28408）。阶段D（150s）、阶段E（401s）driver 均正常收到 `[turn.completed]`，无悬挂，修复生效。
- **未改**：未碰 Agent 架构、canonical 权限、Bridge/Orchestrator、状态机。driver/verify 脚本（`_golden_path_driver.py`、`_gp_verify_assets.py`）为本次验收新建的测试工具，非生产代码。

## 7. 对下一阶段的证据（仅依据本次真实 Golden Path）

- **Director 上下文是否膨胀？** —— 本次 15 shots 规模下**未明显膨胀**。Director 读 story 用 story.read×4（每段一次，略有冗余但可控），读 character 用 character.read×3（含1次失败探测奶奶）。shot.create_many 按场景批量创建，无逐 shot 读取。assistant 总结适中。**但**规模仅 15 shots/4 场景，无法证明 40+ shots 长片下是否膨胀——本次数据不足以下定论，需更大规模验证。

- **Scene Director Sub-Agent 实际需求？** —— 本次**未出现明确需求**。Director 单 turn 完成分镜(155s)、图片(150s)、视频(401s)均在单会话单 tool-loop 内完成，无上下文溢出、无迷失。批量工具（create_many/generate_images/generate_videos）已有效分摊。**本次不构成 Sub-Agent 的必要证据**。

- **Continuity Checker 需求？** —— 本次 Director 自行保证了 scene 内连续性（index 顺序、角色引用一致）和 scene 间叙事连续（景别/运动渐变）。未发现需要独立 Continuity Checker 的断裂。**本次不构成需求证据**。

- **Orchestrator 需求？** —— 人工切换三 Agent 全程**顺畅**：canonical 充当共享媒介，每 Agent 自主跨域读取（story/character/shot list+read），无需人工复制上下文。**摩擦点很小**（仅奶奶引用1处，且自愈）。本次规模下**未明显需要 Orchestrator**——但这是人工替代的结果；若要自动化无人值守生产，Orchestrator 仍是必要（决定何时切换 Agent、传递"已完成"信号）。本次证明的是"canonical+人工切换"链路本身可行，而非 Orchestrator 不需要。

- **三个 Agent 仅靠 canonical+人工切换是否顺畅？** —— **是，顺畅**。Script Writer→Character Designer→Director 三阶段，每阶段只给自然语言目标+规模约束，Agent 自主读 canonical、自主选工具、自主批量、自验证。跨域 hash 追踪证明零越权。canonical 已足够担当 Agent 间共享媒介。

- **最大的真实摩擦点？** —— ① Agent 范围决策与故事输入的不一致（奶奶：story 引用但 CD 未建，Director 读取失败）——需 Agent 间就"哪些实体需要 character card"达成约定，或 Orchestrator 预协调。② terminal-event QueueFull（已修）。③ 长视频 turn 6.7min，若无 SSE progress 会触发看门狗阈值边缘（本次未触发，因 progress 正常）。

---

## 最终现场保留
- ✅ 未删除 Golden Path Project（用户亲自检查）
- ✅ 未删除图片/视频
- ✅ 未覆盖用户原有项目
- ✅ 无 git reset / git clean / git commit
- 后端 PID 28408、前端 Vite 仍在运行，用户可直接打开 `localhost:7777` 检查

## 完成条件核对
- [x] 新项目真实存在
- [x] Story 真实完成（4 Scene, receipts verified）
- [x] Character 真实完成（2 角色, receipts verified）
- [x] 三视图真实落盘（6/6 文件）
- [x] Storyboard 真实完成（15 shots 全结构正确）
- [x] 分镜图片真实落盘（30/30 文件）
- [x] 至少若干真实视频落盘（4 个 LOCAL_OK mp4）
- [x] canonical 对账完成（全阶段 hash 追踪零越权）
- [x] locks 清空（locks=[]）
- [x] 最终报告完成
