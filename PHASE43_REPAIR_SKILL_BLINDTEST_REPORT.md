# PHASE 43 — Skill 规则沉淀、正式修复与冻结盲测 报告

日期：2026-09-30 起。执行依据：`详细流程文件/修复阶段/43.md`（用户/GPT 规划）。
执行：Claude Code（搭建/记录/核验）+ 系统 Agent（修复轮与盲测正式入口）。
线索文档：`PHASE42_VIDEO_SKILL_SEAL_ACCEPTANCE_REPORT.md`（证据索引，逐项核验后使用）。

状态标记：〔已核验〕=仓库/回执可复现；〔进行中〕；〔待用户评审〕；〔未做〕。

---

## §0 基线核对（43.md 要求：报告是证据索引，不是免核验的事实）

| 核对项 | 结果 |
|---|---|
| 仓库状态 | 子仓 c7955c8 = 父仓指针 356b1c1（一致）〔已核验〕 |
| Phase 42 报告存在且完整 | 617 行 §0-§9，判据/配方/五缺陷/回归记录在档〔已核验〕 |
| 正式 Skill | 两包 v1.1.0（full-story-relay-calibration / keyframes-bridge-repair），registry fail-closed 加载机制在案〔已核验〕 |
| 偏差根回执 | s01_c04 batches[4][5] / s02_c02 batch[3]（ff_cb6f94a5）`generation_basis.dropped_optional` 含 `human_reference_drop`（Look ref_92ad3512a47f，阿獭）——与报告 §3.1 一致〔已核验〕 |
| s01_c04 缺 prop 绑定 | shot_production_bindings.json: prop_bindings=[]（对比 s02_c02 有 prop_fb1bd0b18fb1+状态注记）〔已核验〕 |
| 灯室内部 SIV 缺口 | scene_system.json：loc_212643ad898c 仅 1 视图（main establishing view）/1 SIV，无灯室内部〔已核验〕 |
| 13 镜 canonical | 13/13 final_video=2.5-flash 版本在案（vv 列表见 project.json）〔已核验〕 |
| 用户评审记录 | `_video25_probe/FEEDBACK.json` 仅存 EXP2 轮（30 键）+ qa3/qa4；EXP3-6 轮每轮换 manifest 时被重置——EXP3-6 定案以 Phase 42 报告 §6.8-§6.14 原文为权威记录。**档案缺口：评审记录被换轮覆盖，跨轮聚合不完整**（本轮起每轮独立落档）〔已核验〕 |
| EXP 候选时长 | 29 条 submit 回执：统一 5s（唯一例外 exp5 c06 4s 对照）——好评候选全部 5s，canonical 8s〔已核验〕 |
| 回归基线 | 2516 passed + 1 预存间歇 flake（test_ai_7ch_cancel_race，单跑即绿，39F 在档）〔已核验〕 |

## §1 Skill v1.2 沉淀（§一步）

提交 a6fe629（子仓）/ 222e786（父仓）。全量回归 2516 passed。

### 1.1 系统层默认修正（43.md：审查 auto 静默丢 Look 路径）

- `resolve_human_reference_mode`（image_adapter.py:562）auto 定版翻转：
  **景别驱动紧景别（CU/ECU/MCU 家族）→ crop**（P3 三臂：相对 drop 构图不变身份
  变好；EXP2/3 用户逐图评审=跨镜最稳配方；31.md §19 悬案落定）；
  **角度驱动（OTS/insert/detail）维持 drop**（无 crop 实证；insert/detail 主体
  常为道具——例外有档）。显式 keep/drop/crop 直通不变。
- 派生失败诚实降级（回落源媒体+diagnostic）；**发现并修复 provenance 缺口：
  generation_basis 此前不落 diagnostics**（首帧/尾帧批次两侧补齐）——派生失败
  等诚实降级诊断从此留痕。
- 测试：D2 断言反转+例外用例+crop 方法映射；provenance 拆派生成功/失败回落
  两形态（FakeImageClient 环境 stub identity_crop 派生，确定性）。

### 1.2 Skill 内容（决策入口=简短条目，细节进 references）

- SKILL.md 新增「定版生产判据」节：6 条按适用条件/选择动作/禁用条件/失败
  修复方向组织（特写身份锚定检查/道具入画绑定检查/根帧先修再接力/修法按
  空间-身份问题选择/动作-时长-场景容量一起估/候选与裁决），十二条原文不堆入。
- 路径 e 重写：双参考 `prev_tail_look` 已接线已验证（P1+链4）+已知权衡
  （Look 与尾帧锚定竞争——根帧有错先修根帧，双参考只接力合格端点）。
- 时长配方补容量耦合（判据 5）。
- 新增 references/production-recipes.md：方法细节+反例+端点修复矩阵+
  证据边界（「N 选 M」不写成模型保证）；**项目镜头 ID/种子/评语不入 Skill 包
  （测试锚定），留在项目实验档案**。
- keyframes-bridge-repair v1.2.0：2.5 双帧真机状态+「修复阶段工具/首次生成
  不插桥」定位（用户 EXP2 裁定）。
- capability-facts：双参考行从「未验证」更新为实证（P1 task 回执+视觉验证
  结论+权衡）；新增 auto→crop 与 diagnostics 落档两条守卫。
- skill.json 1.1.0→1.2.0（注册表加载测试锚定）；director.md 补哨兵设置方法。

### 1.3 用户评审记录档案缺口（本轮新发现）

FEEDBACK.json 为单文件合并写，每轮换 manifest 后被重置——EXP3-6 轮的用户
逐图/逐链评语仅存于 Phase 42 报告转述。43.md 起每轮评审反馈独立落档
（`_43_repair/FEEDBACK_repair.json`），不再互相覆盖。

## §2 正式修复轮（§二步，集成验证）〔进行中〕

执行方式：进程内正式入口（`_43_repair_run.py`，复用 Phase 41 验证过的生产
装配+recover 钩子+隔离队列）。两个正式入口 turn：

1. **Scene Agent turn**（task_scene.txt）：灯室内部 LocationView+SceneInstanceView
   （用户已授权）。
2. **Director 全新会话 turn**（task_repair.txt）：修复主链——任务书给目标/
   项目事实/好评候选索引/用户新定意见，方法选择由 Agent 按 Skill v1.2 提出
   并记录理由（补齐 Phase 42 封板条件 2 的「真实 Agent 选择记录」）。

用户裁定（2026-09-30）：
- 偏差链镜时长 8→5s（好评证据全在 5s；8s=凭空甲板诱因）。
- 新试验方向（本轮在 s01_c06 落地）：①动作描述粒度对照（约 2s/动作 vs
  0.5s/动作——若成立则核心=描述密度与秒数对应关系）；②动作描述必须按尾帧
  场景可行性写（场景前方没路不能让人物一直走=凭空延伸另一诱因）。
- 今天修复轮+盲测全部跑完。

（§2 执行记录随轮次回填）

## §3 冻结与独立盲测（§三步）〔未做〕

## §4 交付判定（Phase 42 §8 两条件）〔未做〕
