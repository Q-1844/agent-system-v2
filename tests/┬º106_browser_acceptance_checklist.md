# AI-7C §106 Browser Acceptance Checklist

**Prerequisites:** Backend restarted (`D:/Delta Force/python.exe api_server.py`, port 8000) — **必须重启**，否则 AI-7B/7C 新 router/runtime/change-protocol 代码未加载。Frontend (`npm run dev`, port 7777)。一个测试项目（≥1 scene + ≥1 character + ≥1 shot，已 approved production plan）。**不要用正式项目测。**

> 执行链路重点：**批准 ≠ apply（§3）**。批准后项目不动；Execute 是显式动作。执行精确重放批准内容，不重决策、不重提交、不重复 Agnes。

## Manual steps

1. **Team Chat 创建变更请求**：AI Team → Team Chat → 发一条跨模块 change request（如「小雨改成长发，最后几个镜头更悲壮」）。
   - 自动识别为 change request → ChangeSession → **ChangePlanReview 卡片**出现。
   - 状态经 polling（~2s）轮询推进：`路由中` → `咨询中…` → `分析依赖…` → `校验中…` → `构建预览…` → `待审查`。
2. **待审查**：Review plan card（Requested / Related / Unchanged / Visual Diff tabs / Impact / Execution order）。badge `preview_only · applyable=false`。
3. **批准此方案** → helper text「批准只会保存你的确认…」。状态 → `方案已批准`。
   - 🔴 **确认批准后项目内容不变**（canonical project.json 字节未变）。
   - 🔴 **确认无自动 Execute**——批准后停在「方案已批准」+ Execute 按钮（§3）。
4. **Execute**：选制作模式（affected_production 默认 / creative_only）→ 点「执行计划」。
   - 看 **execution timeline**（§86）：✓重验 → ✓建 Workspace → ✓暂存创作变更 → ✓校验预览一致 → ●原子提交创作变更 → ○同步角色参考图 → ○… 各 stage done/current/pending。
   - **§79 pre/post-commit 措辞**：提交前「尚未提交任何项目变更」；提交后「创作变更已提交。制作尚未完成。」
   - creative_only：提交后 → `完成`（production_skipped，0 Agnes）。
5. **F5 刷新**（§89）：execution 状态恢复（timeline + stage + commit 态保留）；无重复 execution。
   - 若 production_run_id 存在：production run 进度恢复；JobQueue SSE 重连（snapshot 恢复队列，SSE 失效降级轮询）。
6. **stale Plan**（§81）：批准后手动改 canonical（如编辑剧本）→ Execute → `PROJECT_CHANGED_AFTER_APPROVAL` blocked。
   - 措辞：「需重新提案/审批（不可 rebasing resume）」+ 无 Resume 恢复（仅提示重新提案）。
   - 🔴 0 mutation（canonical 未被覆盖）。
7. **cancel before commit**（§38）：Execute 到 canonical_commit 前取消 → `cancelled`，canonical 不动。
   - 措辞：「取消将安全停止，canonical 不动」。
8. **cancel after commit**（§38）：提交后取消 → creative 保留，`cancel_requested`，停后续 production。
   - 措辞：「取消不会回滚已提交的创作变更」。

## What to verify（PASS 判据）

- ✅ 批准 alone → 0 mutation（canonical 字节不变、0 workspace、0 job、0 Agnes）。
- ✅ Execute 不自动发生在批准时（§3）。
- ✅ execution timeline 反映真实 stage（不造 LLM 百分比）。
- ✅ pre/post-commit 措辞与真实状态一致（§79）。
- ✅ canonical creative commit 至多一次（刷新/重试不二次提交）。
- ✅ 受影响 scope only（不整项目重生成）——改 1 个 shot 不触发 50-shot 重做。
- ✅ 历史资产保留（旧 VideoVersion 不删）。
- ✅ refresh 恢复 execution（无重复 execution、无重复 Agnes submit）。
- ✅ stale → blocked + 0 mutation + 不可 rebasing resume。
- ✅ cancel before/after commit 措辞 truthful（§38）。
- ✅ JobQueue SSE 重连：刷新后 snapshot 恢复队列；SSE 失效降级轮询。

## 看似正常其实应判 FAIL

- 批准后项目**变了** → FAIL（批准不该 mutate）。
- Execute 后 canonical 提交了**两次** → FAIL（§16/§34）。
- 改 1 个 Scene 却跑**全项目** → FAIL（§59 scoped）。
- 刷新后出现**第二个 execution** → FAIL（§15 幂等）。
- 刷新后**重新提交 video**（video_id 变）→ FAIL（§71/§72）。
- stale 后 UI 仍显示「Resume」可恢复 → FAIL（§81）。
- 创意 Specialist 在执行期**重新决策**内容（plan 文本与批准不符）→ FAIL（§9）。
- 旧 VideoVersion 被**删除** → FAIL（§42）。
