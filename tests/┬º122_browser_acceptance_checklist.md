# AI-7B §122 Browser Acceptance Checklist

**Prerequisites:** Backend running (`D:/Delta Force/python.exe api_server.py`, port 8000),
Frontend running (`npm run dev`, port 7777). A project with at least 1 scene + 2 characters + 1 shot.

## Manual steps

1. Open project → navigate to **AI Team** page → click **Team Chat**
2. Send a project-level question (e.g., "Xiaoyu 出现在哪些场景？") → see Producer's read-only answer
3. Send a cross-module change request (e.g., "小雨改成长发，结局改成她救阿岚，最后几个镜头更悲壮")
   - The message should auto-detect as a change request (change verbs) and create a ChangeSession
   - A **ChangePlanReview card** appears below the Team Chat messages
   - Watch the status cycle: `路由中` → `咨询中…` → `分析依赖…` → `校验中…` → `构建预览…` → `待审查`
   - The status updates via polling (every ~2s) — no manual refresh needed
4. When **Needs your decision** appears:
   - Read the question
   - Click one of the option buttons (方案 A / 方案 B)
   - The session should auto-resume (recompile) and continue to `待审查`
5. When **Ready for review**:
   - Review the plan card:
     - **你要求修改** (Requested): direct changes with owner labels (剧本/角色/分镜)
     - **牵连修改** (Related): downstream impact-derived items
     - **保持不变** (Will remain unchanged): unaffected entities
     - **视觉变化** (Visual changes): tabs for Story / Characters / Scenes / Storyboard
       - Click each tab to see the diff entries (insert/delete/update/reorder)
     - **影响** (Impact): summary grid (scenes changed, shots to regenerate, etc.)
     - **执行顺序** (Execution order): DAG (script ∥ character → storyboard)
   - Review the **申请方案** badge: `preview_only · applyable=false（AI-7B 不修改项目）`
6. Click **批准此方案**:
   - Helper text below: "批准只会保存你的确认。当前阶段不会立即修改项目或重新生成素材。"
   - Status changes to `方案已批准 · 等待执行阶段（AI-7C）`
7. **Refresh browser** (F5):
   - Same ChangeSession restored (status `approved_for_execution` persists)
   - No duplicate sessions
8. **Return to project** — verify project content is **unchanged** (canonical project.json bytes identical)
9. **Open production/job UI** — verify **no new production job** was created
10. Try **请求修改** (Request Changes): enter feedback (e.g., "不要改 Scene 55") → plan version+1 created
11. Try **拒绝** (Reject): session status = rejected; historical proposals preserved

## What to verify

- ✅ No Apply/Execute button anywhere (only 批准/拒绝/请求修改)
- ✅ Approval does NOT execute anything (no workspace, no job, no canonical change)
- ✅ Polling works (status updates without manual refresh)
- ✅ Refresh restore works (session persists across reload)
- ✅ Visual Diff tabs show real structured diff entries
- ✅ Impact summary is deterministic (same inputs → same output)
- ✅ All in Chinese (user-facing labels, not internal IDs)
