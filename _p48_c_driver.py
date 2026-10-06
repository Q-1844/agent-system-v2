# -*- coding: utf-8 -*-
"""Phase48 C：接续方式受控对照驱动（隔离项目 P48_C_Continuity）。

四条件 × 四方法矩阵（执行书 PHASE48_C_CONTINUITY_CONTROLLED_COMPARISON.md）：
  条件 A 同动作继续 / B 远景转近景身份信息不足 / C 景别机位变化 / D 上游尾帧身份已错误
  方法 M1 独立首帧 / M2 直接尾帧 / M3 重建首帧（尾帧+Look）/ M4 重建首帧+计划结束帧
条件 D 的 M2 = 决策检查（规则应拒绝错误尾帧锚定），不强迫生成。

夹具：镜头字段/末帧注入由脚本直写 store（夹具准备）；
生成一律走正式路径：first-frame / last-frame / video 任务管线 + provider 真实调用。
证据：_p48_c_evidence.json（请求/回执/hash 全量）。
用法：python _p48_c_driver.py create|sources_ff|sources_vid|targets_prep|targets_vid|review|report
"""
import base64
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://localhost:8000"
PROJ = "P48_C_Continuity"
ROOT = Path(__file__).parent
EVID = ROOT / "_p48_c_evidence.json"
sys.path.insert(0, str(ROOT))

METHODS = ["M1_independent", "M2_direct_tail", "M3_rebuilt_first", "M4_rebuilt_plus_end"]
CONDITIONS = {
    "A": {"name": "同动作继续",
          "src_ff": "medium shot, a girl with twin braids in a yellow raincoat stands at a wooden table pouring tea from a ceramic teapot into a cup, tea stream visible, mid-action",
          "src_motion": "she keeps pouring tea steadily, tea level rises",
          "tgt_plan": "同一动作继续：她把茶倒满，放下茶壶，端起茶杯",
          "tgt_ff_prompt": "medium shot, the same girl with twin braids in a yellow raincoat at the same wooden table, the cup nearly full of tea, she begins to set the teapot down",
          "tgt_motion": "she finishes pouring, sets the teapot down, lifts the cup",
          "tgt_end": "the girl holds the cup of tea with both hands, teapot set aside"},
    "B": {"name": "远景转近景身份不足",
          "src_ff": "extreme wide shot, a narrow rainy alley at dusk, a small figure of a girl with twin braids in a yellow raincoat stands far from camera under a lantern",
          "src_motion": "the distant figure walks slowly toward the lantern",
          "tgt_plan": "切近景：同一位双辫黄雨衣女孩在灯笼下的笑脸",
          "tgt_ff_prompt": "medium close-up, a girl with twin braids wearing a yellow raincoat stands under a paper lantern in the alley, face clearly visible",
          "tgt_motion": "she looks up and smiles at the lantern light",
          "tgt_end": "close on her smiling face under the lantern"},
    "C": {"name": "景别机位变化",
          "src_ff": "medium side shot, a girl with twin braids in a yellow raincoat sits at a desk painting a watercolor, brush in hand, seen from her left side",
          "src_motion": "the brush moves across the paper in steady strokes",
          "tgt_plan": "反向越肩机位：从女孩身后看画纸与完成的画",
          "tgt_ff_prompt": "over-the-shoulder shot from behind the girl with twin braids in a yellow raincoat, looking down at the desk and the half-finished watercolor painting",
          "tgt_motion": "she lays down the brush and lifts the painting to look at it",
          "tgt_end": "the painting held up facing the camera, brush resting on the desk"},
    "D": {"name": "上游尾帧身份已错误",
          "src_ff": "medium shot, a tall man with short black hair in a blue jacket stands at the same rainy alley lantern where the girl in the yellow raincoat should be, he checks his watch",
          "src_motion": "the man checks his watch then looks around",
          "tgt_plan": "切近景：按批准 Look 的双辫黄雨衣女孩（错误尾帧不得作锚）",
          "tgt_ff_prompt": "medium close-up, a girl with twin braids wearing a yellow raincoat under the paper lantern, face clearly visible",
          "tgt_motion": "she steps out from under the lantern and smiles",
          "tgt_end": "her smiling face under the lantern, rain behind"},
}
REPEATS = 3  # 每条件 3 个独立源（观察随机性）
# 条件 A/C 源镜头 v2（v1 为男孩/厨师，与批准 Look 身份矛盾——夹具缺陷修正，
# 旧镜头保留为 superseded_fixture 不入矩阵）
COND_SOURCE_IDS = {
    "A": ["s01_c01", "s01_c02", "s01_c03"],
    "B": ["s01_c04", "s01_c05", "s01_c06"],
    "C": ["s01_c07", "s01_c08", "s01_c09"],
    "D": ["s01_c10", "s01_c11", "s01_c12"],
}
COND_SOURCE_IDS_V2 = {
    "A": ["s01_c13", "s01_c14", "s01_c15"],
    "B": ["s01_c04", "s01_c05", "s01_c06"],
    "C": ["s01_c16", "s01_c17", "s01_c18"],
    "D": ["s01_c10", "s01_c11", "s01_c12"],
}
USE_SOURCE_IDS = COND_SOURCE_IDS_V2


def api(method, path, payload=None, params=""):
    url = f"{BASE}{path}{params}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        body = r.read().decode("utf-8")
    return json.loads(body) if body else {}


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def load_evid():
    if EVID.exists():
        return json.loads(EVID.read_text(encoding="utf-8"))
    return {"sources": {}, "targets": {}, "fixture": {}}


def save_evid(ev):
    EVID.write_text(json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")


def sha16(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def wait_job(job_id, timeout=2400):
    t0 = time.time()
    while time.time() - t0 < timeout:
        jobs = api("GET", f"/api/project/{PROJ}/jobs").get("jobs", [])
        me = next((j for j in jobs if j.get("job_id") == job_id), None)
        if me is None:
            return {"status": "done", "wall_s": round(time.time() - t0, 1)}
        st = me.get("state") or me.get("status")
        if st in ("done", "success", "completed"):
            return {"status": st, "wall_s": round(time.time() - t0, 1),
                    "message": me.get("message")}
        if st in ("failed", "error", "cancelled", "canceled"):
            return {"status": st, "wall_s": round(time.time() - t0, 1),
                    "error": str(me.get("error") or me.get("message"))[:300]}
        time.sleep(5)
    return {"status": "timeout", "wall_s": round(time.time() - t0, 1)}


def store():
    from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
    from src.utils.project_manager import ProjectManager
    from src.core.store import ProjectStore
    return ProjectStore(Path(ProjectManager("./projects").get_project_dir(PROJ)))


def phase_create():
    qs = urllib.parse.urlencode({
        "name": PROJ, "render_family": "2d_animation",
        "art_direction": "clean flat 2D animation, consistent character identity, warm palette"})
    try:
        r = api("POST", "/api/projects/create", None, f"?{qs}")
        log("project:", r.get("message"))
    except Exception as e:
        log("project exists?", str(e)[:80])
    # W02 角色资源 run：1 角色 + Look（批准参考）——夹具身份依据
    body = {"workflowId": "wf_c_char",
            "params": {"approveLook": "true",
                       "characterObjective": (
                           "生产 1 名测试角色与 1 套 Look：名字「小满」，女孩，双马尾辫，"
                           "明黄色雨衣，背带裤，圆脸雀斑——外观特征必须鲜明可辨。"
                           "步骤：character.create → character.generate_views → "
                           "resource.look.prepare 创建 Look「黄雨衣常服」并生成参考图。")}}
    r = api("POST", f"/api/project/{PROJ}/workflow/templates/W02/create", body)
    wf = r.get("workflowId") or "wf_c_char"
    run = api("POST", f"/api/project/{PROJ}/workflow/definitions/{wf}/runs",
              {"idempotencyKey": f"p48c-char-{int(time.time())}"})
    rid = run["run"]["runId"]
    ev = load_evid()
    ev["fixture"]["char_run"] = {"runId": rid, "workflowId": wf,
                                 "snapshot": run["run"].get("snapshot")}
    save_evid(ev)
    log("char run:", rid)
    # 轮询至审核
    for _ in range(200):
        rr = api("GET", f"/api/project/{PROJ}/workflow/runs/{rid}")
        run_j = rr.get("run") or rr
        pend = [rv for rv in run_j.get("reviews", []) if rv.get("status") == "pending"]
        if pend:
            break
        if run_j.get("status") in ("completed", "failed", "stopped", "rejected"):
            log("char run ended:", run_j.get("status"))
            return
        time.sleep(6)
    # 批准 Look（实验夹具审批，非用户关口；注记如实）
    lk = api("GET", f"/api/project/{PROJ}/resources").get("looks") or {}
    look_id = next(iter(lk)) if lk else None
    if not look_id:
        log("!! no look produced")
        return
    asset_id = ((lk.get(look_id) or {}).get("reference_asset_ids") or [None])[0]
    d = api("POST", f"/api/project/{PROJ}/workflow/runs/{rid}/reviews/"
                    f"{pend[0]['requestId']}/decision",
            {"decision": "approve", "note": "C批实验夹具审批（非用户关口）：批准 Look 作受控对照身份依据",
             "withSelection": True,
             "selectionOverride": {"kind": "look_reference", "entityId": look_id,
                                   "versionId": asset_id}})
    ev["fixture"]["look"] = {"look_id": look_id, "asset_id": asset_id,
                             "approve_receipt": d}
    save_evid(ev)
    log("look approved:", look_id, asset_id)
    # 等 run 完成
    for _ in range(60):
        rr = api("GET", f"/api/project/{PROJ}/workflow/runs/{rid}")
        run_j = rr.get("run") or rr
        if run_j.get("status") in ("completed", "failed", "stopped", "rejected"):
            ev["fixture"]["char_run"]["final_status"] = run_j.get("status")
            save_evid(ev)
            log("char run final:", run_j.get("status"))
            break
        time.sleep(6)


def look_refs_b64():
    """批准 Look 参考图 → data URI 列表（生成 image_refs 用）。
    resources 端点返回 {"resources":[...]} 列表形（2026-10 实测）；兼容旧 dict 形。"""
    ev = load_evid()
    lk = ev["fixture"].get("look") or {}
    if not lk.get("look_id"):
        return []
    r = api("GET", f"/api/project/{PROJ}/resources")
    res = r.get("resources")
    if isinstance(res, list):
        looks = {x["id"]: x for x in res if x.get("type") == "look"}
        assets = {x["id"]: x for x in res if str(x.get("type", "")).startswith("ref")
                  or x.get("type") in ("reference_asset", "asset")}
    else:
        looks = r.get("looks") or {}
        assets = r.get("assets") or {}
    lkj = looks.get(lk["look_id"]) or {}
    # 资产清单优先；清单无引用时回退 Look 生成产物文件（cover/generation candidate）
    cand_files = sorted((ROOT / "projects" / PROJ / "output" / "resources" / "looks" /
                         lk["look_id"] / "generations").glob("*/candidate_*.png"))
    aids = (lkj.get("approved_reference_asset_ids") or
            lkj.get("reference_asset_ids") or
            ([lk["asset_id"]] if lk.get("asset_id") and lk["asset_id"] != "ref_cb905855971e" else []))
    if not aids and cand_files:
        refs = []
        for p in cand_files[:1]:
            b64 = base64.b64encode(p.read_bytes()).decode()
            refs.append(f"data:image/png;base64,{b64}")
        return refs
    refs = []
    for aid in (lkj.get("approved_reference_asset_ids") or
                lkj.get("reference_asset_ids") or []):
        a = assets.get(aid) or {}
        lp = a.get("local")
        p = None
        if lp:
            cands = [ROOT / lp, ROOT / "shared_library" / "media" / f"{aid}.png"]
            p = next((c for c in cands if c.exists()), None)
        if p is None:
            base = (ROOT / "projects" / PROJ / "output" / "resources")
            p = next(iter(base.glob(f"**/{aid}*.png")), None) if base.exists() else None
        if p is None:
            p = next(iter((ROOT / "shared_library" / "media").glob(f"{aid}*")), None)
        if p is not None and p.exists():
            b64 = base64.b64encode(p.read_bytes()).decode()
            suffix = p.suffix.lstrip(".").lower() or "png"
            refs.append(f"data:image/{suffix};base64,{b64}")
        else:
            log(f"!! look ref asset file missing: {aid}")
    return refs


def mk_shot(st, shot_id, scene_id, idx, desc, ff_prompt, motion, dialogue=""):
    from src.core.models import Shot
    return Shot(shot_id=shot_id, scene_id=scene_id, index=idx,
                description=desc, prompt_en=ff_prompt, motion=motion,
                dialogue=dialogue, camera="static camera", lighting="natural light",
                link_mode="i2v", opening_source="independent", target_duration_s=4.0)


def phase_sources_ff():
    """12 个源镜头夹具 + 首帧生成/选用（按 USE_SOURCE_IDS v2 映射）。"""
    ev = load_evid()
    st = store()
    project = st.load()
    from src.core.models import Scene, Shot
    sc = next((x for x in project.scenes if x.scene_id == "s01"), None)
    if sc is None:
        sc = Scene(scene_id="s01", summary="C批源镜头", characters=[], shots=[])
        project.scenes = [sc]
    exist = {s.shot_id for s in sc.shots}
    plan = ev.get("fixture", {}).get("sources") or {}
    n = len(sc.shots)
    for cond, sids in USE_SOURCE_IDS.items():
        for rep, sid in enumerate(sids, start=1):
            cfg = CONDITIONS[cond]
            plan[sid] = {"cond": cond, "rep": rep}
            if sid not in exist:
                n += 1
                sc.shots = list(sc.shots) + [mk_shot(
                    st, sid, "s01", n,
                    f"[C批源·条件{cond}·r{rep}] {cfg['name']}",
                    cfg["src_ff"], cfg["src_motion"])]
    sc.shots = sorted(sc.shots, key=lambda s: int(s.shot_id.split("_c")[1]))
    for i, s in enumerate(sc.shots, start=1):
        s.index = i
    project.next_scene_seq = max(project.next_scene_seq, 2)
    st.save(project)
    ev.setdefault("fixture", {})["sources"] = plan
    ev["fixture"]["superseded_sources"] = ["s01_c01", "s01_c02", "s01_c03",
                                           "s01_c07", "s01_c08", "s01_c09"]
    ev["fixture"]["superseded_reason"] = ("v1 条件A/C 源（男孩/厨师）与批准 Look 身份矛盾"
                                          "（M3 重建会换身份）——夹具缺陷修正，换为女孩版本")
    save_evid(ev)
    for sid in plan:
        info = ev["sources"].setdefault(sid, {})
        if info.get("ff_chosen"):
            continue
        if not info.get("ff_job"):
            j = api("POST", f"/api/project/{PROJ}/shot/{sid}/first-frame", {"variants": 1})
            info["ff_job"] = j.get("job_id")
            save_evid(ev)
        r = wait_job(info["ff_job"])
        info["ff_result"] = r
        save_evid(ev)
        log("src ff", sid, r["status"])
        sh = st.load().get_shot(sid)
        if sh.first_frame_batches and sh.first_frame_batches[0].candidates:
            cand = sh.first_frame_batches[0].candidates[0].candidate_id
            info["ff_chosen"] = api("POST", f"/api/project/{PROJ}/shot/{sid}/first-frame/choose",
                                    {"candidate_id": cand})
            save_evid(ev)


def phase_sources_vid():
    ev = load_evid()
    st = store()
    for sid, info in ev["fixture"]["sources"].items():
        info = ev["sources"][sid]
        if info.get("video_version"):
            continue
        if not info.get("video_job"):
            j = api("POST", f"/api/project/{PROJ}/shot/{sid}/video")
            info["video_job"] = j.get("job_id")
            save_evid(ev)
            log("src video submit", sid)
        r = wait_job(info["video_job"])
        info["video_result"] = r
        save_evid(ev)
        log("src video", sid, r.get("status"), r.get("error", ""))
        sh = st.load().get_shot(sid)
        if sh.video_versions and r.get("status") == "done":
            vv = sh.video_versions[-1]
            api("POST", f"/api/project/{PROJ}/shot/{sid}/video/set-final",
                {"version_id": vv.version_id})
            info["video_version"] = vv.version_id
            vlocal = vv.local or ""
            info["video_local"] = vlocal
            vp = ROOT / vlocal if vlocal and not Path(vlocal).is_absolute() else Path(vlocal)
            if vp.exists():
                import subprocess as _sp
                pr = _sp.run(["ffprobe", "-v", "error", "-show_entries",
                              "format=duration", "-of", "json", str(vp)],
                             capture_output=True, text=True)
                try:
                    info["streams"] = {"duration": float(json.loads(
                        pr.stdout or "{}").get("format", {}).get("duration") or 4.0)}
                except Exception:
                    info["streams"] = {"duration": 4.0}
            # 末帧（视频完成后 handler 自动提取；此处核验并补记）
            ef = sh.video_end_frame
            info["end_frame_local"] = ef.local if ef else None
            info["end_frame_url"] = ef.url if ef else None
            info["end_frame_sha16"] = sha16(Path(ef.local)) if (ef and ef.local and Path(ef.local).exists()) else None
            save_evid(ev)
            log("src final+endframe", sid, vv.version_id, info["end_frame_sha16"])


def inject_first_frame(st, shot_id, img_local: Path, note: str) -> dict:
    """把指定图片注入为首帧候选并选用（夹具操作：M2 直接尾帧 / M3/M4 重建首帧）。
    rehost 取公网 URL（视频 i2v 输入必需）；记录注入依据 note。
    github Pages 就绪轮询偶发 180s 超时 → 3 次重试；
    png 内容寻址碰撞（422 真冲突，旧对象残留）→ 转 JPEG 换路径。"""
    import asyncio
    from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
    from deps import get_rehost_host
    from src.core.models import Candidate, ImageBatch, ShotStatus
    from PIL import Image as _PILImage

    def _jpeg_copy(src: Path) -> Path:
        dst = src.with_suffix(".rj.jpg")
        if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
            _PILImage.open(src).convert("RGB").save(dst, "JPEG", quality=92)
        return dst

    url = None
    upload_path = _jpeg_copy(img_local) if img_local.suffix.lower() == ".png" else img_local
    for attempt in range(3):
        try:
            url = asyncio.run(get_rehost_host().upload(str(upload_path)))
            break
        except Exception as e:
            if attempt == 2:
                raise
            log(f"rehost retry {attempt+1} for {shot_id}: {str(e)[:120]}")
            time.sleep(20)
    cid = f"c_inj_{shot_id}_{int(time.time())}"
    batch = ImageBatch(batch_id=f"ffb_inj_{shot_id}_{int(time.time())}",
                       kind="first_frame",
                       prompt_snapshot=f"[injected] {note}",
                       candidates=[Candidate(candidate_id=cid, local=str(img_local),
                                             url=url, status=ShotStatus.DONE.value,
                                             created_at=time.time())])
    def mut(s, b=batch, c=cid):
        from src.core.models import Asset
        s.first_frame_batches = list(s.first_frame_batches) + [b]
        s.first_frame.candidates = [Asset(local=x.local, url=x.url, status=x.status)
                                    for x in b.candidates]
        s.first_frame.selected_index = 0
        setattr(s.first_frame, "status", ShotStatus.DONE.value)
        s.selected_first_frame = c
    st.update_shot(shot_id, mut)
    return {"candidate_id": cid, "url": url, "local": str(img_local),
            "sha16": sha16(img_local), "note": note}


def _inject_with_url(st, shot_id, img_local: Path, url: str, note: str) -> dict:
    """用已有公网 URL 注入首帧候选（M4 复用 M3 上传，provider 输入逐字节相同）。"""
    from src.core.models import Asset, Candidate, ImageBatch, ShotStatus
    cid = f"c_inj_{shot_id}_{int(time.time())}"
    batch = ImageBatch(batch_id=f"ffb_inj_{shot_id}_{int(time.time())}",
                       kind="first_frame",
                       prompt_snapshot=f"[injected-reuse] {note}",
                       candidates=[Candidate(candidate_id=cid, local=str(img_local),
                                             url=url, status=ShotStatus.DONE.value,
                                             created_at=time.time())])
    def mut(s, b=batch, c=cid):
        s.first_frame_batches = list(s.first_frame_batches) + [b]
        s.first_frame.candidates = [Asset(local=x.local, url=x.url, status=x.status)
                                    for x in b.candidates]
        s.first_frame.selected_index = 0
        setattr(s.first_frame, "status", ShotStatus.DONE.value)
        s.selected_first_frame = c
    st.update_shot(shot_id, mut)
    return {"candidate_id": cid, "url": url, "local": str(img_local),
            "sha16": sha16(img_local), "note": note}


def gen_image(prompt: str, refs: list, out: Path) -> dict:
    """provider 真实图像生成（i2i/重建首帧/计划结束帧），记录请求与产物 hash。"""
    from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
    from src.pipeline.controller import PipelineController
    import asyncio
    ctl = PipelineController()
    client = ctl.agnes_client

    async def _g():
        return await client.generate_image(prompt, image_refs=refs or None,
                                           size_tier="1K", ratio="16:9", want_b64=True)
    r = asyncio.run(_g())
    b64 = r.get("b64")
    if not b64 and r.get("url"):
        req = urllib.request.Request(r["url"])
        b64 = base64.b64encode(urllib.request.urlopen(req, timeout=60).read()).decode()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(b64))
    return {"prompt": prompt[:200], "refs_count": len(refs), "url": r.get("url"),
            "local": str(out), "sha16": sha16(out)}


def tgt_shot_id(cond, rep, method):
    """目标镜头 id：按方法分段（M1: c01-12 / M2: c13-24 / M3: c25-36 / M4: c37-48），场景 s02。"""
    seg = {"M1_independent": 0, "M2_direct_tail": 12,
           "M3_rebuilt_first": 24, "M4_rebuilt_plus_end": 36}[method]
    idx_order = [(c, r) for c in CONDITIONS for r in range(1, REPEATS + 1)]
    n = seg + idx_order.index((cond, rep)) + 1
    return f"s02_c{n:02d}"


def src_of(cond, rep):
    return USE_SOURCE_IDS[cond][rep - 1]


def identity_check(tail_local: Path) -> dict:
    """条件 D 决策检查：尾帧身份 vs 批准 Look（Vision 模型，真实请求）。"""
    from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
    from src.pipeline.controller import PipelineController
    import asyncio
    client = PipelineController().agnes_client
    tail_b64 = base64.b64encode(Path(tail_local).read_bytes()).decode()
    look = look_refs_b64()
    if not look:
        return {"error": "no_look_refs", "reject": False}
    look_b64 = look[0].split(",", 1)[1]

    async def _c():
        return await client.chat_completion_vision(
            [{"role": "user", "content": [
                {"type": "text", "text":
                    "图1是角色设定参考。图2是从视频提取的尾帧。图2中的主要人物与图1的角色设定"
                    "是否为同一角色身份（发型/服装/体型/性别整体判断）？只回答 JSON："
                    '{"same_identity": true/false, "reason": "一句话"}'},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{look_b64}"}},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{tail_b64}"}},
            ]}], model="agnes-2.5-flash", max_tokens=200)
    r = asyncio.run(_c())
    txt = (r.get("content") or "")
    same = ("true" in txt.lower())
    return {"raw": txt[:300], "same_identity": same, "reject": not same}


def phase_targets_prep():
    """48 个目标镜头夹具 + 各方法首帧/尾帧输入准备。
    🔴 两段式（教训：单 project 对象贯穿全程会用陈旧状态覆盖任务 handler/
    choose 的写入——2026-10-06 全部 45 条目标视频因首帧槽被清空而失败）：
      阶段1：一次性创建全部镜头（单一 save）；
      阶段2：逐目标做输入准备——REST 任务官方写盘，注入走 update_shot（每次
      fresh load+save），全程不再持有/保存 project 对象。"""
    ev = load_evid()
    st = store()
    from src.core.models import Scene, Shot
    # ---- 阶段 1：镜头创建（缺失才建） ----
    project = st.load()
    order = [(c, r) for c in CONDITIONS for r in range(1, REPEATS + 1)]
    sc = next((x for x in project.scenes if x.scene_id == "s02"), None)
    if sc is None:
        sc = Scene(scene_id="s02", summary="C批目标镜头（4方法×12源）", characters=[], shots=[])
        project.scenes = list(project.scenes) + [sc]
    exist = {s.shot_id for s in sc.shots}
    n = len(sc.shots)
    created = 0
    for method in METHODS:
        for (cond, rep) in order:
            tid = tgt_shot_id(cond, rep, method)
            if tid in exist:
                continue
            cfg = CONDITIONS[cond]
            n += 1
            sc.shots = list(sc.shots) + [Shot(
                shot_id=tid, scene_id="s02", index=n,
                description=f"[C批目标·{method}·条件{cond}·r{rep}] {cfg['tgt_plan']}",
                prompt_en=cfg["tgt_ff_prompt"], motion=cfg["tgt_motion"],
                camera="static camera", lighting="natural light",
                link_mode="keyframes" if method == "M4_rebuilt_plus_end" else "i2v",
                opening_source="independent", target_duration_s=4.0)]
            created += 1
    if created:
        st.save(project)
    log("shots created:", created)
    # ---- 阶段 2：输入准备（不持有 project 对象；注入走 update_shot） ----
    for method in METHODS:
        for (cond, rep) in order:
            tid = tgt_shot_id(cond, rep, method)
            info = ev["targets"].setdefault(tid, {"cond": cond, "rep": rep,
                                                  "method": method,
                                                  "src": src_of(cond, rep)})
            if info.get("ff_ready") or info.get("rule_rejected"):
                continue
            src_info = ev["sources"][info["src"]]
            tail_local = src_info.get("end_frame_local")
            if method == "M1_independent":
                if not info.get("ff_job"):
                    j = api("POST", f"/api/project/{PROJ}/shot/{tid}/first-frame",
                            {"variants": 1})
                    info["ff_job"] = j.get("job_id")
                    save_evid(ev)
                r = wait_job(info["ff_job"])
                info["ff_result"] = r
                sh = st.load().get_shot(tid)
                if sh.first_frame_batches and sh.first_frame_batches[0].candidates:
                    cand = sh.first_frame_batches[0].candidates[0].candidate_id
                    info["ff_injection"] = api(
                        "POST", f"/api/project/{PROJ}/shot/{tid}/first-frame/choose",
                        {"candidate_id": cand})
                    info["ff_ready"] = True
                    save_evid(ev)
            elif method == "M2_direct_tail":
                cfg = CONDITIONS[cond]
                if cond == "D":
                    chk = identity_check(tail_local)
                    info["decision_check"] = chk
                    if chk.get("reject"):
                        info["rule_rejected"] = True
                        info["ff_ready"] = False
                        save_evid(ev)
                        log("M2 cond-D 正确拒绝（尾帧身份错）", tid)
                        continue
                info["ff_injection"] = inject_first_frame(
                    st, tid, Path(tail_local),
                    f"M2 直接尾帧：源 {info['src']} 实际末帧原样作首帧输入")
                info["ff_ready"] = True
                save_evid(ev)
            elif method in ("M3_rebuilt_first", "M4_rebuilt_plus_end"):
                cfg = CONDITIONS[cond]
                out = ROOT / "projects" / PROJ / "output" / "frames" / f"{tid}_rebuilt_first.png"
                # M4 与 M3 共用同一张重建首帧文件（同 hash）：M3 先生成，M4 复用
                m3_tid = tgt_shot_id(cond, rep, "M3_rebuilt_first")
                m3_file = (ROOT / "projects" / PROJ / "output" / "frames" /
                           f"{m3_tid}_rebuilt_first.png")
                if method == "M3_rebuilt_first" and not info.get("ff_injection"):
                    refs = []
                    if tail_local:
                        tb = base64.b64encode(Path(tail_local).read_bytes()).decode()
                        refs.append(f"data:image/jpeg;base64,{tb}")
                    refs += look_refs_b64()
                    rec = gen_image(
                        f"Rebuild this exact frame as a new image: keep the same "
                        f"composition, camera angle and pose as the reference photo, "
                        f"but render the character strictly as: girl with twin braids, "
                        f"yellow raincoat (approved character design). "
                        f"Scene: {cfg['tgt_ff_prompt']}",
                        refs, out)
                    info["rebuild_receipt"] = rec
                    info["ff_injection"] = inject_first_frame(
                        st, tid, out, "M3 重建首帧：尾帧+批准 Look 图生图重建（非原末帧）")
                    info["ff_ready"] = True
                    save_evid(ev)
                elif method == "M4_rebuilt_plus_end":
                    if not m3_file.exists():
                        log("!! M4 依赖 M3 首帧未生成", tid)
                        continue
                    m3_tid2 = tgt_shot_id(cond, rep, "M3_rebuilt_first")
                    m3_inj = (ev["targets"].get(m3_tid2) or {}).get("ff_injection") or {}
                    if m3_inj.get("url"):
                        # 复用 M3 同一次上传 URL：provider 收到的首帧输入与 M3 逐字节相同
                        info["ff_injection"] = _inject_with_url(
                            st, tid, m3_file, m3_inj["url"],
                            f"M4 与 M3 共用同一重建首帧文件与同一次上传 URL（hash={sha16(m3_file)}）")
                    else:
                        info["ff_injection"] = inject_first_frame(
                            st, tid, m3_file,
                            f"M4 与 M3 共用同一重建首帧文件（hash={sha16(m3_file)}）")
                    # 计划结束帧：官方 last-frame 管线（基于已选首帧）
                    sh = st.load().get_shot(tid)
                    if not sh.last_frame_batches:
                        j = api("POST", f"/api/project/{PROJ}/shot/{tid}/last-frame", {})
                        info["lf_job"] = j.get("job_id")
                        save_evid(ev)
                        r = wait_job(info["lf_job"])
                        info["lf_result"] = r
                    sh = st.load().get_shot(tid)
                    if sh.last_frame_batches and sh.last_frame_batches[0].candidates:
                        cand = sh.last_frame_batches[0].candidates[0].candidate_id
                        info["lf_chosen"] = api(
                            "POST", f"/api/project/{PROJ}/shot/{tid}/last-frame/choose",
                            {"candidate_id": cand})
                        info["ff_ready"] = True
                    save_evid(ev)
    log("targets prep done:", len(ev["targets"]))


def phase_targets_vid():
    ev = load_evid()
    st = store()
    for tid, info in ev["targets"].items():
        info = ev["targets"][tid]
        if info.get("rule_rejected"):
            continue
        if info.get("video_version"):
            continue
        if not info.get("video_job"):
            try:
                j = api("POST", f"/api/project/{PROJ}/shot/{tid}/video")
            except Exception as e:
                info["video_error"] = str(e)[:200]
                save_evid(ev)
                log("video submit failed", tid, str(e)[:100])
                continue
            info["video_job"] = j.get("job_id")
            save_evid(ev)
            log("tgt video submit", tid, info["method"])
        r = wait_job(info["video_job"])
        info["video_result"] = r
        save_evid(ev)
        log("tgt video", tid, r.get("status"), r.get("error", ""))
        if r.get("status") == "done":
            sh = st.load().get_shot(tid)
            if sh.video_versions:
                vv = sh.video_versions[-1]
                api("POST", f"/api/project/{PROJ}/shot/{tid}/video/set-final",
                    {"version_id": vv.version_id})
                info["video_version"] = vv.version_id
                info["video_local"] = vv.local
                save_evid(ev)


def phase_review():
    """抽帧 5 点 + 接缝预览片 + Vision 匿名评审。"""
    import subprocess
    ev = load_evid()
    st = store()
    rdir = ROOT / "_p48_c_review"
    rdir.mkdir(exist_ok=True)
    from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
    from src.pipeline.controller import PipelineController
    import asyncio

    async def vis(content, mx=400):
        # 每次新建 controller/client：共享 httpx AsyncClient 绑定首个事件循环，
        # 跨 asyncio.run 复用会 "Event loop is closed"（2026-10-06 实测）
        client = PipelineController().agnes_client
        return await client.chat_completion_vision(
            [{"role": "user", "content": content}],
            model="agnes-2.5-flash", max_tokens=mx)

    for tid, info in ev["targets"].items():
        info = ev["targets"][tid]
        if info.get("rule_rejected") or not info.get("video_local"):
            continue
        td = rdir / tid
        td.mkdir(exist_ok=True)
        v = Path(info["video_local"])
        if not v.exists():
            v = ROOT / info["video_local"]
        # 抽帧 0/25/50/75/100%
        dur = info.get("streams", {}).get("duration") or 4.0
        for pct in (0, 25, 50, 75, 100):
            fp = td / f"f{pct}.png"
            if not fp.exists():
                t = min(max(dur * pct / 100 - (0.04 if pct == 100 else 0), 0), dur - 0.04)
                subprocess.run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(v),
                                "-frames:v", "1", str(fp)], capture_output=True)
        # 接缝预览片：源尾 1.2s + 目标头 1.2s
        seam = td / "seam_preview.mp4"
        if not seam.exists():
            src_v = ROOT / (ev["sources"][info["src"]].get("video_local") or "")
            if src_v.exists():
                sdur = float((ev["sources"][info["src"]].get("streams") or {}).get("duration") or 4)
                subprocess.run(["ffmpeg", "-y", "-ss", f"{max(sdur - 1.2, 0):.2f}",
                                "-i", str(src_v), "-t", "1.2", "-c", "copy", "_s0.mp4"],
                               capture_output=True, cwd=str(td))
                subprocess.run(["ffmpeg", "-y", "-i", str(v), "-t", "1.2", "-c", "copy",
                                "_s1.mp4"], capture_output=True, cwd=str(td))
                with open(td / "_lst.txt", "w") as fh:
                    fh.write("file '_s0.mp4'\nfile '_s1.mp4'\n")
                subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                                "-i", "_lst.txt", "-c", "copy", seam.name],
                               capture_output=True, cwd=str(td))
                (td / "_lst.txt").unlink(missing_ok=True)
                (td / "_s0.mp4").unlink(missing_ok=True)
                (td / "_s1.mp4").unlink(missing_ok=True)
        # Vision 匿名评审（含源尾帧对照）
        if not info.get("vision_review"):
            tail_local = ev["sources"][info["src"]].get("end_frame_local")
            content = [{"type": "text", "text":
                        f"这是一条目标视频的 5 帧抽样（0/25/50/75/100%）。评审："
                        f"1) 人物身份/外观是否全程一致且合理；2) 动作是否完成计划"
                        f"「{CONDITIONS[info['cond']]['tgt_plan']}」；"
                        f"3) 是否有变形/闪切/道具错乱；4) 末帧状态。"
                        f"只答 JSON：{{\"identity_ok\":b,\"action_ok\":b,\"anomaly\":\"\",\"end_state\":\"\"}}"}]
            for pct in (0, 25, 50, 75, 100):
                fp = td / f"f{pct}.png"
                if fp.exists():
                    b = base64.b64encode(fp.read_bytes()).decode()
                    content.append({"type": "image_url",
                                    "image_url": {"url": f"data:image/png;base64,{b}"}})
            try:
                r = asyncio.run(vis(content))
                info["vision_review"] = {"raw": (r.get("content") or "")[:500]}
            except Exception as e:
                info["vision_review"] = {"error": str(e)[:200]}
            save_evid(ev)
    log("review done")


def phase_targets_reset():
    """夹具缺陷修正：清除全部目标镜头与证据（v1 条件A/C 身份矛盾），准备 v2 重跑。
    清除记录入 evidence（可追溯，不删历史文件）。"""
    ev = load_evid()
    st = store()
    project = st.load()
    sc = next((x for x in project.scenes if x.scene_id == "s02"), None)
    removed = []
    if sc is not None:
        removed = [s.shot_id for s in sc.shots]
        project.scenes = [x for x in project.scenes if x.scene_id != "s02"]
        st.save(project)
    old = ev.get("targets", {})
    ev["targets_reset"] = {"removed_shots": removed,
                           "cleared_evidence": list(old.keys()),
                           "reason": ev.get("fixture", {}).get("superseded_reason")}
    ev["targets"] = {}
    save_evid(ev)
    for f in (ROOT / "projects" / PROJ / "output" / "frames").glob("*_rebuilt_first.png"):
        f.unlink()
    log("targets reset:", len(removed), "shots removed")


def phase_report():
    ev = load_evid()
    rows = []
    for tid, info in sorted(ev["targets"].items()):
        rows.append({"tgt": tid, "cond": info["cond"], "rep": info["rep"],
                     "method": info["method"], "rejected": info.get("rule_rejected", False),
                     "video": info.get("video_result", {}).get("status"),
                     "version": info.get("video_version")})
    print(json.dumps(rows, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    ph = sys.argv[1] if len(sys.argv) > 1 else "report"
    {"create": phase_create, "sources_ff": phase_sources_ff,
     "sources_vid": phase_sources_vid, "targets_reset": phase_targets_reset,
     "targets_prep": phase_targets_prep,
     "targets_vid": phase_targets_vid, "review": phase_review,
     "report": phase_report}.get(ph, phase_report)()
