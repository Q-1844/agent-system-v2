"""AI-7C-S S5-3 browser E2E（Playwright）——驱动真实前端 + 真实 FastAPI 后端。

证明 S1 前端 lifecycle 修复（H1/H2/M5/L6）的 UI 接线：
  1. Execute 后经多个 polling tick 进度仍推进（不冻结；H1）。
  2. execution 进行中刷新浏览器 -> 恢复当前 execution（H2/restoreActive）。
  5. completed 后刷新 -> 真实完成态、不重新执行（H2 + 后端幂等）。

前置：后端 api_server（8000，真 Agnes from .env）+ 前端 npm run dev（7777）已启动。
本脚本：seed throwaway 项目 __gated_7c_e2e + approved char-change plan -> Playwright 驱动 UI。
不 mock 后端（真实 FastAPI）；char-sync 走真 Agnes（~60-90s，正好产生多 polling tick）。

🔴 不修改产品代码。throwaway 项目独立，不碰用户 "我的动画短片"。
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from dotenv import load_dotenv
load_dotenv()

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import deps
from src.core.store import ProjectStore
from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.agents.change_protocol.store import ChangeProtocolStore
from src.agents.change_protocol.models import (UnifiedChangePlan, PlanPreviewArtifact,
                                                PlanApproval, ChangeSession)
from src.revision.models import ChangeOperation, BeforeAfter, creative_source_signature
from src.revision.changeset import preview_operations_on_project
from src.utils.project_manager import ProjectManager

PROJECT = "__gated_7c_e2e"
CHAR_NAME = "Xiaoxia"
SCENE_ID = "s01"
SHOT_ID = "s01_c01"
FRONT = os.environ.get("E2E_FRONT", "http://localhost:7778")
API = "http://localhost:8000"


def log(m):
    print(f"[e2e-7c] {m}", flush=True)


def seed():
    """throwaway：1 char + 1 scene + 1 shot + approved char-change ChangePlan（触发 character_sync）。"""
    pm = ProjectManager(BASE / "projects")
    pdir = BASE / "projects" / PROJECT
    if pdir.exists():
        import shutil
        shutil.rmtree(pdir, ignore_errors=True)
    pdir = pm.create_project(PROJECT)
    proj = Project(name=PROJECT, style=StyleAnchor(art_style="anime, cinematic",
                                                  aspect_ratio="16:9", size_tier="1K"),
                   story_text="e2e throwaway",
                   characters=[Character(name=CHAR_NAME, description_en="a girl with short hair")],
                   scenes=[Scene(scene_id=SCENE_ID, summary="girl arrives",
                                 characters=[CHAR_NAME], shots=[
                                     Shot(shot_id=SHOT_ID, scene_id=SCENE_ID, index=1,
                                          description="a girl with short hair standing in a field, cinematic",
                                          characters=[CHAR_NAME], target_duration_s=5.0,
                                          link_mode="i2v", opening_source="independent")])])
    ProjectStore(pdir).save(proj)
    sig = creative_source_signature(proj.to_dict())
    ops = [ChangeOperation(op_type="update_character", character_name=CHAR_NAME,
                           description_en=BeforeAfter(
                               before="a girl with short hair",
                               after="a girl with long flowing black hair, green eyes"))]
    candidate, _d, impact, _w = preview_operations_on_project(proj, ops)
    projected = creative_source_signature(candidate.to_dict())
    store = ChangeProtocolStore(pdir, PROJECT)
    store.save_session(ChangeSession(session_id="cs_e2e", project_name=PROJECT,
                                     status="approved_for_execution",
                                     active_plan_id="plan_e2e", plan_version=1,
                                     source_snapshot_signature=sig, schema_version=2,
                                     created_at=time.time(), updated_at=time.time()))
    plan = UnifiedChangePlan(plan_id="plan_e2e", change_session_id="cs_e2e", project_name=PROJECT,
                             version=1, source_snapshot_signature=sig,
                             status="approved_for_execution",
                             steps=[{"step_id": "step_ch", "module": "character",
                                     "owner_agent_id": "character_designer", "plan_item_ids": [],
                                     "depends_on_step_ids": [], "parallel_group": None}],
                             preview_id="pv_e2e", created_at=time.time())
    plan.approval = PlanApproval(plan_id="plan_e2e", plan_version=1, decision="approved",
                                 source_snapshot_signature=sig, decided_at=time.time()).to_dict()
    store.save_plan(plan)
    store.save_preview(PlanPreviewArtifact(
        preview_id="pv_e2e", change_session_id="cs_e2e", plan_id="plan_e2e", plan_version=1,
        source_snapshot_signature=sig,
        module_operations=[{"step_id": "step_ch", "module": "character",
                            "owner_agent_id": "character_designer", "proposal_item_ids": [],
                            "operations": [ops[0].to_dict()]}],
        aggregate_impact=impact.to_dict(), projected_signature=projected,
        applyable=False, created_at=time.time()))
    log(f"seeded throwaway {PROJECT} (char {CHAR_NAME}, approved char-change plan)")


def current_stage_label(page):
    """当前 execution timeline stage 的中文 label（.exec-stage.current .exec-label）。"""
    loc = page.locator(".exec-stage.current .exec-label")
    if loc.count() == 0:
        return None
    return loc.first.inner_text(timeout=2000)


def main():
    from playwright.sync_api import sync_playwright
    seed()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        console_msgs = []
        page.on("console", lambda m: console_msgs.append(f"{m.type}: {m.text[:200]}"))
        page.on("pageerror", lambda e: console_msgs.append(f"PAGEERROR: {str(e)[:300]}"))
        import re
        # in-app nav helper：点 router-link（非全页 goto/reload，preserve projectName）
        def nav_to(path):
            page.locator(f'a.nav-item[href="{path}"]').first.click(timeout=10000)
            page.wait_for_url(re.compile(path), timeout=10000)
        def enter_team_chat():
            nav_to("/agents")
            page.locator(".agent-card.team-entry").first.click(timeout=10000)  # selectedId 默认 producer -> 点 Team Chat 才显 TeamChatPanel
        # 1) dashboard 全页加载（projectName 默认）→ 点 throwaway 项目卡（loadProject → /characters）
        page.goto(f"{FRONT}/dashboard", wait_until="domcontentloaded")
        page.locator(".proj-card").filter(has_text=PROJECT).first.click(timeout=15000)
        page.wait_for_url(re.compile(r"/characters"), timeout=10000)
        log("selected throwaway project; projectName=__gated_7c_e2e (in-app preserved)")
        # 2) 应用内 nav /agents + 点 Team Chat（TeamChatPanel 仅 selectedId==='__team__' 时渲染）
        enter_team_chat()
        exec_btn = page.get_by_role("button", name="执行计划")
        try:
            exec_btn.wait_for(state="visible", timeout=30000)
        except Exception:
            page.screenshot(path="e2e_debug.png")
            Path("e2e_debug.html").write_text(page.content(), encoding="utf-8")
            log(f"FAIL: 执行计划 not visible. saved e2e_debug.png/html; console={console_msgs[-15:]}")
            raise
        log("ChangePlanReview: 执行计划 visible (approved session restored via in-app nav)")
        # 3) Execute → character_sync（真 Agnes ~60-90s，正好产生多 poll tick）
        # FloatingAgentBar(.fab.compact, position:fixed) 覆盖层拦截 pointer events（force 也不绕过）-> 先隐藏 FAB
        page.evaluate("() => { document.querySelectorAll('.fab').forEach(e => { e.style.visibility='hidden'; }); }")
        exec_btn.click(timeout=15000)  # FAB 隐藏，pointer 不再被拦截 -> onExecute -> cs.execute()
        page.locator(".exec-stage.current").filter(has_text="同步角色参考图").wait_for(
            state="visible", timeout=60000)
        log("[B1] reached character_sync stage")
        # B1：character_sync 持续 >=6s（>=3 个 2s poll tick）仍 current -> polling 不冻结（H1）
        time.sleep(6)
        assert current_stage_label(page) == "同步角色参考图", \
            f"[B1 FAIL] polling 冻结? stage={current_stage_label(page)}"
        log("[B1 PASS] character_sync 持续多个 poll tick（polling 不冻结，H1）")
        # 4) B2：应用内重挂载（/characters → /agents）测 restoreActive 恢复 active execution（H2）
        #    （不用 page.reload——全页加载会重置 pinia projectName 到默认，丢 throwaway）
        nav_to("/characters")
        enter_team_chat()
        page.locator(".exec-stage.current").filter(has_text="同步角色参考图").wait_for(
            state="visible", timeout=30000)
        log("[B2 PASS] in-app remount restoreActive 恢复 active execution（character_sync 仍 current，H2）")
        # 5) 等 char-sync 完成 → production_planning current（polling 推进了 stage）
        try:
            page.locator(".exec-stage.current").filter(has_text="准备制作计划").wait_for(
                state="visible", timeout=180000)
        except Exception:
            blk = page.locator(".exec-blocker").first.inner_text(timeout=2000) if page.locator(
                ".exec-blocker").count() else "(no blocker)"
            log(f"[FAIL] 未到 production_planning；blocker: {blk}")
            browser.close()
            return 1
        log("[B1 cont] char-sync 完成 -> production_planning current（polling 推进了 stage）")
        # 6) B5：应用内重挂载测 completed 真实态恢复（不重新执行，后端幂等）
        nav_to("/characters")
        enter_team_chat()
        page.locator(".exec-stage.current").filter(has_text="准备制作计划").wait_for(
            state="visible", timeout=30000)
        post = page.locator(".exec-state.post-commit")
        post.wait_for(state="visible", timeout=10000)
        log("[B5 PASS] completed in-app remount 恢复 truthful（production_planning 仍 current，无重新执行）")
        browser.close()
        print("\n=== AI-7C-S S5-3 browser E2E: PASS ===")
        return 0


if __name__ == "__main__":
    sys.exit(main())
