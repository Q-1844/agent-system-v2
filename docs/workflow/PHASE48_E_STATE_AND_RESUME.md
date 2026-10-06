# PHASE48_E_STATE_AND_RESUME

## 1. 会话中断的真实原因（用户更正后归因）

**崩溃根因 = 用户关闭了进程，因为 Agnes 余额耗尽。**
这不是 harness 进程树回收，也不是后端崩溃 bug。
（本文件之前版本里所有"harness 清理进程树"的归因一律作废。）

用户原话：**余额耗尽我关闭了所有进程导致的**。

后果与约束：
- 后端 API 服务（localhost:8000）已随进程关闭而下线。
- 已生成的视频/图像文件在磁盘上完好，审核不受影响。
- **在余额充值之前，任何新生成调用都会失败**——不要发起，也不要重试。

## 2. Phase48 A-D 批状态（已完成并提交）

| 批次 | 状态 | 提交 |
| --- | --- | --- |
| A 基线与最小修复 | 完成 | e42910b |
| B 原生音效保留与装配政策 | 完成 | 本会话提交 |
| C 接续方式受控对照 | 完成 | 本会话提交（两个提交：驱动+证据 / 报告+M4审计） |
| D 多人物、道具交接与人物对白 | 完成 | 本会话提交（两个提交） |

四批报告：`docs/workflow/PHASE48_{B,C,D}_REPORT.md`（均可从证据文件重算）。

## 3. E 批当前进度（run_e9484ba7ad72 @ P48_E_2D）

- 节点进度：n_producer✅ n_sw✅ n_rv_sw✅ n_cd✅ n_rv_cd✅ → **n_sc failed（1 个 attempt）**。
- **勘误（2026-10-06 晚，以 events.jsonl 为准）**：本文件此前写"两次 attempt"有误。
  n_sc 实际只有 1 个 attempt `att_6cdd471c8d`：11:11 派发意图，场景 Agent 真实工作
  约 17 分钟后 11:28:59 结算，完成校验不过（`目标级输出缺项：要求 location ×2…`
  仅 1 个（需 2）——节点未启用 allow_verified_reuse）→ 诚实落 failed。
  此前会话的 continue 尝试实际返回 **409 run_terminal**（引擎缺陷，见下），并未
  开出"attempt2"。
- **引擎缺陷修复（Phase48 E，随本批提交）**：`continue_run` 的 docstring 契约是
  "failed 节点开新 attempt"，但 Phase 46 起的前置 `run_terminal` 检查把 failed
  run 一律拒绝——failed 节点分支不可达。修复：run=failed 且目标节点可重试
  （failed/needs_reconciliation）时放行；stopped/rejected/completed 仍拒绝；
  回归测试 `test_continue_failed_run_resets_failed_node` +
  `test_continue_still_rejects_stopped_and_completed`。
- 已完成审查链：n_rv_cd 曾 needs_reconciliation（选用缺 versionId）→
  修复 pendingSelection.spec.versionId=ref_e85d29842cbd → 补 Look 机器审查
  （rev_2988888902b9）+ 人评 pass（hr_ea230fbf60ce，执行者目检：双马尾/黄雨衣/牛仔裤，
  无伪影，2D 风格一致）→ continue → n_rv_cd success。

### 续跑步骤（新会话按序执行）

1. 启动后端（可靠方式，见 §4），`curl http://localhost:8000/api/health` 确认
   `{"status":"ok","controller_ready":true}`。
2. **确认余额可用**——2026-10-06 晚已用 .env 的 AGNES_API_KEY（注意 config.yaml
   顶层 `agnes:` 段的 `${AGNES_API_KEY}` 才是后端实际用的 key；`ai:` 段是遗留
   旧 key，用它探针会得 401 无效令牌）做 1 次 1-token 文本探针得 HTTP 200，
   账户活跃且有额度（Agnes 为统一额度，文本被拒即媒体也不可用）。若后续再遇
   余额耗尽：即停、告知用户、不重试生成。
3. `python _p48_e_check.py continue`（POST continue nodeId=n_sc，开新 attempt；
   引擎修复后 failed run 可 continue）。
4. 轮询 run 状态；6 个中间关口（n_rv_sw/cd/sc/board/v1/v2）按实验惯例自动通过
   （注记"E批实验中间关口自动通过（非用户关口）"）；**n_rv_final 成片审定留用户**。
5. 场景节点要求 2 个新 Location：若 Agent 判定故事只需 1 个地点，正确做法是
   生产第 2 个（如"傍晚田野"），不改小完成标准。

## 4. 后端启动（可靠方式）

```powershell
Start-Process -FilePath "D:\Delta Force\python.exe" `
  -ArgumentList "api_server.py" `
  -WorkingDirectory "<仓库根>" `
  -WindowStyle Hidden `
  -RedirectStandardOutput backend_runtime.log `
  -RedirectStandardError backend_err.log
```

验证：`curl http://localhost:8000/api/health` → `{"status":"ok","controller_ready":true}`。
`_launch_backend_detached.py` 的孙进程方式在本 harness 下 ~9h 后仍被宿主进程树
回收（11:28 静默死亡实测）——不要再用。

## 5. E 批剩余清单（按序）

1. 风格片 1 续跑至 n_rv_final（4 镜 ≈16s 配额 + ~1h 队列）。
2. 风格片 2/3（Watercolor/Realistic）：同 brief 同参数开 W09
   （项目 render_family/art_direction 见 `_p48_e_evidence.json`）。
3. 说明片（P48_E_Explainer）：手摇手电筒组装与工作原理，无旁白，W09 videoCount=4。
4. 新窗口盲测：全新会话 + 冻结 Skill v1.4.0 + 新故事（非《风筝与竹竿》），
   只给目标/约束/批准资产/允许审核点，不透露每镜"标准答案"。
5. 三轮独立复验（独立 run/会话，不同故事）：①连续动作与身份；②多人物道具与对白
   （D 批路径已验证）；③风格迁移与视觉因果。每轮查出站 thinking 与 Skill v1.4.0
   contentHash（catalog 端点实测）。
6. 穿插一次刷新/中断/晚到回执演练。
7. 交付 PHASE48_E_REPORT.md 与 PHASE48_FINAL_REPORT.md（E 全部完成后才写终报）。

## 6. 用户关口（现有可审产物）

- B 批成片：`projects/P48_B_Audio/output/sequences/seq_7fd54ccc/srender_5ddd85d0.mp4`
  （与 srender_202f5365 逐字节同；试听点见 PHASE48_B_REPORT.md §6）。
- D 批 18 条：`projects/P48_D_Cast/output/videos/s01_c01.mp4 … s01_c18.mp4`
  （重点 c07 交接方向反转裁定、c10/c12 对白清晰度、c16-c18 跨镜声线人耳终判）。
- C 批 45 份接缝预览：`_p48_c_review/<tgt>/seam_preview.mp4`
  （重点 s02_c44 M4-C 分身例、s02_c13-21 M2-B 构图受限例）。
- Phase47 四部旧片 run 层成片审定仍 pending（未动）。

## 7. 纪律（新会话必须遵守）

诚实恢复（中断≠failed 翻转、失败保留）；不谎报成功；用户关口不代办；
**余额耗尽即停并告知用户，不重试生成**；所有生成走正式路径并落 provider job id
+媒体 hash+回执；报告只写可验证事实，写入前自查一遍。
