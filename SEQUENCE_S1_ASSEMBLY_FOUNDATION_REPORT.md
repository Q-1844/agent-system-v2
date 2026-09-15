# SEQUENCE_S1_ASSEMBLY_FOUNDATION_REPORT

Sequence S1 — Assembly Foundation 执行报告（24.md）。

日期：2026-09-16（实现跨 09-15/09-16）
状态：**IMPLEMENTATION + E2E PASS，待人评**（§34 STOP 已遵守——Music/Narration/Audio Mixing/Sequence Agent/Editor Agent/Final Film Export 一概未动）
分支：子仓库当前分支 `feat/scene-system-v1-s1`（与 C1/C2/C2.1 各阶段同分支连续提交；父仓库按 commit 指针跟踪）@ `ef96b2e`

---

## 1. 结论摘要

- 独立 Sequence Assembly backend domain 已建立：`src/sequence/`（models/store/timeline/basis/media_ops/service）+ `routers/sequences.py`。
- 真机 E2E **ALL PASS**：4 镜人评通过 final → 22.8s hard-cut Sequence，duration Δ+21ms、AV 漂移 38ms、黑帧/静音 advisory 均为 0、freshness CURRENT、basis 逐 clip sha256 指纹在档。
- §31 freshness 真机测试 PASS：切 final_video → STALE（零 ffmpeg/provider）、恢复 → CURRENT、无任何 auto render。
- 全量回归 **1995 passed / 0 failed**（12 分 08 秒，含本阶段新增 49 项确定性测试）。
- Shot Production 冻结纪律核对：本阶段 13 个文件 = 12 个全新 + `api_server.py` 2 行 router 注册；`src/core/`、`src/generation/`、`src/audio/`、`routers/shots.py` 等冻结域**零改动**。

---

## 2. §4 开工前审计结论（代码证据）

| 审计项 | 结论 | 证据 |
|---|---|---|
| 现有 timeline/sequence 真相源 | 不存在，新建 SequenceBlock 合法（无第二真相风险） | grep `SequenceBlock\|sequence_id\|SequenceRender` 全仓 0 命中 |
| Story/Scene Shot ordering truth | 仅有场内顺序（`Scene.shots` 列表 + `Shot.index`），无跨场 Sequence 对象、无 trim 概念 | `src/core/models.py:288-292,415-432` |
| timeline-like 数据 | `CompositionManifest/CompositionClip`（AI-5A/AI-7C）为 Run 临时物：无 trim、无 canonical 顺序、无版本/freshness——不满足 SequenceBlock，按规格保持原样未动 | `src/ai/project_autopilot.py:125-140` |
| ffmpeg helper | `VideoComposer` 有归一化配方与 concat 先例；但老 `compose_shots` 路径 `-an` 丢原生音轨（违反 §15），manifest 段方法无 trim 且绑定 FileSaver 私有缓存 → 按 §18 建专属 `src/sequence/media_ops.py`，**复用配方（scale/pad/setsar/fps + libx264 yuv420p + aac 48k stereo）不复制真相** | `src/pipeline/video_composer.py:103-106,370,401-426` |
| render/export artifact/version 模型 | A1 `DialogueAudioVersion` + 独立 `dialogue_audio.json` 范式（append version + selection 指针 + generation_basis + freshness 派生 + schema fail-closed）被严格复用 | `src/audio/models.py:282`、`src/audio/store.py` |
| JobQueue 可复用 | ✓ 全局单例 + `JOB_HANDLERS` 注册先例 + 非 video 走轻 worker（3 并发）+ 看门狗 + 崩溃恢复，零改动 | `src/core/job_queue.py`、`routers/compose.py:38` |
| 真实媒体规格（§16） | 全部 done final = h264/1280x704/24fps/yuv420p/AAC 48kHz 立体声 → `SEQUENCE_RENDER_PROFILE` 即此 | ffprobe 实测 `__native_av_c1_e2e` + `__native_av_c1r_e2e` 全部 11 个 final |

---

## 3. 实现清单

```
src/sequence/
  models.py      SequenceClip/SequenceBlock/SequenceRenderVersion/SequenceEntry
                 · 引用 Shot 不复制（§6：只有 shot_id+trim，无 dialogue/character/path/prompt）
                 · revision = render 语义指纹（clips 顺序+shot+trim；title/scene_id 不进——无关变更不误报）
                 · trim 结构校验 fail closed（§12：非法不自动修正；bool 显式排除）
                 · SEQUENCE_RENDER_PROFILE = 生产实测规格（§16）
  store.py       output/sequences.json（独立文件，A1 store 同构：atomic_write_json +
                 project_write_lock 族 + schema fail-closed；不进 project.json）
  timeline.py    ResolvedClip/SequenceTimelinePlan（§13：ephemeral/deterministic/
                 zero provider；duration 全部 ffprobe 文件实测 §14；cursor 单调累加=hard cut）
  basis.py       §7 Source Video Truth（只认 Shot.final_video，禁止 versions[-1]/glob/
                 mtime 回落，解析不到=明确错误）；§19 sequence_render_basis_v1（actual
                 dependencies only + per-clip 源文件 sha256）；§20 freshness 状态机
                 （missing/current/stale/untracked_legacy；无关 Shot 变化不误报）
  media_ops.py   §18 窄职责：probe_video / render_clip（一遍完成 trim+归一化，AV together
                 §15，无音轨补 silence）/ concat_clips（hard cut stream copy §11）/
                 validate_output（§30 机器检查）+ black/silence advisory 诊断
  service.py     canonical ops（OCC revision_conflict）+ render 三阶段（§23）：
                 Phase1 prepare（快照 basis）→ Phase2 execute（ffmpeg 不持锁，线程跑）→
                 Phase3 commit（recompute；不一致= raced_superseded，输出保留 evidence）
                 🔴 No Auto Render（§22）/ No Auto Select（§10）
routers/sequences.py   §25 最小 API + JOB_HANDLERS["sequence_render"] 注册
api_server.py          include_router 注册（唯一既有文件改动，2 行）
```

**并发模型**：sequences.json 读改写临界区用 per-project `threading.Lock`（调用方既有事件循环又有 JobQueue worker 线程，asyncio.Lock 不可用）；probe/hash/ffmpeg 等长工作一律锁外。

### API（8 端点，§25 逐项对应）

| 规格能力 | 端点 |
|---|---|
| create sequence | `POST /api/project/{p}/sequences` |
| get sequence | `GET /api/project/{p}/sequences/{sid}` |
| update clips/order/trims | `PUT /api/project/{p}/sequences/{sid}`（可选 `expected_revision` → 409 OCC） |
| delete sequence | `DELETE /api/project/{p}/sequences/{sid}` |
| render sequence | `POST /api/project/{p}/sequences/{sid}/render`（显式，§22） |
| list render versions | `GET /api/project/{p}/sequences/{sid}/renders` |
| select render version | `POST /api/project/{p}/sequences/{sid}/renders/select`（仅 done，§10） |
| get freshness | `GET /api/project/{p}/sequences/{sid}/freshness[?version_id=]` |

错误口径：fail closed 结构化 `SequenceError` → 404（不存在）/ 409（OCC 冲突）/ 400（校验），不自动修正。

---

## 4. 关键设计决策（与规格的对应/偏差）

1. **trim 语义**（§12）：采用规格推荐定义——`trim_in_ms`=从源开头裁掉、`trim_out_ms`=从源结尾裁掉；`trim_in+trim_out < source_duration`（ffprobe 实测）非法即 fail closed。
2. **§10 严格读法**：render 只 append version，**首次 render 也不自动 final**；select 全显式（规格原文「生成 append version；选择 explicit select」）。E2E 已验证 render 后 final_render 保持 None。
3. **render 通道**：JobQueue 新 kind `sequence_render`（轻 worker，3 并发）——免费获得持久化/进度 SSE/看门狗/崩溃恢复；handler 三阶段与 C1 video race guard 同构。
4. **race guard 的 ffmpeg 计数**（§31「ffmpeg 调用 0」验证方式）：切换 final_video 用 ProjectStore 直接改指针而非 `set-final` API——后者会触发末帧 ffmpeg 提取（Shot 域既有行为）。结构性证据：切换前后 render 行数不变、sequences 目录文件 mtime 全等。
5. **basis 源指纹**：源 mp4 全量 sha256（几 MB 级毫秒开销），诚实优于 size/mtime 近似。
6. **AV together**（§15）：trim+归一化单 ffmpeg pass 内完成，音视频同窗裁切；无音轨源补等长 silence（concat 兼容前提，compose 先例）。零提取/替换/重生成。
7. **S2 保留点**（§28）：未建 track graph、未预设计 track type；sequences.json 顶层 schema 与 clips 结构对未来 `Sequence + Music + Narration` 无堵死（独立 store 文件可平行扩第二实体，canonical 不动）。

---

## 5. 测试（§32 工程纪律）

确定性测试 **49 项全过**（零 ffmpeg——媒体操作 monkeypatch；真实 ffmpeg 只在真机 E2E）：

| 文件 | 覆盖 |
|---|---|
| `test_sequence_models_store.py`（18） | clip/block 校验、revision 决定性+顺序敏感+display 不敏感、版本 known-field 过滤、entry 指针语义、store 原子读写/schema fail-closed |
| `test_sequence_timeline_basis.py`（13） | §12 trim 验证（含规格边界=拒）、§13 规格示例逐字（6000/0/500→5500@0、8000/500/500→7000@5500、总 12500）、§7 解析 fail-closed 全分支、§19 basis 键集、§20/§21 freshness 全状态机（含无关 Shot 变化仍 CURRENT、源文件被换 STALE） |
| `test_sequence_service.py`（12） | canonical CRUD+OCC、Phase1 快照/两路 fail-closed、Phase2+3 happy、race guard 两场景（final 切换/trim 窗口内变更→raced_superseded+evidence 保留）、No Auto Select、freshness 生命周期 |
| `test_sequences_api.py`（6） | CRUD 合同、400/404/409、dispatch 合同（stub 队列）、handler 注册、freshness 端点契约 |

**全量回归：1995 passed / 0 failed**（12:08；上基线 1946 → +49 新测试，零回归）。

---

## 6. 真机 E2E（§29/§30/§31）——ALL PASS

驱动：`_sequence_s1_e2e.py`（scratch）；结果：`_sequence_s1_e2e_results.json`。

### Part A 主序列（`__native_av_c1_e2e`，§29：不重新生成 Shot）

- 序列 `seq_3164f556`：s01_c01 → s01_c02 → s02_c01 → s02_c02(尾 trim 400ms)，4 镜全部 C1 人评通过 final。
- render `srender_b6b126d2` → **done**；输出 `projects/__native_av_c1_e2e/output/sequences/seq_3164f556/srender_b6b126d2.mp4`（4.36MB）。

§30 机器检查：

| 检查 | 结果 |
|---|---|
| 输出存在 / video stream | ✓ h264 1280x704 24fps yuv420p |
| audio stream | ✓ aac 48000Hz 立体声（原生音轨保留） |
| duration ≈ 计划总和 | actual 22789ms vs planned 22768ms → **Δ+21ms**（独立复算总和 22767ms，Δ22ms） |
| AV sync | video 22.751s / audio 22.789s → 漂移 38ms（容差 ±150ms） |
| 无意外黑帧 | blackdetect(≥1s) = **0 段** |
| 无意外静音 | silencedetect(≥1.5s,-55dB) = **0 段** |
| basis 正确 | sequence_render_basis_v1，4 clips 逐个 resolved version + sha256 源指纹 + trim 在档 |
| freshness | select 前 missing（§10 无 auto select）→ 显式 select 200 → **CURRENT** |

### Part B freshness 真机（`__native_av_c1r_e2e`，§31 小状态测试）

1. mini 序列 `seq_cbe21eb1`（s01_c01→s01_c02→s02_c01，20s）render done + select → **CURRENT**。
2. 显式切换 `s02_c01.final_video`：vv_bb262352 → vv_b1f41cdd（另一已有 done 版本）。
3. freshness → **STALE**，`mismatched_keys=['clips[2].resolved_video_version_id']`；canonical 不变（revision 未动）✓ §21 语义。
4. **零 provider/ffmpeg**：render 行数不变、sequences 目录文件 mtime 全等、无 auto render ✓ §22。
5. 恢复指针 → freshness **CURRENT**。项目状态已还原，两处 sequence 留作评审证据（均在 `__` 前缀 e2e 项目内，零用户项目污染）。

---

## 7. §33 成功标准逐条核对

| 标准 | 状态 |
|---|---|
| 多个 authoritative Shot 可被确定性组织 | ✓（timeline 纯派生+回归钉死） |
| ordering / trim 正确 | ✓（§13 示例逐字测试 + E2E trim 路径） |
| native AV preserved | ✓（AV together 单 pass；E2E 音轨在、静音 0） |
| Sequence output 可播放 / duration 正确 | ✓（22789ms vs 22768ms，Δ21ms；播放请人评） |
| render versioning 成立 | ✓（append-only + 显式 select + 状态机） |
| basis 成立 | ✓（sequence_render_basis_v1 actual-deps-only） |
| freshness 成立 | ✓（四状态机 + §31 真机翻转） |
| race guard 成立 | ✓（三阶段 + 窗口内变更两场景单测 + E2E 结构证据） |
| Shot 更新后 sequence stale / 不 auto render | ✓（§21/§22 单测 + 真机） |
| 现有 Shot backend 零 regression | ✓（1995 passed；冻结域零文件改动） |

→ **SEQUENCE ASSEMBLY S1 FOUNDATION PASS**（待人评确认）。

---

## 8. 人评指引（§30 人为只判五项）

review page（scratch）：`http://localhost:8915/_sequence_s1_review.html?project=__native_av_c1_e2e`
（静态服务器已起；后端 :8000 已运行，含新 router。页面支持播放每个 source Shot 与 final render、触发 render/select、显示 freshness。）

请判断：
1. 镜头顺序正确（s01_c01 → s01_c02 → s02_c01 → s02_c02）
2. 裁切点正确（末镜尾部裁掉 400ms）
3. 声音没有消失
4. 切镜没有明显技术错误
5. 整体连续播放正常

不要评价电影艺术上限（§30）。

---

## 9. 遗留 / 边界说明

- **AAC 帧取整**：per-clip ±120ms 容差常量（1 视频帧+1 AAC 帧+容器取整）；E2E 实测远小于此（Δ21ms 总量）。若未来 provider 产生异常 fps/规格，容差在 `media_ops.py` 常量处可调。
- **JobQueue 重启恢复**：sequence_render 属非 video kind——重启后 queued/rendering 行会被重路由，handler 幂等重跑（重渲染覆盖同 version_id 输出）；Phase3 race guard 保证不 attach 过期结果。
- **`CompositionManifest`（AI-5A/AI-7C）未动**：Run 临时物与 Sequence canonical 职责不同（无 trim/无版本/无 freshness），未来是否收敛由后续阶段决定。
- **已冻结域零接触**：`src/core/`、`src/generation/`、`src/audio/`、`routers/shots.py` 等无任何 diff（§3）。

## 10. STOP（§34）

Music / Narration / Audio Mixing / Sequence Agent / Editor Agent / Final Film Export **均未开始**。等待用户评审本报告与人评五项后再定 S2（Audio Layer Foundation）规格。

## 11. 人评返修记录

- **[2026-09-16] 播放无反应 bug（用户发现）**：`SequenceRenderVersion.output_file` 从未持久化（§9 minimum fields 漏实现）——review 页拿 None 拼播放地址 → 空地址。E2E 未抓到：驱动自拼文件路径、未读该字段（教训：E2E 断言应走消费方视角）。修复 = Phase 1 prepare 即落盘 `output_file`（`ef96b2e`）+ 测试钉死；两条既有渲染行元数据已回填；后端已重启加载修复。
- 顺带发现：用户试播期间重复点击「渲染」按钮产生 12 个冗余渲染版本（每次都真实渲染成功——pipeline 稳定性顺带得证；已清理，保留首个正式版本与 final 指针）。review 页已中文化 + 渲染中禁用按钮 + 自动轮询 + 播放失败可见提示（用户反馈）。

## 12. 人评结论（2026-09-16，用户）

**S1 五项人评全部通过**：镜头顺序 ✓ / 裁切点 ✓ / 声音没有消失 ✓ / 切镜无技术错误 ✓ / 整体连续播放正常 ✓（用户原话：「全都接上了……没什么大问题……总之目前接的不错」）。

→ **SEQUENCE ASSEMBLY S1 FOUNDATION PASS = FINAL**。

用户同时观察：**分镜间人物模样存在不一致**——已定性为 Shot 画面生产域课题（非 Sequence 域，§36：Sequence 不重新导演 Shot）。该课题与既有系统对应：Visual V1（视觉档案）+ Shot Binding V1（S3 系列 BOUND 路径把角色/场景参考钉进每镜）；E2E 所用 `__native_av_c1_e2e` 镜头为 C1 时代 UNBOUND i2v 镜，跨镜漂移属预期内信号，无需在本阶段处置。

按 §34：本阶段到此 STOP，等待用户拟定下一阶段（S2 Audio Layer Foundation 或其他方向）规格，不自动开始。
