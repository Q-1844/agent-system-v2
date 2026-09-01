# Golden Path 全流程真实验收 · 进度记录

> 本轮身份：真实用户 + 总操作员 + 验收工程师。人工替代 Orchestrator，通过真实 Agent tool-loop 驱动三个 Agent 围绕同一 Project canonical 组成动画生产链。禁止绕过 Agent 直接改 canonical。

## 项目信息
- **Project name**: `Golden_Path_AI_20260901_1132`
- **Project ID/path**: `animation-storyboard/projects/Golden_Path_AI_20260901_1132/output/project.json`
- **创建时间**: 2026-09-01 11:32
- **后端**: localhost:8000（11:28:05 startup，脱离式启动器）
- **前端**: localhost:7777（Vite，用户后续可打开检查）
- **Agnes 配额（起跑）**: images 0/500, video 0/300s, text 0/2000

## 阶段进度
- [x] 阶段0：理解项目环境
- [x] 阶段A：Script Writer 创作故事（约4 Scene）— PASS
- [x] 阶段B：Character Designer 设计角色+三视图 — PASS
- [x] 阶段C：Director 文本分镜（约14-20 Shot）— PASS
- [x] 阶段D：真实分镜图片 — PASS
- [x] 阶段E：真实视频（每 Scene ≥1，约4个）— PASS（4 视频 LOCAL_OK 共20.4s）
- [x] 阶段F：最终完整性检查+报告 — PASS（_golden_path_report.md）

## 最终状态（2026-09-01 12:15 完成）
- **整体 Golden Path：PASS** —— 三 Agent 围绕同一 canonical 组成可用动画生产链
- Scene=4, Character=2(+6 views), Shot=15, 图片=30文件, 视频=4
- 跨域 hash 追踪全程零越权（story/scenes/characters 各阶段不变）
- locks=[]，JobQueue 0 stuck
- 1 处最小代码修复（QueueFull 终端事件，routers/agents.py，已重启验证生效）
- 现场保留：未删项目/资产，无 git reset/clean/commit，后端 PID28408+前端运行中

## 阶段A 结果（PASS · 2026-09-01 11:33, turn 耗时 45s）
- 故事：《记忆盒》——近未来记忆修复师阿默，小女孩小铃带来奶奶藏在旧怀表里的记忆芯片，阿默通宵修复，记忆片段中祖孙温馨过往，最终小铃看到奶奶投影重新"活"过来。贯穿道具=旧怀表（挂门→小铃带来→取芯片→投影）。
- Script Writer tool trace: script.read → story.list → script.update(0→760字) → story.create s01/s02/s03/s04（各第1-4位，串行 fence 跳过的并行多写属正常）→ story.list(verify) → script.read(verify) → turn.completed
- canonical: story_text=760字, scenes=4 (s01-s04), next_scene_seq=5
- scene chars: s01=[阿默], s02=[阿默,小铃], s03=[阿默,小铃,奶奶], s04=[阿默,小铃,奶奶]
- receipts: 5/5 (script.update + 4×story.create) ALL ok=True verified=True
- 越权检查: characters=0, shots=0（Script Writer 未写 character/shot 域）✓
- **基线 hash（跨域对账用）**: story_text=`957cf4eacc2923ed`, scenes_text=`e77c62373958b3a2`, characters=`4f53cda18c2baa0c`(空), shots=`4f53cda18c2baa0c`(空)

## 阶段B 结果（PASS · 2026-09-01 11:35, turn 耗时 147s）
- Character Designer tool trace: story.list → script.read → character.list(空) → character.create(版本过期拒×2,自重读恢复) → character.list(verify) → character.create 阿默 ✓ → character.create 小铃 ✓ → character.generate_views(2/2) → turn.completed
- 跨域读取: 自主用 story.list/script.read 读故事 ✓（未复制内容给它）
- 角色: 阿默（28-30岁，记忆芯片修理师，深棕短发，圆放大镜眼镜，灰蓝衬衫）+ 小铃（7-8岁，深棕双辫，浅蓝连衣裙白围裙），desc_en 各 503/497 字
- 三视图落盘: 6/6 文件 >1KB，阿默 front 483KB/side 313KB/back 357KB，小铃 front 370KB/side 302KB/back 341KB，全部 status=done + url(imgbb)
- receipts: character.create ×2 ok=True verified=True；character.generate_views ok=True verified=True（2 次前置版本过期拒 ok=False，真相闸门正常工作）
- 越权检查: story_text hash=`957cf4eacc2923ed`（未变 ✓），scenes_text=`e77c62373958b3a2`（未变 ✓），shots 未变 ✓
- 锁: 完成后 locks=[] ✓
- **hash 更新**: characters=`dbd816a7ad8b4f69`（Character Designer 写本域），story/shots 不变

## 阶段C 结果（PASS · 2026-09-01 11:41, turn 耗时 ~155s）
- Director tool trace: story.list → character.list → story.read×4(s01-s04) → character.read×3(阿默 ok/小铃 ok/奶奶 FAILED 角色不存在) → shot.list(空) → shot.create_many×4 批(s01:3/s02:4/s03:4/s04:4 = 15) → shot.list(verify) → 生成 assistant 总结 → turn.completed
- 跨域读取: 自主读 story+character ✓（未复制内容）；奶奶读取失败但优雅处理未崩溃
- 自然使用批量工具 ✓: shot.create_many 4 批，每批按 scene，15 shots
- 分镜: 15 shots（s01_c01-c03, s02_c01-c04, s03_c01-c04, s04_c01-c04），shot_id 全唯一，scene 归属正确，index 顺序全 OK(1..N)
- 导演内容丰富: 景别变化(medium/close-up/extreme close-up/wide)、镜头运动变化(slow push in/static/pan right)、动作(motion 字段)、对白 inline 在 description(小铃:"奶奶说她的记忆盒能修好")
- 角色引用: 全部 ∈ {阿默,小铃}，无 BAD_REF（Director 从奶奶读取失败学到，未在 shot 引用不存在的角色）✓
- 越权检查: story_text=`957cf4eacc2923ed`(未变✓), scenes_text=`e77c62373958b3a2`(未变✓), characters=`dbd816a7ad8b4f69`(未变✓) — Director 只写 shot 域
- receipts: write_success=4（4 批 create_many 全 verified）

## 代码修复 #1（2026-09-01 11:50）· routers/agents.py QueueFull 终端事件丢失
- **问题**: Phase C driver 漏收 turn.completed（locks=[] 证明 turn 已结束，但 SENTINEL 丢失致 driver 悬挂在 q.get()）。根因：`_run` 的 turn.completed/turn.failed/SENTINEL 用 `q.put_nowait`，队列满(512)则 QueueFull 静默丢，消费者永挂、看门狗误触发。
- **修复**: 加 `_put_terminal(ev)` 辅助（满则先清空 unconsumed 事件再入队），替换 3 处终端 put。终端时刻丢弃未消费的细粒度事件(progress/assistant.delta)安全——消费者只要终端信号。与 _cb 的 overflow drop 同语义但保证不丢终端。
- **风险**: 局部、低风险（仅改终端事件入队路径，不影响锁释放/_ACTIVE_TURN_EVENTS.pop/正常事件流）。已重启后端（PID 28408，11:53:32 startup）。
- **验证**: 后续 Phase D/E 若 driver 正常收到 turn.completed 即证明修复生效。

## 阶段D 结果（PASS · 2026-09-01 12:00, turn 耗时 150s）
- Director tool trace: shot.generate_images(批次1: s01_c01-c03, 21.4s) → 批次2(s02_c01-c04, 37.7s) → 批次3(s03) → 批次4(s04_c01-c04) → turn.completed
- 真实 Agnes 图片生成：4 批按场景，progress 事件正常流动（"已完成 N/4，进行中 M"）
- **QueueFull 修复验证生效** ✓: driver 正常收到 [turn.completed] attempt 耗时 150s，不再悬挂
- 落盘核验: 30/30 图片文件 >1KB，共 30.1MB（每 shot 2 candidates 变体 + selected_first_frame）
- receipts: shot.generate_images ×4 批，全 ok=True verified=True count=4，每 shot "first done ok=True"
- 越权检查: story_text=`957cf4eacc2923ed`(未变✓), scenes_text=`e77c62373958b3a2`(未变✓), characters=`dbd816a7ad8b4f69`(未变✓), shots=`89782ff6601c8f61`(本域变化)
- locks: 完成后 locks=[] ✓

## 关键约定（canonical 域分离，防越权）
| Agent | 写域 | 资源锁 | 变更事件 | 禁止改写 |
|---|---|---|---|---|
| Script Writer | story_text/scenes | story | story.changed | character/shot |
| Character Designer | characters | character | character.changed | story/shot |
| Director | scenes[].shots + 图片/视频 | storyboard | storyboard.changed | story/character |

每阶段后用 domain_hashes 对账：下一阶段不得改写上一阶段域的 hash。

## 当前状态
- **当前阶段**: 阶段0 完成，准备进入阶段A
- **Scene 数量**: 0
- **Character 数量**: 0
- **Shot 数量**: 0
- **已生成图片**: 0
- **已生成视频**: 0
- **失败/重试**: 无
- **当前下一步**: 启动 Script Writer turn，让其自主创作约4 Scene 的原创短篇

## 失败与重试日志
（待记录）

## 阶段0 理解结论
- 真实 Agent turn 接口：`POST /api/project/{p}/agents/sessions/{sid}/turn/stream`（SSE），按 session.agent_id 路由到 `script_writer_turn`/`character_designer_turn`/`director_turn`，各自原生 tool-loop + 写域资源锁 + 真相闸门
- 项目事件 SSE：`GET /api/project/{p}/events`；权威锁：`GET /api/project/{p}/resource-locks`
- canonical：`projects/{name}/output/project.json`，ProjectStore 原子写；顶层 `name/story_text/characters/scenes/next_scene_seq`；scene 有 `scene_id(sNN)/title/summary/characters/shots`；shot 有 `shot_id/scene_id/index/shot_size/camera/dialogue/first_frame_batches[]/final_video/selected_first_frame/status`
- 工具域：Script Writer→story.*+script.* ；Character Designer→character.*+generate_views ；Director→shot.*+generate_images/generate_videos（+只读 story/script/character）
- driver：`_golden_path_driver.py`（复用 gp_dir_visual_smoke.run_turn 模式 + 语义不活跃看门狗 420s/300s/2次重试，项目参数化）
- 注意：`director-test` 弹窗走 director_min（legacy），非真实路径；真实 Director 用 `/turn/stream`
- 已记录后端日志残留 QueueFull 边界 bug（finally 路径，不泄漏锁，真机若触发再最小修）
