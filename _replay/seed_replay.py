# -*- coding: utf-8 -*-
"""Seed an independent replay project for Session Governance Hardening 验收.

构造一个 schema 合法的小型 canonical（story + characters + 4 个含摘要的故事段，
故意【不】预置镜头——镜头由真实 Director turn 创建，以自然累积结构写计数 +
形成真实的会话 staleness）。同时写入 creative_brief.json（含「陈海锋/老船长
全程只无线电声音、不视觉出镜」硬约束，供 Scenario 4 审查者对照）。

不跑 SW/CD（被测对象是 Director / Runtime 行为，SW/CD 创作非本轮验证内容；
手工 seed canonical 是任务明确允许的「独立 replay 项目」）。
"""
import json, os, sys, time
import httpx

CWD = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(CWD)  # animation-storyboard
sys.path.insert(0, REPO)
from src.core.models import Project, Scene, Character, StyleAnchor

PROJECT = "Replay_Gov_20260902"
BASE = "http://localhost:8000"

BRIEF = (
    "《风暴灯塔》短片分镜：台风深夜，孤立灯塔。老船长陈海锋的渔船在近海引擎失灵，"
    "他通过无线电发出求救。【创作硬约束】陈海锋 / 老船长全程只通过无线电声音出现，"
    "始终不视觉出镜，绝不在画面里出现——观众始终只闻其声、不见其人。"
    "灯塔守林晚星（船医）与少年小满听到呼叫后组织救援，海岸警卫顾岩带队抵达海上施救，"
    "在风浪中救起老船长（老船长仍只在船舱内 / 被裹在担架里，未露面）。"
    "最后风暴减弱，老船长被送医（不出镜），林晚星在灯塔归还老船长的怀表。"
    "请基于此为各故事段设计分镜。"
)


def main():
    # 1. 建项目目录（API 建目录 + 空 project.json；重名忽略）
    try:
        r = httpx.post(f"{BASE}/api/projects/create", params={"name": PROJECT}, timeout=30)
        print("create_project:", r.status_code, r.text[:160])
    except Exception as e:
        print("create_project failed:", e)

    pdir = os.path.join(REPO, "projects", PROJECT)
    out = os.path.join(pdir, "output")
    os.makedirs(out, exist_ok=True)

    # 2. seed canonical
    chars = [
        Character(name="陈海锋",
                  description_en="an old sea captain, weathered face, grey beard, yellow raincoat (radio-only, never on-screen)"),
        Character(name="林晚星",
                  description_en="a young woman in a doctor coat, the lighthouse keeper, determined eyes"),
        Character(name="小满",
                  description_en="a teenager boy in a raincoat, eager and brave"),
        Character(name="顾岩",
                  description_en="a coast guard officer in uniform, athletic build"),
    ]
    scenes = [
        Scene(scene_id="s01", title="风起呼叫", location="灯塔监听室 / 近海",
              time_of_day="深夜",
              summary=("台风深夜，孤立灯塔。老船长陈海锋的渔船在近海引擎失灵，"
                       "他通过无线电发出求救（只闻其声，不见其人）。灯塔守林晚星与少年小满"
                       "在塔顶监听室听到呼叫，林晚星接报、安抚老船长，记录方位。"),
              characters=["陈海锋", "林晚星", "小满"]),
        Scene(scene_id="s02", title="准备救援", location="灯塔 / 码头",
              time_of_day="深夜",
              summary=("林晚星迅速组织救援，指挥小满备好救生艇与绳索。海岸警卫顾岩接到通报，"
                       "带队从码头出动，驶向风浪中的渔船。"),
              characters=["林晚星", "小满", "顾岩"]),
        Scene(scene_id="s03", title="海上施救", location="近海风浪",
              time_of_day="深夜至黎明前",
              summary=("顾岩的救援艇抵近失火渔船。风浪中顾岩抛缆、拉拽，将老船长从船舱救出"
                       "——但老船长始终蜷缩在舱内 / 被裹在担架里，观众仍未看见其面容。"
                       "惊险的拉拽与浪花。"),
              characters=["顾岩", "陈海锋"]),
        Scene(scene_id="s04", title="黎明落幕", location="灯塔 / 码头",
              time_of_day="黎明",
              summary=("风暴减弱，天边泛白。老船长被抬上救护车送医（仍未出镜，只见担架与车）。"
                       "林晚星在灯塔归还老船长的怀表，小满目送。"),
              characters=["林晚星", "小满", "陈海锋"]),
    ]
    proj = Project(name=PROJECT, story_text=BRIEF, characters=chars,
                   scenes=scenes, next_scene_seq=5, style=StyleAnchor())
    pj = os.path.join(out, "project.json")
    with open(pj, "w", encoding="utf-8") as f:
        json.dump(proj.to_dict(), f, ensure_ascii=False, indent=1)
    print("seeded project.json:", pj)

    # 3. seed creative_brief.json（Scenario 4 审查者必须能拿到原始 brief）
    ag = os.path.join(out, "agents")
    os.makedirs(ag, exist_ok=True)
    bp = os.path.join(ag, "creative_brief.json")
    with open(bp, "w", encoding="utf-8") as f:
        json.dump({"project": PROJECT, "brief": BRIEF,
                   "captured_from_agent": "seed", "captured_at": time.time()},
                  f, ensure_ascii=False, indent=2)
    print("seeded creative_brief.json:", bp)

    # 4. 清掉本项目的旧 governance / session 状态（fresh replay）
    for fn in ("session_governance.json",):
        fp = os.path.join(ag, fn)
        if os.path.exists(fp):
            os.remove(fp)
    print("done seed. canonical scenes:", len(scenes), "chars:", [c.name for c in chars])


if __name__ == "__main__":
    main()
