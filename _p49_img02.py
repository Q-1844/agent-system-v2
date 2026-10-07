# -*- coding: utf-8 -*-
"""P49 图片批 02：写实双人物分镜首帧——多手法×30张/格 + 分类预览页。

用户要求：写实风、两个人物、每个分镜首帧用不同手法各生成 30 张、预览页分类显示。

分镜（3）×手法（4）×30 = 360 张：
  分镜 SH1 雨夜巷口递灯笼 / SH2 共伞同行 / SH3 灯下对视大笑
  手法 T1 短prompt直出 / T2 设定图i2i / T3 场景底图i2i / T4 模型自写长prompt
断点续跑：_p49_img02_evidence.json 记录已完成格；预览页 projects/P49_IMG_02/output/preview.html

用法：
  python _p49_img02.py sheet        # 生成双人物设定图（T2 参考）
  python _p49_img02.py scene        # 生成场景底图（T3 参考）
  python _p49_img02.py long         # 模型自写 T4 长 prompt（每分镜一条）
  python _p49_img02.py batch [限额] # 批量生成（默认全量 360）
  python _p49_img02.py preview      # 生成分类预览页
"""
import asyncio
import base64
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
PROJ = "P49_IMG_02"
OUT = ROOT / "projects" / PROJ / "output" / "img_batch"
EV = ROOT / "_p49_img02_evidence.json"
PER_TECH = 30
STYLE = ("photorealistic cinematic rendering, natural lighting, shallow depth of field, "
         "filmic grade")

BOY = ("10-year-old Chinese boy, messy short dark hair, blue-and-white striped T-shirt, "
       "dark green overalls")
GIRL = ("8-year-old Chinese girl, twin tails tied with red hair ties, bright yellow "
        "raincoat")

SHOTS = {
    "SH1": "雨夜巷口，男孩把手持的暖黄纸灯笼递向女孩，女孩双手来接",
    "SH2": "雨夜巷子，两人共撑一把油纸伞并肩同行，女孩提着灯笼照亮脚下",
    "SH3": "灯笼暖光下两人对视大笑，雨丝在光里斜织",
}

SHEET_PROMPT = (f"Character reference sheet, two Chinese children standing side by side "
                f"full body, photorealistic cinematic rendering: LEFT {BOY}; RIGHT {GIRL}; "
                f"plain light background, natural skin texture, high detail, no text")
SCENE_PROMPT = (f"Empty night alley scene in the rain, no people, {STYLE}: old brick "
                f"walls, wet cobblestones with warm reflections, a street lamp glowing, "
                f"rain streaks visible against the dark, cinematic composition, no text")

T1_PROMPTS = {
    "SH1": f"Two Chinese children in a rainy night alley: {BOY} hands a glowing paper "
           f"lantern to {GIRL} who reaches with both hands, wet cobblestones reflect warm "
           f"light, {STYLE}, cinematic two-shot, no text",
    "SH2": f"{BOY} and {GIRL} walk together under one oil-paper umbrella in a rainy night "
           f"alley, the girl holds a glowing lantern lighting the puddles, {STYLE}, "
           f"cinematic medium shot, no text",
    "SH3": f"{BOY} and {GIRL} laugh looking at each other under warm lantern light in a "
           f"rainy night alley, rain streaks lit by the lantern glow, {STYLE}, close "
           f"two-shot, no text",
}
T4_INSTRUCTION = (
    "请为一张 photorealistic 电影感剧照写一个 600-900 字的中文图像生成提示词（只输出提示词）。"
    "主题：{theme}。人物：{boy}；{girl}。请覆盖：两人站位与肢体动作的每一处细节、表情与视线、"
    "服装材质与雨湿痕迹、灯笼/伞等道具的光色、雨夜光线分析（暖冷对比/反射/景深）、环境细节"
    "（砖墙/青石板/雨丝/雾气）、镜头语言（机位/焦段/构图）、胶片质感。约 600-900 字。")


def log(*msg):
    print(f"[{time.strftime('%H:%M:%S')}]", *msg, flush=True)


def load_ev():
    return json.loads(EV.read_text(encoding="utf-8")) if EV.exists() else {"done": {}, "images": []}


def save_ev(ev):
    EV.write_text(json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")


def make_client():
    import os
    for line in open(ROOT / ".env", encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    from src.pipeline.controller import PipelineController
    return PipelineController().agnes_client


async def gen_one(client, prompt, refs=None, seed=None):
    kw = {"size_tier": "1K", "ratio": "16:9", "want_b64": True}
    if refs:
        kw["image_refs"] = refs
    if seed is not None:
        kw["seed"] = seed
    r = await client.generate_image(prompt, **kw)
    b64 = r.get("b64")
    if not b64 and r.get("url"):
        b64 = base64.b64encode(
            urllib.request.urlopen(r["url"], timeout=90).read()).decode()
    return base64.b64decode(b64)


async def sheet():
    client = make_client()
    raw = await gen_one(client, SHEET_PROMPT)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "REF_char_sheet.png").write_bytes(raw)
    ev = load_ev()
    ev["refs"] = {"sheet": str(OUT / "REF_char_sheet.png")}
    save_ev(ev)
    log("char sheet saved")


async def scene():
    client = make_client()
    raw = await gen_one(client, SCENE_PROMPT)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "REF_scene.png").write_bytes(raw)
    ev = load_ev()
    ev["refs"]["scene"] = str(OUT / "REF_scene.png")
    save_ev(ev)
    log("scene ref saved")


def long_prompts():
    import asyncio
    client = make_client()
    ev = load_ev()
    ev.setdefault("t4", {})

    async def one(key, theme):
        if ev["t4"].get(key):
            return
        instr = T4_INSTRUCTION.format(theme=theme, boy=BOY, girl=GIRL)

        async def _g():
            return await client.chat_completion([{"role": "user", "content": instr}],
                                                thinking=True, max_tokens=4000)
        msg = await _g()
        ev["t4"][key] = (msg.get("content") or "").strip()
        save_ev(ev)
        log(f"T4 prompt {key}: {len(ev['t4'][key])}字")

    async def all():
        await one("SH1", SHOTS["SH1"])
        await one("SH2", SHOTS["SH2"])
        await one("SH3", SHOTS["SH3"])
    asyncio.run(all())


def build_grid(limit=None):
    """(shot, tech, i, prompt, refs) 全格清单。"""
    ev = load_ev()
    refs_sheet = [base64.b64encode(Path(ev["refs"]["sheet"]).read_bytes()).decode()] \
        if ev.get("refs", {}).get("sheet") else None
    refs_scene = [base64.b64encode(Path(ev["refs"]["scene"]).read_bytes()).decode()] \
        if ev.get("refs", {}).get("scene") else None
    grid = []
    for shot in SHOTS:
        n = int(limit or PER_TECH)
        for i in range(1, n + 1):
            grid.append((shot, "T1", i, T1_PROMPTS[shot], None))
            if refs_sheet:
                grid.append((shot, "T2", i, T1_PROMPTS[shot] +
                             " Keep both children's hairstyles and clothes EXACTLY as the "
                             "reference sheet.", refs_sheet))
            if refs_scene:
                grid.append((shot, "T3", i, T1_PROMPTS[shot] +
                             " The alley environment must match the reference scene "
                             "(same walls, cobblestones, street lamp).", refs_scene))
            if ev.get("t4", {}).get(shot):
                grid.append((shot, "T4", i, ev["t4"][shot], None))
    return grid


async def batch(limit=None, workers=6):
    import aiofiles  # noqa: F401  (占位：实际用线程写盘)
    client = make_client()
    grid = build_grid(limit)
    ev = load_ev()
    todo = [(s, t, i, p, r) for (s, t, i, p, r) in grid
            if f"{s}_{t}_{i}" not in ev["done"]]
    log(f"grid={len(grid)} todo={len(todo)}")
    sem = asyncio.Semaphore(workers)
    counter = {"n": 0, "fail": 0}

    async def one(shot, tech, i, prompt, refs):
        key = f"{shot}_{tech}_{i}"
        async with sem:
            for attempt in range(2):
                try:
                    seed = abs(hash(key)) % 999
                    raw = await gen_one(client, prompt, refs=refs, seed=seed)
                    d = OUT / shot / tech
                    d.mkdir(parents=True, exist_ok=True)
                    f = d / f"{shot}_{tech}_{i:02d}.png"
                    f.write_bytes(raw)
                    ev["done"][key] = {"file": str(f),
                                       "sha16": hashlib.sha256(raw).hexdigest()[:16]}
                    counter["n"] += 1
                    if counter["n"] % 10 == 0:
                        save_ev(ev)
                        log(f"progress {counter['n']}/{len(todo)}")
                    return
                except Exception as e:
                    if attempt == 1:
                        ev["done"][key] = {"error": str(e)[:120]}
                        counter["fail"] += 1
                        log(f"FAIL {key}: {str(e)[:100]}")
                    else:
                        await asyncio.sleep(5)
    await asyncio.gather(*[one(*g) for g in todo])
    save_ev(ev)
    log(f"batch done: ok={counter['n']} fail={counter['fail']} total_done={len(ev['done'])}")


def preview():
    ev = load_ev()
    done = ev["done"]
    cards = {}
    for key, info in sorted(done.items()):
        if "file" not in info:
            continue
        shot, tech = key.split("_")[0], key.split("_")[1]
        cards.setdefault(shot, {}).setdefault(tech, []).append(
            {"key": key, "file": info["file"], "err": info.get("error")})
    tech_names = {"T1": "T1 短prompt直出", "T2": "T2 设定图i2i",
                  "T3": "T3 场景底图i2i", "T4": "T4 模型长prompt"}
    shot_names = {"SH1": "分镜1 雨夜递灯笼", "SH2": "分镜2 共伞同行",
                  "SH3": "分镜3 灯下对视大笑"}
    html = ["<!doctype html><meta charset='utf-8'><title>P49_IMG_02 首帧手法测试预览</title>",
            "<style>body{font-family:system-ui;background:#111;color:#eee;margin:20px}"
            "h1{font-size:20px}h2{font-size:17px;margin:28px 0 8px;color:#8fd}h3{font-size:15px;"
            "color:#fc6;margin:18px 0 6px}.grid{display:grid;grid-template-columns:repeat(auto-fill,"
            "minmax(210px,1fr));gap:8px}.card{background:#1c1c1c;border-radius:6px;overflow:hidden}"
            ".card img{width:100%;display:block}.card .cap{font-size:11px;padding:4px;color:#999}"
            ".meta{color:#888;font-size:13px}</style>",
            "<h1>P49_IMG_02 写实双人物首帧——多手法×30 测试预览</h1>"
            "<div class='meta'>手法 T1=短prompt直出 T2=设定图i2i T3=场景底图i2i T4=模型长prompt；"
            "每格 30 张。点图看原图。</div>"]
    for shot in sorted(cards):
        html.append(f"<h2>{shot_names.get(shot, shot)}</h2>")
        for tech in sorted(cards[shot]):
            lst = cards[shot][tech]
            html.append(f"<h3>{tech_names.get(tech, tech)}（{len(lst)} 张）</h3><div class='grid'>")
            for c in lst:
                rel = Path(c["file"]).relative_to(OUT.parent.parent.parent)
                # 预览页与 OUT 的相对路径：preview.html 在 projects/P49_IMG_02/output/
                rel2 = Path(c["file"]).relative_to(OUT.parent)
                html.append(f"<div class='card'><a href='{rel2.as_posix()}' target='_blank'>"
                            f"<img loading='lazy' src='{rel2.as_posix()}'></a>"
                            f"<div class='cap'>{c['key']}</div></div>")
            html.append("</div>")
    out_html = OUT.parent / "preview.html"
    out_html.write_text("\n".join(html), encoding="utf-8")
    log("preview:", out_html)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd == "sheet":
        asyncio.run(sheet())
    elif cmd == "scene":
        asyncio.run(scene())
    elif cmd == "long":
        long_prompts()
    elif cmd == "batch":
        lim = int(sys.argv[2]) if len(sys.argv) > 2 else None
        asyncio.run(batch(lim))
    elif cmd == "preview":
        preview()
    elif cmd == "prep":
        asyncio.run(sheet())
        asyncio.run(scene())
        long_prompts()
    elif cmd == "all":
        asyncio.run(sheet())
        asyncio.run(scene())
        long_prompts()
        asyncio.run(batch())
        preview()
