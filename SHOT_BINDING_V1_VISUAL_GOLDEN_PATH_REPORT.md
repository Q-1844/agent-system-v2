# Shot Production Binding V1 — Final Visual Golden Path 报告

> 阶段：Visual Golden Path（执行指令 `详细流程文件/修复阶段/13.md`）。
> 结论先行：**VISUAL GOLDEN PATH PASS**。
> 真实 Agnes（image=agnes-image-2.1-flash / video=agnes-video-v2.0）在真实
> JobQueue/handler/持久化链路上完成
> Shot → Binding → Validity READY → Pack COMPILED → real first frame → real last
> frame → real keyframes video → dependency stale → NOT_READY 阻断 → explicit
> refresh → 旧 artifact 仍被拒 → explicit regenerate → real video 再成功 →
> UNBOUND legacy → inherit_previous relay 全链验收。
> 分阶段真机验收合计 **93 PASS / 0 FAIL**（9 阶段逐项见 §3-§11）。
> 过程中发现并修复 1 项 **跨阶段集成 BLOCKER**（§14；非 S3C adapter 问题，已独立
> commit `25cb1a1`，用户授权的最小修复）。

---

## 1. Baseline

- branch `feat/scene-system-v1-s1`，开工 HEAD `1cad012`（S3C.2 PASS），tracked 工作区干净
- §0 targeted：S3B.1+S3B.2+S3B.3+S3C.1+S3C.2 = **182 passed** 全绿后进入真机
- 真机期间发现的 blocker 修复 commit `25cb1a1`（独立业务 commit，§14）

## 2. Real Project Fixture

独立项目 `projects/__shot_binding_visual_final_gp`（未触碰任何用户真实项目），
全部经**真实 service 链**构造（真实 Agnes 生成 + 真实 Vision review + 真实 approve saga；
审查不通过=诚实 needs_fix 重生成，最多 3 轮）：

| 域 | 实体 | 真实链 |
|---|---|---|
| ProjectVisualProfile | render_family=2d_animation + art_direction/style_tags/negative | VisualProfileService.update |
| Character | Alice（t2i 2D 肖像 → legacy front view） | t2i + ProjectStore |
| Look | `look_b107678633f5`（Alice · Field Explorer，approved） | create_look→generate(i2i×2)→review(pass)→approve |
| Prop | `prop_365d035f2156`（Antique Flintlock Pistol，approved） | create_prop→t2i→register→attach→review(pass)→approve |
| Composition | `comp_b6afd3992851`（Alice+Look+Pistol held@right_hand，approved） | create→generate(i2i×3)→六维度 review(pass)→approve |
| Scene | Old Harbormaster Office（2 anchors）+ base view（approved） | create_location/anchors/view→generate→review→approve |
| SceneInstance | `scin_01e9cea9f7d5`（s01；evening/warm desk lamp）+ SceneState | create_instance→patch_scene_state |
| SIV | `sciv_46828c42fca4`（approved） | create→generate(×2)→review(pass)→approve saga |
| Shot A | `s01_c01`（pick_up 主链，link_mode=keyframes，4s） | ProjectStore（creative 字段） |
| Binding | pick_up：start="Alice right hand empty" / end="Alice right hand holding the pistol" | ShotBindingService.create |
| Shot B | `s01_c02`（inherit_previous relay，i2v）+ 自有 Binding（none） | §11 |
| Shot C | `s01_c03`（UNBOUND legacy smoke，i2v） | §10 |

最终状态：**Validity = READY**（issues=[]）；**ReferencePack = COMPILED**（refs=5，
temporal 语义：SIV=throughout/primary、identity=throughout/supporting、
**Look=start/primary**、**持枪 Composition=end/supporting**、prop=throughout/supporting
——pick_up 硬规则经真实 pack 验证）。

## 3. Real First-frame Result（Step A，15 PASS / 0 FAIL）

- 路径：JobQueue submit → `_handler_first_frame` → BOUND 分支 →
  `build_bound_first_frame_input` → **真实** `agnes.generate_image`（i2i，
  image_refs=SIV/Look/Prop 事实参考）→ download → ImageBatch → 自动选中
- 真实证据：candidate URL `https://cos-platform-outputs.agnes-ai.cn/images/i2i/task_EQkV4BwLVO3XroDHU6Ztwix`；
  本地 `output/frames/s01_c01_first_v1.png`（**1,097,519 B**，1312x736）
- `generation_basis` 落盘 ✓（binding_revision=65d87b34 + resolved SIV + readiness=READY）
- prompt_snapshot：**含 "Alice right hand empty"**（start state）✓；
  **绝不含 end state** ✓；持枪 end Composition 不在首帧 evidence（roles 实证）✓
- P1 风格指令生效（"2D animated image..."）✓；legacy StyleAnchor（"legacy marker
  oil painting" 测试标记）零出现 ✓
- canonical 无反写：resources/scene_system/project_profile/bindings 四文件字节不变 ✓

## 4. Real Last-frame Result（Step B，13 PASS / 0 FAIL）

- 路径：等待首帧 URL → freshness check PASS → `build_bound_last_frame_input` →
  真实 generate_image（i2i，refs=[首帧, 持枪组合(end), SIV, prop, identity]）→
  ImageBatch(last_frame)
- 真实证据：URL `.../images/i2i/task_j8xCJPhwUgAopp3djse45RU`（公网可读，视频 last 槽可用）
- `based_on_first_frame = ff_e3acbfd9_1`（= selected first frame）✓；
  `generation_basis` 落盘（65d87b34）✓
- prompt：**含 "Action end state: ... holding the pistol"**；**零 start_state 行** ✓；
  持枪 Composition（end）作为 end evidence 进入 ✓；Look(start) 排除 ✓
- canonical 无反写 ✓

## 5. Real Video Result（Step C，15 PASS / 0 FAIL）

- 路径：preflight → mode=keyframes → BOUND 分支 `build_bound_video_input`
  （boundary freshness PASS）→ **真实** `create_video`（v2.0 keyframes，
  `extra_body.image=[first,last]`）→ poll → download → VideoVersion → final_video
  → ffmpeg 提取末帧
- 真实证据：video_id `video_bGl0ZWxsbTpjdXN0b21fbGxtX3Byb3ZpZGVyOm9wZW5haTttb2RlbF9pZDphZ25lcy12aWRlby12Mi4w...`
  （model_id=agnes-video-v2.0）；本地 mp4 **2,017,692 B**；seconds_returned=4.0
- VideoVersion `vv_00c93c11` = final_video；`video_end_frame` 提取成功（本地 PNG +
  imgbb 公网 URL，source_version 身份可追踪）
- prompt_snapshot：start→end motion（empty→holding）+ Scene/Look/Prop 生产上下文
  + P1 风格 + 零 StyleAnchor ✓
- provider image order = **[first(start-compatible), last(end-compatible)]**（keyframes）✓
- canonical 无反写 ✓

## 6. Audio Stream Observation（仅 ffprobe 事实）

对 Step C 与 Step F 真实 MP4：

```json
{"audio_streams": 1, "codec": "aac", "sample_rate": "48000", "channels": 2,
 "duration": "4.041"}
```

**有 audio stream（aac 48kHz 立体声）。observation only——不设计声音系统、不改视频
代码、不影响 Visual PASS。** 证据留给下一轮 Agnes Audio Capability 调查。

## 7. generation_basis（first / last 真实落盘内容）

```text
first_frame_batches:
  ff_e3acbfd9  generation_basis.binding_revision=65d87b34  binding_readiness=READY   ← Step A（变更前）
  ff_6f08d755  generation_basis.binding_revision=8971d3ba  binding_readiness=READY   ← Step F（refresh 后重生成）
last_frame_batches:
  lf_bef83518  generation_basis.binding_revision=65d87b34                             ← Step B
  lf_92c9325f  generation_basis.binding_revision=8971d3ba                             ← Step F
```

每条均含 resolved_scene_instance_view_id / binding_document_revision /
dependency_fingerprints / visual_profile_fingerprint（§5 Case C 最小 derived
provenance 的真实运行形态）。

## 8. Stale → Refresh → Old Artifact Still Stale（独立证据）

- **Step D**（6 PASS）：`update_look`（外观描述+围巾）→ Validity **NOT_READY**
  （fingerprint_mismatch a63f8b16→1726bc7e）→ 重提交 video → job failed
  **binding_not_ready**；attempts 1→1（**provider video submit = 0**）、无新 video_id、
  existing `vv_00c93c11` 不被覆盖、**bindings.json 字节不变（无 auto refresh）**
- **Step E**（4 PASS，本 Golden Path 最重要验收）：显式 `refresh_binding` →
  **READY** 且 binding_revision 65d87b34→8971d3ba → 立即重提交 video → job failed
  **boundary_frame_stale（artifact_basis_mismatch：recorded=65d87b34 ≠
  current=8971d3ba，slot=first）**；provider submit=0；existing result 不被覆盖

> 证明：**READY Binding 不会让旧图片自动变 fresh。**

## 9. Regeneration Recovery（独立证据）

- **Step F**（8 PASS）：显式重生成 first/last（新批次 generation_basis =
  8971d3ba = 当前 binding basis）→ 再次真实 video **成功**（新 video_id
  `video_...video_id:video_b7e43fd78fd47ff8a3d81e839861f87`，2.0MB / 4.0s，
  final_video=vv_1004b630）；binding canonical 全程零反写
- 完整闭环：change dependency → NOT_READY → explicit refresh → old artifact remains
  stale → explicit boundary regeneration → new video succeeds
- 全程**零** auto refresh / auto rebind / auto regenerate / legacy fallback

## 10. Legacy Smoke（UNBOUND）

- **Step G**（5 PASS）：s01_c03 无 binding → legacy 首帧真实生成
  （t2i URL `.../images/t2i/task_7BJgKW5NSpQZm9sSu1Ev5ZO`）；
  prompt == `build_first_frame_prompt`（**含 legacy StyleAnchor 后缀**——未绑定
  shot 行为逐字节保留）；批次**不写** generation_basis
- Binding 集成未破坏旧项目可用性 ✓

## 11. inherit_previous（relay / continuity）

- **Step H**（12 PASS）：Shot A 真实末帧（imgbb 公网 URL
  `https://i.ibb.co/qf1d3RN/1d7902c719ad.png`，source_version=vv_1004b630 身份可追踪）
  → Shot B（s01_c02）opening_source=inherit_previous + 自有 Binding（READY）
  → BOUND video adapter 将 relay 物料判定为非本镜 Binding 产物（diagnostic
  `boundary_relay_no_binding_basis`，**不误报 stale、不要求 Shot B basis**）
  → i2v 真实视频**成功**（`vv_b485708a`，1.1MB / 4.0s）
- 无 filesystem glob / newest-file guessing（relay 走 `_effective_first_frame_url`
  语义的 selection：prev.video_end_frame.url + source_version 锚定）
- **stale relay 最小验证**：source_version 失效（指向旧 version）→
  `continuity_status = ('stale', '上一镜头已更新')`（机制级正确；未发起 provider
  调用——relay freshness 属 status 语义，S3C.2 既定边界）

## 12. Canonical Mutation Audit

每一步生成前后对五 canonical 文件做 sha256 比对：**resources.json /
scene_system.json / project_profile.json / shot_production_bindings.json 在全部
生成步骤（A/B/C/D/E/F/G/H）字节不变**；project.json 仅按既有结果路径写入批次/
VideoVersion/end_frame（允许的既有行为）。binding 无 auto refresh（Step D 实证
字节不变）；无任何越界反写。

## 13. Provider Evidence（真实外部调用 vs adapter/test）

| 项 | 值 |
|---|---|
| image model | agnes-image-2.1-flash |
| video model | agnes-video-v2.0（video_id 内嵌 model_id 佐证） |
| 真实 image 调用 | ≈16 次（fixture 11：Alice 1+Look 2+Prop 4（3 轮诚实 needs_fix+1 过）+Composition 3+base view 2+SIV 2 ——含 review 退避；shots 链 5：A 首尾 2+F 重生成 2+legacy 1） |
| 真实 video 提交 | **3 次**（Step C / Step F / Step H），poll 间隔 5s，均 completed |
| video_id | C：`video_86ec482bdc9d429ba354caa7571c18d5`（vv_00c93c11）；F：`video_b7e43fd78fd47ff8a3d81e839861f87`（vv_1004b630）；H：`video_2feb298fb8224c72be7d1d8eac6d0f90`（vv_b485708a）——id 内嵌 `model_id:agnes-video-v2.0` |
| 下载产物 | 首帧 1,097,519B PNG（1312x736）；mp4 2,017,692B / 2,017,692B / 1,109,501B；video_end_frame PNG + imgbb URL |
| 被阻断的提交 | Step D（binding_not_ready）与 Step E（boundary_frame_stale）各 1 次重提交尝试，**provider submit = 0**（attempts 计数不变为证） |

review 链同样真实（Agnes Vision）：Look/Composition/base view/SIV 均 pass，
Prop 前 3 轮诚实 needs_fix（写实风格 vs 项目 2D 风格——reviewer 正确执行风格一致
性），第 4 轮 2D 风格 prompt 通过。**未篡改任何审查。**

## 14. Golden Path Discovered Cross-stage Integration Blocker（非 S3C adapter 问题）

- **Symptom**：fixture 的 SIV 生成连续 `location_view_stale`（base view 刚批准即被判
  旧），Golden Path 在 Base LocationView → SIV 步骤被阻断。
- **Root cause**：P1 §15 将 `visual_profile_fingerprint` 加入 review/approve 的期望
  指纹（两侧同口径），但 Scene System `check_base_location_view`
  （`src/scene_system/visual_service.py`，SIV 生成门槛）重算时**漏传该维度** →
  任何有 ProjectVisualProfile 的项目，真实链批准的 base view 永远 mismatch。
- **Affected function**：`SceneVisualService.check_base_location_view`（单点）。
- **Why previous phase tests did not expose it**：S2A approval 测试不触达该检查；
  S2B/S3B 测试的 base view 批准用 store 快捷记录（无 profile 维度）——与旧检查同样
  "profile 盲"，**两个错误口径一致 = 假绿**；S2B 真机 E2E 项目无 Profile。唯有 GP
  首次把「真实 review+approve 链 + ProjectVisualProfile + SIV 生成」串起来才暴露。
- **Minimal fix（授权后）**：check 重算复用同文件 approve 侧既有 P1 模式
  （`load_profile_or_none` → `visual_profile_fingerprint`），不建第二套指纹算法；
  profile 变化 → base view 正确 stale（批准语义含项目风格）。
- **Regression test**：新增 `tests/test_scene_system_baseview_profile_fp.py` 双向锁定
  （Case 1 同 Profile：真实链批准 → check fresh + SIV 门槛放行；Case 2 Profile 真变化
  → check 判 stale）；测试快捷批准统一对齐真实口径
  （`approval_record_fingerprint` 共享 helper，6 处）。
- **Commit**：`25cb1a1`（独立 fix(scene) commit，与 GP evidence 分离）。
- **Affected files**：`src/scene_system/visual_service.py`（1 处）+ 7 个测试文件。
- **回归**：受影响域（S1+S2A+S2B+P1+blocker 测试+S3B/S3C binding 全家）
  **340 passed / 0 failed**；对照实验（stash 修复前同子集 81 passed/0 failed →
  修复后 34 failed → fixture 对齐后全绿）逐条归因，零不可解释失败。
- **明确非**：S3C.2 adapter bug（根因不在 S3C generation path）。

## 15. Deviations

None（生产代码改动仅为上述经授权的 blocker 最小修复；GP 本体零生产代码改动；
driver/evidence 为 untracked scratch，不入库）。

## 16. Final Verdict

```text
VISUAL GOLDEN PATH PASS

Shot Production Binding V1 visual production truth and real generation path are accepted end-to-end.

Visual architecture may now be frozen.

No Audio / Voice implementation was started.
```

- READY → real first/last/video 全部成功（真实 provider 证据齐备，§3-§5）
- dependency change → NOT_READY → provider submit blocked（§8）
- explicit refresh → READY → old boundary artifact still rejected（§8，核心验收）
- explicit regenerate → real video succeeds again（§9）
- UNBOUND → legacy remains usable（§10）
- inherit_previous → existing continuity mechanism works correctly（§11）
- no auto refresh / no auto regenerate / no legacy fallback for BOUND NOT_READY /
  no canonical pollution / no new regression（§8/§9/§12/§14）
- Audio stream：observation only（§6），不参与 PASS 判定
