# -*- coding: utf-8 -*-
"""P49 图片优先测试批 01：首帧质感/提示词长度/多人物互动。

用户方向：视频暂停；首帧先做好（人物质感），图片配额充足（4000张/天）迭代快。
测试组：
  A. 提示词长度 A/B——同一主体（双马尾少女提灯笼雨夜巷口），短 prompt(~100字)
     vs 模型自写长 prompt(目标 ~2000字)，各 2 张。
  B. 多人物互动——两角色递接灯笼，3 张变体。
  C. 场景+人物合成——人物入景，2 张。

用法：python _p49_img01.py write_long   # 让 agnes-3.0-flash 写 ~2000字 prompt
      python _p49_img01.py gen_all      # 生成全部测试图
"""
import base64
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
PROJ = "P49_IMG_01"
BASE = "http://localhost:8000"
OUT = ROOT / "projects" / PROJ / "output" / "img_tests"
EV = ROOT / "_p49_img01_evidence.json"

SHORT_PROMPT = ("雨夜的中式老巷口，一个十岁中国小女孩提着一盏暖黄纸灯笼站在青石板上，"
                "双马尾辫，明黄色雨衣，湿润的石板反光，电影感构图，2D 动画风格，高细节")

LONG_INSTRUCTION = (
    "请为一张 2D 动画风格的插画写一个约 2000 字的中文图像生成提示词（只输出提示词本身，"
    "不要任何解释、标题或分段标记）。主题：雨夜的中式老巷口，一个十岁中国小女孩提着一盏"
    "暖黄纸灯笼站在青石板上。请用连续的描述性段落覆盖：人物外貌的每一处细节（发型发丝走向、"
    "五官、表情、肤色调、雨衣的材质与水珠、衣褶、鞋袜）、灯笼的构造与光色、雨的形态"
    "（雨丝密度/水洼反光/溅落水花）、巷子建筑（砖墙/木门/屋瓦/苔藓/电线）、空气透视与雾气、"
    "光源分析（灯笼暖光/远处冷色路灯/天光）、构图与镜头（机位高度/焦段/景深）、色彩脚本"
    "（主色/辅色/点缀色/饱和度策略）、2D 动画画风的具体定义（线条粗细/上色方式/边缘处理/"
    "背景绘制风格）、情绪与叙事暗示。约 2000 字，越具体越好。")


def log(*msg):
    print(f"[{time.strftime('%H:%M:%S')}]", *msg, flush=True)


def agnes():
    import os
    for line in open(ROOT / ".env", encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    from src.pipeline.controller import PipelineController
    return PipelineController().agnes_client


def gen_image(prompt, tag, seed=None):
    import asyncio
    client = agnes()

    async def _g():
        kw = {"size_tier": "1K", "ratio": "16:9", "want_b64": True}
        if seed is not None:
            kw["seed"] = seed
        return await client.generate_image(prompt, **kw)
    r = asyncio.run(_g())
    b64 = r.get("b64")
    if not b64 and r.get("url"):
        b64 = base64.b64encode(urllib.request.urlopen(r["url"], timeout=90).read()).decode()
    raw = base64.b64decode(b64)
    OUT.mkdir(parents=True, exist_ok=True)
    img = OUT / f"{tag}.png"
    img.write_bytes(raw)
    rec = {"file": str(img), "sha16": hashlib.sha256(raw).hexdigest()[:16],
           "prompt_chars": len(prompt)}
    log("saved", tag, rec["sha16"], f"prompt={len(prompt)}字")
    return rec


def write_long():
    import asyncio
    client = agnes()

    async def _g():
        return await client.chat_completion(
            [{"role": "user", "content": LONG_INSTRUCTION}], thinking=True,
            max_tokens=8000)
    msg = asyncio.run(_g())
    text = (msg.get("content") or "").strip()
    (OUT.parent / "img_tests_long_prompt.txt").write_text(text, encoding="utf-8")
    ev = json.loads(EV.read_text(encoding="utf-8")) if EV.exists() else {}
    ev["long_prompt_chars"] = len(text)
    EV.write_text(json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"长 prompt 已写：{len(text)} 字 → img_tests_long_prompt.txt")
    return text


def gen_all():
    ev = json.loads(EV.read_text(encoding="utf-8")) if EV.exists() else {}
    results = ev.setdefault("images", [])
    # A1 短 prompt ×2
    for i in (1, 2):
        results.append({"tag": f"A_short_{i}", "group": "A_prompt_len",
                        **gen_image(SHORT_PROMPT, f"A_short_{i}", seed=100 + i)})
    # A2 长 prompt ×2
    long_p = (ROOT / "projects" / PROJ / "output" / "img_tests_long_prompt.txt")
    if not long_p.exists():
        write_long()
    lp = long_p.read_text(encoding="utf-8")
    for i in (1, 2):
        results.append({"tag": f"A_long_{i}", "group": "A_prompt_len",
                        **gen_image(lp, f"A_long_{i}", seed=200 + i)})
    ev_path_write(results)
    log("A 组完成（短×2 + 长×2）")


def ev_path_write(results):
    ev = json.loads(EV.read_text(encoding="utf-8")) if EV.exists() else {}
    ev["images"] = results
    EV.write_text(json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "gen_all"
    if cmd == "write_long":
        write_long()
    elif cmd == "gen_all":
        gen_all()
