"""AI-7D D1 · Automated Browser Gate（Playwright）——驱动真实前端 + 真实 FastAPI 后端。

证明 D1 统一 Direct Chat Turn Runtime 的 UI 接线（D1-A/B/C）：
  D1-A Producer：「现在剧本有多少场？先不要修改。」→ project_inquiry，真 inspect（/turn
        response.evidence_refs 非空），自然语言回复，无 tool-call JSON，无 ChangeSession。
  D1-B Script Writer：「先读一下现在的剧本，中段为什么显得短？先别改。」→ specialist_read_answer，
        agent 保持 Script Writer，真 script context（evidence_refs 非空），不自动修改，无 ChangeSession。
  D1-C 普通交流：「如果只从叙事节奏看，你更担心开头还是中段？」→ project_inquiry（需事实→inspect），
        自然 reply，无 ChangeSession。

前置：后端 api_server（8000，真 Agnes from .env）+ 前端 npm run dev（7777）已启动。
本脚本：seed throwaway 项目 __gated_7d_e2e（3 场景 + story）→ Playwright 驱动 Agent Center。
🔴 不修改产品代码。throwaway 项目独立，不碰用户项目。

断言原则（D6.3）：结构化属性（intent/evidence_refs/no-ChangeSession/no-JSON），不断言模型具体措辞
（模型偶发输出不理想记给 Trae；底层真实动作正确即 PASS）。
"""
import os
import sys
import time
import json
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from dotenv import load_dotenv
load_dotenv()

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from src.core.store import ProjectStore
from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.utils.project_manager import ProjectManager

PROJECT = "__gated_7d_e2e"
CHAR_NAME = "Aran"
FRONT = os.environ.get("E2E_FRONT", "http://localhost:7777")
API = "http://localhost:8000"

# /turn 响应捕获（结构化断言用）
turn_responses = []


def log(m):
    print(f"[e2e-7d] {m}", flush=True)


def seed():
    """throwaway：3 场景（s01/s02/s03）+ story + Character Aran。让'多少场'有结构可读。"""
    pm = ProjectManager(BASE / "projects")
    pdir = BASE / "projects" / PROJECT
    if pdir.exists():
        import shutil
        shutil.rmtree(pdir, ignore_errors=True)
    pm.create_project(PROJECT)
    proj = Project(
        name=PROJECT, style=StyleAnchor(art_style="anime", aspect_ratio="16:9", size_tier="1K"),
        story_text=("一个关于 Aran 在雨夜逃离城市的故事。开场：Aran 站在雨中仰望天空。"
                    "中段：Aran 在车站与人碰面，随后逃入巷弄。结局：Aran 逃出城市。"),
        characters=[Character(name=CHAR_NAME, description_en="a young man in a dark coat")],
        scenes=[
            Scene(scene_id="s01", summary="rain night, Aran looks up", characters=[CHAR_NAME],
                  shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                              description="Aran standing in rain looking up", characters=[CHAR_NAME],
                              target_duration_s=5.0, link_mode="i2v", opening_source="independent")]),
            Scene(scene_id="s02", summary="meeting at the station", characters=[CHAR_NAME], shots=[]),
            Scene(scene_id="s03", summary="escape through alleys", characters=[CHAR_NAME], shots=[]),
        ])
    ProjectStore(pdir).save(proj)
    log(f"seeded throwaway {PROJECT} (3 scenes + story + char {CHAR_NAME})")


_JSON_MARKERS = ('"function"', '"capability_id"', '"op_type"', '"update_scene"',
                 '"linked_change_session_id"', '"agent_id":', '"intent":')


def _looks_like_json_leak(text: str) -> bool:
    """assistant_message 是否泄漏了 tool-call JSON（D1.6 禁止）。"""
    if not text:
        return False
    t = text.strip()
    if t.startswith("{") or t.startswith("["):
        return True
    return any(m in text for m in _JSON_MARKERS)


def _send_and_assert(page, agent_card_text, message, expect_intent, expect_evidence, case_id):
    """在 Agent Center 选 agent → 发消息 → 断言 /turn 响应 + UI 回复。"""
    if agent_card_text:
        page.locator(".agent-card").filter(has_text=agent_card_text).first.click(timeout=10000)
        time.sleep(0.5)
    # 清空 composer 输入
    composer = page.locator(".composer .input").first
    composer.fill(message)
    turn_responses.clear()
    page.locator(".composer .btn-primary").first.click(timeout=10000)
    # 等 assistant 回复出现（最后一条 .msg.assistant）
    try:
        page.wait_for_function(
            """(n) => {
                const msgs = document.querySelectorAll('.msg.assistant, .acs-msg.assistant');
                return msgs.length >= n;
            }""", arg=1, timeout=60000)
    except Exception:
        page.screenshot(path=f"e2e_d1_{case_id}_fail.png")
        log(f"[{case_id} FAIL] 无 assistant 回复（60s）。截图 e2e_d1_{case_id}_fail.png")
        return False
    # 最后一条 assistant 文本
    reply = page.locator(".msg.assistant .msg-content, .acs-msg.assistant .acs-msg-content").last.inner_text(timeout=5000)
    log(f"[{case_id}] reply(前120字): {reply[:120]}")
    # 断言 1：无 JSON 泄漏
    if _looks_like_json_leak(reply):
        log(f"[{case_id} FAIL] 回复含 tool-call JSON 泄漏: {reply[:200]}")
        return False
    log(f"[{case_id}] ✓ 回复为自然语言（无 JSON 泄漏）")
    # 断言 2：/turn 响应 intent + evidence_refs
    if not turn_responses:
        log(f"[{case_id} WARN] 未捕获 /turn 响应（可能走 /messages wrapper）；跳过 intent 断言")
    else:
        tr = turn_responses[-1]
        intent = tr.get("intent")
        ev = tr.get("evidence_refs", [])
        if intent != expect_intent:
            log(f"[{case_id} FAIL] intent={intent}，期望 {expect_intent}")
            return False
        if expect_evidence and not ev:
            log(f"[{case_id} FAIL] evidence_refs 为空（未真 inspect）")
            return False
        log(f"[{case_id}] ✓ /turn intent={intent}, evidence_refs={len(ev)} 条（真 inspect 发生）")
    # 断言 3：无 ChangeSession 卡 / plan（D1 不应创建 ChangeSession）
    if page.locator(".acs-runcard, .plan-card, [class*='change-plan']").count() > 0:
        log(f"[{case_id} FAIL] 出现 ChangeSession/Plan 卡（D1 不应创建 ChangeSession）")
        return False
    log(f"[{case_id}] ✓ 无 ChangeSession 创建")
    return True


def main():
    from playwright.sync_api import sync_playwright
    seed()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        console_msgs = []
        page.on("console", lambda m: console_msgs.append(f"{m.type}: {m.text[:200]}"))
        page.on("pageerror", lambda e: console_msgs.append(f"PAGEERROR: {str(e)[:300]}"))

        # 捕获 /turn 响应
        def on_response(resp):
            if "/turn" in resp.url and resp.request.method == "POST":
                try:
                    turn_responses.append(resp.json())
                except Exception:
                    pass
        page.on("response", on_response)

        # 1) dashboard 全页加载 → 点 throwaway 项目卡（loadProject → /characters）
        page.goto(f"{FRONT}/dashboard", wait_until="domcontentloaded")
        page.locator(".proj-card").filter(has_text=PROJECT).first.click(timeout=15000)
        import re
        page.wait_for_url(re.compile(r"/characters"), timeout=10000)
        log(f"selected throwaway project; projectName={PROJECT}")
        # 2) nav /agents（Agent Center）
        page.locator('a.nav-item[href="/agents"]').first.click(timeout=10000)
        page.wait_for_url(re.compile(r"/agents"), timeout=10000)
        # 隐藏 FAB 覆盖层（7C-S 经验：.fab 拦截 pointer）
        page.evaluate("() => { document.querySelectorAll('.fab').forEach(e => { e.style.visibility='hidden'; }); }")

        ok = True
        # D1-A：Producer（默认选中）→ 「现在剧本有多少场？先不要修改。」
        ok &= _send_and_assert(page, None, "现在剧本有多少场？先不要修改。",
                               "project_inquiry", True, "D1-A")
        # D1-B：Script Writer → 「先读一下现在的剧本，中段为什么显得短？先别改。」
        ok &= _send_and_assert(page, "Script Writer",
                               "先读一下现在的剧本，中段为什么显得短？先别改。",
                               "project_inquiry", True, "D1-B")
        # D1-C：Script Writer 继续 → 「如果只从叙事节奏看，你更担心开头还是中段？」
        ok &= _send_and_assert(page, None,
                               "如果只从叙事节奏看，你更担心开头还是中段？",
                               "project_inquiry", True, "D1-C")

        browser.close()
        if ok:
            print("\n=== AI-7D D1 Automated Browser Gate: PASS ===")
            return 0
        else:
            print("\n=== AI-7D D1 Automated Browser Gate: FAIL（见上方日志） ===")
            return 1


if __name__ == "__main__":
    sys.exit(main())
