# PHASE 44A 报告——旧项目收口、媒体快照与冻结

日期：2026-09-30。执行：zcode。依据：`详细流程文件/新阶段1/PHASE44_A_选片收口与冻结.md`
+ `PHASE44_00_总控与批次路线.md`。基线：`PHASE43_REPAIR_SKILL_BLINDTEST_REPORT.md`
（仅作线索，全部动作按仓库实况重新核验）。

状态标记：〔已核验〕＝命令/回执/哈希可复现；〔待用户〕＝等用户看片/裁定；〔已更正〕＝对 Phase 43 报告的更正。

---

## §0 结论摘要

1. **两笔回拨已按用户最新裁定执行并验证**〔已核验〕：s01_c04→vv_80dc90a5、
   s02_c02→vv_4bdf6b08。回拨采用「provider URL 下载→受管副本→哈希校验→改
   vv.local→set-final→末帧重提」流程（见 §3 与 §4 的覆盖写危害——直接跑旧脚
   本会污染末帧）。s01_c05 的接力源经**内容寻址图床逐字节一致性**实证恢复 fresh。
2. **s02_c03 开门动作迭代完成**：两个新候选（vv_1c54b0af / vv_7e31d14b）经执行
   侧逐帧亲证**均真实完成开门动作**（工作台→走门→握把→拉门→敞开门口+灯塔剪影），
   无凭空元素；Agent 自检误报「双双失败」已记录（§6）。**待用户终审**〔待用户〕：
   评审页 http://127.0.0.1:8785/REVIEW_repair.html 。
3. **制作快照已冻结**〔已核验〕：`snap_44a_production_20260930_221636`——13 镜
   final 媒体全部按 URL 下载核验 sha256、镜像落盘、ffprobe 探测（全部 h264+aac）；
   c03 按纪律标记 `temp_candidate_for_dev`。**正式快照被守卫阻断**（s02_c03 缺用户
   终审），守卫行为已实测。
4. **两处 Phase 43 结论已更正**〔已更正〕：①白斑传播链的归因对象（§4.2）；
   ②input_visuals 三参考第三槽记录缺陷（已修+测试，§5）。
5. B 批次（基础成片 Agent）的输入契约草案已就绪：
   `_44a/assembly_input_manifest_production.json`（15 字段/镜，含哈希/探测/relay/
   末帧/回执），89.9s 全部带原生音轨。

---

## §1 A0 核验差异表（报告线索 vs 仓库实况）

| 项 | Phase 43 报告 | 实况核验 | 结论 |
|---|---|---|---|
| 子仓 HEAD | 6722bd0（Skill v1.3.0） | a236da8（43 报告终版提交）→ 本批次后 c2b499d | ✅ 一致演进 |
| Skill 版本 | 两包 v1.3.0 | builtin 两包 skill.json 均 1.3.0 | ✅ |
| 13 镜 final | §3.1 表 | 13/13 指针逐一吻合（含两笔待执行回拨的起始状态） | ✅ |
| 两笔待回拨 | s01_c04→vv_80dc90a5、s02_c02→vv_4bdf6b08 | 目标版本在案、status=done、URL 可取（哈希核验） | ✅ 已执行 |
| 用户裁定依据 | repair43_v3 层逐字在档 | `_43_repair/FEEDBACK_repair.json` v3 层：q_sel_s01_c04=「一轮候选」、q_sel_s02_c02=「一轮候选」、s02_c03=「…人物不开门了」 | ✅ |
| c06 终审（1s 分拍） | 报告 §2.8 称 v3 层逐字在档 | **v3 层反馈文件无 c06 条目**（v2 层只记二轮 0.5s 选择）；终审翻回的逐字证据仅在报告转述 | ⚠ 档案缺口（不影响行动：final=5ad9192d 与报告一致，未再被争议） |
| `python` 可用性 | —（43 报告未提） | Git Bash 下 `python/python3` 为 WindowsApps stub（exit 49）；项目既定解释器=`D:\Delta Force\python.exe`（start.bat 同源，3.13.0+fastapi） | ⚠ 环境事实，执行须用项目解释器 |
| 评审页/脚本适用性 | §3.3 操作手册 | `_43_set_final.py`/`_43_extract_end_frames.py`/`_43_review_server.py` 在位可复用；但 set-final 脚本的 PICKS 为 43 数据，且**直接复用会在回拨场景污染末帧**（§4.1），本批改用新脚本 | ✅（有条件） |

## §2 A0 逐镜状态表（回拨+迭代后的 canonical 现状）

| # | 镜 | final | 时长(s) | 音轨 | 裁定状态 | 依据 |
|---|---|---|---|---|---|---|
| 1 | s01_c01 | vv_55accb16 | 8.0 | aac | user_locked | 09-28 基线合格保留 |
| 2 | s01_c02 | vv_9cda3271 | 8.0 | aac | user_locked | 同上 |
| 3 | s01_c03 | vv_703eb03f | 8.0 | aac | user_locked | 同上 |
| 4 | s01_c04 | **vv_80dc90a5** | 5.2 | aac | user_locked | repair43_v3「一轮候选」+本批回拨 |
| 5 | s01_c05 | vv_70728b9b | 5.2 | aac | user_locked | v2 层一轮候选；接力源=fresh（§3） |
| 6 | s01_c06 | vv_5ad9192d | 5.2 | aac | user_locked | 1s 分拍终选（v2 曾选 0.5s，终审翻回）；媒体已修复（§4.1） |
| 7 | s02_c01 | vv_ebbc77f8 | 8.0 | aac | user_locked | 09-28 基线 |
| 8 | s02_c02 | **vv_4bdf6b08** | 5.2 | aac | user_locked | repair43_v3「一轮候选」+本批回拨 |
| 9 | s02_c03 | vv_7e31d14b（临时指针） | 5.2 | aac | **pending_user_review** | A2 迭代两候选待用户终审 |
| 10 | s03_c01 | vv_f741a354 | 8.0 | aac | user_locked | v2 层一轮候选 |
| 11 | s03_c02 | vv_03ff6e45 | 8.0 | aac | user_locked | 同上 |
| 12 | s03_c03 | vv_49b85cd1 | 8.0 | aac | user_locked_with_caveat | v3「暂时就这样吧」（灯口径接受） |
| 13 | s03_c04 | vv_6684f5dd | 8.0 | aac | user_locked | 09-28 基线 |

12 剪接点初步标记见 §7。所有 final 均为 2.5-flash 代，媒体全部哈希核验（§6）。

## §3 A1 两笔回拨回执〔已核验〕

执行脚本：`_44a1_rollback.py`（提交 c2b499d）。流程与旧脚本差异：**先按 URL 下载
版本后缀受管副本→ffprobe 校验→改 vv.local→set_final_video_business（指针+video
槽同步+从正确字节重提末帧）**；不覆盖共享路径（保留被撤下版本磁盘字节）。

| 项 | s01_c04 | s02_c02 |
|---|---|---|
| 撤下 | vv_d90ff290（三轮未点亮对照，自动前移） | vv_1c4b7001（二轮 seed42） |
| 设为 final | vv_80dc90a5 | vv_4bdf6b08 |
| 受管副本 | `output/videos/s01_c04__vv_80dc90a5.mp4`，sha256 `c78778c5c353c35c…` | `output/videos/s02_c02__vv_4bdf6b08.mp4`，sha256 `5f836b5ee7dbfbbe…` |
| 探测 | 5.175s h264 1280×704 24fps + aac | 同左 |
| 末帧重提 | `0791e96d0cb6c558.jpg` | `47a6874c588072c8.jpg` |
| 下游 | **c05 接力源=0791e96d 与重提产物逐字节一致（图床内容寻址）→c05 依赖实证恢复 fresh** | 成为 A2 c03 迭代的接力锚点 |
| trace 收口 | `cal_…_f540/s01_c04_setfinal_rollback`（human_action=other，枚举守卫实测生效） | `…/s02_c02_setfinal_rollback` |
| 保留回看 | d90ff290 磁盘字节未动（共享路径）+URL | 1c4b7001/1a18d954/0e7fe39f 同上 |

自动 final 前移与回拨记录：本批无新生成触发的意外前移（A2 的 c03 前移见 §5，
属预期自动行为，已标临时）。

## §4 媒体取证（本批新发现）〔已更正/已修复〕

### 4.1 覆盖写危害实证（三镜受累，两镜已修复）

`VideoVersion.local` 覆盖写 + set-final 从 `vv.local` 重提末帧 ⇒ **当 final 不是
该镜最新代时，末帧提取读到的是最新代的字节**（幽灵记录：版本 ID 对、字节错）。

| 镜 | final vs 最新代 | 受累证据 | 处置 |
|---|---|---|---|
| s01_c04 | final=80dc90a5 < d90ff290 | （回拨前末帧=a7dd076f，属 d90ff290 字节） | 本批回拨流程天然修复 |
| s02_c02 | final=1c4b7001 < 0e7fe39f | 见 4.2 | 本批回拨流程天然修复 |
| s01_c06 | final=5ad9192d < 6d3b05db | 修复前末帧对象=6d3b05db 尾帧（管理副本 sha `a0d5654e…`≠共享文件 sha `b1f480ac…`） | `_44a2_media_forensics.py` 下载受管副本→重提末帧=`2c41192826715e45`（真 5ad9192d 尾帧）；c06 是场 1 末镜无下游接力，无级联 |

**纪律沉淀（建议 B 批固化）**：任何 set-final/回拨，若目标版本非最新代，必须先建
版本后缀受管副本再重提末帧；成片 manifest 只认「URL 下载哈希=登记哈希」的媒体。

### 4.2 白斑传播链归因更正〔已更正〕

Phase 43 §2.10 结论「s02_c02 seed42（1c4b7001）i2v 过程白斑漂移→尾帧传给全部
5 条 c03 候选」。字节级取证（`_44a/a1_media_forensics.json`）：

- 对象 `75056b51d6c77c06.jpg`（登记为 1c4b7001 末帧、第四轮 c03 哨兵候选的接力源）
  **= vv_0e7fe39f（三轮最新种子候选）的尾帧**（sha 逐字节一致）；
- 1c4b7001 真实尾帧提取 sha=`38dbee76…` ≠ 该对象。

⇒ **第四轮 c03 候选的实际接力源是 0e7fe39f 的尾帧**；白斑传播的机制结论
（尾帧层偏差传播，「根帧先修」须延伸到端点两层）不变，但归因对象由 1c4b7001
更正为 0e7fe39f。评审页 v5 的侧别因果链图组说明文字需同步（遗留）。

## §5 A2：s02_c03 开门动作迭代〔候选完成，待用户终审〕

- 用户裁定（v3 层逐字）：「我认为你可以继续按照之前来做，目前虽然画面没问题但是
  **人物不开门了**」；根因（亲证）：第四轮描述写「站在**已完全拉开**的门门口」+重
  负向约束，动作节拍被吃掉（判据 5「约束-动作平衡」反例）。
- 最小方案：保留三参考哨兵 prev_tail_look_scene + 门规格（拱形原木门+石砌门框+
  黑铁铰链，左墙唯一出入口）+5s；描述按 **1s 粒度五拍**重写（抱灯起身→走向左门→
  握把手→拉开门夜色涌入→停于敞开门口望远山灯塔剪影=衔接 s03_c01）。哨兵不动，
  接力源自动重锚到回拨后的 c02 新尾帧。
- 正式入口：`_44a_c03_run.py --continue sess_7efa81c69a83`（同一 Director 会话，
  任务书 `_44a/task_c03_iter.txt`）。Agent 行为记录：OCC 守卫拦截一次缺
  expected_revision 的 shot.update 后自行 recovery；提交 2 个候选；
  **配额真实 +10s（账本 135/500）——Agent 汇报「355s」又是墙钟非配额**（§2.3 老毛病二次实证）。

| 候选 | 入缝（c02尾→首帧） | 级联出缝（尾帧→s03_c01首） | 亲证 |
|---|---|---|---|
| vv_1c54b0af | **12.4** | **36.0** | ✓ 开门动作完整；尾帧=敞开门口+灯塔剪影；无凭空元素 |
| vv_7e31d14b（现临时指针） | 16.3 | 38.4 | ✓ 动作起步较慢（中段仍在台前），尾帧跨出门槛 |
| （旧 e4441f21 对照） | 50.8 | 52.4 | ✗ 门第 0 帧已开、无动作 |

两候选均较旧版大幅改善接缝。**Agent 自检误报**：turn 汇总称两候选「门缺失/人物未
走动」等四项全败——执行侧逐帧亲证推翻（评审页已附对照表）；其视觉抽帧/判读流程
有缺陷，列为系统改进项（与配额误报同类：Agent 自述不可作验收依据，须回执+亲证）。

**用户待办**：评审页 http://127.0.0.1:8785/REVIEW_repair.html ——选片（候选 1/候选 2/
回退旧版/再迭代）+意见；反馈独立落盘 `_44a/review/FEEDBACK_44a.json`。用户裁定后：
set-final（+受管副本纪律）→ 末帧重提 → 复测 c02→c03 与 c03→s03_c01 接缝 → 生成
正式快照。**级联提示**：s03_c01 接力源=s02_c03 尾帧，c03 终选后必须复测该接缝
（当前临时值 38.6 在跨段经验区间内；超差如实上报由用户决定是否级联重做 s03_c01）。

## §6 A3：冻结清单与 manifest〔工程预览可用〕

- **制作快照** `snap_44a_production_20260930_221636`（`_44a/snapshot_production.json`）：
  git HEAD `c2b499d`、Skill 两包 1.3.0、模型摘要（video=agnes-video-2.5-flash /
  text=agnes-2.5-flash，密钥不入档）、批准资产（Look approved×N + SIV approved×4
  含灯室内部 + 晨露快递夹具另档）、配额账本（135/500s 视频）、12 接缝实测。
- **assembly_input_manifest**（`_44a/assembly_input_manifest_production.json`）：
  13 镜×15 字段（order/video_version_id/approved_status/media_uri/media_sha256/
  snapshot_media/provider_url/source_receipt_ref{task,video_id,origin_run,model}/
  duration_probe{…has_audio}/end_frame{local,url,source_version}/relay{source_shot,
  end_source_version,tail_url}/sentinel/shot_updated_at）。**13/13 媒体 URL 下载
  哈希=登记哈希，全部 h264+aac，不可变镜像落 `_44a/snapshot_media/`**——另一台
  渲染进程不依赖任何临时 URL 即可复现素材。
- **正式快照未生成**：守卫实测阻断（s02_c03 pending_user_review），符合 44A §A3
  「最终成片导出仍由正式快照守卫」。用户终审 c03 后一条命令生成。
- 状态区分：本快照=**工程预览可用**（B 批可开工：静音画面剪接/时间线契约/管线
  开发）；**正式作品可用**待 c03 终审+正式快照。

## §7 12 剪接点初步标记（快照实测，MAE 同一口径）

| 接缝 | MAE | 初步标记 |
|---|---|---|
| c01→c02 | 61.4 | 剧变构图带（90+ 边缘），剧情暴雨→室内转场，待用户看片确认 |
| c02→c03 | 17.0 | 同景切换带内 |
| c03→c04 | 51.1 | 切换带内（c04 硬开场） |
| c04→c05 | 10.8 | 接力带 ✅ |
| c05→c06 | 9.8 | 接力带 ✅ |
| c06→s02c01 | 45.2 | 跨场切换带内 |
| s02c01→c02 | 38.1 | 切换带内 |
| s02c02→c03 | **15.5** | A2 后大幅改善（旧 50.8）；接力带附近 |
| s02c03→s03c01 | **38.6** | 跨段区间内（旧 52.4）；c03 终选后必须复测 |
| s03c01→c02 | 28.7 | 切换带内 |
| s03c02→c03 | 31.8 | 切换带内 |
| s03c03→c04 | **86.6** | 接近剧变带（90+）；c03 是四轮重做版、c04 是 09-28 未动版——**该接缝从未经用户看片**，列入用户待看 |

（相邻帧像素不相似不单独判失败——以上是风险排序，不是验收结论。）

## §8 回归与测试

- 本批代码变更仅 `_build_input_visuals`（+scene_instance_view 分支）与评审服务器
  反馈文件名路由；新增测试 `test_multi_scene_reference_takes_scene_url_not_tail`。
- `tests/test_input_visuals.py` 7/7 绿；哨兵相邻面
  `test_skill_p42_recipes + test_video_multi_mode_34 + test_shot_binding_s3c2 +
  test_p39f_semantic_closure` 89/89 绿。
- 全量回归未在本批重跑（Phase 43 基线 2516 passed；本批未触碰 adapter/生成链语义，
  变更面为纯记录层）。建议 B 批开工前跑一次全量。

## §9 本批变更清单（提交 c2b499d + 本次收尾）

- 新增：`_44a1_rollback.py`、`_44a2_media_forensics.py`、`_44a3_seams.py`、
  `_44a_c03_run.py`、`_44a4_manifest.py`、`_44a/`（任务书/评审页/证据 JSON/媒体镜像）。
- 修改：`routers/shots.py`（input_visuals 第三槽修复）、`tests/test_input_visuals.py`、
  `_43_review_server.py`（_44a 反馈文件路由，1 行）。
- 证据：`_44a/a1_before/after_project.json`（回拨前后 canonical 快照）、
  `a1_media_audit.json`、`a1_media_forensics.json`、`a2_seams.json`、
  `events_c03_1.jsonl`/`result_c03_1.json`（Agent turn 全事件）。

## §10 用户待办与 B/D 批交接

1. **看片终审 c03**（评审页 8785）→ 告知选择；执行端完成 set-final+复测接缝+正式快照。
2. s03_c03→s03_c04 接缝（86.6）与 c01→c02（61.4）是否需要处理，看片时一并给口径。
3. B 批可立即开工：读 `_44a/assembly_input_manifest_production.json`（临时 c03 已
   标记；终审后换正式快照重导出即可，时间线契约不变）。
4. D 批盲测按既定边界：A 批旧片修复不计入盲测成绩；盲测启动仍以「正式 Skill 与
   测试资产冻结 + 用户对评审页最后一次确认」为前提（Phase 43 §5.5 边界不变）。

——以上，Phase 44A 完毕（正式快照与 set-final(c03) 两项等用户裁定后闭合）。

---

## §11 增补（2026-10-01）：s02_c03 终选落定与正式快照（本节更新并取代前文相应结论）

### 11.1 第 6-7 轮迭代与用户终选时间线

1. 用户看片否决第 5 轮候选（「开门动作没问题，但是门还是在背后凭空出现」）→ 归因
   帧层根因：接力首帧（c02 尾帧）无门，视野扩大时模型在已照到的墙上现编。
2. **第 6 轮（融合首帧 v1）**：合成首帧（门在左墙）+ i2v 硬首帧。用户验收
   B=vv_e8d43507（融合·全景）与 C=vv_1b24ecac（直生·半全景）——「门都没问题了」。
   **流程缺陷第三次实证**：Agent 的 select_frame 只同步 legacy 首帧槽、未翻转
   link_mode，而 multi 模式构建不消费 boundary frames（video_adapter.py:489）——
   两支「直生」视频在无首帧状态下生成、回执仍报成功。执行侧接管机械步骤
   （link_mode→i2v）后融合路径打通；`_build_input_visuals` 修复在后续生产实测生效
   （哨兵三参考的 SIV URL 首次正确落档）。
3. **第 7 轮（全路线测试，用户指引）**：
   - **路线甲=镜头跟随·盲区揭露**（用户建议：未知区域可自由创建）：首帧保持
     接力连续性，运动指向右侧盲区，门在新显露墙面出现。甲-1 vv_6bf66392
     （入缝 19.1/出缝 35.2，风格一致）——**执行侧综合最优**；甲-2 vv_49e4bd19
     （19.0/39.4，中段原木墙风格漂移已标注）。
   - **路线乙 v2=融合首帧·门在身后**（用户基准构图=早期测试首帧后方有门）：
     reference_previous+continuity_tail+override 完整 EXPB 配方。乙-1/乙-2
     （入缝 44/出缝 54-57）。
4. **用户终选（评审页 phase44a_c03_final，逐字在档）**：
   **vv_49e4bd19（甲-2）**——「求稳的话就甲2」「其实甲-1也可以，但是甲-2没有
   背景音，所以我才选甲-2」（无声轨利于 44C 旁白/混音；音轨差异以用户听审为准）。
   同步指令：**全景路线写进 Skill**——用户要求或剧本合适时选用。

### 11.2 收口回执〔已核验〕

- set-final：s02_c03 → vv_49e4bd19，受管副本 `s02_c03__vv_49e4bd19.mp4`
  （sha256 `7fdcfe4890786dea…`，5.184s+aac），末帧重提 `93b7a138daf9007c`、ready。
- 接缝收官：**c02→c03=15.2（接力带）；c03→s03_c01=39.1（跨段带内，优于修复前
  52.4）——无需级联**，s03_c01/c02 维持现 final。
- trace 收口：`cal_…_f540/s02_c03` 终选裁定落档（含选片理由+音轨注记）。
- 音轨实测（档案）：甲-2 mean -29.9dB / 甲-1 -31.0dB / B -43.8dB——dB 不替代听审。

### 11.3 Skill v1.3.1（用户指令落地）

- SKILL.md 判据 5 新增「**元素出现方式二选一**」：镜头跟随·盲区揭露（默认求稳）
  / 融合首帧·全景（按需）；production-recipes.md 新增 §5 方法块（机制/描述写法/
  **link_mode=multi 不消费硬首帧**的配方坑/音轨注记/证据边界——无项目细节入包）。
- 两包 skill.json 1.3.0→1.3.1；版本锚定测试更新；`test_skill_p42_recipes +
  test_skill_system + test_skill_turn` **27/27 绿**。

### 11.4 正式快照与 B 批交接

- **正式快照 `snap_44a_formal_20261001_001602`**：13/13 全部 user_locked
  （s03_c03 为 user_locked_with_caveat 口径）、13 媒体 URL 哈希核验、12 接缝实测、
  不可变媒体镜像落 `_44a/snapshot_media/`。`assembly_input_manifest_formal.json`
  为 **B 批（基础成片 Agent）的正式输入契约**——甲-2 的原生音轨=无背景音，
  混音基线干净。
- 遗留（不阻断）：评审页 v5 白斑侧别图组的归因说明按 §4.2 更正（0e7fe39f）；
  s03_c03→s03_c04 接缝 86.6 与 c01→c02 61.4 的处理口径留用户看片时定。

**Phase 44A 至此完全闭合**：13/13 用户裁定、正式快照冻结、Skill v1.3.1 沉淀、
B 批输入契约就绪。
