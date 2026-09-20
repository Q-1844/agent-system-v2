# -*- coding: utf-8 -*-
"""32.md Video-First Phase V1 实验驱动。

子命令:
  plan            生成 _32_video_experiments/EXPERIMENT_PLAN.jsonl（幂等，重跑覆盖）
  run [--limit N] [--only X001,X002] [--batch B]   串行执行计划（75s 间隔，断点续跑）
  status          打印当前完成/失败统计

原则（PLAN.md）:
  - 直调 client 层（mode=t2v/multi/i2v/keyframes），不进 handler（handler 无 t2v/multi 入口）
  - 同一镜跨模式共用同一 P2 prompt；P0 仅 s17_c03 基线复现
  - run1 配对 seed（同镜所有模式同 seed）；run2 无 seed
  - multi refs: 人物 look 在前 +（需要时）SIV 托管 URL 在后；逐条记录 role/来源
  - 每条真实生成写 EXPERIMENTS.jsonl；产物 videos/{exp_id}_{shot}_{mode}.mp4
  - Golden Project 只读：引用 approved 事实，不写回任何 shot 状态
"""
import asyncio
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

E32 = ROOT / "_32_video_experiments"
VIDS = E32 / "videos"
FRAMES = E32 / "frames"
PLAN_FILE = E32 / "EXPERIMENT_PLAN.jsonl"
RECORDS = E32 / "EXPERIMENTS.jsonl"
SIV_URLS = E32 / "siv_urls.json"
GOLDEN = ROOT / "projects" / "Golden_Full_Production_V1"

INTERVAL = 75.0          # 1 RPM 串行口径（31.md 校准值）
NF, FR, W, H = 121, 24, 1280, 704   # 统一 5.04s / 1280x704（§八 同镜同参）

# multi 只送场景参考的镜：画面无人物（s14_c01 空院）或人物为远景墨点剪影
# （s13_c01 EWS），look 参考无意义（§六 最小充分）。
SCENE_ONLY_MULTI = {"s13_c01", "s14_c01"}

STYLE_LINE = ("Visual production style: 2D animated image. "
              "Do not render as live-action photography or 3D rendered CGI.")

# ---------------------------------------------------------------------------
# P2（意图+时序+身份锚）与 P1（仅意图）。同一镜跨模式共用同一 prompt。
# ---------------------------------------------------------------------------
PROMPTS = {
 "s09_c01": {
  "P2": "Heavy rain on a muddy mountain road at night. A lone rider on an old horse moves slowly through the rain, hunched forward sheltering a yellowed letter inside his coat. Shen Ye: lean 25-year-old former escort, thin scar on left cheek, black low topknot, grey-black standing-collar robe and straw rain cloak with bamboo hat. He is the only person in the frame, small and weary in the vast dark landscape. Camera: slow push in toward the rider, extreme wide shot, cold dim blue night. " + STYLE_LINE,
  "P1": "Heavy rain on a muddy mountain road at night, a lone rider on an old horse moving slowly through the rain. Camera: slow push in, extreme wide shot, cold dim blue night. " + STYLE_LINE},
 "s09_c02": {
  "P2": "A man dismounts from an old horse and leads it by the reins into an overgrown way-station courtyard at night in heavy rain, knee-high weeds, wooden door half open. He leads the horse a few steps, stops, and looks up at faint candlelight inside the inn, wary and tired. Shen Ye: lean 25-year-old former escort, thin scar on left cheek, black low topknot, grey-black robe, dripping straw rain cloak. Camera: static medium shot. " + STYLE_LINE,
  "P1": "A man dismounts and leads his horse into an overgrown way-station courtyard at night in heavy rain, then looks up at faint candlelight. Camera: static medium shot. " + STYLE_LINE},
 "s09_c08": {
  "P2": "Two men face each other in close-up, faces and necks filling most of the frame, gazes locked. One candle between them lights half of each face; background pure black. Shen Ye (younger, thin scar on left cheek, black topknot) alert; Zhou Bo (elderly innkeeper, grey stubble) restrained. Exactly two people. Camera: static close-up. " + STYLE_LINE,
  "P1": "Two men face each other in close-up, gazes locked, one candle lighting half of each face against a black background. Camera: static close-up. " + STYLE_LINE},
 "s10_c01": {
  "P2": "Inside a dim woodshed lit by candlelight, three people gather around a table. Liu Yi opens a rusted iron box; a yellowed letter clearly visible inside; Shen Ye and Zhou Bo lean in to look. Triangular composition, iron box at center, warm candle glow, dark blurred background. Liu Yi: graceful woman, long black hair. Shen Ye: lean man, thin scar on left cheek. Zhou Bo: elderly innkeeper, grey stubble. Exactly three people. Camera: static medium shot. " + STYLE_LINE,
  "P1": "Inside a candlelit woodshed a woman opens a rusted iron box on a table while two men lean in to look. Camera: static medium shot, warm candlelight. " + STYLE_LINE},
 "s10_c03": {
  "P2": "Close-up of Shen Ye's face: pupils contract sharply, breathing quickens, fingers clutch the edge of a letter until knuckles whiten, tears welling — twenty years of suppressed grief near breaking. Candlelight from one side, half the face lit. Shen Ye: 25-year-old man, thin scar on left cheek, black low topknot. Camera: static close-up. " + STYLE_LINE,
  "P1": "Close-up of a man's face in candlelight: pupils contracting, fingers clutching a letter, tears welling. Camera: static close-up. " + STYLE_LINE},
 "s11_c06": {
  "P2": "An elderly innkeeper slowly rises, takes a worn old seal from inside his coat, and holds it out with a bitter smile, warm firelight on his face. Zhou Bo: elderly man, grey stubble, plain dark innkeeper clothes. Camera: static medium shot. " + STYLE_LINE,
  "P1": "An elderly man slowly rises and takes a worn old seal from his coat, holding it out with a bitter smile in warm firelight. Camera: static medium shot. " + STYLE_LINE},
 "s13_c01": {
  "P2": "Dawn after the rain: layered ink-wash mountains fill three quarters of the frame, golden morning light, low mist. Three tiny human silhouettes walk along a mountain path at the bottom of the frame, small as ink dots, no visible faces. Exactly three travelers. Camera: slow pull back, extreme wide shot; figures stay tiny in the vast landscape. " + STYLE_LINE,
  "P1": "Dawn mist over layered ink-wash mountains, three tiny silhouettes walking along a mountain path. Camera: slow pull back, extreme wide shot, golden morning light. " + STYLE_LINE},
 "s13_c06": {
  "P2": "Close-up of a woman's hand gripping a broken sword, the snapped blade catching cold morning light. She slowly raises the hand to wave farewell, fingers white-knuckled and trembling slightly. Soft dawn backlight, blurred background. Camera: slow push in on the hand. " + STYLE_LINE,
  "P1": "Close-up of a woman's hand gripping a broken sword, slowly raising to wave farewell in soft dawn light. Camera: slow push in. " + STYLE_LINE},
 "s14_c01": {
  "P2": "An old mountain way-station courtyard at dawn, empty and quiet after the rain. Water drips steadily from the eaves; far away on the mountain road faint riders approach. Cold grey-blue morning light, lingering quiet. No people in the courtyard itself. Camera: static wide shot. " + STYLE_LINE,
  "P1": "An old way-station courtyard at dawn after rain, empty and quiet, water dripping from the eaves, distant mountains. Camera: static wide shot, cold grey-blue morning light. " + STYLE_LINE},
 "s14_c04": {
  "P2": "Extreme close-up of water dripping from the tiled eave of an old way-station at dawn, each drop catching the first light; sun breaking through clouds, distant mountain outlines emerging. No people. Camera: static extreme close-up. " + STYLE_LINE,
  "P1": None},
 "s16_c05": {
  "P2": "A sword shatters: a heavy strike breaks Liu Yi's long sword into two pieces, blade fragments flashing through the air as she staggers back and falls; a bolt of lightning freezes the instant. Liu Yi: graceful woman, long black hair, slender swordswoman. Camera: static close-up on the breaking blades, sudden violent motion. " + STYLE_LINE,
  "P1": "A heavy strike shatters a swordswoman's long blade; fragments fly as she staggers back, lightning flash. Camera: static close-up, sudden motion. " + STYLE_LINE},
 "s17_c01": {
  "P2": "Knocked backward through a woodshed door into darkness, Shen Ye falls and catches a dusty rotten wooden box. He pries open the intact lock catch; inside lies a finely carved jade pendant engraved with the character 赵, and beneath it an old ledger page. Dim interior, door light falling on the box. Shen Ye: lean 25-year-old man, thin scar on left cheek, dishevelled black topknot, bloodstained grey robe. Camera: slow pan following him from doorway to floor, medium shot. " + STYLE_LINE,
  "P1": "A man falls backward through a woodshed door into darkness, catches a dusty wooden box and pries it open, finding a jade pendant inside. Camera: slow pan, medium shot, dim interior. " + STYLE_LINE},
 "s17_c03": {
  "P2": "Close-up of Shen Ye crouching in a dark woodshed, a jade pendant clutched in his hand. His gaze shifts from lost and wavering to firm and resolute; he tightens his grip on the pendant and rises to his feet, the light on his face warming as he stands. Shen Ye: 25-year-old man, thin scar on left cheek, black low topknot, grey-black robe. Camera: slow push in, close-up. " + STYLE_LINE,
  "P1": "Close-up of a crouching man whose gaze shifts from lost to resolute; he grips a jade pendant and rises to his feet as the light warms. Camera: slow push in. " + STYLE_LINE},
}

# 批次1 复现用 P0：31.md s17_c03 原编译 prompt（逐字节取自 MANIFEST）
def _load_p0():
    m = json.loads((ROOT / "_31_integration_e2e" / "MANIFEST.json").read_text(encoding="utf-8"))
    e = next(x for x in m if x.get("shot_id") == "s17_c03" and x.get("variant") == "video_auto")
    return e["prompt"]

# (shot, mode, style, with_scene_ref) 计划项；重复次数按 (style,mode,scene_ref) 计
# mode: t2v / multi / i2v / keyframes ；scene_ref 仅对 multi 有意义
MATRIX = {
 "s09_c01": [("t2v","P2",0,2), ("multi","P2",0,2), ("multi","P2",1,1), ("i2v","P2",0,1)],
 "s09_c02": [("t2v","P2",0,2), ("multi","P2",0,2), ("multi","P2",1,1)],
  # s09_c02 keyframes 撤销：首尾对目检 ff≈lf（双人克隆、无马、无动作差）——§六 Mode D 条件不成立，不强行送模型（kf_check/s09_c02_ff.png / _lf.png）
 "s09_c08": [("t2v","P2",0,2), ("multi","P2",0,2), ("multi","P2",1,1)],
 "s10_c01": [("t2v","P2",0,2), ("multi","P2",0,2), ("multi","P2",1,1)],
 "s10_c03": [("t2v","P2",0,2), ("multi","P2",0,2), ("multi","P2",1,1), ("i2v","P2",0,1)],
 "s11_c06": [("t2v","P2",0,2), ("multi","P2",0,2), ("multi","P2",1,1)],
 "s13_c01": [("t2v","P2",0,2), ("multi","P2",1,2)],
 "s13_c06": [("t2v","P2",0,1), ("multi","P2",0,1), ("multi","P2",1,1), ("keyframes","P2",0,1)],
 "s14_c01": [("t2v","P2",0,2), ("multi","P2",1,1)],
 "s14_c04": [("t2v","P2",0,2)],
 "s16_c05": [("t2v","P2",0,1), ("multi","P2",0,1), ("multi","P2",1,1), ("keyframes","P2",0,1)],
 "s17_c01": [("t2v","P2",0,2), ("multi","P2",0,2), ("multi","P2",1,1), ("i2v","P2",0,1),
             ("keyframes","P2",0,1)],  # kf 替补 s09_c02：首尾对目检可用（站→跪开箱，人物/环境一致）
 "s17_c03": [("t2v","P0",0,1), ("multi","P0",0,1), ("i2v","P0",0,1),       # 批次1 基线复现
             ("t2v","P2",0,1), ("multi","P2",0,1), ("multi","P2",1,1), ("i2v","P2",0,1)],
}

def _seed_for(shot):
    return int(hashlib.sha1(shot.encode()).hexdigest()[:8], 16) % 10**8

def build_plan():
    import _31_e2e_driver as d31
    p0 = _load_p0()
    rows = []
    for shot, items in MATRIX.items():
        for mode, style, with_scene, n in items:
            for i in range(n):
                run2 = i >= 1  # 第 2 次起无 seed
                eid = None
                rows.append({
                    "shot_id": shot, "mode": mode, "style": style,
                    "with_scene_ref": bool(with_scene), "run_index": i + 1,
                    # P0 基线复现必须匹配 31.md 原始条件（无 seed）
                    "seed": None if (run2 or style == "P0") else _seed_for(shot),
                    "prompt": p0 if style == "P0" else (PROMPTS[shot][style] or PROMPTS[shot]["P2"]),
                    "num_frames": NF, "frame_rate": FR, "width": W, "height": H,
                    "batch": "batch1" if style == "P0" else "batch2",
                })
    for idx, r in enumerate(rows, 1):
        tag = "" if r["mode"] != "multi" else ("ls" if r["with_scene_ref"] else "l")
        r["exp_id"] = f"X{idx:03d}"
        r["tag"] = f"{r['mode']}{tag}_{r['style'].lower()}{'_r2' if r['run_index']>1 else ''}"
    PLAN_FILE.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    print(f"plan written: {len(rows)} experiments -> {PLAN_FILE.name}")

# ---------------------------------------------------------------------------
# 资源解析：look 公网 URL（resources.json）+ SIV 本地→imgbb 托管（缓存）
# ---------------------------------------------------------------------------
def resolve_refs(with_scene_shots=None):
    from src.shot_binding import ShotBindingService
    from src.resources.store import ResourceStore
    from src.scene_system.store import SceneSystemStore
    svc = ShotBindingService(GOLDEN)
    rdoc = ResourceStore(GOLDEN, GOLDEN.name).load()
    sdoc = SceneSystemStore(GOLDEN, GOLDEN.name).load()
    siv_urls = json.loads(SIV_URLS.read_text(encoding="utf-8")) if SIV_URLS.exists() else {}
    cache = {}

    def shot_refs(shot_id, want_scene):
        b = svc.read_binding(shot_id)
        if b is None:
            return [], None, None
        binding = b["binding"] if isinstance(b, dict) and "binding" in b else b
        v = svc.read_validity(shot_id)
        dep = (v.get("resolved_dependencies") or {}) if isinstance(v, dict) else {}
        siv = dep.get("scene_instance_view_id")
        refs = []
        skip_looks = want_scene and shot_id in SCENE_ONLY_MULTI
        for cb in ([] if skip_looks else binding.get("character_bindings", [])):
            lk = rdoc.looks[cb["look_id"]]
            hero = (lk.approved_reference_asset_ids or [None])[0]
            url = getattr(rdoc.reference_assets[hero], "url", None) if hero else None
            if url:
                refs.append({"role": f"character_look:{cb.get('character_name')}",
                             "url": url, "asset_id": hero, "local_origin": None})
        scene_url = None
        if want_scene and siv:
            if siv in cache:
                scene_url = cache[siv]
            else:
                s = sdoc.scene_instance_views[siv]
                ra = getattr(s, "reference_asset_id", None)
                if ra:
                    a = rdoc.reference_assets[ra]
                    local = getattr(a, "local", None)
                    if local and (GOLDEN / local).exists():
                        if siv in siv_urls and siv_urls[siv].get("asset_id") == ra:
                            scene_url = siv_urls[siv]["url"]
                        else:
                            scene_url = _rehost(GOLDEN / local)
                            siv_urls[siv] = {"url": scene_url, "asset_id": ra,
                                             "local": str(local),
                                             "rehosted_at": time.time()}
                            SIV_URLS.write_text(json.dumps(siv_urls, ensure_ascii=False, indent=1),
                                                encoding="utf-8")
                cache[siv] = scene_url
        if scene_url:
            refs.append({"role": "scene_instance_view", "url": scene_url,
                         "asset_id": siv, "local_origin": siv})
        readiness = v.get("readiness") if isinstance(v, dict) else None
        binding_revision = v.get("binding_revision") if isinstance(v, dict) else None
        return refs, {"readiness": readiness, "binding_revision": binding_revision,
                      "scene_instance_view_id": siv}, binding

    return shot_refs


def _rehost(path):
    import deps
    import _31_e2e_driver as _d31
    _d31.load_env()
    if getattr(deps, "_APP_CFG", None) is None:
        import yaml
        deps._APP_CFG = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    host = deps.get_rehost_host()
    url = asyncio.get_event_loop().run_until_complete(host.upload(str(path)))
    print(f"  rehosted {Path(path).name} -> {url[:60]}")
    return url

# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------
def _records():
    if not RECORDS.exists():
        return {}
    out = {}
    for line in RECORDS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["exp_id"]] = r.get("status", "?")
    return out


def _append(rec):
    with RECORDS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _frames_and_metrics(mp4, exp_id, duration):
    ts = [0.1, 0.5, 1.0, duration * .25, duration * .5, duration * .75, max(0.05, duration - 0.15)]
    paths = []
    for i, t in enumerate(ts):
        p = FRAMES / f"{exp_id}_f{i}.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(t), "-i", str(mp4),
                        "-frames:v", "1", str(p)], check=True, timeout=120)
        paths.append(p)
    # 首秒运动代理：0.1s→1.0s 平均像素差 / 中段 50%→75% 平均像素差
    try:
        from PIL import Image, ImageChops
        def diff(a, b):
            ia, ib = Image.open(a).convert("L").resize((256, 144)), Image.open(b).convert("L").resize((256, 144))
            h = ImageChops.difference(ia, ib).histogram()
            total = sum(i * v for i, v in enumerate(h))
            return total / (256 * 144)
        early = diff(paths[0], paths[2]); mid = diff(paths[4], paths[5])
        ratio = round(early / mid, 3) if mid > 0.01 else None
    except Exception as e:
        print(f"  metric err {e}")
        early = mid = ratio = None
    # 接触表 4x2
    sheet = FRAMES / f"{exp_id}_sheet.jpg"
    try:
        from PIL import Image
        imgs = [Image.open(p).resize((320, 180)) for p in paths]
        grid = Image.new("RGB", (320 * 4, 180 * 2), "black")
        for i, im in enumerate(imgs):
            grid.paste(im, ((i % 4) * 320, (i // 4) * 180))
        grid.save(sheet, quality=80)
    except Exception as e:
        sheet = None
        print(f"  sheet err {e}")
    return {"early_diff": early, "mid_diff": mid, "early_over_mid": ratio}, sheet


PER_EXP_TIMEOUT = 900  # 单条实验看门狗：提交+轮询+下载+抽帧 正常 <6min，超 15min 必为挂死


async def _run_row(r, client, shot_refs):
    """单条实验：解析参考→构造请求→提交→轮询→下载→抽帧指标→记录。
    任何路径都恰好落一条最终记录（done/error/blocked）；continue 语义改为 return。"""
    from src.core.store import ProjectStore
    exp_id, shot_id, mode = r["exp_id"], r["shot_id"], r["mode"]
    refs, basis, _ = shot_refs(shot_id, r.get("with_scene_ref", False))
    rec_note = None
    if r.get("scene_only"):
        # v2.0 实测 ti2vid 最多 1 图（X005: "ti2vid supports at most 1 image"）——
        # 原 look+scene 双参考格改为仅场景参考（身份靠文本锚）
        refs = [x for x in refs if x["role"] == "scene_instance_view"]
    elif mode == "multi" and len(refs) > 1:
        # look-only multi 同受 1 图上限约束：多角色镜只送 binding 首位角色 look
        # （X014/X015/X019/X020 实证 2-3 look 全部 400）
        refs = refs[:1]
        rec_note = "ref_cap:v2.0 ti2vid ≤1 image, binding 首位角色"
    rec = {"exp_id": exp_id, "batch": r["batch"], "shot_id": shot_id,
           "mode": mode, "prompt_style": r["style"], "tag": r["tag"],
           "run_index": r["run_index"], "seed": r["seed"],
           "prompt": r["prompt"], "prompt_chars": len(r["prompt"]),
           "refs": refs, "num_frames": r["num_frames"], "frame_rate": r["frame_rate"],
           "width": r["width"], "height": r["height"], "model": "agnes-video-v2.0",
           "binding_basis": basis, "submitted_at": time.time()}
    if rec_note:
        rec["ref_cap_note"] = rec_note
    store = ProjectStore(GOLDEN)
    sh = store.load().get_shot(shot_id)
    if mode in ("i2v", "keyframes"):
        ff = (sh.first_frame.candidates[sh.first_frame.selected_index]
              if sh.first_frame and sh.first_frame.candidates else None)
        ff_url = getattr(ff, "url", None)
        if not ff_url:
            rec["status"] = "blocked"
            rec["error"] = "selected first frame has no public url"
            _append(rec); print(f"[{exp_id}] BLOCKED no ff url", flush=True); return
        rec["first_frame_url"] = ff_url
    if mode == "keyframes":
        lf = (sh.last_frame.candidates[sh.last_frame.selected_index]
              if sh.last_frame and sh.last_frame.candidates else None)
        lf_url = getattr(lf, "url", None)
        if not lf_url:
            rec["status"] = "blocked"
            rec["error"] = "selected last frame has no public url"
            _append(rec); print(f"[{exp_id}] BLOCKED no lf url", flush=True); return
        rec["last_frame_url"] = lf_url
    kw = {"num_frames": r["num_frames"], "frame_rate": r["frame_rate"],
          "width": r["width"], "height": r["height"]}
    if r["seed"] is not None:
        kw["seed"] = r["seed"]
    if mode == "t2v":
        kw["mode"] = "t2v"
    elif mode == "multi":
        kw.update(mode="multi", image_urls=[x["url"] for x in refs])
    elif mode == "i2v":
        kw.update(mode="i2v", image_url=rec["first_frame_url"])
    elif mode == "keyframes":
        kw.update(mode="keyframes", image_urls=[rec["first_frame_url"], rec["last_frame_url"]])
    rec["request_kwargs"] = {k: (v if k != "image_urls" else [u[:60] + "..." for u in v])
                             for k, v in kw.items()}
    dest = VIDS / f"{exp_id}_{shot_id}_{r['tag']}.mp4"
    try:
        created = await client.create_video(r["prompt"], **kw)
        vid = created.get("video_id") or created.get("task_id")
        rec["video_id"] = vid
        rec["status"] = "submitted"
        _append(rec)
        print(f"[{exp_id}] {shot_id} {r['tag']} submitted {str(vid)[:30]} refs={len(refs)}", flush=True)
        res = await client.poll_video(vid, interval=5, on_progress=lambda pct, st, w:
                                      print(f"  [{exp_id}] {st} {pct}% {int(w)}s", flush=True))
        await client.download(res["url"], dest)
        rec.update({"status": "done", "provider_url": res["url"],
                    "local_mp4": str(dest), "bytes": dest.stat().st_size,
                    "completed_at": time.time()})
        pr = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json",
                             "-show_format", "-show_streams", str(dest)],
                            capture_output=True, text=True, timeout=120)
        try:
            finfo = json.loads(pr.stdout)
            vst = next(s for s in finfo["streams"] if s["codec_type"] == "video")
            rec["ffprobe"] = {"duration": finfo["format"].get("duration"),
                              "size": f"{vst.get('width')}x{vst.get('height')}",
                              "has_audio": any(s["codec_type"] == "audio"
                                               for s in finfo["streams"])}
            dur = float(finfo["format"].get("duration") or 5)
        except Exception:
            dur = 5.0
        metrics, sheet = _frames_and_metrics(dest, exp_id, dur)
        rec["motion_proxy"] = metrics
        rec["contact_sheet"] = str(sheet) if sheet else None
        _append(rec)
        print(f"[{exp_id}] done {dest.name} dur={rec.get('ffprobe',{}).get('duration')} "
              f"motion_ratio={metrics.get('early_over_mid')}", flush=True)
    except Exception as e:  # noqa: BLE001 失败也是记录
        msg = f"{type(e).__name__}: {str(e)[:400]}"
        if "429" in msg or "rate" in msg.lower():
            print(f"[{exp_id}] 429/backoff, retry once after 90s", flush=True)
            await asyncio.sleep(90)
            try:
                created = await client.create_video(r["prompt"], **kw)
                vid = created.get("video_id") or created.get("task_id")
                rec["video_id"] = vid
                rec["status"] = "submitted"
                _append(rec)
                res = await client.poll_video(vid, interval=5)
                await client.download(res["url"], dest)
                rec.update({"status": "done", "provider_url": res["url"],
                            "local_mp4": str(dest), "bytes": dest.stat().st_size,
                            "completed_at": time.time(), "retried_429": True})
                _append(rec)
                print(f"[{exp_id}] done after retry", flush=True)
                return
            except Exception as e2:  # noqa: BLE001
                msg = f"{type(e2).__name__}: {str(e2)[:400]}"
        rec["status"] = "error"
        rec["error"] = msg
        _append(rec)
        print(f"[{exp_id}] ERROR {msg[:160]}", flush=True)
        if "quota" in msg.lower() or "配额" in msg:
            raise RuntimeError("QUOTA_SIGNAL: " + msg[:100])


async def run(limit=None, only=None, batch=None):
    import _31_e2e_driver as d31
    d31.load_env()
    client = d31.setup_clients()
    # 恢复：上次中断留下 status=submitted 的孤儿任务——先轮询收尾，不重复提交
    recs_latest = {}
    if RECORDS.exists():
        for line in RECORDS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                recs_latest[r["exp_id"]] = r
    orphans = [r for r in recs_latest.values() if r.get("status") == "submitted" and r.get("video_id")]
    for r in orphans:
        print(f"[{r['exp_id']}] orphan submitted — recovering poll", flush=True)
        dest = VIDS / f"{r['exp_id']}_{r['shot_id']}_{r['tag']}.mp4"
        try:
            res = await client.poll_video(r["video_id"], interval=5)
            await client.download(res["url"], dest)
            r.update({"status": "done", "provider_url": res["url"], "local_mp4": str(dest),
                      "bytes": dest.stat().st_size, "completed_at": time.time(),
                      "recovered_orphan": True})
            _append(r)
            print(f"[{r['exp_id']}] recovered", flush=True)
        except Exception as e:  # noqa: BLE001
            r["status"] = "error"
            r["error"] = f"recovery failed {type(e).__name__}: {str(e)[:300]}"
            _append(r)
            print(f"[{r['exp_id']}] recovery failed: {r['error'][:120]}", flush=True)
        await asyncio.sleep(5)
    rows = [json.loads(l) for l in PLAN_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    if batch:
        rows = [r for r in rows if r["batch"] == batch]
    if only:
        ids = set(only.split(","))
        rows = [r for r in rows if r["exp_id"] in ids]
    done = _records()
    rows = [r for r in rows if done.get(r["exp_id"]) != "done"]
    if limit:
        rows = rows[:int(limit)]
    print(f"to run: {len(rows)} (skipped {len(done)} recorded)")
    shot_refs = resolve_refs()
    from src.core.store import ProjectStore

    for r in rows:
        try:
            await asyncio.wait_for(_run_row(r, client, shot_refs), timeout=PER_EXP_TIMEOUT)
        except asyncio.TimeoutError:
            rec = {"exp_id": r["exp_id"], "batch": r["batch"], "shot_id": r["shot_id"],
                   "mode": r["mode"], "prompt_style": r["style"], "tag": r["tag"],
                   "run_index": r["run_index"], "seed": r["seed"],
                   "prompt": r["prompt"], "refs": [], "model": "agnes-video-v2.0",
                   "status": "error", "error": f"watchdog timeout after {PER_EXP_TIMEOUT}s",
                   "submitted_at": time.time()}
            _append(rec)
            print(f"[{r['exp_id']}] WATCHDOG TIMEOUT after {PER_EXP_TIMEOUT}s", flush=True)
        except RuntimeError as e:
            if str(e).startswith("QUOTA_SIGNAL"):
                print("quota signal — stopping run for safety", flush=True)
                break
            raise
        await asyncio.sleep(INTERVAL)


def status():
    done = _records()
    n = len(done)
    ok = sum(1 for v in done.values() if v == "done")
    err = sum(1 for v in done.values() if v == "error")
    print(f"recorded={n} done={ok} error={err}")
    plan_n = len(PLAN_FILE.read_text(encoding="utf-8").splitlines()) if PLAN_FILE.exists() else 0
    print(f"plan={plan_n} pending={plan_n - n}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "plan":
        build_plan()
    elif cmd == "run":
        limit = only = batch = None
        args = sys.argv[2:]
        for i, a in enumerate(args):
            if a == "--limit":
                limit = args[i + 1]
            elif a == "--only":
                only = args[i + 1]
            elif a == "--batch":
                batch = args[i + 1]
        asyncio.run(run(limit, only, batch))
    elif cmd == "status":
        status()
    else:
        print(__doc__)
